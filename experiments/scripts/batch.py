"""Sequential experiment launcher; review logic remains in the native implementations."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

EXPERIMENT = Path(__file__).resolve().parents[1]
PROJECT = EXPERIMENT.parent
EXTERNAL = PROJECT.parent / "external_baselines"
INTERNAL = {
    "SpaceBioReview": None,
    "SingleResearchAgent": "single_research_agent",
    "StaticResearchCommittee": "static_research_committee",
    "WithoutConsultation": "disable_consultation",
}
EXTERNAL_REPOS = {
    "ThorneSectionWise": "grant-perturbation-analysis",
    "smolagents": "smolagents",
    "ScholarEval": "ScholarEval",
}
DELIVERED_STATUSES = {"completed", "fixed_by_me"}


def read_json(path):
    return json.loads(Path(path).read_text("utf-8"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", "utf-8")
    temporary.replace(path)


def now():
    return datetime.now(timezone.utc).isoformat()


def python(repo):
    return repo / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def command_for(group, application, output, as_of):
    if group in INTERNAL:
        command = [
            str(python(PROJECT)),
            "-B",
            "-X",
            "utf8",
            "-u",
            "-m",
            "spacebio_review.cli",
            "--application",
            str(application),
            "--output",
            str(output),
            "--env-file",
            str(PROJECT / ".env"),
        ]
        for kind in ("runtime", "admission", "reporting", "evidence"):
            command += [f"--{kind}-config", str(EXPERIMENT / "config" / f"{kind}.yaml")]
        command += ["--review-config", str(EXPERIMENT / group / "config/review.yaml")]
        return command, PROJECT
    repo = EXTERNAL / EXTERNAL_REPOS[group]
    return [
        str(python(repo)),
        "-B",
        "-X",
        "utf8",
        "-u",
        str(repo / "smoke_review.py"),
        "--input",
        str(application),
        "--output",
        str(output),
        "--as-of",
        as_of,
    ], repo


def process_environment(as_of):
    return {
        **os.environ,
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "SBR_EXPERIMENT_CONFIG_DIR": str(EXPERIMENT / "config"),
        "SBR_EXPERIMENT_AS_OF": as_of,
    }


@contextmanager
def batch_lock(directory):
    """The OS releases this lock even after Ctrl+C or a crashed launcher."""
    path = directory / ".batch.lock"
    with path.open("a+b") as handle:
        if path.stat().st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def terminate_tree(process):
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        os.killpg(process.pid, signal.SIGTERM)
    process.wait()


def execute(command, cwd, log, env, timeout):
    with log.open("a", encoding="utf-8") as handle:
        handle.write(f"\n--- Launch {now()} ---\n")
        handle.flush()
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=os.name != "nt",
        )
        try:
            return process.wait(timeout=timeout), None
        except subprocess.TimeoutExpired:
            terminate_tree(process)
            return process.returncode, "timed_out"
        except KeyboardInterrupt:
            terminate_tree(process)
            raise


def outcome(group, native, returncode):
    path = native / ("review.json" if group in INTERNAL else "status.json")
    details = read_json(path) if path.exists() else {}
    report = native / "report.en.md"
    if group in INTERNAL and details.get("report_en_path"):
        report = native / details["report_en_path"]
    status = details.get("status", "failed")
    if returncode != 0 and status == "completed":
        status = "failed"
    if status == "completed" and (not report.is_file() or not report.stat().st_size):
        status = "failed"
    return status, report, details.get("usage", details.get("token_usage"))


def run_batch(group, entries, *, as_of, retry_failed=False, timeout=14400, execute_fn=execute):
    directory = EXPERIMENT / group
    status_path = directory / "status.json"
    state = (
        read_json(status_path)
        if status_path.exists()
        else {
            "group": group,
            "as_of": as_of,
            "projects": {},
        }
    )
    interrupted = False
    for item in entries:
        pid = item["project_id"]
        history = state["projects"].setdefault(pid, [])
        previous = history[-1] if history else None
        if previous and previous["status"] in DELIVERED_STATUSES:
            print(f"SKIP {previous['status']}: {pid}", flush=True)
            continue
        if previous and previous["status"] not in ("running", "interrupted") and not retry_failed:
            print(f"SKIP {previous['status']}: {pid} (use --retry-failed)", flush=True)
            continue
        # ScholarEval's existing adapter can resume its completed stages and retrieval batches.
        # Other implementations require an empty native directory for a fresh project attempt.
        resume = group == "ScholarEval" and previous is not None
        attempt = (
            directory / previous["attempt"]
            if resume
            else directory / "runs" / pid / f"attempt-{len(history) + 1:03d}"
        )
        attempt.mkdir(parents=True, exist_ok=True)
        native = attempt / "native"
        command, cwd = command_for(group, EXPERIMENT / item["input"], native, as_of)
        row = {
            "status": "running",
            "started_at": now(),
            "attempt": str(attempt.relative_to(directory)),
            "resumed": resume,
            "input_sha256": item["sha256"],
            "command": command,
        }
        history.append(row)
        write_json(status_path, state)
        print(f"START {group}: {pid}{' (resume)' if resume else ''}", flush=True)
        started = time.monotonic()
        try:
            code, forced = execute_fn(
                command, cwd, attempt / "console.log", process_environment(as_of), timeout
            )
            status, report, usage = outcome(group, native, code)
            row.update(status=forced or status, exit_code=code, usage=usage)
            if row["status"] == "completed":
                destination = directory / f"{pid}_report.md"
                shutil.copyfile(report, destination)
                row["report"] = destination.name
        except KeyboardInterrupt:
            row["status"] = "interrupted"
            interrupted = True
        except Exception as error:
            row.update(status="failed", error=f"{type(error).__name__}: {error}")
        row.update(finished_at=now(), elapsed_seconds=round(time.monotonic() - started, 3))
        write_json(status_path, state)
        print(f"END {pid}: {row['status']}", flush=True)
        if interrupted:
            break
    if interrupted:
        return 130
    return (
        0
        if all(
            state["projects"][e["project_id"]][-1]["status"] in DELIVERED_STATUSES for e in entries
        )
        else 1
    )


def main(group):
    parser = argparse.ArgumentParser(description=f"Run {group} sequentially on 20 proposals")
    parser.add_argument("--dry-run", action="store_true", help="Print commands; no service calls")
    parser.add_argument("--project", action="append", help="Select project ID; repeat for several")
    parser.add_argument("--limit", type=int, help="Run only the first N selected projects")
    parser.add_argument(
        "--retry-failed", action="store_true", help="Reattempt failed/limited items"
    )
    args = parser.parse_args()
    manifest = read_json(EXPERIMENT / "manifest.json")
    entries = manifest["projects"]
    if args.project:
        missing = set(args.project) - {e["project_id"] for e in entries}
        if missing:
            parser.error(f"Unknown projects: {sorted(missing)}")
        entries = [e for e in entries if e["project_id"] in args.project]
    if args.limit is not None:
        if args.limit < 1:
            parser.error("--limit must be positive")
        entries = entries[: args.limit]
    for item in entries:
        path = EXPERIMENT / item["input"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            parser.error(f"Frozen input changed: {path}")
        command, cwd = command_for(
            group,
            path,
            EXPERIMENT / group / "runs" / item["project_id"] / "attempt-001/native",
            manifest["as_of"],
        )
        if not Path(command[0]).is_file():
            parser.error(f"Virtual environment missing: {command[0]}")
        if args.dry_run:
            print(f"cwd={cwd}\n{subprocess.list2cmdline(command)}\n")
    if args.dry_run:
        print(f"{group}: {len(entries)} inputs; no execution")
        return 0
    with batch_lock(EXPERIMENT / group):
        return run_batch(
            group,
            entries,
            as_of=manifest["as_of"],
            retry_failed=args.retry_failed,
            timeout=manifest["project_timeout_seconds"],
        )


if __name__ == "__main__":
    raise SystemExit("Use a group's scripts/run.py entry point")
