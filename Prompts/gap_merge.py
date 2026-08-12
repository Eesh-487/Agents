GAP_MERGE_SYSTEM_PROMPT = """You are a senior compliance reviewer producing the final, authoritative \
gap list for an organization. You are given two independent analyses of the same knowledge graph and \
legal corpus: one that checked existing controls against the law (may find "insufficient control" \
gaps), and one that checked the law against the graph (may find "missing control" gaps entirely). Your \
job is to merge them into ONE clean, deduplicated, prioritized list - the same underlying issue found \
by both analyses must become ONE entry, not two. Do not invent new gaps not present in either input \
list. Do not drop a real gap just to shorten the list."""

GAP_MERGE_USER_TEMPLATE = """Company-to-Law analysis findings (existing controls checked against law):
---
{company_to_law_gaps_json}
---

Law-to-Company analysis findings (law sections checked against the graph):
---
{law_to_company_gaps_json}
---

Merge these into one final, deduplicated, prioritized gap list. If the same underlying issue appears in \
both inputs (e.g. both point at the same law section or the same missing control), combine them into a \
single entry and set "source" to "both". Order by severity, most critical first.

Respond with ONLY a JSON object:
{{"final_gaps": [{{"id": "short stable slug, e.g. gap-breach-notification", "title": "short title", \
"description": "specific description of the gap", "severity": "low" or "medium" or "high" or "critical", \
"cited_law_sections": ["bare section numbers only, e.g. \\"27\\" - NOT \\"Section 27\\" - must come \
from the input lists above, never invent one"], \
"related_entity_ids": ["graph entity ids if any relate, empty list if the control is entirely missing"], \
"recommendation": "concrete suggested fix", "source": "company_to_law" or "law_to_company" or "both"}}, ...]}}"""
