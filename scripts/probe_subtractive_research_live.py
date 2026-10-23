"""Test-only subtraction of persistent writing demands in autonomous expert research."""

import argparse
import asyncio
import json
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

from spacebio_review.persistence import write_json
from spacebio_review.v5.scientific_roles import EXPERT_OPINION

DELIVERY = """提交你所负责问题的专业评审意见及必要论据，篇幅由实际内容决定。
说明相关研究实际发现了什么、它与申请的关系，以及据此作出的评价。
保留决定判断的研究对象、比较与条件，给出有依据的科学价值和实质限制。
title 是简短议题名，content 是意见正文。采用原文时使用提供的 [证据N] 标记，
代码维护引用身份与正式编号。"""


def lean_instruction(text):
    return (
        text.replace(EXPERT_OPINION, "")
        .replace(
            "并联系申请形成判断。用 read_materials、browse_materials "
            "或 retrieve_knowledge 继续阅读，",
            "用 read_materials、browse_materials 或 retrieve_knowledge 继续阅读，",
        )
        .replace(
            "交给主审的是有充分论据的评审意见：说明已有研究发现、与申请的关系以及由此作出的判断。\n",
            "",
        )
    )


class SubtractiveRuntime(ObservedRuntime):
    def __init__(self, root, *args, **kwargs):
        super().__init__(root, *args, **kwargs)
        self.condition = load(Path(root).parent / "plan.json")["condition"]

    async def _call_model(self, state, model, messages, schemas, **kwargs):
        if self.condition != "lean" or state.task.role != "researcher":
            return await super()._call_model(state, model, messages, schemas, **kwargs)
        outgoing = deepcopy(messages)
        for message in outgoing:
            if message["role"] == "system":
                message["content"] = lean_instruction(message["content"])
            elif message["role"] == "user" and isinstance(message.get("content"), str):
                try:
                    body = json.loads(message["content"])
                except json.JSONDecodeError:
                    continue
                # Alter only explicit runtime instruction fields, never originals,
                # observations, authored output, responsibilities or scientific skills.
                if isinstance(body, dict) and isinstance(body.get("instruction"), str):
                    original = body["instruction"]
                    body["instruction"] = lean_instruction(original)
                    if (
                        "当前研究预算已到交付窗口" in original
                        or "当前会话需要腾出阅读空间" in original
                    ):
                        body["instruction"] += "\n" + DELIVERY
                    message["content"] = json.dumps(body, ensure_ascii=False)
        wire_schemas = deepcopy(schemas)
        for schema in wire_schemas:
            if schema["function"]["name"] == "publish_manuscript":
                # The publication contract belongs to the publication tool; there
                # is no new generation step, fixed length or extra review template.
                schema["function"]["description"] = DELIVERY
        return await super()._call_model(state, model, outgoing, wire_schemas, **kwargs)


def prepare(args):
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    parent = Path(args.parent).resolve()
    plan = load(parent / "plan.json")
    if production_hashes() != plan["production_hashes"]:
        raise ValueError("production_changed_since_parent")
    config = yaml.safe_load((parent / "probe-config.yaml").read_text("utf-8"))
    for condition in ["baseline", "lean"]:
        folder = output / condition
        folder.mkdir()
        (folder / "probe-config.yaml").write_text(
            yaml.safe_dump(config, allow_unicode=True), "utf-8"
        )
        write_json(folder / "plan.json", {**plan, "condition": condition})
    write_json(
        output / "design.json",
        {
            "conditions": ["baseline", "lean"],
            "repeats_per_condition": 3,
            "production_hashes": production_hashes(),
            "parent": str(parent),
            "delivery_instruction": DELIVERY,
            "scope": "Autonomous topic research plus current chief assessment; no full review.",
        },
    )
    (output / "protocol.md").write_text(
        "# 减法实验方案\n\n"
        "先做当前实现与精简研究提示各3次完整专题研究，再将意见送入正式主审评价输入。"
        "保持同一完整申请、5个留存来源、职责、技能、检索/阅读能力、模型和预算；thinking关闭。"
        "专家自主选材、研究、交稿与停止；不注入历史意见、标准答案或易错段落。"
        "保留真实召回、重排、按需阅读员；不开展新网络发现，不跑完整评审。\n\n"
        "精简组仅从研究阶段指令中删除常驻完整交稿规范、重复交付论据要求及一处读完即联系申请的表述。"
        "publish_manuscript工具保留简短交稿要求；没有字数、首尾结论或每段服务项目判断的规定。"
        "专业技能、原文、对话记录和意见持久化不变；不新建阅读模式或补加纠错提示。"
        "这是提示组织与交稿要求的组合减法，不能归因到某一句删除。\n\n"
        "评价：对象/阶段/数据含义错配，未说明变不存在，申请操作被改写，引用及有用科学论证。"
        "单列实际材料暴露；漏读或不提某事实不记作理解正确。记录全部失败样本及服务错误。"
        "后续咨询减法单独比较实际请求交付，沿用本轮真实生成意见；不人工造错稿。"
        "是否追加普通文本阅读员移除，由前述结果和实际调用情况决定并记录。\n",
        "utf-8",
    )


async def main(args):
    if args.prepare:
        prepare(args)
        return
    if not args.live:
        raise ValueError("Use --live for real services")
    gate = asyncio.Semaphore(2)

    async def job(spec):
        group, repeat = spec.split(":")
        output = Path(args.output).resolve() / group
        async with gate:
            await run_one(output, int(repeat), runtime_class=SubtractiveRuntime)
            render_trajectory(output / f"run-{repeat}")
            summarize_trajectory(output / f"run-{repeat}")

    results = await asyncio.gather(*(job(spec) for spec in args.jobs), return_exceptions=True)
    failures = [
        f"{spec}: {result!r}"
        for spec, result in zip(args.jobs, results, strict=True)
        if isinstance(result, BaseException)
    ]
    if failures:
        raise RuntimeError("\n".join(failures))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--parent", default="runs/question_workspace_sample2/baseline")
    parser.add_argument("--output", default="runs/subtractive_research_sample2")
    parser.add_argument(
        "--jobs",
        nargs="+",
        default=["baseline:1", "lean:1", "baseline:2", "lean:2", "baseline:3", "lean:3"],
    )
    asyncio.run(main(parser.parse_args()))
