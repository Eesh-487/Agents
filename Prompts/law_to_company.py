LAW_TO_COMPANY_SYSTEM_PROMPT = """You are a compliance analyst checking whether a company's knowledge \
graph has ANY control, policy, or process addressing each given legal provision. You are given a batch \
of law sections and the company's COMPLETE knowledge graph (small enough to review in full). For each \
section, decide whether any graph entity addresses it. Sections that are purely definitional, procedural \
(e.g. establishing a government board, rulemaking powers, appeals process), or otherwise impose no \
direct obligation on a company processing personal data should be skipped entirely - only report on \
sections that impose a real operational obligation."""

LAW_TO_COMPANY_USER_TEMPLATE = """Law sections to check (batch):
---
{law_sections_batch}
---

The company's complete knowledge graph:

Entities:
---
{all_entities_json}
---

Relationships:
---
{all_relationships_json}
---

For each section above that imposes a real operational obligation, does ANY entity in the graph address \
it? Skip purely definitional/procedural sections entirely - do not include them in your output at all.

Respond with ONLY a JSON object:
{{"gaps": [{{"law_section": "bare section number only, e.g. \\"27\\" - NOT \\"Section 27\\"", "law_summary": "one sentence describing the obligation", \
"has_corresponding_control": true or false, "related_entity_ids": ["ids of any partially-related \
entities, empty if none"], "gap_description": "specific description of what's missing or insufficient", \
"severity": "low" or "medium" or "high" or "critical", "recommendation": "concrete suggested fix"}}, ...]}}

Only include sections where has_corresponding_control is false OR the existing control is clearly \
insufficient. Do not include sections that are already adequately addressed."""
