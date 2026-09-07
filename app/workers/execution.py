"""One live execution per job, with database fencing after connection loss."""
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import PurePosixPath
from uuid import uuid4

from psycopg import AsyncConnection
from psycopg.rows import dict_row

from app.config import settings


_execution = ContextVar("job_execution", default=None)


class StaleExecutionError(RuntimeError):
    pass


def check_job_owner(job):
    current = _execution.get()
    if current and (str(job["id"]) != current[0] or job["run_token"] != current[1]):
        raise StaleExecutionError("job execution was superseded")


async def assert_job_owner(conn):
    current = _execution.get()
    if current:
        cur = await conn.execute("SELECT id, run_token FROM processing_job WHERE id = %s FOR UPDATE", (current[0],))
        row = await cur.fetchone()
        if not row:
            raise StaleExecutionError("job no longer exists")
        check_job_owner(row)


def attempt_artifact_name(name):
    current = _execution.get()
    if not current:
        return name
    path = PurePosixPath(name)
    return f"{path.stem}-{current[1]}{path.suffix}"


@asynccontextmanager
async def job_execution(job_id):
    from app.knowledge import assets
    job_id = str(job_id)
    async with await AsyncConnection.connect(settings.database_url, autocommit=True, row_factory=dict_row) as conn:
        cur = await conn.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 7)) AS locked", (job_id,))
        if not (await cur.fetchone())["locked"]:
            yield False
            return
        token = uuid4()
        await conn.execute("UPDATE processing_job SET run_token = %s WHERE id = %s", (token, job_id))
        state = _execution.set((job_id, token))
        try:
            job = await assets.get_job(job_id)
            if job and job["status"] == "running":
                await assets.transition_job(job_id, "failed", error_code="worker_interrupted",
                                            error_message="Previous execution lost its database lock")
                if job["attempt_count"] < job["max_attempts"]:
                    await assets.transition_job(job_id, "queued", progress=0)
            yield True
        finally:
            _execution.reset(state)
            # Closing this dedicated session releases the advisory lock even on errors.


async def reconcile_jobs(limit=100):
    from app import db
    from app.knowledge import assets
    from app.queue import enqueue_processing_job
    rows = await db.fetch_all(
        """SELECT id, queue_name FROM processing_job
           WHERE status IN ('queued', 'running')
             AND (dispatch_status <> 'sent' OR dispatched_at IS NULL
                  OR dispatched_at < now() - interval '10 minutes')
           ORDER BY dispatched_at NULLS FIRST, created_at LIMIT %s""", (limit,))
    sent = 0
    async with await AsyncConnection.connect(settings.database_url, autocommit=True, row_factory=dict_row) as conn:
        for row in rows:
            key = str(row["id"])
            cur = await conn.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 7)) AS locked", (key,))
            if not (await cur.fetchone())["locked"]:
                continue
            try:
                enqueue_processing_job(row["id"], row["queue_name"])
                await assets.mark_job_dispatch(row["id"], "sent")
                sent += 1
            except Exception:
                await assets.mark_job_dispatch(row["id"], "failed", "Broker dispatch failed")
            finally:
                await conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 7))", (key,))
    return sent
