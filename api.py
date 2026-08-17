import os
import shutil

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import graph_db
import jobs
from set1_graph_builder import graph_builder
from set2_law_monitor import document_ingest, law_ingest
from set3_gap_analysis import gap_analysis
from set4_remediation import remediation, version_store

app = FastAPI(title="Compliance Memory System")

# Local React dev servers (Vite's default port + the common CRA fallback),
# plus every real deployed Vercel origin - without this, the browser blocks
# every request before it even reaches these endpoints. The regex alone
# only matches hash-based preview URLs (frontend-<hash>-eesh-487s-projects
# .vercel.app) - it does NOT match the bare production URL (no hash) or a
# custom alias, so those need to be listed explicitly. Confirmed via
# `vercel alias ls` on the "frontend" project - update this list if new
# aliases are added there.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:3000",
        "https://frontend-delta-mauve-ah93snc1ai.vercel.app",
        "https://frontend-eesh-487s-projects.vercel.app",
        "https://complianceconsole.vercel.app",
    ],
    allow_origin_regex=r"https://frontend-.*-eesh-487s-projects\.vercel\.app",
    allow_methods=["*"],
    allow_headers=["*"],
)

USER_DOCUMENTS_DIR = "data/user_documents"


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/graph-builder/run", status_code=202)
def run_graph_builder():
    """Set 1: extract entities/relationships from the current (git-backed,
    possibly remediated) policy doc, verify, and write to Neo4j. Reads from
    version_store's live policy file, not the static original sample - so
    this correctly re-reflects whatever Set 4 has committed.

    Runs as a background job rather than blocking the request: this can
    take several build-attempt retries, each with multiple LLM calls, and a
    single HTTP connection held open that long is fragile to any
    intermediary (tunnel, mobile NAT, proxy) with its own idle timeout -
    the frontend polls GET /jobs/{job_id} instead."""
    try:
        version_store.ensure_repo_initialized()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    job_id = jobs.start_job("graph-builder", graph_builder.build_graph, version_store.POLICY_FILE_PATH)
    return {"job_id": job_id}


@app.post("/law-ingest/run", status_code=202)
def run_law_ingest():
    """Set 2: chunk, embed, and ingest the DPDP Act 2023 text into Chroma."""
    job_id = jobs.start_job("law-ingest", law_ingest.ingest)
    return {"job_id": job_id}


@app.get("/graph")
def get_graph():
    """Read-only snapshot of the current Neo4j graph, for the frontend's
    Graph Explorer subpage. Whatever Set 1 last wrote - no LLM calls, just a
    passthrough read."""
    try:
        return graph_db.get_full_graph()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# Scraper (law_scraper.py) intentionally not wired here right now - real HTTP
# scraping against a live external site proved less stable than the rest of
# the pipeline (which only depends on Groq/Neo4j/Chroma). The module itself
# (scraper + bill_relevance + regulatory_profile) is untouched on disk in
# set2_law_monitor/ and Prompts/ - re-add the import + endpoint above to
# bring it back.


@app.post("/documents/ingest")
def ingest_user_document(file: UploadFile = File(...)):
    """Set 2: ingest a user-provided document (PDF/DOCX/TXT/MD) so Set 3 can
    compare it against ingested law text later."""
    os.makedirs(USER_DOCUMENTS_DIR, exist_ok=True)
    dest_path = os.path.join(USER_DOCUMENTS_DIR, file.filename)
    with open(dest_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        return document_ingest.ingest_document(dest_path, doc_label=file.filename)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/gap-analysis/run", status_code=202)
def run_gap_analysis():
    """Set 3: compare the company's Neo4j graph against ingested law text in
    both directions (existing controls vs. law, and law vs. missing
    controls), merge into one final gap list, gated by a deterministic
    citation-validity check."""
    job_id = jobs.start_job("gap-analysis", gap_analysis.run_gap_analysis)
    return {"job_id": job_id}


class SuggestionUpdate(BaseModel):
    status: str  # "accepted" | "rejected" | "edited"
    final_text: str | None = None


class FinalDraftUpdate(BaseModel):
    text: str


@app.post("/remediation/draft", status_code=202)
def run_remediation_draft():
    """Set 4: runs Set 3 fresh, then drafts a grounded, verified inline
    suggestion for every gap found - anchored to verbatim text from the
    real policy document so a frontend can render it as an inline diff."""
    job_id = jobs.start_job("remediation-draft", remediation.draft_remediation)
    return {"job_id": job_id}


@app.get("/jobs/{job_id}")
def get_job_status(job_id: str):
    """Frontend polls this instead of holding one long HTTP request open for
    a /*/run call - see jobs.py for why. status is one of: running,
    cancelling, cancelled, done, failed."""
    status = jobs.get_status(job_id)
    if status is None:
        raise HTTPException(status_code=404, detail=f"Unknown job_id '{job_id}'")
    return status


@app.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    """Flips the job's cooperative cancel flag, checked between LLM calls
    (see json_utils.call_agent_for_json) - stops the run at the next
    checkpoint rather than letting it burn through the remaining retries."""
    if not jobs.cancel_job(job_id):
        raise HTTPException(status_code=404, detail=f"Unknown job_id '{job_id}'")
    return {"status": "cancelling"}


@app.get("/remediation/suggestions")
def list_remediation_suggestions():
    return remediation.get_suggestions()


@app.patch("/remediation/suggestions/{suggestion_id}")
def patch_remediation_suggestion(suggestion_id: str, body: SuggestionUpdate):
    """Accept / reject / edit one suggestion. `final_text` lets a human
    submit their own wording instead of the AI's exact suggestion."""
    try:
        return remediation.update_suggestion(suggestion_id, body.status, body.final_text)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/remediation/final-draft")
def get_remediation_final_draft():
    """The consolidated document: human override if one was submitted, else
    auto-assembled from currently-accepted suggestions."""
    return {"text": remediation.get_final_draft()}


@app.patch("/remediation/final-draft")
def patch_remediation_final_draft(body: FinalDraftUpdate):
    """Lets a human directly edit the whole consolidated draft before
    finalizing - not limited to per-suggestion accept/reject/edit."""
    remediation.set_final_draft_override(body.text)
    return {"status": "updated"}


@app.post("/remediation/finalize")
def finalize_remediation():
    """Commits the current final draft as a new policy version (real git
    commit), then re-triggers Set 1 to regenerate the graph from it -
    closing the loop the whole pipeline was built around."""
    try:
        result = remediation.finalize()
        graph_result = graph_builder.build_graph(version_store.POLICY_FILE_PATH)
        return {**result, "graph_rebuild": graph_result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/remediation/history")
def get_remediation_history(limit: int = 20):
    return remediation.get_history(limit=limit)


@app.get("/remediation/diff")
def get_remediation_diff(from_rev: str, to_rev: str = "HEAD"):
    return {"diff": remediation.get_diff(from_rev, to_rev)}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
