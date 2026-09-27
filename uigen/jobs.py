"""Asynchronous generation jobs.

Two small interfaces keep the API independent of where jobs run and where their state lives:
  JobStore   - job records + progress events   (InMemoryJobStore now; Redis/Postgres later)
  JobRunner  - executes jobs with a bounded queue (ThreadJobRunner now; SQS + worker processes later)
Swapping an implementation does not change the API.
"""

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field


class QueueFull(Exception):
    """More jobs are pending than the runner accepts; the API answers 429 (retry later)."""


@dataclass
class Job:
    id: str
    owner: str  # which API key created it; only the owner may read it
    status: str = "queued"  # queued | running | done | failed
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    events: list = field(default_factory=list)
    result: dict | None = None  # public summary (no bytes)
    error: str | None = None
    run_dir: str | None = None


class InMemoryJobStore:
    """Single-process store. For several API instances use a shared store (Redis/Postgres) instead."""

    def __init__(self, max_jobs=1000):
        self._jobs, self._lock, self._max = {}, threading.Lock(), max_jobs

    def create(self, owner):
        job = Job(id="job_" + uuid.uuid4().hex[:16], owner=owner)
        with self._lock:
            if len(self._jobs) >= self._max:  # forget the oldest finished jobs first
                for old in sorted((j for j in self._jobs.values() if j.finished_at), key=lambda j: j.finished_at):
                    del self._jobs[old.id]
                    if len(self._jobs) < self._max:
                        break
            self._jobs[job.id] = job
        return job

    def get(self, job_id):
        with self._lock:
            return self._jobs.get(job_id)

    def add_event(self, job_id, event):
        with self._lock:
            self._jobs[job_id].events.append({**event, "t": round(time.time(), 2)})

    def events_since(self, job_id, index):
        with self._lock:
            job = self._jobs[job_id]
            return job.events[index:], job.status in ("done", "failed")

    def update(self, job_id, **fields):
        with self._lock:
            for k, v in fields.items():
                setattr(self._jobs[job_id], k, v)


class ThreadJobRunner:
    """Runs jobs on a fixed pool of threads with a bounded number of pending jobs."""

    def __init__(self, workers=2, max_pending=20):
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="uigen-job")
        self._slots = threading.BoundedSemaphore(workers + max_pending)

    def submit(self, fn, *args):
        if not self._slots.acquire(blocking=False):
            raise QueueFull()

        def wrapped():
            try:
                fn(*args)
            finally:
                self._slots.release()

        self._pool.submit(wrapped)

    def shutdown(self):
        self._pool.shutdown(wait=True)
