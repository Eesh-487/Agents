"""In-memory background job registry for the long-running (LLM-backed)
pipeline stages. Every /*/run route used to block the HTTP connection for
the whole job - fine on a direct localhost connection, but fatal once the
request travels through anything with its own idle-connection timeout
(a reverse tunnel, a mobile NAT, a load balancer): the intermediary drops
the browser's connection while the job keeps running server-side, so the
client sees a network failure even though the job later succeeds.

This decouples "start the job" from "wait for the result": /*/run submits
the job to a thread pool and returns a job_id immediately: the frontend
polls GET /jobs/{job_id} instead of holding one request open. Also gives a
real cancel path - each job function accepts a `cancel_event` and checks it
between LLM calls (see json_utils.call_agent_for_json), so a genuine stop
request actually interrupts the run instead of just abandoning the socket.
"""
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone


class JobCancelled(Exception):
    """Raised inside a job function once its cancel_event has been set."""


@dataclass
class _Job:
    id: str
    resource: str
    future: object
    cancel_event: threading.Event
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# 4 workers: enough that one resource's job doesn't block another (graph
# builder + gap analysis could reasonably run back to back), without
# needing a real task queue for a single-user local app.
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="job")
_jobs: dict[str, _Job] = {}
_jobs_lock = threading.Lock()


def start_job(resource, fn, *args, **kwargs):
    """Runs fn(*args, cancel_event=<Event>, **kwargs) in a background thread.
    fn must accept a `cancel_event` kwarg (every job function in this repo
    does) and check it between LLM calls to make cancellation real rather
    than cosmetic."""
    cancel_event = threading.Event()
    future = _executor.submit(fn, *args, cancel_event=cancel_event, **kwargs)
    job_id = uuid.uuid4().hex
    job = _Job(id=job_id, resource=resource, future=future, cancel_event=cancel_event)
    with _jobs_lock:
        _jobs[job_id] = job
    return job_id


def get_status(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        return None

    future = job.future
    if not future.done():
        return {
            "job_id": job_id,
            "resource": job.resource,
            "status": "cancelling" if job.cancel_event.is_set() else "running",
        }

    if future.cancelled():
        return {"job_id": job_id, "resource": job.resource, "status": "cancelled"}

    exc = future.exception()
    if exc is not None:
        if isinstance(exc, JobCancelled):
            return {"job_id": job_id, "resource": job.resource, "status": "cancelled"}
        return {"job_id": job_id, "resource": job.resource, "status": "failed", "error": str(exc)}

    return {"job_id": job_id, "resource": job.resource, "status": "done", "result": future.result()}


def cancel_job(job_id):
    """Best-effort: flips the cooperative cancel_event (checked between LLM
    calls) and tries future.cancel() in case it hasn't started running yet.
    Returns False only if job_id is unknown - a job already finished, or
    already cancelling, is still a valid target (idempotent)."""
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        return False
    job.cancel_event.set()
    job.future.cancel()
    return True
