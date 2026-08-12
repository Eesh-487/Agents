"""Set 4: drafts and verifies inline policy-change suggestions per gap, and
manages their lifecycle (pending -> accepted/rejected/edited) so a frontend
can render them against the real document and let a human resolve them.

Same three-layer pattern as every other set: Drafter (generator) -> Draft
Verifier (discriminator) -> deterministic rules check (does the anchor
excerpt actually exist verbatim in the document - the source of truth,
same grounding discipline as Set 1's source_excerpt requirement).
"""
import json
import os
from datetime import datetime, timezone

from json_utils import call_agent_for_json
from llm import DISCRIMINATOR_MODEL, chatbot
from Prompts.draft_verifier import DRAFT_VERIFIER_SYSTEM_PROMPT, DRAFT_VERIFIER_USER_TEMPLATE
from Prompts.policy_drafter import POLICY_DRAFTER_SYSTEM_PROMPT, POLICY_DRAFTER_USER_TEMPLATE
from set4_remediation import version_store

SUGGESTIONS_PATH = "data/remediation/suggestions.json"
MAX_DRAFT_ATTEMPTS = 3
VERIFIER_CONFIDENCE_THRESHOLD = 0.6


def _load_suggestions():
    if not os.path.exists(SUGGESTIONS_PATH):
        return []
    with open(SUGGESTIONS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_suggestions(suggestions):
    os.makedirs(os.path.dirname(SUGGESTIONS_PATH), exist_ok=True)
    with open(SUGGESTIONS_PATH, "w", encoding="utf-8") as f:
        json.dump(suggestions, f, indent=2)


def clear_suggestions():
    _save_suggestions([])


def draft_suggestion_for_gap(gap, policy_text):
    """Generator -> discriminator -> deterministic rules-check for one gap.
    Returns a suggestion dict, or None if no valid, grounded draft could be
    produced after retries (logged, not silently skipped)."""
    feedback = "None."
    for attempt in range(1, MAX_DRAFT_ATTEMPTS + 1):
        agent = chatbot(system=POLICY_DRAFTER_SYSTEM_PROMPT, temperature=0.3, max_tokens=800)
        prompt = POLICY_DRAFTER_USER_TEMPLATE.format(
            gap_json=json.dumps(gap, indent=2), policy_text=policy_text, feedback=feedback
        )
        draft, error = call_agent_for_json(agent, prompt)

        if error is not None:
            feedback = f"Your previous output could not be parsed: {error}"
            print(f"[suggestions] gap '{gap.get('id')}' attempt {attempt}: {feedback}")
            continue

        anchor = draft.get("anchor_excerpt", "")
        # Rules check (source of truth): the anchor must be real, verbatim
        # text from the document - never a hallucinated position.
        if not anchor or anchor not in policy_text:
            feedback = "Your anchor_excerpt must be copied verbatim from the policy document above - it wasn't found in the text."
            print(f"[suggestions] gap '{gap.get('id')}' attempt {attempt}: anchor not found in document")
            continue

        verifier_agent = chatbot(system=DRAFT_VERIFIER_SYSTEM_PROMPT, model=DISCRIMINATOR_MODEL, temperature=0.0)
        verifier_prompt = DRAFT_VERIFIER_USER_TEMPLATE.format(
            gap_json=json.dumps(gap, indent=2),
            draft_json=json.dumps(draft, indent=2),
            policy_text=policy_text,
        )
        verdict, verror = call_agent_for_json(verifier_agent, verifier_prompt)
        if verror is not None:
            feedback = f"Verifier could not be reached: {verror}"
            print(f"[suggestions] gap '{gap.get('id')}' attempt {attempt}: {feedback}")
            continue

        confidence = float(verdict.get("confidence", 0.0))
        if not verdict.get("accepted") or confidence < VERIFIER_CONFIDENCE_THRESHOLD:
            feedback = verdict.get("critique", "verifier rejected the draft")
            print(f"[suggestions] gap '{gap.get('id')}' attempt {attempt}: verifier rejected - {feedback}")
            continue

        return {
            "id": f"suggestion-{gap['id']}",
            "gap_id": gap["id"],
            "gap_title": gap.get("title", ""),
            "severity": gap.get("severity", ""),
            "operation": draft.get("operation"),
            "anchor_excerpt": anchor,
            "suggested_text": draft.get("suggested_text", ""),
            "final_text": draft.get("suggested_text", ""),
            "rationale": draft.get("rationale", ""),
            "verifier_confidence": confidence,
            "status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    print(f"[suggestions] gap '{gap.get('id')}' could not produce a grounded, verified draft after {MAX_DRAFT_ATTEMPTS} attempts")
    return None


def generate_suggestions(gaps):
    """Drafts a suggestion for every gap, stores the batch (replacing any
    previous pending batch), returns it."""
    policy_text = version_store.read_current_policy()
    suggestions = []
    for gap in gaps:
        suggestion = draft_suggestion_for_gap(gap, policy_text)
        if suggestion is not None:
            suggestions.append(suggestion)
    _save_suggestions(suggestions)
    return suggestions


def get_suggestions():
    return _load_suggestions()


def update_suggestion(suggestion_id, status, final_text=None):
    """status: 'accepted' | 'rejected' | 'edited' (edited implies accepted
    with human-modified wording - final_text should be provided)."""
    suggestions = _load_suggestions()
    for suggestion in suggestions:
        if suggestion["id"] == suggestion_id:
            suggestion["status"] = status
            if final_text is not None:
                suggestion["final_text"] = final_text
            _save_suggestions(suggestions)
            return suggestion
    raise ValueError(f"No suggestion with id '{suggestion_id}'")


def assemble_final_draft():
    """Applies every accepted suggestion's final_text to the current policy
    text, anchored on each suggestion's verbatim anchor_excerpt."""
    policy_text = version_store.read_current_policy()

    for suggestion in _load_suggestions():
        if suggestion["status"] not in ("accepted", "edited"):
            continue
        anchor = suggestion["anchor_excerpt"]
        if anchor not in policy_text:
            # Anchor text may have shifted if an earlier accepted suggestion
            # already touched overlapping text - skip rather than corrupt the
            # document. The human still sees this in the editable final draft.
            print(f"[suggestions] anchor for '{suggestion['id']}' no longer found - skipping automatic application")
            continue
        if suggestion["operation"] == "replace":
            policy_text = policy_text.replace(anchor, suggestion["final_text"], 1)
        else:  # insert_after
            policy_text = policy_text.replace(anchor, anchor + "\n\n" + suggestion["final_text"], 1)

    return policy_text
