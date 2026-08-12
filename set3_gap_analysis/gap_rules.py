"""Set 3: deterministic checks on the final merged gap list - the source of
truth, same principle as Set 1's structural_rules.py. Citations must
reference law sections/entities that actually exist, not hallucinated ones -
this is the "tight guardrails" gate for what is the highest-stakes output in
the pipeline so far.
"""

import re

VALID_SEVERITIES = {"low", "medium", "high", "critical"}

# Models reliably drift toward writing "Section 27" in prose even when told
# to use bare numbers, same lesson as Set 1's relationship-type near-misses -
# normalize obvious formatting variants rather than reject a citation that's
# actually valid just because of a prefix.
_SECTION_PREFIX_RE = re.compile(r"^(section|sec\.?|§)\s*", re.IGNORECASE)


def _normalize_section(section):
    return _SECTION_PREFIX_RE.sub("", str(section).strip()).strip()


def check_gap_list(final_gaps, valid_law_sections, valid_entity_ids):
    """valid_law_sections: set of section numbers actually present in the
    ingested law corpus (not just "1"-"44" - only sections genuinely
    retrieved/reviewed). valid_entity_ids: set of entity ids actually in the
    graph. Returns (passed, explanation).

    Mutates each gap's cited_law_sections to their normalized (bare-number)
    form in place, so the final returned list has consistent citations
    regardless of whether the model wrote "27" or "Section 27"."""

    if not isinstance(final_gaps, list):
        return False, "final_gaps is not a list."

    if not final_gaps:
        return True, "No gaps found - nothing further to validate."

    seen_ids = []
    for gap in final_gaps:
        gap_id = gap.get("id")
        if not gap_id:
            return False, f"A gap entry is missing its 'id' field: {gap}"
        seen_ids.append(gap_id)

        if not gap.get("description"):
            return False, f"Gap '{gap_id}' has no description."

        if gap.get("severity") not in VALID_SEVERITIES:
            return False, f"Gap '{gap_id}' has invalid severity '{gap.get('severity')}'."

        normalized_sections = [_normalize_section(s) for s in gap.get("cited_law_sections", [])]
        gap["cited_law_sections"] = normalized_sections
        for section in normalized_sections:
            if section not in valid_law_sections:
                return False, (
                    f"Gap '{gap_id}' cites law section '{section}', which was never actually "
                    "retrieved or reviewed - likely a hallucinated citation."
                )

        for entity_id in gap.get("related_entity_ids", []):
            if entity_id not in valid_entity_ids:
                return False, (
                    f"Gap '{gap_id}' references entity '{entity_id}', which does not exist "
                    "in the graph - likely a hallucinated reference."
                )

    if len(seen_ids) != len(set(seen_ids)):
        duplicates = {i for i in seen_ids if seen_ids.count(i) > 1}
        return False, f"Duplicate gap ids in the final list: {duplicates}"

    return True, "All structural checks passed."
