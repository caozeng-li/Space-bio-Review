"""Replay complete chief inputs through the production decision submission stage."""

import argparse
import asyncio
import hashlib
import json
import math
import random
import re
import statistics
import time
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import yaml
from pydantic import ConfigDict, Field, create_model

from spacebio_review.persistence import write_json
from spacebio_review.review.evaluation import EvaluationSystem
from spacebio_review.v5.assessment import DECISION_INSTRUCTION, AssessmentSubmission
from spacebio_review.v5.assessment_authoring import decision_schema, decision_type
from spacebio_review.v5.budget import SessionAllowance
from spacebio_review.v5.citation_writing import author_messages
from spacebio_review.v5.config import load_config
from spacebio_review.v5.records import SessionTask
from spacebio_review.v5.session import ResearchRuntime

ARMS = ("schema", "template", "schema_guidance", "template_guidance")
ALL_ARMS = (*ARMS, "strict_schema", "strict_described", "strict_parts")
CASES = ("mytest49", "mytest50", "original_combined", "facts_examples")
PARTS = {
    "project_and_value": "项目对象与研究安排，以及本项目最值得获得的认识。写成一个自然段。",
    "support_and_limits": "主要支持依据与真正影响判断的限制。写成一个自然段。",
    "overall_tradeoff": "依据前述价值和限制形成整体取舍，直接评价现有计划。写成一个自然段。",
}
GUIDANCE = """本次完整响应由三个同级字段组成：overall_judgment、dimensions、safety_veto。
综合评价的段落和篇幅要求只适用于 overall_judgment；写完该字段后仍须完成全部六项评分和安全否决判断。
dimensions 内每项分别填写 reason 和整数 score。安全性得分与安全否决是两个分别作出的判断：
dimensions.safety.score 填安全性得分；safety_veto.applies 填是否触发安全否决，二者均须填写。
按下面显示的字段及层级直接完成整个对象。总分、档位和最终支持结论由程序计算。
"""


def load(path):
    return json.loads(Path(path).read_text("utf-8"))


def sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()


def production_hashes():
    return {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for base, pattern in (("src", "*.py"), ("config", "*.yaml"), ("skills", "*.md"))
        for p in sorted(Path(base).rglob(pattern))
    }


def annotated_schema(system):
    schema = decision_schema(system)
    for criterion in system.criteria:
        props = schema["$defs"][criterion.id]["properties"]
        props["reason"]["description"] = f"{criterion.label}的评价依据。"
        props["score"]["description"] = f"{criterion.label}得分，填写整数。"
    safety = schema["$defs"]["safety"]["properties"]
    safety["score"]["description"] = "安全性指标得分，填写整数；是否否决另填 safety_veto.applies。"
    veto = schema["$defs"]["SafetyDecision"]["properties"]
    veto["applies"]["description"] = (
        "是否触发安全否决，填写布尔值；安全性得分另填 dimensions.safety.score。"
    )
    veto["reason"]["description"] = "触发或不触发安全否决的判断依据。"
    return schema


def described_schema(system):
    schema = decision_schema(system)
    # The unchanged production writing requirement, at the field's definition.
    requirement = DECISION_INSTRUCTION.split("overall_judgment ", 1)[1].split(
        "专家意见提供", 1
    )[0].strip()
    schema["properties"]["overall_judgment"]["description"] = requirement
    return schema


def parts_type(system):
    paragraphs = create_model(
        "OverallParagraphs", __config__=ConfigDict(extra="forbid"),
        **{key: (str, Field(min_length=1, description=description))
           for key, description in PARTS.items()},
    )
    return create_model("ParagraphDecision", __base__=decision_type(system),
                        overall_judgment=(paragraphs, ...))


def output_template(schema):
    """Render the same schema as a filled-out shape with typed, non-numeric placeholders."""
    def render(node, indent=0):
        if "$ref" in node:
            node = schema["$defs"][node["$ref"].rsplit("/", 1)[1]]
        kind = node["type"]
        if kind == "object":
            rows = [
                " " * (indent + 2) + json.dumps(key) + ": " + render(value, indent + 2)
                for key, value in node["properties"].items()
            ]
            return "{\n" + ",\n".join(rows) + "\n" + " " * indent + "}"
        if kind == "integer":
            return f"<{node.get('description', '')}范围 {node['minimum']}—{node['maximum']}>"
        if kind == "boolean":
            return f"<{node.get('description', '')}true 或 false>"
        if kind == "string":
            return json.dumps(
                "<" + node.get("description", "填写评价文字") + ">", ensure_ascii=False
            )
        raise ValueError(f"Unexpected decision field: {kind}")

    return (
        "提交的 JSON 对象模板如下。尖括号内是占位说明，替换为你实际形成的判断；"
        "整数和布尔值直接填写，不加引号。保留展示的全部字段和层级，仅输出填好的 JSON 对象。\n"
        + render(schema)
    )


def make_prompt(instruction, system, arm):
    schema = annotated_schema(system) if arm.endswith("guidance") else decision_schema(system)
    contract = (
        output_template(schema) if arm.startswith("template")
        else "提交的 JSON 结构：\n" + json.dumps(schema, ensure_ascii=False)
    )
    return instruction + "\n" + (GUIDANCE + "\n" if arm.endswith("guidance") else "") + contract


def prepare(root):
    root.mkdir(parents=True, exist_ok=True)
    if (root / "design.json").exists():
        raise ValueError("Experiment already prepared; preserve frozen inputs.")
    sources = {}
    views, instructions = {}, {}
    for case in ("mytest49", "mytest50"):
        saved = next((Path("runs") / f"{case}_sample2/v5/assessments").glob("assessment-*.json"))
        views[case] = load(saved)["review_input"]
        instructions[case] = DECISION_INSTRUCTION
        sources[case] = str(saved)
    prior = Path("runs/scientific_value_sample2")
    for case, material, prompt in (
        ("original_combined", "sample2_original", "combined"),
        ("facts_examples", "sample2_facts", "examples"),
    ):
        saved = prior / f"inputs/{material}.json"
        views[case] = load(saved)
        instructions[case] = (prior / f"prompts/{prompt}.txt").read_text("utf-8")
        sources[case] = str(saved)
    config = load_config("config/runtime.yaml").model_dump(mode="json")
    config["budget"] = {"max_model_calls": 200, "max_embedding_calls": 0, "max_seconds": 14400}
    for model in config["models"].values():
        model["retries"] = 0
    (root / "probe-config.yaml").write_text(yaml.safe_dump(config, allow_unicode=True), "utf-8")
    prompts = {}
    for case, view in views.items():
        write_json(root / f"inputs/{case}.json", view)
        system = EvaluationSystem.model_validate(view["evaluation"])
        for arm in ARMS:
            prompt = make_prompt(instructions[case], system, arm)
            path = root / f"prompts/{case}/{arm}.txt"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(prompt, "utf-8")
            prompts[f"{case}/{arm}"] = sha(prompt)
    jobs = []
    for repeat in range(1, 7):
        block = [{"case": case, "arm": arm, "repeat": repeat} for case in CASES for arm in ARMS]
        random.Random(2026102200 + repeat).shuffle(block)
        jobs.extend(block)
    write_json(root / "design.json", {
        "sources": sources, "arms": ARMS, "cases": CASES, "jobs": jobs,
        "model": config["models"]["scientific"], "production_hashes": production_hashes(),
        "input_hashes": {case: sha(view) for case, view in views.items()},
        "prompt_hashes": prompts,
        "confirmation": "After 96 calls, compare baseline and the best nonbaseline arm on all "
        "four cases for six fresh repeats (48 calls). Select by lowest submission failures, "
        "then fewest incomplete decisions, then template, schema_guidance, template_guidance. "
        "Do not pool confirmation with screening to claim an unbiased selected-arm estimate.",
    })
    (root / "protocol.md").write_text("""# 评价决定结构对照实验

正式链路保持不变，只重放主审提交评价决定环节。完整申请、已交付专家意见、资料原文、国标及分档全部保留。
mytest49、mytest50使用其保存的完整review_input和当前正式决定提示词；mytest49保留当时输入中的主审稿，属于历史材料回放。
original_combined、facts_examples使用上次失败场景的冻结材料及对应科学任务提示词，不输入失败回复。
因此四个场景均来自sample2，不能推论所有项目。两个压力场景也不冒充当前生产提示词。

四组构成2×2：原Schema、展开模板、Schema＋说明、模板＋说明。展开模板由同一份正式Schema生成，
不填示范分数或默认否决值；带说明的组在字段附近解释用途，并说明整个响应包含综合评价、六项分数、安全否决。
完整结构和类型约束不变；不改模型、thinking、采样、材料或评分标准，不追加纠错调用。

筛选阶段每个场景每组6次，共96次，按三批每批两轮记录。每轮随机交错顺序，共享正式限流器，最多3并发。
随后依据预定规则选择一个方案，与原Schema做48次全新确认（四场景×两组×六次）。确认结果单列。
每次新会话、一次模型调用；不传此前回复、重复编号或目标分数。服务默认采样，不宣称控制了独立随机种子。
失败计入分母，不补抽替代。保留原请求、原响应、完成原因、token、结构错误和已产生的内容。

执行复用正式_call_model、_formal_submission、_deliver_manuscript、accept_authoring以及计分与落盘路径。
适配层仅冻结研究后输入、替换所比较的结构呈现、把后续工作限定为submit_assessment；不运行检索、专家研究或报告。
独立统计：服务/限流失败、未完成响应、JSON语法错误、Schema错误；对结构错误区分完整六项分数与内容缺项。
对成功决定统计六项分数、否决、理由长度；抽取各场景同编号产物精读，检查模板照抄、缺失论证及明显语义变化。
主指标为原样通过正式接口的比例；辅助看完整性和论述。分数高低不是本次成功标准。
样本量有限，给出实际计数与区间；0次失败不代表以后不会失败。不开启thinking，不更换服务，不使用强制修复。
""", "utf-8")
    print(json.dumps({"prepared": str(root), "screening_calls": len(jobs)}), flush=True)


class ReplaySubmission(AssessmentSubmission):
    def __init__(self, runtime, state, view, prompt, described=False, parts=False):
        self.view, self.prompt = view, prompt
        self.described = described
        self.parts = parts
        chief = SimpleNamespace(runtime=runtime, _manifest=lambda _: {
            "review_context": {"evaluation": view["evaluation"], "admission": {}},
        })
        super().__init__(chief, state.session_id)

    def _review_input(self, state):
        return deepcopy(self.view)

    def authoring_input(self, state, operation):
        return [
            {"role": "system", "content": self.prompt},
            {"role": "user", "content": json.dumps(self.view, ensure_ascii=False)},
        ], list(state.evidence)

    def completion_operations(self, state):
        return [] if state.assessment_ref else ["submit_assessment"]

    def response_schema(self, state, operation):
        if self.parts and operation == "submit_assessment":
            return parts_type(self.system).model_json_schema()
        if self.described and operation == "submit_assessment":
            return described_schema(self.system)
        return super().response_schema(state, operation)

    async def accept_authoring(self, state, raw_ref, raw):
        if self.parts and raw["operation"] == "submit_assessment":
            decision = parts_type(self.system).model_validate_json(raw["content"]).model_dump()
            decision["overall_judgment"] = "\n\n".join(
                decision["overall_judgment"][key] for key in PARTS
            )
            # Lossless deterministic projection; no generated text, defaults, or repairs.
            raw = {**raw, "content": json.dumps(decision, ensure_ascii=False)}
        return await super().accept_authoring(state, raw_ref, raw)


class ReplayRuntime(ResearchRuntime):
    async def _call_model(self, state, *args, **kwargs):
        reply = await super()._call_model(state, *args, **kwargs)
        self.replies[state.session_id] = reply
        return reply


def completeness(content, system):
    try:
        value = json.loads(content)
    except (ValueError, TypeError):
        return {"json_valid": False, "complete_scores": False}
    dims = value.get("dimensions", {}) if isinstance(value, dict) else {}
    scores = {
        c.id: dims.get(c.id, {}).get("score")
        for c in system.criteria if isinstance(dims.get(c.id), dict)
    } if isinstance(dims, dict) else {}
    return {
        "json_valid": True,
        "complete_scores": len(scores) == 6 and all(type(v) is int for v in scores.values()),
        "observed_scores": scores,
    }


def confirmation_plan(root):
    rows = [load(p) for p in (root / "outputs/screening").glob("*/*/*/result.json")]
    if len(rows) != 96:
        raise ValueError("Complete all screening calls before selecting confirmation.")
    priority = {"template": 0, "schema_guidance": 1, "template_guidance": 2}
    candidates = []
    for arm in ARMS[1:]:
        subset = [r for r in rows if r["arm"] == arm]
        candidates.append((
            sum(r["status"] != "complete" for r in subset),
            sum(not r.get("complete_scores", False) for r in subset), priority[arm], arm,
        ))
    selected = min(candidates)[-1]
    arms = ["schema", selected]
    probe = root / "capability-full-result.json"
    if probe.exists() and load(probe)["accepted"]:
        arms.append("strict_schema")
        config = yaml.safe_load((root / "probe-config.yaml").read_text("utf-8"))
        config["models"]["scientific_strict"] = {
            **config["models"]["scientific"], "structured_output": "json_schema",
            "schema_supported": True, "capability_verified": False,
            "capability_source": str(probe.resolve()),
        }
        (root / "probe-config-confirmation.yaml").write_text(
            yaml.safe_dump(config, allow_unicode=True), "utf-8"
        )
        for case in CASES:
            (root / f"prompts/{case}/strict_schema.txt").write_text(
                (root / f"prompts/{case}/schema.txt").read_text("utf-8"), "utf-8"
            )
    jobs = []
    for repeat in range(1, 7):
        block = [
            {"case": case, "arm": arm, "repeat": repeat}
            for case in CASES for arm in arms
        ]
        random.Random(2026102300 + repeat).shuffle(block)
        jobs.extend(block)
    plan = {"selected": selected, "selection_counts": candidates, "arms": arms, "jobs": jobs}
    write_json(root / "confirmation-plan.json", plan)
    return plan


def description_plan(root):
    jobs = []
    for case in CASES:
        view = load(root / f"inputs/{case}.json")
        system = EvaluationSystem.model_validate(view["evaluation"])
        original = (root / f"prompts/{case}/strict_schema.txt").read_text("utf-8")
        prompt = original.replace(
            json.dumps(decision_schema(system), ensure_ascii=False),
            json.dumps(described_schema(system), ensure_ascii=False),
        )
        (root / f"prompts/{case}/strict_described.txt").write_text(prompt, "utf-8")
    for repeat in range(1, 4):
        block = [
            {"case": case, "arm": arm, "repeat": repeat}
            for case in CASES for arm in ("strict_schema", "strict_described")
        ]
        random.Random(2026102400 + repeat).shuffle(block)
        jobs.extend(block)
    plan = {"jobs": jobs, "reason": "Full-material pilot and confirmation responses include "
            "background-only overall_judgment. Compare the unchanged production writing "
            "requirement placed at that field in both the displayed and submitted schema. "
            "Both arms use strict JSON Schema; 24 fresh calls, no rewrite of prior outputs."}
    write_json(root / "description-plan.json", plan)
    return plan


def parts_plan(root):
    jobs = []
    for case in CASES:
        view = load(root / f"inputs/{case}.json")
        system = EvaluationSystem.model_validate(view["evaluation"])
        original = (root / f"prompts/{case}/strict_schema.txt").read_text("utf-8")
        instruction = original.split("\n提交的 JSON 结构：\n", 1)[0]
        prompt = instruction + (
            "\n本次 overall_judgment 分为下面三个必填文字字段，同一次调用完成。"
            "三个字段依次构成综合评价的三个自然段，合计约450—700中文字，"
            "分别交付项目与价值、主要支持与限制、整体取舍。"
            "代码只按顺序连接你写出的原文，其余评分和安全否决字段保持不变。\n"
            "提交的 JSON 结构：\n"
        ) + json.dumps(parts_type(system).model_json_schema(), ensure_ascii=False)
        (root / f"prompts/{case}/strict_parts.txt").write_text(prompt, "utf-8")
    for repeat in range(1, 4):
        block = [
            {"case": case, "arm": arm, "repeat": repeat}
            for case in CASES for arm in ("strict_schema", "strict_parts")
        ]
        random.Random(2026102500 + repeat).shuffle(block)
        jobs.extend(block)
    plan = {"jobs": jobs, "reason": "Field descriptions did not consistently preserve the "
            "overall assessment. Test three required content-bearing paragraph fields in one "
            "strict response, joined verbatim by code. Original raw response retained, "
            "experimental schema validated before deterministic projection to production schema. "
            "No filling, rewriting, repair, or extra model round; 24 fresh calls."}
    write_json(root / "parts-plan.json", plan)
    return plan


async def run(root, phase, concurrency):
    design = load(root / "design.json")
    jobs = (design["jobs"] if phase == "screening" else
            confirmation_plan(root)["jobs"] if phase == "confirmation" else
            description_plan(root)["jobs"] if phase == "description" else
            parts_plan(root)["jobs"])
    runtime_path = root / f"runtime-{phase}"
    if runtime_path.exists():
        raise ValueError("Preserve prior executions; this batch cannot be overwritten.")
    config_path = root / "probe-config.yaml"
    if phase != "screening" and (root / "probe-config-confirmation.yaml").exists():
        config_path = root / "probe-config-confirmation.yaml"
    runtime = ReplayRuntime.from_config(config_path, runtime_path)
    if "scientific_strict" in runtime.models:
        runtime.routes["strict_probe"] = "scientific_strict"
    runtime.replies = {}
    gate = asyncio.Semaphore(concurrency)

    async def job(spec):
        async with gate:
            start = time.monotonic()
            folder = root / "outputs" / phase / spec["case"] / spec["arm"] / str(spec["repeat"])
            folder.mkdir(parents=True, exist_ok=False)
            view = load(root / f"inputs/{spec['case']}.json")
            prompt = (root / f"prompts/{spec['case']}/{spec['arm']}.txt").read_text("utf-8")
            state = runtime.load(runtime.create(
                SessionTask(role="chief", objective="依据完整材料形成国标评价决定。",
                            application=view["application"]),
                allowance=SessionAllowance(completion_calls=1, completion_seconds=600,
                                           completion_input_tokens=250000,
                                           completion_output_tokens=16384),
                model_role="strict_probe" if spec["arm"].startswith("strict_") else None,
            ))
            state.evidence = dict.fromkeys(
                dict.fromkeys(re.findall(r"evidence-[0-9a-f]{16}", json.dumps(view))), {}
            )
            submission = ReplaySubmission(
                runtime, state, view, prompt, described=spec["arm"] == "strict_described",
                parts=spec["arm"] == "strict_parts",
            )
            messages = submission.authoring_input(state, "submit_assessment")[0]
            actual = author_messages(messages, state.evidence)
            write_json(folder / "request.json", actual)
            row = {**spec, "phase": phase, "session_id": state.session_id,
                   "request_sha256": sha(actual), "user_sha256": sha(actual[1]),
                   "status": "running"}
            print(json.dumps({"start": f"{phase}/{spec}"}), flush=True)
            try:
                await runtime._formal_submission(
                    state, runtime.models[state.model_alias], operation="submit_assessment",
                    submission=submission,
                )
                artifact = load(runtime.store._path("assessments", state.assessment_ref))
                assessment = artifact["assessment"]
                write_json(folder / "assessment.json", assessment)
                row.update(
                    status="complete", total=assessment["total_score"],
                    scores={k: v["score"] for k, v in assessment["dimensions"].items()},
                    veto=assessment["safety_veto"]["applies"],
                    reason_chars=sum(len(v["reason"]) for v in assessment["dimensions"].values()),
                    overall_chars=len(assessment["overall_judgment"]),
                )
                (folder / "review.md").write_text(
                    assessment["overall_judgment"] + "\n\n" + "\n\n".join(
                        f"## {c.label}：{assessment['dimensions'][c.id]['score']}/{c.max_score}\n\n"
                        + assessment["dimensions"][c.id]["reason"]
                        for c in submission.system.criteria
                    ) + "\n\n安全否决："
                    + json.dumps(assessment["safety_veto"], ensure_ascii=False),
                    "utf-8",
                )
            except Exception as exc:
                row.update(status="failed", error=str(exc), error_type=type(exc).__name__)
                if exc.__cause__ is not None:
                    row["cause"] = repr(exc.__cause__)
            finally:
                reply = runtime.replies.pop(state.session_id, None)
                if reply:
                    write_json(folder / "response.json", {
                        "message": reply.message, "finish_reason": reply.finish_reason,
                        "input_tokens": reply.prompt_tokens,
                        "output_tokens": reply.completion_tokens,
                    })
                    row.update(
                        finish_reason=reply.finish_reason, input_tokens=reply.prompt_tokens,
                        output_tokens=reply.completion_tokens,
                        **completeness(reply.message.get("content"), submission.system),
                    )
                row["seconds"] = round(time.monotonic() - start, 2)
                runtime.budget.finish(state.session_id)
                write_json(folder / "result.json", row)
                print(json.dumps(row, ensure_ascii=False), flush=True)

    try:
        for repeat in sorted({s["repeat"] for s in jobs}):
            await asyncio.gather(*(job(s) for s in jobs if s["repeat"] == repeat))
            summarize(root)
    finally:
        write_json(root / f"execution-{phase}.json", {
            "usage": runtime.budget.usage(), "production_unchanged":
            production_hashes() == design["production_hashes"], "full_review_runs": 0,
        })
        write_json(root / f"scheduler-{phase}.json", runtime.scheduler.records)
        await runtime.close()
        summarize(root)


def wilson(fail, n):
    z = 1.96
    p = fail / n
    mid = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [round(100 * (mid - half), 2), round(100 * (mid + half), 2)]


def summarize(root):
    rows = [load(p) for p in sorted((root / "outputs").glob("*/*/*/*/result.json"))]
    groups = []
    for phase in ("screening", "confirmation", "description", "parts"):
        for case in ("ALL", *CASES):
            for arm in ALL_ARMS:
                subset = [r for r in rows if r["phase"] == phase and r["arm"] == arm
                          and (case == "ALL" or r["case"] == case)]
                if not subset:
                    continue
                valid = [r for r in subset if r["status"] == "complete"]
                fails = len(subset) - len(valid)
                totals = [r["total"] for r in valid]
                groups.append({
                    "phase": phase, "case": case, "arm": arm, "n": len(subset),
                    "failed": fails, "failure_percent": round(fails / len(subset) * 100, 2),
                    "wilson95_percent": wilson(fails, len(subset)),
                    "missing_scores": sum(not r.get("complete_scores", False) for r in subset),
                    "totals": totals, "mean_total": statistics.mean(totals) if totals else None,
                    "mean_reason_chars": statistics.mean(r["reason_chars"] for r in valid)
                    if valid else None, "veto_count": sum(r["veto"] for r in valid),
                })
    write_json(root / "summary.json", {"rows": rows, "groups": groups})
    lines = ["# 结构通过率", "", "筛选与独立确认分别列出；失败不补抽。", "",
             "|阶段|场景|方案|失败/总调用|失败率|Wilson 95%区间|缺完整六项分数|有效总分|",
             "|---|---|---|---|---|---|---|---|"]
    for g in groups:
        lines.append(f"|{g['phase']}|{g['case']}|{g['arm']}|{g['failed']}/{g['n']}|"
                     f"{g['failure_percent']}%|{g['wilson95_percent']}|{g['missing_scores']}|"
                     f"{g['totals']}|")
    (root / "counts.md").write_text("\n".join(lines) + "\n", "utf-8")
    print(json.dumps({"finished": len(rows), "failed": sum(r['status'] != 'complete'
                                                             for r in rows)}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "run", "summarize"))
    parser.add_argument("--output", default="runs/decision_structure_sample2")
    parser.add_argument("--phase", choices=("screening", "confirmation", "description", "parts"),
                        default="screening")
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    root = Path(args.output).resolve()
    if args.action == "prepare":
        prepare(root)
    elif args.action == "summarize":
        summarize(root)
    elif args.live:
        asyncio.run(run(root, args.phase, args.concurrency))
    else:
        parser.error("run requires --live")
