"""APScheduler-backed periodic crawl/link-check execution."""

from __future__ import annotations

import subprocess
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from apscheduler.schedulers.background import BackgroundScheduler
from loguru import logger

from app.models.job_config import ScheduleTaskConfig
from app.schedule.concurrency import ScheduledTaskConcurrencyGate
from app.schedule.maintenance import is_start_blocked_by_maintenance
from app.schedule.registry import ScheduleRegistryEntry, load_registry
from app.schedule.runner import run_scheduled_task
from app.schedule.runtime_config import SchedulerRuntimeConfig
from app.schedule.state import SchedulerStateStore
from app.schedule.triggers import build_trigger
from app.schedule.triggers import interval_seconds

TaskType = Literal["crawl", "link_check"]

APSCHEDULER_JOB_PREFIX = "blink_sched"


def _iso_utc_z() -> str:
    return datetime.now(tz=UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _job_next_run_time(job: Any, *, now: datetime | None = None) -> datetime | None:
    """Next run for an APScheduler job (works before the scheduler thread is started)."""
    if job is None:
        return None
    now = now or datetime.now(tz=UTC)
    next_run = getattr(job, "next_run_time", None)
    if next_run is not None:
        return next_run
    trigger = getattr(job, "trigger", None)
    if trigger is None:
        return None
    try:
        return trigger.get_next_fire_time(None, now)
    except (AttributeError, TypeError, ValueError):
        return None


def _parse_iso_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _task_phase_offset_seconds(task: ScheduleTaskConfig) -> int:
    raw = task.get("phase_offset_seconds", 0)
    return max(0, int(raw))


def _has_finished_crawl_run(jobs_root: Path, job_id: str) -> bool:
    db_path = jobs_root / "data" / job_id / "db" / f"{job_id}.sqlite3"
    if not db_path.exists():
        return False
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        row = conn.execute(
            """
            SELECT 1
            FROM crawl_runs
            WHERE job_id = ? AND finished_at IS NOT NULL
            LIMIT 1
            """,
            (job_id,),
        ).fetchone()
        conn.close()
        return row is not None
    except sqlite3.Error:
        return False


class BlinkSchedulerService:
    """Owns BackgroundScheduler + SQLite state under ``jobs_root``."""

    def __init__(
        self,
        jobs_root: Path | str,
        *,
        runtime_config: SchedulerRuntimeConfig | None = None,
    ) -> None:
        self.jobs_root = Path(jobs_root).resolve()
        self._runtime = runtime_config or SchedulerRuntimeConfig.from_env()
        self._concurrency = ScheduledTaskConcurrencyGate(self._runtime.max_concurrent_tasks)
        self._store = SchedulerStateStore(self.jobs_root)
        self._scheduler = BackgroundScheduler()
        self._started = False
        entries = load_registry(self.jobs_root)
        for entry in entries:
            sch = entry.config["schedule"]
            for tt in ("crawl", "link_check"):
                if sch[tt]["enabled"]:  # type: ignore[literal-required]
                    self._store.clear_running_if_stale(entry.job_id, tt)
            self._register_entry(entry)
        logger.debug("Blink scheduler registry loaded (jobs={})", len(entries))

    def start(self) -> None:
        """Start APScheduler thread (jobs already registered)."""
        if self._started:
            return
        self._scheduler.start()
        self._started = True
        logger.info("Blink scheduler thread started")

    def shutdown(self, *, wait: bool = True) -> None:
        if not self._started:
            return
        self._scheduler.shutdown(wait=wait)
        self._started = False
        logger.info("Blink scheduler shut down")

    def _register_entry(self, entry: ScheduleRegistryEntry) -> None:
        cfg = entry.config
        sch = cfg["schedule"]
        if sch["overlap_policy"] not in ("skip",):
            logger.warning(
                "Job {} overlap_policy={!r} not fully supported; using skip semantics",
                entry.job_id,
                sch["overlap_policy"],
            )

        if sch["crawl"]["enabled"]:
            self._add_task_job(entry, "crawl", sch["crawl"])
        if sch["link_check"]["enabled"]:
            self._add_task_job(entry, "link_check", sch["link_check"])

    def _job_ap_id(self, job_id: str, task_type: TaskType) -> str:
        return f"{APSCHEDULER_JOB_PREFIX}:{job_id}:{task_type}"

    def _add_task_job(
        self,
        entry: ScheduleRegistryEntry,
        task_type: TaskType,
        task: ScheduleTaskConfig,
    ) -> None:
        cfg = entry.config
        sch = cfg["schedule"]
        delay = max(0, int(task["startup_delay_seconds"]))
        phase = _task_phase_offset_seconds(task)
        now = datetime.now(tz=UTC)
        not_before = now + timedelta(seconds=delay + phase)
        # Preserve interval cadence over service restarts by aligning the next run
        # to the last known completion time when available.
        if task["mode"] == "interval":
            st = self._store.get(cfg["meta"]["job_id"], task_type)
            last_end = _parse_iso_utc(st.last_end_at if st else None)
            if last_end is not None:
                cadence_seconds = interval_seconds(task)
                resumed_not_before = last_end + timedelta(seconds=cadence_seconds)
                if resumed_not_before > not_before:
                    not_before = resumed_not_before
        try:
            trigger = build_trigger(task, timezone_name=sch["timezone"], not_before=not_before)
        except (TypeError, ValueError) as exc:
            logger.warning(
                "Skipping scheduled task {} {} (invalid trigger: {})",
                entry.job_id,
                task_type,
                exc,
            )
            return

        def tick() -> None:
            self._dispatch_task(entry, task_type, task)

        jid = self._job_ap_id(cfg["meta"]["job_id"], task_type)
        try:
            self._scheduler.add_job(
                tick,
                trigger=trigger,
                id=jid,
                replace_existing=True,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=max(60, delay),
            )
        except Exception as exc:
            logger.warning(
                "Could not register scheduled task {} {}: {}",
                entry.job_id,
                task_type,
                exc,
            )

    def _dispatch_task(
        self,
        entry: ScheduleRegistryEntry,
        task_type: TaskType,
        task: ScheduleTaskConfig,
    ) -> None:
        job_id = entry.config["meta"]["job_id"]
        self._concurrency.run(
            job_id,
            task_type,
            lambda: self._run_task_body(entry, task_type, task),
        )

    def _run_task_body(
        self,
        entry: ScheduleRegistryEntry,
        task_type: TaskType,
        task: ScheduleTaskConfig,
    ) -> None:
        job_id = entry.config["meta"]["job_id"]
        sch = entry.config["schedule"]
        lock = _task_lock(self._job_ap_id(job_id, task_type))

        if is_start_blocked_by_maintenance(sch):
            logger.info("Skipping {} {} (maintenance window)", job_id, task_type)
            return
        if task_type == "link_check" and not _has_finished_crawl_run(self.jobs_root, job_id):
            logger.info("Skipping {} {} (waiting for first finished crawl run)", job_id, task_type)
            return

        timeout = max(1, int(task["max_runtime_seconds"]))

        with lock:
            row = self._store.get(job_id, task_type)
            if row and row.running:
                logger.info("Skipping {} {} (overlap: still running)", job_id, task_type)
                return
            now = _iso_utc_z()
            self._store.set_running(job_id, task_type, pid=None, start_iso=now)

        try:
            proc = run_scheduled_task(
                self.jobs_root,
                entry.job_path,
                task_type,
                timeout_seconds=timeout,
            )
            err_tail = (proc.stderr or "")[-4000:] if proc.stderr else None
            end = _iso_utc_z()
            code = int(proc.returncode if proc.returncode is not None else -1)
            err_msg = None if code == 0 else (err_tail or f"exit {code}")
            self._store.set_finished(job_id, task_type, end_iso=end, exit_code=code, error=err_msg)
            if code != 0:
                stdout_tail = (proc.stdout or "")[-2000:] if proc.stdout else ""
                logger.error(
                    "Scheduled task failed job_id={} task_type={} exit={} job_file={} "
                    "timeout_seconds={}\n--- stderr (tail) ---\n{}\n--- stdout (tail) ---\n{}",
                    job_id,
                    task_type,
                    code,
                    entry.job_path,
                    timeout,
                    err_tail or "(empty)",
                    stdout_tail or "(empty)",
                )
        except subprocess.TimeoutExpired as exc:
            end = _iso_utc_z()
            self._store.set_finished(
                job_id,
                task_type,
                end_iso=end,
                exit_code=-9,
                error=f"timeout after {timeout}s",
            )
            logger.error("Scheduled {} {} timed out: {}", job_id, task_type, exc)
        except Exception as exc:
            end = _iso_utc_z()
            self._store.set_finished(job_id, task_type, end_iso=end, exit_code=-1, error=str(exc)[:4000])
            logger.exception("Scheduled {} {} error: {}", job_id, task_type, exc)

    def build_schedule_payload(self) -> dict[str, Any]:
        """JSON-serializable status for HTTP API and CLI."""
        entries = load_registry(self.jobs_root)
        tasks: list[dict[str, Any]] = []
        crawl_tasks: list[dict[str, Any]] = []
        link_tasks: list[dict[str, Any]] = []

        for entry in entries:
            cfg = entry.config
            sch = cfg["schedule"]
            for task_type in ("crawl", "link_check"):
                tcfg = sch[task_type]  # type: ignore[literal-required]
                if not tcfg["enabled"]:
                    continue
                tt: TaskType = task_type  # type: ignore[assignment]
                ap_id = self._job_ap_id(cfg["meta"]["job_id"], tt)
                job = self._scheduler.get_job(ap_id)
                next_run = _job_next_run_time(job)
                next_iso = None
                if next_run is not None:
                    if next_run.tzinfo is None:
                        next_iso = next_run.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")
                    else:
                        next_iso = next_run.isoformat()

                st = self._store.get(cfg["meta"]["job_id"], task_type)
                err = st.last_error if st else None
                if err and len(err) > 500:
                    err = err[:500] + "…"
                runtime = {
                    "next_run_at": next_iso,
                    "last_start_at": st.last_start_at if st else None,
                    "last_end_at": st.last_end_at if st else None,
                    "last_exit_code": st.last_exit_code if st else None,
                    "last_error": err,
                    "running": bool(st.running) if st else False,
                    "pid": st.pid if st else None,
                }
                declarative = {
                    "enabled": tcfg["enabled"],
                    "mode": tcfg["mode"],
                    "expression": tcfg["expression"],
                    "jitter_seconds": tcfg["jitter_seconds"],
                    "max_runtime_seconds": tcfg["max_runtime_seconds"],
                    "startup_delay_seconds": tcfg["startup_delay_seconds"],
                    "phase_offset_seconds": _task_phase_offset_seconds(tcfg),
                }
                row = {
                    "job_id": cfg["meta"]["job_id"],
                    "slug": entry.slug,
                    "job_file": str(entry.job_path),
                    "task_type": task_type,
                    "timezone": sch["timezone"],
                    "overlap_policy": sch["overlap_policy"],
                    "declarative": declarative,
                    "maintenance_windows": [
                        {"name": w["name"], "cron": w["cron"]} for w in sch["maintenance_windows"]
                    ],
                    "runtime": runtime,
                    "apscheduler_job_id": ap_id,
                }
                tasks.append(row)
                if task_type == "crawl":
                    crawl_tasks.append(row)
                else:
                    link_tasks.append(row)

        tasks.sort(key=lambda r: (r["runtime"]["next_run_at"] or "", r["job_id"], r["task_type"]))
        crawl_tasks.sort(key=lambda r: (r["runtime"]["next_run_at"] or "", r["job_id"]))
        link_tasks.sort(key=lambda r: (r["runtime"]["next_run_at"] or "", r["job_id"]))

        return {
            "jobs_root": str(self.jobs_root),
            "scheduler_running": self._started,
            "scheduler": {
                "max_concurrent_tasks": self._runtime.max_concurrent_tasks,
                "queued_tasks": self._concurrency.queued_count,
            },
            "tasks": tasks,
            "crawl_tasks": crawl_tasks,
            "link_check_tasks": link_tasks,
        }


_locks: dict[str, threading.Lock] = {}


def _task_lock(ap_job_id: str) -> threading.Lock:
    if ap_job_id not in _locks:
        _locks[ap_job_id] = threading.Lock()
    return _locks[ap_job_id]
