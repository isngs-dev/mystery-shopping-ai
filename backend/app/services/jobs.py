"""Minimal in-process background job runner.

Job state is persisted on the domain rows (QCRun.status/stages), so a restart never leaves silent
"running" work: `recover_interrupted_runs` marks them failed. For multi-node production swap this
module for Celery/RQ/Arq behind the same `submit` signature.
"""
import logging
from concurrent.futures import ThreadPoolExecutor

from ..config import get_settings

log = logging.getLogger("msai.jobs")
_pool: ThreadPoolExecutor | None = None


def _wrap(fn, args):
    try:
        fn(*args)
    except Exception:  # noqa: BLE001
        log.exception("Background job %s failed", getattr(fn, "__name__", fn))


def submit(fn, *args) -> None:
    s = get_settings()
    if s.jobs_inline:
        _wrap(fn, args)
        return
    global _pool
    if _pool is None:
        _pool = ThreadPoolExecutor(max_workers=s.job_workers, thread_name_prefix="msai-job")
    _pool.submit(_wrap, fn, args)
