VALID_ENTITY_TYPES = {
    "Regulation", "Article", "Requirement", "Policy", "Control", "Department", "Risk", "Process",
}
VALID_RELATIONSHIP_TYPES = {
    "HAS_ARTICLE", "REQUIRES", "IMPLEMENTS", "SATISFIES", "MITIGATES", "VIOLATES", "RELATES_TO", "OWNS",
}


def check_graph_structure(entities, relationships):
    """Deterministic, rule-based checks - the source of truth. An LLM judge can
    be wrong or inconsistent; these rules can't. Returns (passed, explanation).

    Collects every violation rather than stopping at the first, so a retry
    attempt gets complete feedback in one shot instead of fixing issues one
    at a time across multiple attempts."""

    violations = []

    if not entities:
        return False, "No entities extracted."

    ids = [e["id"] for e in entities]
    if len(ids) != len(set(ids)):
        duplicates = {i for i in ids if ids.count(i) > 1}
        violations.append(f"Duplicate entity ids: {duplicates}")

    id_set = set(ids)

    for entity in entities:
        if entity.get("type") not in VALID_ENTITY_TYPES:
            violations.append(f"Entity '{entity.get('id')}' has invalid type '{entity.get('type')}'")
        owner_id = entity.get("owner_id")
        if owner_id is not None and owner_id not in id_set:
            violations.append(f"Entity '{entity.get('id')}' has owner_id '{owner_id}' which does not exist among extracted entities")

    for rel in relationships:
        if rel.get("type") not in VALID_RELATIONSHIP_TYPES:
            violations.append(f"Relationship has invalid type '{rel.get('type')}'")
        if rel.get("source_id") not in id_set or rel.get("target_id") not in id_set:
            violations.append(f"Relationship {rel.get('source_id')} -> {rel.get('target_id')} references an unknown entity id")
        elif rel.get("source_id") == rel.get("target_id"):
            violations.append(f"Self-referencing relationship on entity '{rel.get('source_id')}'")

    if not violations:
        touched_ids = {rel["source_id"] for rel in relationships} | {rel["target_id"] for rel in relationships}
        orphans = [entity_id for entity_id in ids if entity_id not in touched_ids]
        if orphans:
            violations.append(f"Orphan entities with no relationships: {orphans}")

        if not any(entity["type"] == "Policy" for entity in entities):
            violations.append("No Policy entity found in the extracted graph.")

    if violations:
        return False, " | ".join(violations)
    return True, "All structural checks passed."
