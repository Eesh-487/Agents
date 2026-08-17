"""Shared utilities for calling an LLM agent and handling its output. Used by
every agent that asks a model for structured JSON - Set 1's extractor/
verifier and Set 3's three agents alike, so this exists once rather than
being copy-pasted (inconsistently - see call_agent_for_json) per set.
"""
import json

from json_repair import repair_json
from openai import OpenAIError

from jobs import JobCancelled


def strip_code_fence(text):
    text = text.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def parse_json_lenient(text):
    """Small models frequently forget to escape quotes inside verbatim
    excerpts, which breaks strict JSON parsing. Try strict parsing first;
    only fall back to repair on failure, so a repair bug can't silently
    corrupt good output."""
    cleaned = strip_code_fence(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return json.loads(repair_json(cleaned))


def call_agent_for_json(agent, prompt, cancel_event=None):
    """Calls `agent(prompt)` and parses the response as JSON leniently.
    Never raises for a bad/failed call - every LLM call site needs this same
    safety net (a malformed response and a rate-limited/failed API call are
    both real, observed failure modes, not hypotheticals - see Set 3's
    rate-limit debugging). Returns (result, error): error is None on
    success, else a string describing what went wrong, and result is None
    on failure.

    DOES raise JobCancelled if cancel_event is set - this is every job's
    single most common call site (every retry loop across all 4 pipeline
    stages routes through here), so checking here before dispatching each
    LLM call is what makes a mid-run Stop take effect within one call's
    latency instead of only between whole pipeline stages."""
    if cancel_event is not None and cancel_event.is_set():
        raise JobCancelled("Stopped before the next LLM call.")
    try:
        return parse_json_lenient(agent(prompt)), None
    except (json.JSONDecodeError, ValueError, TypeError, KeyError, OpenAIError) as exc:
        return None, str(exc)
