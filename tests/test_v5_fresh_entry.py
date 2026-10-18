"""Fresh-run entry and in-process preparation; no provider or whole-review calls."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from spacebio_review.admission import AdmissionRun
from spacebio_review.cli import build_parser
from spacebio_review.runner import ReviewConfig, read_result
from spacebio_review.v5 import entry


def test_cli_and_options_have_no_resume_mode():
    assert "--resume" not in build_parser().format_help()
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--application", "plan.md", "--resume"])
    with pytest.raises(ValidationError):
        ReviewConfig(resume=True)


def test_existing_output_is_read_only_and_cannot_start_another_run(tmp_path):
    output = tmp_path / "finished"
    output.mkdir()
    result_path = output / "review.json"
    data = {"status": "completed", "report_path": "report.zh.md"}
    result_path.write_text(json.dumps(data), "utf-8")
    assert read_result(result_path)["data"] == data
    with pytest.raises(ValueError, match="empty output directory"):
        entry.validate_run([tmp_path / "application.md"], SimpleNamespace(output=output))
    assert json.loads(result_path.read_text("utf-8")) == data


async def test_preparation_always_creates_new_materials_admission_and_chief(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.chdir(root)
    application = tmp_path / "application.md"
    application.write_text("# 科学计划\n\n在轨活细胞成像与地面对照比较。", "utf-8")
    options = ReviewConfig(output=tmp_path / "first", env_file=str(tmp_path / "unused.env"))
    settings = entry.load_settings(options)
    settings.review.external_research_enabled = False
    settings.reporting.english_enabled = False
    monkeypatch.setattr(entry, "load_settings", lambda _: settings)
    admissions, models = [], []

    class LocalModel:
        def __init__(self, config, *, budget):
            self.config, self.budget = config, budget
            models.append(self)

        async def complete(self, *args, **kwargs):
            raise AssertionError("Preparation must not call the scientific author.")

        async def close(self):
            pass

    class LocalAdmission:
        def __init__(self, runtime, store, config, *, evaluation_system):
            self.store = store

        async def check(self, bundle):
            admissions.append(bundle.source_ids)
            assert "活细胞" in self.store.retained(bundle.source_ids[0]).passages[0].text
            return AdmissionRun(
                kind="proposal_content", status="skipped", reason="formal_review_disabled"
            )

    chief_ids = []
    for output in (tmp_path / "first", tmp_path / "second"):
        current_options = options.model_copy(update={"output": output})
        prepared = await entry.prepare_review(
            [application], current_options,
            model_factory=LocalModel, admission_factory=LocalAdmission,
        )
        try:
            chief_ids.append(prepared.chief_id)
            assert prepared.runtime.budget.usage()["model_calls"] == 0
            assert (output / "application.json").is_file()
            assert (output / "admission.json").is_file()
            assert not (output / "v5" / "review-state.json").exists()
            assert not (output / "v5" / "format.json").exists()
            assert not hasattr(prepared, "checkpoint")
        finally:
            await prepared.close()
    assert len(admissions) == 2
    assert len(set(chief_ids)) == 2
    before = len(models)
    with pytest.raises(ValueError, match="empty output directory"):
        await entry.prepare_review(
            [application], options, model_factory=LocalModel, admission_factory=LocalAdmission
        )
    assert len(models) == before
