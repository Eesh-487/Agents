"""Set 4: git-backed version control for the company's policy document.
Real git via subprocess, not a hand-rolled versioning scheme - version
history is `git log`, diffs are `git diff`, nothing custom-built.
"""
import os
import re
import subprocess

POLICY_REPO_DIR = "data/policy_versions/nimbuspay"
POLICY_FILENAME = "policy.txt"
POLICY_FILE_PATH = os.path.join(POLICY_REPO_DIR, POLICY_FILENAME)

_INITIAL_POLICY_SOURCE = "data/sample_policies/nimbuspay_data_privacy_retention_policy.txt"


def _normalize_paragraph_wrapping(text):
    """The source policy doc uses manual hard line-wraps within paragraphs
    (typewriter-style, ~78 chars/line). An LLM asked to quote text verbatim
    naturally reproduces a wrapped paragraph as flowing single-line text -
    it doesn't treat the artificial mid-sentence line break as meaningful -
    so every anchor-grounding check failed 100% of the time against the raw
    file. Collapsing single newlines into spaces (preserving blank-line
    paragraph/section breaks) fixes this at the source, once, instead of
    working around it at every consumption site."""
    lines = text.split("\n")
    normalized_lines = []
    buffer = []
    for line in lines:
        if line.strip() == "":
            if buffer:
                normalized_lines.append(" ".join(buffer))
                buffer = []
            normalized_lines.append("")
        else:
            buffer.append(line.strip())
    if buffer:
        normalized_lines.append(" ".join(buffer))
    return "\n".join(normalized_lines)


def _run_git(*args):
    result = subprocess.run(
        ["git", *args], cwd=POLICY_REPO_DIR, capture_output=True, text=True, encoding="utf-8"
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def ensure_repo_initialized():
    """Creates the policy version-control repo on first use, seeded from the
    original sample policy doc (paragraph-unwrapped, see above) as the
    initial commit."""
    if os.path.exists(os.path.join(POLICY_REPO_DIR, ".git")):
        return

    os.makedirs(POLICY_REPO_DIR, exist_ok=True)
    with open(_INITIAL_POLICY_SOURCE, "r", encoding="utf-8") as src:
        initial_text = _normalize_paragraph_wrapping(src.read())
    with open(POLICY_FILE_PATH, "w", encoding="utf-8") as dst:
        dst.write(initial_text)

    _run_git("init")
    _run_git("config", "user.email", "compliance-pipeline@local")
    _run_git("config", "user.name", "Compliance Memory System")
    _run_git("add", POLICY_FILENAME)
    _run_git("commit", "-m", "Initial policy version")


def read_current_policy():
    ensure_repo_initialized()
    with open(POLICY_FILE_PATH, "r", encoding="utf-8") as f:
        return f.read()


def commit_new_version(new_text, message):
    """Writes new_text to the policy file and commits it as a new version.
    Returns the new commit hash. --allow-empty so finalizing with no actual
    text change still records that a review cycle happened."""
    ensure_repo_initialized()
    with open(POLICY_FILE_PATH, "w", encoding="utf-8") as f:
        f.write(new_text)
    _run_git("add", POLICY_FILENAME)
    _run_git("commit", "-m", message, "--allow-empty")
    return _run_git("rev-parse", "HEAD")


def get_history(limit=20):
    ensure_repo_initialized()
    log_format = "%H%x1f%ai%x1f%s"
    raw = _run_git("log", f"-{limit}", f"--pretty=format:{log_format}")
    history = []
    for line in raw.splitlines():
        if not line:
            continue
        commit_hash, date, message = line.split("\x1f")
        history.append({"commit": commit_hash, "date": date, "message": message})
    return history


def get_diff(from_rev, to_rev="HEAD"):
    ensure_repo_initialized()
    return _run_git("diff", from_rev, to_rev, "--", POLICY_FILENAME)


def read_at_revision(revision):
    ensure_repo_initialized()
    return _run_git("show", f"{revision}:{POLICY_FILENAME}")
