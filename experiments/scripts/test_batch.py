"""Offline checks: real entry setup, subprocess handling, and batch recovery semantics."""

import asyncio
import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import batch


class BatchTests(unittest.TestCase):
    def test_manually_fixed_reports_are_not_retried(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(batch, "EXPERIMENT", Path(temp)):
            path = Path(temp) / "SpaceBioReview/status.json"
            batch.write_json(
                path,
                {
                    "group": "SpaceBioReview",
                    "projects": {
                        "a": [{"status": "fixed_by_me", "report": "a_report.md"}],
                    },
                },
            )

            def must_not_execute(*args):
                raise AssertionError("A manually closed report must not be regenerated")

            self.assertEqual(
                batch.run_batch(
                    "SpaceBioReview",
                    [{"project_id": "a"}],
                    as_of="2026-10-28",
                    retry_failed=True,
                    execute_fn=must_not_execute,
                ),
                0,
            )

    def test_frozen_inputs_and_group_commands(self):
        manifest = batch.read_json(batch.EXPERIMENT / "manifest.json")
        entries = manifest["projects"]
        self.assertEqual(len(entries), 20)
        self.assertEqual(len({e["project_id"] for e in entries}), 20)
        self.assertFalse(
            set(manifest["excluded_development_projects"]) & {e["project_id"] for e in entries}
        )
        for e in entries:
            path = batch.EXPERIMENT / e["input"]
            self.assertEqual(path.read_bytes(), (batch.PROJECT / e["source"]).read_bytes())
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), e["sha256"])
            self.assertNotRegex(path.read_text("utf-8"), r"!\[[^\]]*\]\(|<img\b")
        for group in list(batch.INTERNAL) + list(batch.EXTERNAL_REPOS):
            command, cwd = batch.command_for(group, path, Path("unused"), manifest["as_of"])
            self.assertTrue(Path(command[0]).is_file())
            self.assertTrue(cwd.is_dir())
            self.assertIn(str(path), command)

    def test_fail_continue_skip_success_and_retry_in_fresh_directory(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(batch, "EXPERIMENT", Path(temp)):
            calls = []

            def fake(command, cwd, log, env, timeout):
                native = Path(command[command.index("--output") + 1])
                self.assertFalse(native.exists())  # Required by the real internal CLI.
                calls.append(native)
                native.mkdir()
                success = len(calls) != 1
                batch.write_json(
                    native / "review.json",
                    {
                        "status": "completed" if success else "partial",
                        "report_en_path": "report.en.md",
                        "usage": {"model_calls": 3},
                    },
                )
                (native / "report.en.md").write_text("# Fixture report", "utf-8")
                return (0 if success else 1), None

            entries = [
                {"project_id": p, "input": f"data/{p}.md", "sha256": "fixture"} for p in ("a", "b")
            ]
            self.assertEqual(
                batch.run_batch("SpaceBioReview", entries, as_of="2026-10-28", execute_fn=fake), 1
            )
            folder = Path(temp) / "SpaceBioReview"
            self.assertFalse((folder / "a_report.md").exists())
            self.assertTrue((folder / "b_report.md").exists())
            batch.run_batch("SpaceBioReview", entries, as_of="2026-10-28", execute_fn=fake)
            self.assertEqual(len(calls), 2)
            self.assertEqual(
                batch.run_batch(
                    "SpaceBioReview",
                    entries,
                    as_of="2026-10-28",
                    retry_failed=True,
                    execute_fn=fake,
                ),
                0,
            )
            self.assertEqual(len(calls), 3)
            self.assertNotEqual(calls[0], calls[2])
            self.assertTrue((folder / "a_report.md").exists())

    def test_scholareval_resumes_native_checkpoints(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(batch, "EXPERIMENT", Path(temp)):
            calls = []

            def fake(command, *args):
                native = Path(command[command.index("--output") + 1])
                calls.append(native)
                if len(calls) == 1:
                    native.mkdir()
                    batch.write_json(native / "completed_stages.json", ["query-generation"])
                    batch.write_json(native / "status.json", {"status": "failed"})
                    return 1, None
                self.assertEqual(
                    batch.read_json(native / "completed_stages.json"), ["query-generation"]
                )
                batch.write_json(native / "status.json", {"status": "completed"})
                (native / "report.en.md").write_text("# Fixture", "utf-8")
                return 0, None

            entries = [{"project_id": "x", "input": "x.md", "sha256": "fixture"}]
            batch.run_batch("ScholarEval", entries, as_of="2026-10-28", execute_fn=fake)
            self.assertEqual(
                batch.run_batch(
                    "ScholarEval", entries, as_of="2026-10-28", retry_failed=True, execute_fn=fake
                ),
                0,
            )
            self.assertEqual(calls[0], calls[1])

    def test_limited_external_output_is_not_published_as_success(self):
        with tempfile.TemporaryDirectory() as temp:
            native = Path(temp)
            (native / "report.en.md").write_text("# Incomplete", "utf-8")
            batch.write_json(native / "status.json", {"status": "limited"})
            self.assertEqual(batch.outcome("smolagents", native, 0)[0], "limited")

    def test_interrupt_does_not_start_next_project(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(batch, "EXPERIMENT", Path(temp)):

            def interrupted(*args):
                raise KeyboardInterrupt

            entries = [{"project_id": p, "input": p, "sha256": "fixture"} for p in ("a", "b")]
            self.assertEqual(
                batch.run_batch(
                    "SpaceBioReview", entries, as_of="2026-10-28", execute_fn=interrupted
                ),
                130,
            )
            state = batch.read_json(Path(temp) / "SpaceBioReview/status.json")
            self.assertEqual(state["projects"]["a"][-1]["status"], "interrupted")
            self.assertNotIn("b", state["projects"])

    def test_actual_child_process_and_timeout(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            code, status = batch.execute(
                [sys.executable, "-c", "print('child-ok')"],
                folder,
                folder / "child.log",
                os.environ.copy(),
                20,
            )
            self.assertEqual((code, status), (0, None))
            self.assertIn("child-ok", (folder / "child.log").read_text("utf-8"))
            _, status = batch.execute(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                folder,
                folder / "timeout.log",
                os.environ.copy(),
                0.5,
            )
            self.assertEqual(status, "timed_out")

    def test_native_entry_for_four_internal_configurations(self):
        from spacebio_review.admission import AdmissionDecision, AdmissionRun
        from spacebio_review.runner import ReviewConfig
        from spacebio_review.v5.entry import prepare_review

        class NoCallsModel:
            def __init__(self, config, **kwargs):
                self.config = config

            async def complete(self, *args, **kwargs):
                raise AssertionError("This boundary test must not call a model")

            async def close(self):
                pass

        class FixedAdmission:
            def __init__(self, *args, **kwargs):
                pass

            async def check(self, bundle):
                return AdmissionRun(
                    status="completed",
                    decision=AdmissionDecision(
                        decision="admit", reason_code="reviewable", rationale="Offline fixture"
                    ),
                )

        async def check():
            with tempfile.TemporaryDirectory() as temp:
                app = Path(temp) / "fixture.md"
                app.write_text(
                    "# Research plan\nCompare numerical estimators on simulated data.", "utf-8"
                )
                for group, switch in batch.INTERNAL.items():
                    options = {
                        f"{k}_config": batch.EXPERIMENT / "config" / f"{k}.yaml"
                        for k in ("runtime", "admission", "reporting", "evidence")
                    }
                    options.update(
                        review_config=batch.EXPERIMENT / group / "config/review.yaml",
                        output=Path(temp) / group,
                        env_file=str(batch.PROJECT / ".env"),
                    )
                    prepared = await prepare_review(
                        [app],
                        ReviewConfig(**options),
                        model_factory=NoCallsModel,
                        admission_factory=FixedAdmission,
                    )
                    try:
                        flags = prepared.settings.review.ablations.model_dump()
                        self.assertEqual(
                            [k for k, v in flags.items() if v], [switch] if switch else []
                        )
                        self.assertIsNotNone(prepared.chief_id)
                        self.assertIsNone(prepared.topics.visual_reader)
                        self.assertFalse(
                            any(m.config.vision for m in prepared.runtime.models.values())
                        )
                    finally:
                        await prepared.close()

        asyncio.run(check())


if __name__ == "__main__":
    unittest.main()
