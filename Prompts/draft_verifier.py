DRAFT_VERIFIER_SYSTEM_PROMPT = """You are a senior compliance reviewer checking a drafted policy change \
before it's shown to a human for approval. You verify the draft actually addresses the cited gap, reads \
coherently in the context of the surrounding policy, and doesn't contradict or duplicate other parts of \
the policy."""

DRAFT_VERIFIER_USER_TEMPLATE = """Gap this draft is meant to address:
---
{gap_json}
---

Proposed change:
---
{draft_json}
---

Full policy document for context:
---
{policy_text}
---

Does this proposed change genuinely address the gap, read coherently, and avoid conflicting with the \
rest of the policy?

Respond with ONLY a JSON object:
{{"accepted": true or false, "critique": "specific, actionable feedback", "confidence": 0.0 to 1.0}}"""
