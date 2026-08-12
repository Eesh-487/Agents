"""Set 2: monitors PRS Legislative Research's bill tracker (prsindia.org) for
new/upcoming (introduced, pending) and newly-passed Indian bills. Real HTTP +
HTML parsing against the live site, not a stub - see the `views-row` /
`h3.cate` structure this relies on, verified against the live page.

Bills that newly become "Passed" get their PDF downloaded and run through
document_ingest.py, so their text is searchable alongside the DPDP Act.
"""
import json
import os
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

import graph_db
from json_utils import call_agent_for_json
from llm import chatbot
from Prompts.bill_relevance import BILL_RELEVANCE_SYSTEM_PROMPT, BILL_RELEVANCE_USER_TEMPLATE
from Prompts.regulatory_profile import REGULATORY_PROFILE_SYSTEM_PROMPT, REGULATORY_PROFILE_USER_TEMPLATE

BILLTRACK_URL = "https://prsindia.org/billtrack"
_HEADERS = {"User-Agent": "Mozilla/5.0"}
_SEEN_INDEX_PATH = "data/law_sources/prs_seen_bills.json"
_SCRAPED_BILLS_DIR = "data/law_sources/scraped_bills"
_RELEVANCE_LOG_PATH = "data/law_sources/relevance_decisions.jsonl"
SCRAPED_LAWS_COLLECTION = "scraped_indian_laws"
UNCERTAIN_CONFIDENCE_FLOOR = 0.6  # below this, even a "relevant"/"irrelevant" call is treated as uncertain


def scrape_bill_track(limit=50):
    """Returns the most recent `limit` bills as [{"title", "status", "detail_url"}],
    in the reverse-chronological order PRS lists them (most recent first)."""
    resp = requests.get(BILLTRACK_URL, headers=_HEADERS, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    bills = []
    for row in soup.find_all("div", class_="views-row")[:limit]:
        title_el = row.find("h3", class_="cate")
        link_el = row.find("a", href=True)
        if not title_el or not link_el:
            continue
        full_text = row.get_text(" | ", strip=True)
        status = full_text.split("|")[-1].strip() if "|" in full_text else "Unknown"
        bills.append(
            {
                "title": title_el.get_text(strip=True),
                "status": status,
                "detail_url": urljoin(BILLTRACK_URL, link_el["href"]),
            }
        )
    return bills


def get_bill_pdf_url(detail_url):
    """Best-effort: finds the first PDF link on a bill's detail page. Returns
    None if the detail page doesn't expose one (not every bill has a linked
    PDF at every stage)."""
    resp = requests.get(detail_url, headers=_HEADERS, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    for a in soup.find_all("a", href=True):
        if ".pdf" in a["href"].lower():
            return urljoin(detail_url, a["href"])
    return None


def _load_seen_index():
    if not os.path.exists(_SEEN_INDEX_PATH):
        return {}
    with open(_SEEN_INDEX_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_seen_index(index):
    os.makedirs(os.path.dirname(_SEEN_INDEX_PATH), exist_ok=True)
    with open(_SEEN_INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2)


def check_for_new_or_changed_bills(limit=50):
    """Compares the current bill listing against what was seen on the last
    run (persisted to disk). A bill is "new/changed" if its detail_url hasn't
    been seen before, or its status has changed since last time (e.g.
    Pending -> Passed). Updates the persisted index either way."""
    current_bills = scrape_bill_track(limit=limit)
    seen_index = _load_seen_index()

    changed = []
    for bill in current_bills:
        previous_status = seen_index.get(bill["detail_url"])
        if previous_status != bill["status"]:
            changed.append({**bill, "previous_status": previous_status})
        seen_index[bill["detail_url"]] = bill["status"]

    _save_seen_index(seen_index)
    return changed


def build_regulatory_profile():
    """Derives a compact, stable regulatory profile (industry, domains,
    regulators) from the company's graph - used instead of a raw entity dump
    for relevance classification. More stable than raw entities: it
    generalizes to bills that don't literally resemble anything already in
    the graph (the recall problem with matching against raw entities), and
    it's computed ONCE per scraper run and reused for every bill checked,
    rather than re-sending the full graph on every single classification
    call. Returns None if Set 1 hasn't run yet (no graph to derive from)."""
    entities = graph_db.get_full_graph().get("entities", [])
    if not entities:
        return None

    agent = chatbot(system=REGULATORY_PROFILE_SYSTEM_PROMPT, temperature=0.0, max_tokens=500)
    prompt = REGULATORY_PROFILE_USER_TEMPLATE.format(entities_json=json.dumps(entities, indent=2))
    profile, error = call_agent_for_json(agent, prompt)
    if error is not None:
        print(f"[law_scraper] regulatory profile generation failed: {error}")
        return None
    return profile


def is_bill_relevant(bill_title, regulatory_profile):
    """Cheap classifier agent, run before any PDF download - an irrelevant
    bill costs one short classification call instead of a full download +
    text extraction + embedding + storage cycle for nothing.

    Returns a dict: {relevance: "relevant"|"irrelevant"|"uncertain",
    confidence, matched_domains, reason}. Unparseable output or low-confidence
    verdicts collapse to "uncertain", not "irrelevant" - force-guessing
    irrelevant on genuine doubt is how real legislation gets silently lost;
    "uncertain" still gets ingested (flagged), never silently dropped."""
    agent = chatbot(system=BILL_RELEVANCE_SYSTEM_PROMPT, temperature=0.0, max_tokens=200)
    prompt = BILL_RELEVANCE_USER_TEMPLATE.format(
        regulatory_profile_json=json.dumps(regulatory_profile, indent=2), bill_title=bill_title
    )
    verdict, error = call_agent_for_json(agent, prompt)
    if error is not None:
        return {
            "relevance": "uncertain",
            "confidence": 0.0,
            "matched_domains": [],
            "reason": f"classification failed ({error}) - defaulted to uncertain, not dropped",
        }

    relevance = verdict.get("relevance", "uncertain")
    confidence = float(verdict.get("confidence", 0.0))
    if relevance in ("relevant", "irrelevant") and confidence < UNCERTAIN_CONFIDENCE_FLOOR:
        relevance = "uncertain"
    return {
        "relevance": relevance,
        "confidence": confidence,
        "matched_domains": verdict.get("matched_domains", []),
        "reason": verdict.get("reason", ""),
    }


def _log_relevance_decision(bill, verdict):
    """Every decision - not just skips - gets appended (JSONL, cheap to
    append without rewriting the file) so a bad call can be re-evaluated
    later if the classifier or profile changes; today's false negative
    isn't lost forever just because it wasn't ingested."""
    os.makedirs(os.path.dirname(_RELEVANCE_LOG_PATH), exist_ok=True)
    record = {
        "title": bill["title"],
        "detail_url": bill["detail_url"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **verdict,
    }
    with open(_RELEVANCE_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def check_and_ingest_new_laws(limit=50):
    """The full Set 2 monitoring cycle: check for new/changed bills, and for
    any that are now "Passed" AND judged relevant-or-uncertain against the
    company's regulatory profile, download and ingest their full text so
    Set 3 can compare against it. Only confidently "irrelevant" bills are
    skipped - "uncertain" is a real third outcome, ingested but flagged in
    its metadata, not silently dropped alongside genuine noise."""
    from set2_law_monitor import document_ingest  # local import: keeps the
    # dependency direction (scraper -> document_ingest) explicit without
    # forcing document_ingest to know about the scraper at all

    changed = check_for_new_or_changed_bills(limit=limit)
    newly_passed = [bill for bill in changed if bill["status"] == "Passed"]

    regulatory_profile = build_regulatory_profile()
    if regulatory_profile is None:
        # No company graph yet (Set 1 hasn't run) - can't judge relevance, so
        # don't ingest anything rather than ingest indiscriminately, which is
        # exactly the failure mode this whole check exists to prevent.
        return {
            "new_or_changed_bills": changed,
            "ingested_passed_laws": [
                {"title": bill["title"], "status": "skipped_no_company_context"} for bill in newly_passed
            ],
        }
    print(f"[law_scraper] regulatory profile: {json.dumps(regulatory_profile)}")

    ingested = []
    for bill in newly_passed:
        verdict = is_bill_relevant(bill["title"], regulatory_profile)
        _log_relevance_decision(bill, verdict)

        if verdict["relevance"] == "irrelevant":
            print(f"[law_scraper] skipping irrelevant bill '{bill['title']}': {verdict['reason']}")
            ingested.append({"title": bill["title"], "status": "skipped_not_relevant", **verdict})
            continue
        if verdict["relevance"] == "uncertain":
            print(f"[law_scraper] uncertain relevance for '{bill['title']}' - ingesting with flag: {verdict['reason']}")

        # One bad bill (no PDF, a scanned/image-only PDF with no text layer,
        # a network hiccup) must not abort the whole batch - a monitor that
        # dies on the first bad document across dozens of bills is useless.
        try:
            pdf_url = get_bill_pdf_url(bill["detail_url"])
            if not pdf_url:
                ingested.append({"title": bill["title"], "status": "skipped_no_pdf_found"})
                continue

            slug = bill["detail_url"].rstrip("/").split("/")[-1]
            local_path = os.path.join(_SCRAPED_BILLS_DIR, f"{slug}.pdf")
            os.makedirs(_SCRAPED_BILLS_DIR, exist_ok=True)

            pdf_resp = requests.get(pdf_url, headers=_HEADERS, timeout=30)
            pdf_resp.raise_for_status()
            with open(local_path, "wb") as f:
                f.write(pdf_resp.content)

            result = document_ingest.ingest_document(
                local_path,
                collection_name=SCRAPED_LAWS_COLLECTION,
                doc_label=bill["title"],
                extra_metadata={
                    "status": bill["status"],
                    "source_url": bill["detail_url"],
                    "relevance": verdict["relevance"],
                    "relevance_confidence": verdict["confidence"],
                },
            )
            ingested.append(result)
        except Exception as exc:
            ingested.append({"title": bill["title"], "status": "failed", "error": str(exc)})

    return {"new_or_changed_bills": changed, "ingested_passed_laws": ingested}


if __name__ == "__main__":
    print(json.dumps(check_and_ingest_new_laws(), indent=2))
