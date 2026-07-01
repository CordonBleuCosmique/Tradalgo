"""
scheduler/candle_scheduler.py

Plain-loop daemon (no cron, no APScheduler) that aligns wakeups to M15
candle closes (:00/:15/:30/:45 UTC) plus a configurable latency buffer,
then triggers one full `core.agent` cycle per wakeup, measuring and
logging processing latency, and warning if it exceeds the configured max.

Run continuously via systemd (see scheduler/tradalgo.service) -- NOT via
cron, since precise sub-minute alignment plus a controlled latency buffer
is awkward to express with cron's minute-granularity scheduling.
"""
import argparse
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

from decision_log import db as decision_db

M15_BOUNDARY_MINUTES = (0, 15, 30, 45)


def next_m15_close(now: datetime) -> datetime:
    """
    Pure function. Given a timezone-aware UTC `now`, returns the next M15
    candle close boundary strictly after `now` (:00/:15/:30/:45 UTC,
    seconds/microseconds = 0).

    If `now` is exactly on a boundary, returns the NEXT one (15 minutes
    later), not `now` itself -- a candle that just closed this instant
    hasn't necessarily been indexed/published by the broker yet.
    """
    base = now.replace(second=0, microsecond=0)
    for minute in M15_BOUNDARY_MINUTES:
        candidate = base.replace(minute=minute)
        if candidate > now:
            return candidate
    # No boundary left this hour strictly after `now` -- roll to the next
    # hour's :00 boundary (handles the hour/day rollover cases too, since
    # `datetime` arithmetic below naturally carries over day/month/year).
    next_hour = base.replace(minute=0) + timedelta(hours=1)
    return next_hour


def next_wake_time(now: datetime, latency_buffer_seconds: int) -> datetime:
    """next_m15_close(now) + a configurable latency buffer."""
    return next_m15_close(now) + timedelta(seconds=latency_buffer_seconds)


def is_processing_time_abnormal(latency_ms: int, max_processing_time_seconds: int) -> bool:
    """Pure predicate: strictly greater than the configured max (== is not yet abnormal)."""
    return latency_ms > max_processing_time_seconds * 1000


def run_cycle_subprocess(*, dry_run: bool = False) -> tuple[int, int]:
    """
    Invokes `python -m core.agent [--dry-run] --once` as a subprocess --
    simple failure isolation per cycle, and matches the README's
    documented manual invocation pattern exactly. Returns (return_code,
    latency_ms).
    """
    cmd = [sys.executable, "-m", "core.agent", "--once"]
    if dry_run:
        cmd.append("--dry-run")
    start = time.monotonic()
    result = subprocess.run(cmd)
    latency_ms = int((time.monotonic() - start) * 1000)
    return result.returncode, latency_ms


def main_loop(
    *,
    latency_buffer_seconds: int,
    max_processing_time_seconds: int,
    db_path: str,
    dry_run: bool = False,
) -> None:
    """
    Infinite loop: compute the next wake time, sleep until then (re-checking
    the dashboard's pause flag on each wake -- if paused, skip the cycle but
    still re-arm for the next M15 boundary rather than busy-waiting), run
    one agent cycle, record latency, and warn to stderr if it's abnormal.
    """
    decision_db.init_db(db_path)
    while True:
        now = datetime.now(timezone.utc)
        wake_at = next_wake_time(now, latency_buffer_seconds)
        sleep_seconds = (wake_at - datetime.now(timezone.utc)).total_seconds()
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

        state = decision_db.get_scheduler_state(db_path)
        if state.get("paused"):
            print(f"[scheduler] Paused -- skipping cycle at {wake_at.isoformat()}.", file=sys.stderr)
            decision_db.update_scheduler_heartbeat(db_path)
            continue

        print(f"[scheduler] Triggering cycle for M15 close at {wake_at.isoformat()}.", file=sys.stderr)
        returncode, latency_ms = run_cycle_subprocess(dry_run=dry_run)
        decision_db.update_scheduler_heartbeat(db_path, latency_ms=latency_ms)

        if returncode != 0:
            print(f"[scheduler] core.agent exited with code {returncode}.", file=sys.stderr)
        if is_processing_time_abnormal(latency_ms, max_processing_time_seconds):
            print(
                f"[scheduler] WARNING: processing latency {latency_ms}ms exceeded "
                f"max_processing_time_seconds ({max_processing_time_seconds}s).",
                file=sys.stderr,
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="TradAlgo M15-close-aligned scheduler daemon")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--latency-buffer-seconds", type=int, default=45)
    parser.add_argument("--max-processing-time-seconds", type=int, default=300)
    parser.add_argument("--db-path", default="decision_log/tradalgo.db")
    args = parser.parse_args()
    main_loop(
        latency_buffer_seconds=args.latency_buffer_seconds,
        max_processing_time_seconds=args.max_processing_time_seconds,
        db_path=args.db_path,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
