"""Bounded public submission archives; never serialize conversations or model reasoning."""

import hashlib
import json
from pathlib import Path


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


class SubmissionRecorder:
    def __init__(self, root=None, max_bytes=1048576):
        self.root = Path(root) if root is not None else None
        if max_bytes < 1024:
            raise ValueError("submission archive limit must be at least 1024 bytes")
        self.max_bytes = max_bytes

    def record(self, run_id, attempt, event, *, raw=None, candidate=None):
        payload = {**event, "raw_content": raw, "candidate": candidate}
        payload["content_hash"] = digest(raw)
        encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        omitted = len(encoded) > self.max_bytes
        if omitted:
            payload = {k: v for k, v in event.items() if k != "validated_product"}
            payload.update(content_hash=digest(raw), content_omitted="size_limit")
            encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        if len(encoded) > self.max_bytes:
            payload = {
                "attempt": attempt,
                "task_id": str(event.get("task_id", ""))[:128],
                "state": event.get("state"),
                "finish_reason": event.get("finish_reason"),
                "candidate_id": event.get("candidate_id"),
                "content_hash": digest(raw),
                "content_omitted": "size_limit_including_metadata",
            }
            encoded = json.dumps(payload).encode("utf-8")
        # TaskSummary contains metadata and pointers, never a second copy of the body.
        summary = {
            k: event.get(k)
            for k in (
                "attempt",
                "finish_reason",
                "json_decoded",
                "state",
                "candidate_id",
                "request",
                "usage",
                "stop_reason",
                "budget",
                "recovery",
                "contract",
                "first_submission",
                "schema_valid",
                "references_valid",
            )
        }
        summary.update(
            content_hash=digest(raw),
            error_count=len(event.get("errors", [])),
            error_types=list(dict.fromkeys(e.get("type") for e in event.get("errors", [])))[:32],
            content_omitted=omitted,
            path=None,
            candidate_path=None,
            persistence="unavailable",
        )
        if self.root is None:
            return summary
        path = self.root / "submissions" / run_id / f"attempt-{attempt:03d}.json"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_bytes(encoded)
            temporary.replace(path)
        except OSError as exc:
            summary["persistence"] = "write_failed"
            summary["write_error"] = type(exc).__name__
            return summary
        summary.update(path=str(path), persistence="saved")
        if candidate is not None and not omitted:
            summary["candidate_path"] = str(path)
        return summary
