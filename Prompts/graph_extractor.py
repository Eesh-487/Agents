GRAPH_EXTRACTOR_SYSTEM_PROMPT = """You are a precise information-extraction engine for enterprise \
compliance knowledge graphs. You extract structured entities and relationships from company policy \
documents. You output ONLY valid JSON matching the exact schema given - no commentary, no markdown \
code fences. Any double quotes that appear inside a string value (e.g. inside a verbatim excerpt) \
MUST be escaped as \\" so the JSON stays valid."""

GRAPH_EXTRACTOR_USER_TEMPLATE = """Extract all entities and relationships from the following company policy document.

Valid entity types: Regulation, Article, Requirement, Policy, Control, Department, Risk, Process.
Valid relationship types: HAS_ARTICLE, REQUIRES, IMPLEMENTS, SATISFIES, MITIGATES, VIOLATES, RELATES_TO, OWNS.

For each entity, provide:
- id: a short stable slug (lowercase, hyphenated), e.g. "control-data-encryption"
- type: one of the valid entity types above
- name: a short human-readable name
- description: one to two sentences grounded in the document text
- source_excerpt: a short verbatim excerpt from the document that supports this entity (escape any quotes inside it)
- owner_id: if this is a Control, Process, or Policy explicitly owned/administered by a department (e.g. "the Compliance department is the designated owner of this policy"), the id of that Department entity (must also be extracted). Otherwise null.

For each relationship:
- source_id / target_id: MUST both be ids from the entities you extracted - never invent new ids
- type: MUST be copied EXACTLY, character-for-character, from this list - do not add suffixes like "_BY", do not change tense or pluralize: HAS_ARTICLE, REQUIRES, IMPLEMENTS, SATISFIES, MITIGATES, VIOLATES, RELATES_TO, OWNS
  (OWNS = department owns control/process, IMPLEMENTS = control/process implements a policy, MITIGATES = control addresses a risk, RELATES_TO = fallback when nothing more specific applies)
- description: one short sentence

Only extract what is explicitly grounded in the document text. Do not invent entities or relationships.
Do NOT create entities for glossary/definition-only terms (e.g. a "Personal Data" or "Data Principal"
definition clause) - only extract entities for operative business concepts: actual regulations, policies,
controls, departments, risks, processes, and requirements the document imposes or describes.

Every entity you extract MUST appear as source_id or target_id in at least one relationship - an entity
with no relationship at all is invalid output. If a Department, Regulation, Process, or Risk is only
mentioned in passing (e.g. named once as context, with no clear connection to a control, policy, or
another entity), do NOT extract it as a standalone entity - either omit it, or if it is genuinely
relevant, connect it with a RELATES_TO relationship to the entity it is actually associated with.

The document itself (its overarching policy) MUST always be extracted as exactly one Policy entity -
never omit this even when the extraction is otherwise sparse.

Previous attempt feedback to address (if any): {feedback}

Policy document:
---
{policy_text}
---

Respond with ONLY a JSON object of this shape:
{{"entities": [...], "relationships": [...]}}"""
