import json
import os
import re
import sys

from json_utils import call_agent_for_json

from llm import DISCRIMINATOR_MODEL, chatbot
from Prompts.graph_extractor import GRAPH_EXTRACTOR_SYSTEM_PROMPT, GRAPH_EXTRACTOR_USER_TEMPLATE
from Prompts.graph_verifier import GRAPH_VERIFIER_SYSTEM_PROMPT, GRAPH_VERIFIER_USER_TEMPLATE
from set1_graph_builder.structural_rules import VALID_ENTITY_TYPES, VALID_RELATIONSHIP_TYPES, check_graph_structure
import graph_db

MAX_EXTRACTION_ATTEMPTS = 3  # retries within one section's extraction call, for JSON/shape failures
VERIFIER_CONFIDENCE_THRESHOLD = 0.6

# Extraction is the quality-critical call, so it gets its own model knob rather
# than riding GENERATOR_MODEL: the rest of the pipeline can stay on a smaller
# model while this one runs 8B. Per-section prompts are short enough that 8B no
# longer trips the Metal GPU watchdog the way a whole-document call did.
EXTRACTOR_MODEL = os.getenv("EXTRACTOR_MODEL", "mlx-community/Qwen3-8B-4bit")

# Top-level policy headings: "3. DATA COLLECTION PRINCIPLES". Subsections
# ("3.1 ...") don't match - no space-then-capitals directly after "N.".
_SECTION_HEADING = re.compile(r"^\d+\.\s+[A-Z][A-Z ,&/-]+$", re.MULTILINE)


_RELATIONSHIP_TYPE_ALIASES = {
    "OWNED_BY": "OWNS",
    "REQUIRED_BY": "REQUIRES",
}

_VALID_TYPE_PREFIXES = {t[:5]: t for t in VALID_RELATIONSHIP_TYPES}

# (allowed source types, allowed target types). An edge that fits reversed is
# swapped - small models routinely emit Control -OWNS-> Department - and one
# that fits neither way is dropped. Types not listed here are left alone.
_EDGE_SHAPES = {
    "OWNS": ({"Department"}, {"Control", "Process", "Policy"}),
    "MITIGATES": ({"Control", "Process", "Policy"}, {"Risk"}),
    "HAS_ARTICLE": ({"Regulation"}, {"Article"}),
    "SATISFIES": ({"Control", "Process", "Policy"}, {"Requirement", "Regulation", "Article"}),
}


def _normalize_relationship_types(relationships):
    for rel in relationships:
        rel_type = (rel.get("type") or "").strip().upper()
        if rel_type in VALID_RELATIONSHIP_TYPES:
            rel["type"] = rel_type
        elif rel_type in _RELATIONSHIP_TYPE_ALIASES:
            rel["type"] = _RELATIONSHIP_TYPE_ALIASES[rel_type]
        elif rel_type[:5] in _VALID_TYPE_PREFIXES:
            rel["type"] = _VALID_TYPE_PREFIXES[rel_type[:5]]
    return relationships


def _synthesize_owner_relationships(entities, relationships):
    # .get, not [] - this runs before _drop_invalid_relationships, so a model
    # that omits a key on one relationship would otherwise KeyError here and
    # kill the whole build instead of having that one relationship dropped.
    existing_edges = {
        (rel.get("source_id"), rel.get("target_id"), rel.get("type")) for rel in relationships
    }
    entity_ids = {entity["id"] for entity in entities}

    for entity in entities:
        owner_id = entity.get("owner_id")
        if not owner_id or owner_id not in entity_ids:
            continue
        edge = (owner_id, entity["id"], "OWNS")
        if edge not in existing_edges:
            relationships.append(
                {
                    "source_id": owner_id,
                    "target_id": entity["id"],
                    "type": "OWNS",
                    "description": f"Synthesized from {entity['id']}'s owner_id (deterministic, not model-derived).",
                }
            )
            existing_edges.add(edge)

    return relationships


def _clean_entities(entities):
    """Drops entities with an invalid type and clears owner_ids that don't
    point at an extracted Department - both would otherwise fail the rules
    check for the whole graph over one bad field."""
    kept = [entity for entity in entities if entity.get("type") in VALID_ENTITY_TYPES]
    if len(kept) != len(entities):
        print(f"[clean] dropped {len(entities) - len(kept)} entity(ies) with an invalid type")
    departments = {entity["id"] for entity in kept if entity["type"] == "Department"}
    for entity in kept:
        if entity.get("owner_id") not in departments:
            entity["owner_id"] = None
    return kept


def _drop_invalid_relationships(entities, relationships):
    """Drops dangling/self-loop edges, fixes reversed direction for the typed
    edges in _EDGE_SHAPES (dropping ones that fit neither way), and removes
    duplicates - including the duplicate a swapped edge creates against a
    synthesized OWNS."""
    types = {entity["id"]: entity["type"] for entity in entities}
    kept, dropped, swapped, seen = [], [], 0, set()
    for rel in relationships:
        source_id, target_id = rel.get("source_id"), rel.get("target_id")
        if source_id not in types or target_id not in types or source_id == target_id:
            dropped.append(rel)
            continue
        shape = _EDGE_SHAPES.get(rel["type"])
        if shape and not (types[source_id] in shape[0] and types[target_id] in shape[1]):
            if types[target_id] in shape[0] and types[source_id] in shape[1]:
                rel["source_id"], rel["target_id"] = target_id, source_id
                swapped += 1
            else:
                dropped.append(rel)
                continue
        edge = (rel["source_id"], rel["target_id"], rel["type"])
        if edge in seen:
            continue
        seen.add(edge)
        kept.append(rel)
    if swapped:
        print(f"[invalid-rel] reversed {swapped} relationship(s) that pointed the wrong way")
    if dropped:
        print(f"[invalid-rel] dropped {len(dropped)} relationship(s) (dangling, self-loop or wrong entity types): {dropped}")
    return kept


def _attach_orphans(entities, relationships):
    """Every entity came out of this policy's text, so an entity the model
    left unconnected gets a RELATES_TO from the Policy rather than being
    thrown away - marked as synthesized so it's never mistaken for a
    model-grounded edge."""
    policy_id = next((entity["id"] for entity in entities if entity["type"] == "Policy"), None)
    if policy_id is None:
        return relationships
    touched = {rel["source_id"] for rel in relationships} | {rel["target_id"] for rel in relationships}
    for entity in entities:
        if entity["id"] not in touched and entity["id"] != policy_id:
            relationships.append(
                {
                    "source_id": policy_id,
                    "target_id": entity["id"],
                    "type": "RELATES_TO",
                    "description": "Synthesized: extracted from this policy, but no specific relationship was given (deterministic, not model-derived).",
                }
            )
    return relationships


_REQUIRED_ENTITY_KEYS = ("id", "type", "name")
_REQUIRED_RELATIONSHIP_KEYS = ("source_id", "target_id", "type")


def _shape_error(entities, relationships):
    """Every consumer downstream - the normalizers here, structural_rules,
    graph_db - indexes these dicts by key. Catching a missing key once, here,
    turns what would be a KeyError crash mid-build into one more retry with
    feedback, which is the mechanism this loop already has for bad output.

    Returns an error string, or None when the shape is usable."""
    if not isinstance(entities, list) or not isinstance(relationships, list):
        return "'entities' and 'relationships' must both be JSON arrays"

    for index, entity in enumerate(entities):
        if not isinstance(entity, dict):
            return f"entity at position {index} is not a JSON object"
        missing = [key for key in _REQUIRED_ENTITY_KEYS if not entity.get(key)]
        if missing:
            return f"entity at position {index} is missing required key(s): {', '.join(missing)}"

    for index, rel in enumerate(relationships):
        if not isinstance(rel, dict):
            return f"relationship at position {index} is not a JSON object"
        missing = [key for key in _REQUIRED_RELATIONSHIP_KEYS if not rel.get(key)]
        if missing:
            return f"relationship at position {index} is missing required key(s): {', '.join(missing)}"

    return None


def _split_sections(policy_text):
    """Returns (header, sections): the text before the first top-level heading
    (title, document id) and each "N. HEADING" block. A document with no such
    headings is treated as one section."""
    starts = [match.start() for match in _SECTION_HEADING.finditer(policy_text)]
    if not starts:
        return "", [policy_text.strip()]
    ends = starts[1:] + [len(policy_text)]
    return policy_text[: starts[0]].strip(), [policy_text[a:b].strip() for a, b in zip(starts, ends)]


def extract_section(header, section_text, known_entities, cancel_event=None):
    """One extraction call over one section. Returns (entities, relationships),
    or None if the model never produced usable JSON."""
    known = "\n".join(f"- {e['id']} | {e['type']} | {e['name']}" for e in known_entities) or "(none yet)"
    feedback = "None."
    for attempt in range(1, MAX_EXTRACTION_ATTEMPTS + 1):
        agent = chatbot(system=GRAPH_EXTRACTOR_SYSTEM_PROMPT, model=EXTRACTOR_MODEL, max_tokens=3000, temperature=0.2, cancel_event=cancel_event)
        prompt = GRAPH_EXTRACTOR_USER_TEMPLATE.format(
            document_header=header, known_entities=known, section_text=section_text, feedback=feedback
        )
        data, error = call_agent_for_json(agent, prompt, cancel_event=cancel_event)
        if error is None and isinstance(data, dict) and "entities" in data and "relationships" in data:
            shape_error = _shape_error(data["entities"], data["relationships"])
            if shape_error is None:
                return data["entities"], data["relationships"]
            print(f"[extract] attempt {attempt} produced malformed output: {shape_error}")
            feedback = (
                f"Your previous output was valid JSON but structurally wrong: {shape_error}. "
                "Every entity needs a non-empty 'id', 'type' and 'name'. Every relationship needs a "
                "non-empty 'source_id', 'target_id' and 'type'. Do not omit these keys."
            )
            continue
        error = error or "response was missing 'entities' or 'relationships'"
        print(f"[extract] attempt {attempt} failed to parse: {error}")
        feedback = (
            f"Your previous output could not be parsed as valid JSON ({error}). "
            "Make sure every double quote inside a string value is escaped as \\\"."
        )
    return None


def _name_key(name):
    # "Compliance Department" and "Compliance" are one department.
    return re.sub(r"\bdepartment\b|[^a-z0-9]", "", name.lower())


def _merge_section(entities, relationships, new_entities, new_relationships):
    """Folds one section's output into the running graph. An entity whose id
    or name is already known - or a second Policy - is merged into the
    existing one, so a concept re-emitted by a later section (or emitted
    under two types) becomes one node, and its edges are re-pointed."""
    ids = {entity["id"] for entity in entities}
    names = {_name_key(entity["name"]): entity["id"] for entity in entities}
    policy_id = next((entity["id"] for entity in entities if entity["type"] == "Policy"), None)
    remap, added = {}, []
    for entity in new_entities:
        existing = (
            entity["id"] if entity["id"] in ids
            else names.get(_name_key(entity["name"]))
            or (policy_id if entity["type"] == "Policy" else None)
        )
        if existing:
            remap[entity["id"]] = existing
            continue
        entities.append(entity)
        added.append(entity)
        ids.add(entity["id"])
        names[_name_key(entity["name"])] = entity["id"]
        if entity["type"] == "Policy":
            policy_id = entity["id"]
    for entity in added:
        entity["owner_id"] = remap.get(entity.get("owner_id"), entity.get("owner_id"))
    for rel in new_relationships:
        rel["source_id"] = remap.get(rel["source_id"], rel["source_id"])
        rel["target_id"] = remap.get(rel["target_id"], rel["target_id"])
        relationships.append(rel)


def verify_graph(policy_text, entities, relationships, cancel_event=None):
    agent = chatbot(system=GRAPH_VERIFIER_SYSTEM_PROMPT, model=DISCRIMINATOR_MODEL, temperature=0.0, cancel_event=cancel_event)
    graph_json = json.dumps({"entities": entities, "relationships": relationships}, indent=2)
    prompt = GRAPH_VERIFIER_USER_TEMPLATE.format(policy_text=policy_text, graph_json=graph_json)
    verdict, error = call_agent_for_json(agent, prompt, cancel_event=cancel_event)
    if error is not None:
        return False, f"Verifier output could not be parsed: {error}", 0.0
    return verdict.get("accepted", False), verdict.get("critique", ""), float(verdict.get("confidence", 0.0))


def build_graph(policy_path, cancel_event=None):
    """Extracts section by section (short prompts: no GPU-watchdog timeouts on
    8B, and a model reads a short passage far more thoroughly than a whole
    document), merges, repairs deterministically, then gates the Neo4j write
    on the structural rules. Nothing is re-extracted on failure: every repair
    a retry used to ask the model for is now done in code."""
    with open(policy_path, "r", encoding="utf-8") as f:
        policy_text = f.read()

    header, sections = _split_sections(policy_text)
    entities, relationships, failed_sections = [], [], []

    for index, section_text in enumerate(sections, start=1):
        title = section_text.splitlines()[0][:60]
        print(f"--- Section {index}/{len(sections)}: {title} ---")
        result = extract_section(header, section_text, entities, cancel_event=cancel_event)
        if result is None:
            print(f"[extract] section '{title}' produced no usable JSON after {MAX_EXTRACTION_ATTEMPTS} attempts - skipped")
            failed_sections.append(title)
            continue
        new_entities, new_relationships = result
        _merge_section(entities, relationships, new_entities, new_relationships)
        print(f"Section {index}: +{len(new_entities)} entities, +{len(new_relationships)} relationships (before merge)")

    entities = _clean_entities(entities)
    relationships = _normalize_relationship_types(relationships)
    relationships = _synthesize_owner_relationships(entities, relationships)
    relationships = _drop_invalid_relationships(entities, relationships)
    relationships = _attach_orphans(entities, relationships)
    print(f"Merged graph: {len(entities)} entities, {len(relationships)} relationships.")

    print("Running structural rules check (source of truth)...")
    rules_passed, rules_explanation = check_graph_structure(entities, relationships)
    print(f"Structural check: {'PASSED' if rules_passed else 'FAILED'} - {rules_explanation}")

    print("Running LLM verifier agent...")
    verifier_accepted, critique, confidence = verify_graph(
        policy_text, entities, relationships, cancel_event=cancel_event
    )
    print(f"Verifier: accepted={verifier_accepted} confidence={confidence:.2f} critique={critique}")
    if not verifier_accepted or confidence < VERIFIER_CONFIDENCE_THRESHOLD:
        print("NOTE: LLM verifier flagged concerns (see verifier_critique) - rules passing is what gates the write.")

    result = {
        "status": "rejected",
        "section_count": len(sections),
        "failed_sections": failed_sections,
        "entity_count": len(entities),
        "relationship_count": len(relationships),
        "rules_passed": rules_passed,
        "rules_explanation": rules_explanation,
        "verifier_accepted": verifier_accepted,
        "verifier_confidence": confidence,
        "verifier_critique": critique,
    }
    if not rules_passed:
        print("Rules check failed. Nothing was written to Neo4j.")
        return result

    print("Writing to Neo4j...")
    graph_db.clear_graph()
    graph_db.upsert_entities(entities)
    graph_db.upsert_relationships(relationships)
    print("Done.")
    return {**result, "status": "written"}


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "data/sample_policies/nimbuspay_data_privacy_retention_policy.txt"
    result = build_graph(path)
    print(json.dumps(result, indent=2))
