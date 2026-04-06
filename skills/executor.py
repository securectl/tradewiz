"""
Skill job executor — manages ThreadPoolExecutor and in-memory job store.
"""

import logging
import time
import threading
from concurrent.futures import ThreadPoolExecutor

from skills.models import SkillJob, STATUS_PENDING, STATUS_RUNNING, STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED
from skills.registry import MAX_CONCURRENT

logger = logging.getLogger(__name__)

_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="skill")
_jobs = {}       # job_id -> SkillJob
_jobs_lock = threading.Lock()


def submit_job(job, run_fn):
    """Submit a skill job for background execution.
    run_fn(job) will be called in a thread pool worker.
    """
    with _jobs_lock:
        _jobs[job.id] = job
    future = _pool.submit(_run_wrapper, job, run_fn)
    return job.id


def _run_wrapper(job, run_fn):
    """Wraps skill execution with status tracking and error handling."""
    try:
        job.status = STATUS_RUNNING
        job.started_at = time.time()
        job.notify("started")
        run_fn(job)
        if job.status == STATUS_RUNNING:
            job.status = STATUS_COMPLETED
            job.progress = 1.0
            job.completed_at = time.time()
            job.notify("completed")
    except Exception as e:
        logger.exception("Skill job %s failed: %s", job.id, e)
        job.status = STATUS_FAILED
        job.error = str(e)
        job.completed_at = time.time()
        job.notify("failed")


def get_job(job_id):
    """Get a SkillJob by ID."""
    return _jobs.get(job_id)


def get_user_jobs(user_id):
    """Get all jobs for a user, most recent first."""
    with _jobs_lock:
        user_jobs = [j for j in _jobs.values() if j.user_id == user_id]
    return sorted(user_jobs, key=lambda j: j.created_at, reverse=True)


def count_active_jobs(user_id):
    """Count running/pending jobs for a user."""
    with _jobs_lock:
        return sum(1 for j in _jobs.values()
                   if j.user_id == user_id and j.status in (STATUS_PENDING, STATUS_RUNNING))


def cancel_job(job_id, user_id):
    """Cancel a running or pending job. Returns True if cancelled."""
    job = _jobs.get(job_id)
    if not job or job.user_id != user_id:
        return False
    if job.status in (STATUS_PENDING, STATUS_RUNNING):
        job.status = STATUS_CANCELLED
        job.completed_at = time.time()
        job.notify("cancelled")
        return True
    return False


def delete_job(job_id, user_id):
    """Delete a completed/failed/cancelled job from memory."""
    job = _jobs.get(job_id)
    if not job or job.user_id != user_id:
        return False
    if job.status in (STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED):
        with _jobs_lock:
            _jobs.pop(job_id, None)
        return True
    return False


def can_launch(user_id, user_tier):
    """Check if user can launch another job based on tier concurrency limits."""
    max_jobs = MAX_CONCURRENT.get(user_tier, 1)
    return count_active_jobs(user_id) < max_jobs
