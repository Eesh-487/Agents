from datetime import datetime, timezone

from set4_remediation import suggestions as suggestions_module
from set4_remediation import version_store

policy_text = version_store.read_current_policy()
anchor = (
    "NimbusPay does not currently maintain a documented process for notifying "
    "affected Data Principals or regulators following a confirmed data breach."
)
assert anchor in policy_text, "sanity check: anchor must be real, verbatim text"

suggestion = {
    "id": "suggestion-gap-data-breach-notification",
    "gap_id": "gap-data-breach-notification",
    "gap_title": "Data Breach Notification Gap",
    "severity": "high",
    "operation": "replace",
    "anchor_excerpt": anchor,
    "suggested_text": (
        "In the event of a confirmed data breach involving personal data, the Information Security "
        "department shall notify affected Data Principals and the Data Protection Board of India "
        "without undue delay, and in any case within 72 hours of confirming the breach."
    ),
    "final_text": (
        "In the event of a confirmed data breach involving personal data, the Information Security "
        "department shall notify affected Data Principals and the Data Protection Board of India "
        "without undue delay, and in any case within 72 hours of confirming the breach."
    ),
    "rationale": "Adds the missing breach-notification process required by the DPDP Act.",
    "verifier_confidence": 0.85,
    "status": "pending",
    "created_at": datetime.now(timezone.utc).isoformat(),
}

suggestions_module._save_suggestions([suggestion])
print("Seeded 1 test suggestion.")
