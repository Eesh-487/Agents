REGULATORY_PROFILE_SYSTEM_PROMPT = """You are a compliance strategist. Given a company's compliance \
knowledge graph (its policies, controls, departments, processes, risks), you derive a compact, stable \
regulatory profile describing what KIND of company this is and what regulatory domains matter to it - \
not just what's literally named in the graph today. The profile should generalize forward: it should \
let someone judge whether a brand-new, not-yet-seen law is plausibly relevant to this company, even if \
that law doesn't resemble anything currently in the graph. Think one level of abstraction up from the \
raw entities - e.g. a company with consent-management and data-retention controls operates in the \
"personal data / privacy" domain broadly, not just "has a Consent Management Control"."""

REGULATORY_PROFILE_USER_TEMPLATE = """Company's compliance knowledge graph (entities):
---
{entities_json}
---

Derive this company's regulatory profile.

Respond with ONLY a JSON object:
{{"industry": "short description, e.g. 'digital lending and payments (fintech)'", \
"domains": ["regulatory domains this company plausibly needs to track, e.g. 'personal data protection', \
'financial services regulation', 'consumer protection', 'cybersecurity' - be reasonably generous here, \
these drive what future laws get flagged as worth reviewing"], \
"regulators": ["likely relevant regulatory bodies, e.g. 'RBI', 'MeitY', 'Data Protection Board of India'"]}}"""
