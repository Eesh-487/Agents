"""Set 4 orchestration: ties together suggestion generation/lifecycle
(suggestions.py) and git-backed versioning (version_store.py), and manages
the human-editable final draft override before commit. This is the module
api.py talks to - it owns the public interface, suggestions.py and
version_store.py are implementation details underneath it.
"""
import os

from set3_gap_analysis.gap_analysis import run_gap_analysis
from set4_remediation import suggestions as suggestions_module
from set4_remediation import version_store

FINAL_DRAFT_OVERRIDE_PATH = "data/remediation/final_draft_override.txt"


def draft_remediation():
    """Runs Set 3 fresh, then drafts an inline suggestion for every gap found."""
    gap_result = run_gap_analysis()
    drafted = suggestions_module.generate_suggestions(gap_result["final_gaps"])
    return {
        "gap_count": gap_result["gap_count"],
        "suggestion_count": len(drafted),
        "suggestions": drafted,
    }


def get_suggestions():
    return suggestions_module.get_suggestions()


def update_suggestion(suggestion_id, status, final_text=None):
    return suggestions_module.update_suggestion(suggestion_id, status, final_text)


def get_final_draft():
    """Returns the human override if one has been submitted, else the
    auto-assembled draft from currently-accepted suggestions."""
    if os.path.exists(FINAL_DRAFT_OVERRIDE_PATH):
        with open(FINAL_DRAFT_OVERRIDE_PATH, "r", encoding="utf-8") as f:
            return f.read()
    return suggestions_module.assemble_final_draft()


def set_final_draft_override(text):
    os.makedirs(os.path.dirname(FINAL_DRAFT_OVERRIDE_PATH), exist_ok=True)
    with open(FINAL_DRAFT_OVERRIDE_PATH, "w", encoding="utf-8") as f:
        f.write(text)


def finalize():
    """Commits the current final draft (human-edited or auto-assembled) as a
    new policy version, clears override/suggestion state. Does NOT re-trigger
    Set 1 itself - the caller (API endpoint) does that explicitly, since
    that's a separate, expensive step the caller may want to control."""
    final_text = get_final_draft()
    accepted_gap_ids = [
        s["gap_id"] for s in suggestions_module.get_suggestions() if s["status"] in ("accepted", "edited")
    ]
    message = f"Applied remediation for: {', '.join(accepted_gap_ids)}" if accepted_gap_ids else "Manual policy revision"

    commit_hash = version_store.commit_new_version(final_text, message)

    if os.path.exists(FINAL_DRAFT_OVERRIDE_PATH):
        os.remove(FINAL_DRAFT_OVERRIDE_PATH)
    suggestions_module.clear_suggestions()

    return {"status": "finalized", "commit": commit_hash, "message": message}


def get_history(limit=20):
    return version_store.get_history(limit=limit)


def get_diff(from_rev, to_rev="HEAD"):
    return version_store.get_diff(from_rev, to_rev)
