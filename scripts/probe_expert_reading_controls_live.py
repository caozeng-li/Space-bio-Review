"""Compare thinking and first-reading source comparisons in autonomous expert sessions.

Only test-local settings and messages change. No old opinion or gold answer is supplied.
"""

import argparse
import asyncio
from copy import deepcopy
from pathlib import Path

import yaml
from probe_expert_trajectory_live import (
    ObservedRuntime,
    load,
    production_hashes,
    render_trajectory,
    run_one,
    summarize_trajectory,
)

from spacebio_review.agents.reading import ReadPassages
from spacebio_review.persistence import write_json

CONDITIONS = {
    "baseline": {"thinking": False, "focused_reading": False},
    "thinking": {"thinking": True, "focused_reading": False},
    "focused": {"thinking": False, "focused_reading": True},
}

# Case-specific experimental material selection, not a proposed production rule.
COMPARISONS = [
    {
        "name": "application_operations",
        "source_id": "src-0b4300b4279e",
        "triggers": ["p30", "p31"],
        "passage_ids": ["p29", "p30", "p31"],
        "question": (
            "这些申请原句中的地面准备、镜像调试和在轨操作怎样衔接？"
            "具体操作方式中，哪些已写明，哪些留待调试确定？"
            "这些安排对取得预期科学观测有什么意义？"
        ),
    },
    {
        "name": "imaging_platforms",
        "source_id": "src-b6d47e32405f",
        "triggers": ["m-e5e1236c279d-p148", "m-e5e1236c279d-p149", "m-e5e1236c279d-p194"],
        "passage_ids": ["m-e5e1236c279d-p148", "m-e5e1236c279d-p149", "m-e5e1236c279d-p194"],
        "question": (
            "这些段落各自讨论哪些成像技术、哪个平台和什么发展阶段？"
            "它们之间是什么关系，对本申请的在轨成像安排提供了怎样的支持，"
            "还有哪些具体条件需要了解？"
        ),
    },
    {
        "name": "sample_stages",
        "source_id": "src-83a99b4140a1",
        "triggers": ["m-35f49c203755-p22", "m-35f49c203755-p28", "m-35f49c203755-p29"],
        "passage_ids": ["m-35f49c203755-p17", "m-35f49c203755-p22", "m-35f49c203755-p28"],
        "question": (
            "这些方法与结果分别对应什么样本、实验阶段和检测目标？"
            "其中的质量数值或比例各说明什么？"
            "这一先例对本申请的样品保存、返回后分析能提供怎样的认识？"
        ),
    },
]


class ComparisonRuntime(ObservedRuntime):
    def __init__(self, root, *args, **kwargs):
        super().__init__(root, *args, **kwargs)
        self.treatment = load(Path(root).parent / "plan.json")["treatment"]
        self.comparisons_delivered = set()

    async def _call_model(self, state, model, messages, schemas, **kwargs):
        if self.treatment["focused_reading"] and state.task.role == "researcher":
            await self.compare_first_readings(state, messages)
        return await super()._call_model(state, model, messages, schemas, **kwargs)

    async def compare_first_readings(self, state, messages):
        available = {
            (row["reference"]["source_id"], row["reference"]["passage_id"])
            for row in state.evidence.values()
        }
        for group in COMPARISONS:
            if group["name"] in self.comparisons_delivered or not any(
                (group["source_id"], passage) in available for passage in group["triggers"]
            ):
                continue
            result = await self.probe_source_access.read_materials(ReadPassages(
                source_id=group["source_id"], passage_ids=group["passage_ids"],
                reading_tokens=32000,
            ))
            if not result.ok:
                raise RuntimeError(f"comparison_material_unavailable:{group['name']}")
            captured = self.probe_source_access.references.snapshot_choices(selected=False)
            selected = {
                handle: row for handle, row in captured.items()
                if row["reference"]["source_id"] == group["source_id"]
                and row["reference"]["passage_id"] in group["passage_ids"]
            }
            if len(selected) != len(group["passage_ids"]):
                raise RuntimeError(f"comparison_material_incomplete:{group['name']}")
            newly_captured = self._capture(state, selected)
            lines = [
                "你正在首次研究下面这组相关材料。先在本轮公开答复中用一小段话说明你对问题的认识，"
                "然后按需要继续阅读、检索或形成评审意见。这是同一研究中的一次材料比较。",
                "", group["question"], "",
            ]
            for passage in group["passage_ids"]:
                handle, row = next((h, r) for h, r in selected.items()
                                   if r["reference"]["passage_id"] == passage)
                lines.extend([f"原文 [{handle}] · {row['title']} · {row['locator']}",
                              row["quote"], ""])
            message = {"role": "user", "content": "\n".join(lines)}
            # The same in-memory expert conversation carries both the comparison
            # and its own answer into subsequent normal research turns.
            messages.append(message)
            conversation = self._conversations[state.session_id]
            conversation.remember(selected)
            conversation.shown_handles.update(selected)
            self.comparisons_delivered.add(group["name"])
            self.trace.event(
                "focused_reading", group=group["name"], turn=state.turns_used + 1,
                session_id=state.session_id, message=message,
                readings=selected, newly_captured=list(newly_captured),
            )


def prepare(args):
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    baseline = Path(args.baseline).resolve()
    parent_plan = load(baseline / "plan.json")
    config = yaml.safe_load((baseline / "probe-config.yaml").read_text("utf-8"))
    current_hashes = production_hashes()
    if current_hashes != parent_plan["production_hashes"]:
        raise ValueError("production_changed_since_parent_experiment")
    for name, treatment in CONDITIONS.items():
        folder = output / name
        folder.mkdir()
        condition_config = deepcopy(config)
        condition_config["models"]["scientific"]["extra_body"]["enable_thinking"] = (
            treatment["thinking"]
        )
        (folder / "probe-config.yaml").write_text(
            yaml.safe_dump(condition_config, allow_unicode=True), "utf-8",
        )
        write_json(folder / "plan.json", {
            **parent_plan, "parent_experiment": str(baseline),
            "treatment": treatment,
            "comparisons": COMPARISONS if treatment["focused_reading"] else [],
            "scope": [
                "Current expert, same retained corpus and responsibility as parent experiment.",
                "No historical manuscript, gold answer, forced publication or post-hoc correction.",
                "Thinking changes only expert enable_thinking; auxiliary routes unchanged.",
                "Focused reading adds originals and a relational question at first exposure.",
                "Comparison pairs are researcher-selected, not automatic production selection.",
            ],
        })
    write_json(output / "design.json", {
        "conditions": CONDITIONS, "repeats_per_condition": 3, "concurrency": 2,
        "parent_experiment": str(baseline), "production_hashes": current_hashes,
        "manual_evaluation": [
            "Confocal identity and actual/future stage; specific CSS configuration may be unknown.",
            "EVT versus flight sample quality; numerical unit, preservation and applicable assay.",
            "Application implementation choices versus reviewer suggestions, including automation.",
            "Useful scientific judgments and evidence; saying nothing is not success.",
            "First visible understanding and retention into final submitted opinion.",
        ],
        "service_reference": "https://help.aliyun.com/zh/model-studio/deepseek-api",
        "recording": (
            "Public messages preserved; reasoning_content omitted from disk, "
            "length/usage recorded; live protocol untouched."
        ),
    })


async def main(args):
    if args.prepare:
        prepare(args)
        return
    if not args.live:
        raise ValueError("Real model calls require --live")
    gate = asyncio.Semaphore(args.concurrency)

    async def job(spec):
        name, repeat = spec.split(":")
        if name not in CONDITIONS:
            raise ValueError(name)
        output = Path(args.output).resolve() / name
        async with gate:
            await run_one(output, int(repeat), runtime_class=ComparisonRuntime)
            run = output / f"run-{repeat}"
            render_trajectory(run)
            summarize_trajectory(run)

    results = await asyncio.gather(*(job(spec) for spec in args.jobs), return_exceptions=True)
    failures = [f"{spec}: {result!r}" for spec, result in zip(args.jobs, results, strict=True)
                if isinstance(result, BaseException)]
    if failures:
        raise RuntimeError("\n".join(failures))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--baseline", default="runs/expert_trajectory_sample2")
    parser.add_argument("--output", default="runs/expert_reading_controls_sample2")
    parser.add_argument("--concurrency", type=int, choices=[1, 2], default=2)
    parser.add_argument("--jobs", nargs="+", default=["baseline:1", "thinking:1", "focused:1"])
    asyncio.run(main(parser.parse_args()))
