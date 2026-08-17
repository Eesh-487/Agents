import json
import sys

from json_utils import call_agent_for_json

from llm import DISCRIMINATOR_MODEL, chatbot
from Prompts.graph_extractor import GRAPH_EXTRACTOR_SYSTEM_PROMPT, GRAPH_EXTRACTOR_USER_TEMPLATE
from Prompts.graph_verifier import GRAPH_VERIFIER_SYSTEM_PROMPT, GRAPH_VERIFIER_USER_TEMPLATE
from set1_graph_builder.structural_rules import VALID_RELATIONSHIP_TYPES, check_graph_structure
import graph_db

MAX_EXTRACTION_ATTEMPTS = 3  # retries within one extraction call, for JSON parse failures
MAX_BUILD_ATTEMPTS = 5  # retries of the whole extract -> check -> verify cycle, for content failures
VERIFIER_CONFIDENCE_THRESHOLD = 0.6

# Observed, real near-miss relationship types the model invents despite being
# given the exact allowed list - normalized before validation rather than
# rejected outright, since the model's intent is unambiguous in these cases.
# Handles irregular variants a prefix match won't catch (different word root).
_RELATIONSHIP_TYPE_ALIASES = {
    "OWNED_BY": "OWNS",
    "REQUIRED_BY": "REQUIRES",
}

# General fallback: match on the first 5 characters against each valid type's
# own first 5 characters. Catches suffix variants (IMPLEMENT, IMPLEMENTS,
# IMPLEMENTED_BY, IMPLEMENTES, IMPLEMENTING all share prefix "IMPLE") without
# hardcoding every possible variant. Valid types are distinct enough in their
# first 5 characters that false-positive collisions aren't a real risk here.
_VALID_TYPE_PREFIXES = {t[:5]: t for t in VALID_RELATIONSHIP_TYPES}


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
    """Every entity's owner_id already encodes an OWNS relationship - rather
    than relying on the relationship-extraction call to separately (and
    sometimes unreliably) reproduce it, synthesize it deterministically here.
    Directly eliminates a real, observed class of orphan-entity structural
    failures where an owned entity's OWNS edge was simply missing from the
    model's relationship output despite owner_id being set correctly."""
    existing_edges = {(rel["source_id"], rel["target_id"], rel["type"]) for rel in relationships}
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


def _drop_invalid_relationships(entities, relationships):
    """Drops relationships that structural_rules.py would reject outright and
    that can never be made valid by feedback-driven retry: a dangling
    reference to an entity id that was never extracted (a hallucinated
    reference to something only implied by the text), or a self-loop. Either
    one previously failed the ENTIRE build attempt - burning a retry and a
    rate-limited verifier call over one bad edge while 20+ other valid
    relationships were fine. Dropping them deterministically here (and
    logging what was dropped, so it's still visible for debugging) lets the
    rest of the attempt be judged on its actual merits instead of forcing a
    full re-extraction."""
    id_set = {entity["id"] for entity in entities}
    kept, dropped = [], []
    for rel in relationships:
        source_id, target_id = rel.get("source_id"), rel.get("target_id")
        if source_id not in id_set or target_id not in id_set or source_id == target_id:
            dropped.append(rel)
        else:
            kept.append(rel)
    if dropped:
        print(f"[invalid-rel] dropped {len(dropped)} relationship(s) (dangling reference or self-loop): {dropped}")
    return kept


def extract_graph(policy_text, feedback="None.", cancel_event=None):
    """Generator agent: extracts entities + relationships as JSON. Retries on
    parse failure - a real, observed failure mode with smaller models (they
    sometimes forget to escape quotes inside verbatim excerpts). `feedback` can
    also be seeded from a previous build attempt's rules/verifier rejection."""
    for attempt in range(1, MAX_EXTRACTION_ATTEMPTS + 1):
        # max_tokens well above the class default (1000) - a full entities +
        # relationships extraction for a real policy document genuinely needs it,
        # and a silent truncation was previously masquerading as a parse failure.
        # temperature lowered from the class default (0.7) - extraction is a
        # structured, exhaustive-coverage task, not a creative one; 0.7 was
        # observed to swing entity counts wildly between retries on the SAME
        # document (23/23/18/12), which independently explains orphan and
        # missing-Policy-entity failures on top of whatever the retry feedback
        # was trying to fix. Low but nonzero to preserve retry-to-retry adaptability.
        agent = chatbot(system=GRAPH_EXTRACTOR_SYSTEM_PROMPT, max_tokens=4000, temperature=0.2)
        prompt = GRAPH_EXTRACTOR_USER_TEMPLATE.format(policy_text=policy_text, feedback=feedback)
        data, error = call_agent_for_json(agent, prompt, cancel_event=cancel_event)
        if error is None and data is not None and "entities" in data and "relationships" in data:
            return data["entities"], data["relationships"]
        error = error or "response was missing 'entities' or 'relationships'"
        print(f"[extract] attempt {attempt} failed to parse: {error}")
        feedback = (
            f"Your previous output could not be parsed as valid JSON ({error}). "
            "Make sure every double quote inside a string value is escaped as \\\"."
        )
    raise RuntimeError("Entity/relationship extraction failed to produce valid JSON after retries.")


def verify_graph(policy_text, entities, relationships, cancel_event=None):
    """LLM judge agent. Advisory, not authoritative - see structural_rules.py
    for the actual source of truth. Returns (accepted, critique, confidence)."""
    agent = chatbot(system=GRAPH_VERIFIER_SYSTEM_PROMPT, model=DISCRIMINATOR_MODEL, temperature=0.0)
    graph_json = json.dumps({"entities": entities, "relationships": relationships}, indent=2)
    prompt = GRAPH_VERIFIER_USER_TEMPLATE.format(policy_text=policy_text, graph_json=graph_json)
    verdict, error = call_agent_for_json(agent, prompt, cancel_event=cancel_event)
    if error is not None:
        return False, f"Verifier output could not be parsed: {error}", 0.0
    return verdict.get("accepted", False), verdict.get("critique", ""), float(verdict.get("confidence", 0.0))


def build_graph(policy_path, cancel_event=None):
    with open(policy_path, "r", encoding="utf-8") as f:
        policy_text = f.read()

    feedback = "None."
    result = None

    for build_attempt in range(1, MAX_BUILD_ATTEMPTS + 1):
        print(f"--- Build attempt {build_attempt}/{MAX_BUILD_ATTEMPTS} ---")

        print("Extracting entities and relationships...")
        entities, relationships = extract_graph(policy_text, feedback=feedback, cancel_event=cancel_event)
        relationships = _normalize_relationship_types(relationships)
        relationships = _synthesize_owner_relationships(entities, relationships)
        relationships = _drop_invalid_relationships(entities, relationships)
        print(f"Extracted {len(entities)} entities, {len(relationships)} relationships.")

        print("Running structural rules check (source of truth)...")
        rules_passed, rules_explanation = check_graph_structure(entities, relationships)
        print(f"Structural check: {'PASSED' if rules_passed else 'FAILED'} - {rules_explanation}")

        print("Running LLM verifier agent...")
        verifier_accepted, critique, confidence = verify_graph(
            policy_text, entities, relationships, cancel_event=cancel_event
        )
        print(f"Verifier: accepted={verifier_accepted} confidence={confidence:.2f} critique={critique}")

        # Rules are the actual source of truth (per your call earlier): they gate
        # whether this writes. The verifier's opinion is attached as review notes,
        # not an equal veto - observed in practice to reject structurally-clean
        # graphs while returning the same confidence (0.80) regardless of real
        # quality, which means treating it as a hard gate would never converge.
        if not verifier_accepted or confidence < VERIFIER_CONFIDENCE_THRESHOLD:
            print("NOTE: LLM verifier flagged concerns (see verifier_critique) - rules passing is what gates the write.")

        if rules_passed:
            print("Writing to Neo4j...")
            graph_db.clear_graph()
            graph_db.upsert_entities(entities)
            graph_db.upsert_relationships(relationships)
            print("Done.")
            return {
                "status": "written",
                "build_attempts": build_attempt,
                "entity_count": len(entities),
                "relationship_count": len(relationships),
                "verifier_accepted": verifier_accepted,
                "verifier_confidence": confidence,
                "verifier_critique": critique,
            }

        result = {
            "status": "rejected",
            "build_attempts": build_attempt,
            "entity_count": len(entities),
            "relationship_count": len(relationships),
            "rules_passed": rules_passed,
            "rules_explanation": rules_explanation,
            "verifier_accepted": verifier_accepted,
            "verifier_confidence": confidence,
            "verifier_critique": critique,
        }
        feedback = (
            f"Your previous attempt was rejected by the structural rules check. "
            f"Rules violation: {rules_explanation} "
            f"Additional reviewer notes (address if relevant): {critique}"
        ).strip()
        print(f"Rejected - retrying with feedback: {feedback}\n")

    print(f"Gave up after {MAX_BUILD_ATTEMPTS} build attempts. Nothing was written to Neo4j.")
    return result


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "data/sample_policies/nimbuspay_data_privacy_retention_policy.txt"
    result = build_graph(path)
    print(json.dumps(result, indent=2))
