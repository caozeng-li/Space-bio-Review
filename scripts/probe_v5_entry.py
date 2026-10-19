"""Offline v5 boundary checks; never calls run_review or assembles a complete review."""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fixtures.session import (  # noqa: E402
    MeteredScript,
    reply,
    require_content,
    tool_call,
)

from spacebio_review.admission import (  # noqa: E402
    AdmissionDecision,
    AdmissionRun,
    FormalReviewDecision,
)
from spacebio_review.evidence.types import SourceMaterial, Visual  # noqa: E402
from spacebio_review.persistence import write_json  # noqa: E402
from spacebio_review.runner import ReviewConfig  # noqa: E402
from spacebio_review.v5.config import load_config  # noqa: E402
from spacebio_review.v5.entry import (  # noqa: E402
    prepare_review,
)

APPLICATION = "# 固定局部计划书\n\n本计划含在轨荧光记录和地面对照；样本返回后开展分子检测。\n"
ATTACHMENT = "# 固定附件\n\n地面对照与在轨组按相同时程采样。\n"


class Models:
    def __init__(self, steps=()):
        self.steps, self.models = list(steps), []

    def __call__(self, config, *, budget, scheduler):
        model = MeteredScript(self.steps, name=config.model)
        model.config = config
        model.budget = budget
        model.scheduler = scheduler
        self.models.append(model)
        return model

    @property
    def calls(self):
        return sum(len(model.requests) for model in self.models)


class AdmissionScript:
    """The retained admission executor has a read/submit response and a formal response."""

    def __init__(self, config, budget, result):
        self.config, self.budget, self.result = config, budget, result
        self.calls = 0

    async def complete(self, messages, tools=None, *, response_schema=None):
        ticket = self.budget.reserve(self.config, messages, tools, response_schema)
        self.calls += 1
        try:
            if self.calls == 1:
                assert tools and response_schema is None
                return reply(
                    tool_call("submit_result", {"reason": "固定材料已读完。", "unfinished": []})
                )
            assert self.calls == 2 and response_schema and not tools
            return self.result(messages)
        finally:
            self.budget.settle(ticket, SimpleNamespace(prompt_tokens=120, completion_tokens=40))

    async def close(self):
        pass


class FixedAdmission:
    """Replace the admission result only; real material parsing and chief setup remain."""

    calls = []

    def __init__(self, runtime, store, config, *, evaluation_system):
        self.store, self.config, self.system = store, config, evaluation_system

    async def check(self, bundle):
        originals = "\n".join(
            passage.text
            for sid in bundle.source_ids
            for passage in self.store.retained(sid).passages
        )
        assert "地面对照" in originals and "相同时程采样" in originals
        self.calls.append((self.system, self.config.formal_review_enabled))
        if self.system == "gbt42048_2022":
            if not self.config.formal_review_enabled:
                return AdmissionRun(
                    kind="proposal_content", status="skipped", reason="formal_review_disabled"
                )
            return AdmissionRun(
                kind="proposal_content",
                status="completed",
                decision=FormalReviewDecision(
                    conclusion="A", rationale="固定边界结果：内容齐备。", checks=None
                ),
            )
        return AdmissionRun(
            status="completed",
            decision=AdmissionDecision(
                decision="admit", reason_code="reviewable", rationale="固定边界结果：可以评审。"
            ),
        )


def fixture(folder, *, system="gbt42048_2022", formal=False):
    folder.mkdir(parents=True, exist_ok=False)
    application, attachment = folder / "application.md", folder / "attachment.md"
    application.write_text(APPLICATION, "utf-8")
    attachment.write_text(ATTACHMENT, "utf-8")
    runtime = load_config(ROOT / "config/runtime.yaml").model_dump(mode="json")
    # Provider objects remain fixed responses; no embedding service is part of this check.
    runtime["embedding"] = None
    configs = {
        "runtime": runtime,
        "review": {
            "evaluation_system": system,
            "external_research_enabled": False,
            "parallel_topics": 2,
        },
        "reporting": {"english_enabled": False},
        "admission": {
            **yaml.safe_load((ROOT / "config/admission.yaml").read_text("utf-8")),
            "formal_review_enabled": formal,
        },
        "evidence": yaml.safe_load((ROOT / "config/evidence.yaml").read_text("utf-8")),
    }
    paths = {}
    for name, value in configs.items():
        path = folder / (name + ".yaml")
        path.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), "utf-8")
        paths[name + "_config"] = path
    return [application, attachment], ReviewConfig(
        **paths, output=folder / "run", env_file=str(folder / "unused.env")
    )


async def preparation_cases(output):
    rows = []
    for system in ("general", "gbt42048_2022"):
        for formal in (False, True):
            paths, options = fixture(
                output / f"{system}-formal-{str(formal).lower()}", system=system, formal=formal
            )
            models = Models()
            prepared = await prepare_review(
                paths, options, model_factory=models, admission_factory=FixedAdmission
            )
            try:
                assert prepared.chief_id
                state = prepared.runtime.load(prepared.chief_id)
                require_content(
                    state.task.model_dump(mode="json"), "地面对照", "返回后", "相同时程采样"
                )
                evaluation = state.task.evaluation
                assert evaluation["evaluation"]["id"] == system
                expected = "skipped" if system == "gbt42048_2022" and not formal else "completed"
                assert evaluation["admission"]["status"] == expected
                assert prepared.admission.status == expected
                assert state.turns_used == 0 and models.calls == 0
                assert prepared.runtime.budget.usage()["model_calls"] == 0
                tools = {
                    row["function"]["name"]
                    for row in prepared.chief.tools(prepared.chief_id).schemas()
                }
                assert "read_materials" in tools and "browse_materials" in tools
                assert "search_sources" not in tools and "related_papers" not in tools
                rows.append(
                    {
                        "system": system,
                        "formal_enabled": formal,
                        "admission": expected,
                        "model_calls": 0,
                        "chief_id": prepared.chief_id,
                    }
                )
            finally:
                await prepared.close()
    return rows


async def actual_admission(output):
    paths, options = fixture(output / "actual-formal-skip")
    models = Models()
    prepared = await prepare_review(paths, options, model_factory=models)
    try:
        assert prepared.admission.status == "skipped" and prepared.chief_id
        assert models.calls == 0
        state = prepared.runtime.load(prepared.chief_id)
        state.coordination_phase = "synthesis"
        registry = prepared.chief.tools(prepared.chief_id, state=state)
        sid = prepared.bundle.source_ids[0]
        store = prepared.topics.store
        document = store.retained(sid)
        document.materials.append(
            SourceMaterial(
                material_id="remote-supplement",
                role="supplement",
                url="https://fixture.invalid/supplement.pdf",
            )
        )
        document.visuals.append(
            Visual(
                visual_id="remote-figure",
                locator="Figure 1",
                url="https://fixture.invalid/figure.png",
            )
        )
        store._save(document)
        network = []

        async def no_network(*args, **kwargs):
            network.append((args, kwargs))
            raise AssertionError("External research disabled but requested remote content")

        store.client.request = no_network
        pid = document.passages[-1].passage_id
        local = await registry.execute(
            "read_materials", json.dumps({"source_id": sid, "passage_ids": [pid]})
        )
        assert local.ok and "地面对照" in json.dumps(local.content, ensure_ascii=False)
        external = await registry.execute(
            "acquire_source", json.dumps({"url": "https://fixture.invalid/unprovided.xml"})
        )
        assert not external.ok and external.error == "external_research_disabled"
        supplement = await registry.execute(
            "acquire_source", json.dumps({"source_id": sid, "material_id": "remote-supplement"})
        )
        assert not supplement.ok and supplement.error == "external_research_disabled"
        visual = await registry.execute(
            "read_literature_visual",
            json.dumps(
                {"source_id": sid, "visual_id": "remote-figure", "question": "Explain the image."}
            ),
        )
        assert not visual.ok and visual.error == "local_image_not_available"
        assert models.calls == 0 and not network
    finally:
        await prepared.close()

    def admit(messages):
        require_content(messages, "地面对照", "返回后")
        application = json.loads((options.output / "application.json").read_text("utf-8"))
        sid = application["sources"][0]["source_id"]
        doc = json.loads((options.output / "sources" / sid / "source.json").read_text("utf-8"))
        pid = doc["passages"][-1]["passage_id"]
        return reply(
            content=json.dumps(
                {
                    "decision": "admit",
                    "reason_code": "reviewable",
                    "rationale": "固定响应：目标与方案可以识别。",
                    "objective": "比较在轨与地面对照的荧光变化。",
                    "approach": "在轨记录并于返回后开展分子检测。",
                    "anchors": [f"{sid}/passage/{pid}"],
                    "reading_limits": [],
                    "next_steps": [],
                },
                ensure_ascii=False,
            )
        )

    paths, options = fixture(output / "actual-general-admission", system="general")
    admission_models = []

    def admission_model(config, *, budget, scheduler):
        model = AdmissionScript(config, budget, admit)
        admission_models.append(model)
        return model

    prepared = await prepare_review(paths, options, model_factory=admission_model)
    try:
        assert prepared.admission.status == "completed", prepared.admission
        assert prepared.admission.can_proceed and prepared.chief_id
        assert sum(model.calls for model in admission_models) == 2
        budget = prepared.runtime.budget
        assert budget.usage()["model_calls"] == 2
        assert budget.usage("admission")["model_calls"] == 2
        assert prepared.runtime.load(prepared.chief_id).turns_used == 0
        return {
            "actual_formal_skip_calls": 0,
            "actual_general_admission_calls": 2,
            "owner": "admission",
            "local_reading_available": True,
            "external_acquisition_disabled": True,
        }
    finally:
        await prepared.close()


def cli_help(output):
    help_result = subprocess.run(
        [sys.executable, "-m", "spacebio_review.cli", "--help"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert "--assessment-config ASSESSMENT_CONFIG" not in help_result.stdout
    assert "--resume" not in help_result.stdout
    (output / "cli-help.txt").write_text(help_result.stdout, "utf-8")
    return {"help": "cli-help.txt"}



async def main(output):
    output.mkdir(parents=True, exist_ok=False)
    result = {
        "preparation": await preparation_cases(output),
        "actual_admission": await actual_admission(output),
        "cli_help": cli_help(output),
        "scope": (
            "Application preparation, admission and current entry checks; "
            "no scoring, delivery, external network, or run_review call."
        ),
    }
    write_json(output / "summary.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "runs/v5-local/change13/entry-offline"
    )
    asyncio.run(main(parser.parse_args().output.resolve()))
