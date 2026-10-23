"""Frozen expert/peer/chief experiments; never changes production or runs a full review.

Prepare once, then run the matrix and selected chief handoffs in separate invocations.
All requests, responses, source identities and experiment conditions are retained.
"""

import argparse
import asyncio
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import yaml

from spacebio_review.evidence.reading_render import render_originals
from spacebio_review.persistence import write_json
from spacebio_review.prose import cited_ids, compile_authored_body, prose_inputs
from spacebio_review.v5.assessment import AssessmentSubmission
from spacebio_review.v5.assessment_authoring import decision_type
from spacebio_review.v5.budget import SessionAllowance
from spacebio_review.v5.citation_writing import author_messages
from spacebio_review.v5.config import load_config
from spacebio_review.v5.records import ResearchNote, SessionTask
from spacebio_review.v5.research_context import expand_captured_units
from spacebio_review.v5.scientific_roles import EXPERT_OPINION, role_instruction
from spacebio_review.v5.session import ResearchRuntime

OBJECTIVE = (
    "评价申请的培养、成像和样品返回安排能否支持预期实验；"
    "现有平台与研究先例提供哪些支持，哪些关键衔接仍需落实？"
)
PUBLISH = (
    "本轮申请和研究材料已经提供，不调用工具或另行检索。"
    "结合这些材料形成你自己的当前工程评价，直接交付完整专业意见正文，"
    "使用给定的引用标记。\n" + EXPERT_OPINION
)
INDEPENDENT = (
    "已有稿件是供比较的专业解释。先根据申请和资料形成对本轮问题的判断，"
    "再取舍其中的分析；采用、修正或保留分歧均可，依据是材料与推理。"
    "保留有用的发现和论证，不以赞同或反对已有意见为任务目标。"
)
ANALOGY = (
    "比较已有解释与材料时，可参考这个异领域例子：文献说“医院拟建PET-MRI融合平台”，"
    "另一段介绍了现有MRI临床检查。若已有分析据未来联合平台断言MRI技术尚未应用，"
    "应区分单项方法和集成系统的对象及阶段，再评价；申请若使用MRI，还需结合"
    "该项目具体设备与配套证据。沿同样方式理解本次材料的对象、阶段和条件，"
    "现有意见中有依据的专业论证仍可直接采用。"
)
VARIANTS = (
    "fresh", "self", "full", "prompt", "questions", "questions_sources",
    "paired", "source_notes", "open_questions", "open_questions_sources", "example",
)
SOURCE_QUESTIONS = {
    "review": (
        "微生理系统与多组学",
        "这份材料中显微成像方法、应用示例与平台构想分别涉及哪些对象和实施阶段？"
        "这些内容对本申请的成像安排提供哪些具体支持，哪些关系仍需明确？",
    ),
    "kcpp": (
        "Development of a kidney",
        "这项研究怎样培养、处理和返回样品，实际取得了哪些结果？"
        "关键数值的统计对象和实验条件是什么，对本申请返回后分析有何参照价值？",
    ),
}
OWN_FIXTURES = [
    "runs/focused_reading_sample2/chains/direct-1-expert-note.json",
    "runs/focused_reading_sample2/chains/direct-2-expert-note.json",
    "runs/focused_reading_sample2/chains/focused-1-expert-note.json",
]
PACKET_SCHEMA = {
    "type": "object",
    "properties": {
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "paragraph_ids": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["question", "paragraph_ids"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["issues"],
    "additionalProperties": False,
}


def load(path):
    return json.loads(Path(path).read_text("utf-8"))


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def production_hashes():
    paths = [
        *Path("src").rglob("*.py"), *Path("config").rglob("*.yaml"),
        *Path("skills").rglob("*.md"),
    ]
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


def source_rows(evidence):
    return {
        name: {k: v for k, v in evidence.items()
               if title.casefold() in v.get("title", "").casefold()}
        for name, (title, _) in SOURCE_QUESTIONS.items()
    }


def originals(evidence):
    return render_originals({
        k: {**v, "citation": f"[{k}](evidence:{k})"} for k, v in evidence.items()
    })


def text_leaves(value):
    if isinstance(value, dict):
        for child in value.values():
            yield from text_leaves(child)
    elif isinstance(value, list):
        for child in value:
            yield from text_leaves(child)
    elif isinstance(value, str):
        yield value


def prepare(output, run, config):
    output.mkdir(parents=True, exist_ok=False)
    saved = run / "v5"
    manifest = load(next((saved / "chiefs").glob("*.json")))
    chief = load(saved / "sessions" / f"{manifest['chief_id']}.json")
    sessions, artifacts = [], []
    for tid in manifest["topic_ids"]:
        inquiry = load(saved / "inquiries" / f"{tid}.json")
        state = load(saved / "sessions" / f"{inquiry['current_owner_session_id']}.json")
        sessions.append(state)
        artifacts.append(load(saved / "notes" / f"{state['note_ref']}.json"))
    peer = prose_inputs(artifacts[2]["note"])["body"]
    paragraphs = [
        {"paragraph_id": i + 1, "text": p}
        for i, p in enumerate(peer.split("\n\n")) if p.strip()
    ]
    own = [prose_inputs(load(p)["note"])["body"] for p in OWN_FIXTURES]
    fixture = {
        "manifest": manifest, "chief": chief, "sessions": sessions,
        "artifacts": artifacts, "peer": peer, "paragraphs": paragraphs, "own": own,
    }
    cfg = load_config(config).model_dump(mode="json")
    cfg["budget"] = {
        "max_model_calls": 96, "max_embedding_calls": 0, "max_seconds": 14400,
    }
    for model in cfg["models"].values():
        model["retries"] = 0
    (output / "probe-config.yaml").write_text(
        yaml.safe_dump(cfg, allow_unicode=True), "utf-8",
    )
    write_json(output / "fixture.json", fixture)
    write_json(output / "plan.json", {
        "source_run": str(run), "fixture_sha256": digest(fixture),
        "own_fixtures": OWN_FIXTURES, "variants": VARIANTS,
        "evidence_positions": len(sessions[2]["evidence"]),
        "source_groups": {k: len(v) for k, v in source_rows(sessions[2]["evidence"]).items()},
        "production_hashes": production_hashes(),
        "controls": [
            "All expert groups receive the same full application and captured originals.",
            "All old drafts are frozen and use permanent citation identities.",
            "No hand-written scientific correction or gold answer enters model inputs.",
            "Questions are produced by a real model from peer text; code attaches originals.",
            "Source notes are independent per source, with no peer or own draft.",
            "Other four expert opinions stay fixed for chief tests.",
            "No search, embedding, automatic retry, complete review or report generation.",
        ],
        "manual_rubric": {
            "device": "不把规划共焦拉曼当成LSCM/SDCM尚未部署的证据；允许具体配置未知。",
            "sample_metric": "97%指CRS-22中70/72个PT-MPS小管可用于RNAseq，不是提取率。",
            "preservation": "申请未交代保存条件，不能据此断言无固定、无低温。",
            "causality": "跨任务数值变化不能单独证明固定剂的因果效果。",
            "new_errors": "记录新增对象、阶段、地点、设备和数值错误。",
            "positive_value": "保留已有培养/自动化/镜像平台支持及可获得的科学认识。",
            "handoff": "记录专家引用和主审实际收到的原文，区分保留与理解。",
        },
    })
    print(json.dumps({"prepared": str(output), "evidence": len(sessions[2]["evidence"])}))


class Probe:
    def __init__(self, output):
        self.output = output
        self.fixture = load(output / "fixture.json")
        self.runtime = ResearchRuntime.from_config(output / "probe-config.yaml", output)
        self.evidence = self.fixture["sessions"][2]["evidence"]
        self.sources = source_rows(self.evidence)
        self.results = load(output / "verification.json") if (
            output / "verification.json"
        ).exists() else {"calls": [], "full_review_runs": 0, "production_changes": False}
        self.lock = asyncio.Semaphore(2)
        self.base = None

    def flush(self):
        self.results["production_unchanged"] = (
            production_hashes() == load(self.output / "plan.json")["production_hashes"]
        )
        write_json(self.output / "verification.json", self.results)

    def state(self, role="researcher"):
        state = self.runtime.load(self.runtime.create(SessionTask(
            role=role, objective=OBJECTIVE,
            application=deepcopy(self.fixture["chief"]["task"]["application"]),
            skills=self.fixture["sessions"][2]["task"]["skills"] if role == "researcher" else [],
        ), allowance=SessionAllowance(completion_calls=0)))
        self.runtime._capture(state, self.evidence)
        return state

    def expert_messages(self, state):
        if self.base is None:
            view = self.runtime._context(state)
            view.pop("resources", None)
            self.base = [
                {"role": "system", "content": role_instruction("researcher")},
                {"role": "user", "content": json.dumps(view, ensure_ascii=False)},
            ]
            write_json(self.output / "base-input.json", self.base)
        return deepcopy(self.base)

    async def call(self, state, name, messages, schema=None):
        path = self.output / name
        if path.with_suffix(".md").exists():
            raise ValueError(f"Do not overwrite completed experiment: {name}")
        actual = author_messages(messages, state.evidence)
        write_json(self.output / f"{name}-input.json", actual)
        (self.output / f"{name}-input.txt").write_text(
            "\n\n".join(m["content"] for m in actual), "utf-8",
        )
        async with self.lock:
            print(json.dumps({"start": name}), flush=True)
            try:
                reply = await self.runtime._call_model(
                    state, self.runtime.models[state.model_alias], messages, [],
                    purpose="completion", stage=name, response_schema=schema,
                )
            except Exception as exc:
                self.results["calls"].append({"name": name, "error": str(exc)})
                self.flush()
                raise
            text = reply.message.get("content") or ""
            write_json(self.output / f"{name}-response.json", reply.message)
            path.with_suffix(".md").write_text(text, "utf-8")
            row = {
                "name": name, "finish_reason": reply.finish_reason,
                "input_tokens": reply.prompt_tokens, "output_tokens": reply.completion_tokens,
                "input_sha256": digest(actual),
            }
            self.results["calls"].append(row)
            self.flush()
            print(json.dumps(row), flush=True)
            return text

    def packet(self, repeat, open_question=False):
        name = "open-packet" if open_question else "packet"
        return load(self.output / f"{name}-{repeat}.json")

    def issue_payload(self, repeat, with_originals, open_question=False):
        paragraphs = {p["paragraph_id"]: p["text"] for p in self.fixture["paragraphs"]}
        items = []
        for issue in self.packet(repeat, open_question)["issues"]:
            selected = [paragraphs[index] for index in issue["paragraph_ids"]]
            row = {"question": issue["question"]}
            if with_originals:
                refs = cited_ids(compile_authored_body("\n\n".join(selected)))
                row["originals"] = originals(expand_captured_units(self.evidence, refs))
            items.append(row)
        return {"peer_research_questions": items}

    async def open_packets(self, repeats):
        """Adaptive prompt experiment; the example is not a correction of the fixture."""
        for repeat in repeats:
            state = self.state()
            messages = [
                {"role": "system", "content": (
                    "你负责把专题研究交给下一位专家。选择同伴稿中值得继续理解的3—5个"
                    "科学问题，用中性提问传递研究方向，保留有价值的发现与比较线索。"
                    "每题给出对应的原稿段落编号，程序会据其引用提供原文。"
                    "题目只提出需要理解的对象和关系，能力状态、因果解释及是否缺少条件"
                    "均由接收者阅读原文判断。"
                    "提问示例（来自另一个领域）：旧稿说“医院拟建PET-MRI融合平台，"
                    "所以现阶段MRI检查不可开展”。应问“材料怎样描述MRI检查和PET-MRI"
                    "融合平台，各自的实施阶段与用途是什么？”，不问“缺少MRI时如何替代”。"
                    "按这个方式为本次稿件提出问题，不评价稿件对错，也不替申请人改方案。"
                    "输出JSON：issues数组，每项question、paragraph_ids。"
                )},
                {"role": "user", "content": json.dumps({
                    "responsibility": OBJECTIVE, "peer_paragraphs": self.fixture["paragraphs"],
                }, ensure_ascii=False)},
            ]
            name = f"open-packet-{repeat}"
            raw = await self.call(state, name, messages, PACKET_SCHEMA)
            packet = json.loads(raw)
            ids = {p["paragraph_id"] for p in self.fixture["paragraphs"]}
            for issue in packet["issues"]:
                if not issue["paragraph_ids"] or not set(issue["paragraph_ids"]) <= ids:
                    raise ValueError("Packet selected a nonexistent peer paragraph")
            write_json(self.output / f"{name}.json", packet)
            self.runtime.budget.finish(state.session_id)

    def paired_peer(self):
        return {"peer_opinion_with_originals": [
            {
                "opinion": p["text"],
                "originals": originals(expand_captured_units(
                    self.evidence, cited_ids(compile_authored_body(p["text"])),
                )),
            }
            for p in self.fixture["paragraphs"]
        ]}

    async def artifacts(self, repeats):
        for repeat in repeats:
            state = self.state()
            messages = [
                {"role": "system", "content": (
                    "你是负责传递研究问题的专题专家。将同伴稿中值得进一步理解的内容，"
                    "组织成3—5个具体、开放的科学问题。保留研究关注点，不把稿件结论改写为"
                    "题目的既定前提；不回答问题，不判断同伴对错。每题选择提供研究线索的"
                    "原稿段落编号，原文材料稍后由程序按段落引用随题交付。"
                    "输出JSON：issues数组，每项question、paragraph_ids。"
                )},
                {"role": "user", "content": json.dumps({
                    "responsibility": OBJECTIVE, "peer_paragraphs": self.fixture["paragraphs"],
                }, ensure_ascii=False)},
            ]
            raw = await self.call(state, f"packet-{repeat}", messages, PACKET_SCHEMA)
            packet = json.loads(raw)
            # These are routing identifiers, not a semantic correctness judgement.
            ids = {p["paragraph_id"] for p in self.fixture["paragraphs"]}
            for issue in packet["issues"]:
                if not issue["paragraph_ids"] or not set(issue["paragraph_ids"]) <= ids:
                    raise ValueError("Packet selected a nonexistent peer paragraph")
            write_json(self.output / f"packet-{repeat}.json", packet)
            self.runtime.budget.finish(state.session_id)
            for source, (_, question) in SOURCE_QUESTIONS.items():
                state = self.state()
                view = self.runtime._context(state, originals=self.sources[source])
                view.pop("resources", None)
                messages = [
                    {"role": "system", "content": role_instruction("researcher")},
                    {"role": "user", "content": json.dumps(view, ensure_ascii=False)},
                    {"role": "user", "content": (
                        question + " 本轮只交付这份资料与问题相关的认识，约600—1000字。"
                        "说明材料实际提供的对象、阶段、条件和结果，并联系申请。"
                        "使用给定引用，不调用工具，不写整篇评审意见。"
                    )},
                ]
                raw = await self.call(state, f"source-{source}-{repeat}", messages)
                ref = await self.save_expert(state, f"source-{source}-{repeat}", raw)
                write_json(self.output / f"source-{source}-{repeat}-ref.json", ref)
                self.runtime.budget.finish(state.session_id)

    async def save_expert(self, state, name, text):
        raw = self.runtime.store.save_author_text(
            state, text, "submit_research", absorbed_through=0, title="工程可实现性评价",
        )
        await self.runtime._deliver_manuscript(state, raw, None)
        artifact = self.runtime.store.note(state.note_ref)
        write_json(self.output / f"{name}-note.json", artifact)
        return {"inquiry_id": state.inquiry_id, "note_ref": state.note_ref}

    async def expert_case(self, repeat, variant):
        state = self.state()
        messages = self.expert_messages(state)
        if variant != "fresh":
            messages.append({
                "role": "assistant", "content": self.fixture["own"][repeat - 1],
            })
        if variant in {"full", "prompt", "source_notes", "example"}:
            messages.append({
                "role": "user", "content": "同专题专家意见：\n\n" + self.fixture["peer"],
            })
        if variant == "prompt":
            messages.append({"role": "user", "content": INDEPENDENT})
        if variant == "example":
            messages.append({"role": "user", "content": ANALOGY})
        if variant in {"questions", "questions_sources"}:
            messages.append({"role": "user", "content": json.dumps(
                self.issue_payload(repeat, variant == "questions_sources"), ensure_ascii=False,
            )})
        if variant in {"open_questions", "open_questions_sources"}:
            messages.append({"role": "user", "content": json.dumps(
                self.issue_payload(repeat, variant == "open_questions_sources", True),
                ensure_ascii=False,
            )})
        if variant == "paired":
            messages.append({
                "role": "user", "content": json.dumps(self.paired_peer(), ensure_ascii=False),
            })
        if variant == "source_notes":
            notes = [
                prose_inputs(load(self.output / f"source-{name}-{repeat}-note.json")["note"])
                for name in SOURCE_QUESTIONS
            ]
            messages.append({"role": "user", "content": json.dumps({
                "independent_source_readings": notes,
            }, ensure_ascii=False)})
        messages.append({"role": "user", "content": PUBLISH})
        name = f"expert-{variant}-{repeat}"
        text = await self.call(state, name, messages)
        await self.save_expert(state, name, text)
        self.runtime.budget.finish(state.session_id)

    async def matrix(self, repeats, variants):
        for repeat in repeats:
            # Alternate order; limit requests to two at a time through call().
            order = list(variants)
            shift = (repeat - 1) % len(order)
            order = order[shift:] + order[:shift]
            await asyncio.gather(*(self.expert_case(repeat, name) for name in order))

    async def chief_case(self, expert_name, mode):
        refs = {}
        artifacts = deepcopy(self.fixture["artifacts"])
        artifacts[2] = load(self.output / f"{expert_name}-note.json")
        for artifact in artifacts:
            state = self.state()
            self.runtime._capture(state, artifact["evidence"])
            ref = self.runtime.store.save_note(
                state, ResearchNote.model_validate(artifact["note"]), absorbed_through=0,
            )
            refs[state.inquiry_id] = [ref]
            self.runtime.budget.finish(state.session_id)
        chief = SimpleNamespace(
            runtime=self.runtime,
            topics=SimpleNamespace(contribution_refs=lambda tid: refs[tid]),
            _manifest=lambda _: {**self.fixture["manifest"], "topic_ids": list(refs)},
        )
        state = self.state("chief")
        self.runtime._capture(state, self.fixture["chief"]["evidence"])
        submission = AssessmentSubmission(chief, state.session_id)
        messages, readings = submission.authoring_input(state, "submit_assessment")
        if mode == "bundle":
            # The same source set was available to the expert; no source selection by gold answer.
            bundle = {
                "expert_opinion": prose_inputs(artifacts[2]["note"]),
                "consulted_originals": originals({
                    k: v for rows in self.sources.values() for k, v in rows.items()
                }),
            }
            view = json.loads(messages[1]["content"])
            view["expert_opinions"][2] = bundle
            messages[1]["content"] = json.dumps(view, ensure_ascii=False)
            readings = sorted(set(readings) | {
                k for rows in self.sources.values() for k in rows
            })
        name = f"chief-{expert_name.removeprefix('expert-')}-{mode}"
        shown_text = set(text_leaves(json.loads(messages[1]["content"])))
        shown_sources = {
            source: sum(bool(v.get("quote")) and v["quote"] in shown_text for v in rows.values())
            for source, rows in self.sources.items()
        }
        raw = await self.call(
            state, name, messages, submission.response_schema(state, "submit_assessment"),
        )
        handoff = {
            "mode": mode, "expert": expert_name,
            "expert_citations": len(artifacts[2]["evidence"]), "input_readings": readings,
            "original_positions_actually_shown": shown_sources,
            "consulted_sources": {k: list(v) for k, v in self.sources.items()},
        }
        try:
            decision = decision_type(submission.system).model_validate_json(raw).model_dump()
        except ValueError as exc:
            handoff.update(schema_valid=False, delivery_error=str(exc))
            self.results.setdefault("delivery_errors", []).append({
                "name": name, "error": str(exc),
            })
            write_json(self.output / f"{name}-handoff.json", handoff)
            self.flush()
            self.runtime.budget.finish(state.session_id)
            print(json.dumps({"name": name, "schema_valid": False}), flush=True)
            return
        write_json(self.output / f"{name}-decision.json", decision)
        body = decision["overall_judgment"] + "\n\n" + "\n\n".join(
            "## " + criterion.label + "\n\n" + decision["dimensions"][criterion.id]["reason"]
            for criterion in submission.system.criteria
        )
        (self.output / f"{name}.md").write_text(body, "utf-8")
        handoff.update(
            schema_valid=True,
            note="Schema validated; no report writing or production assessment persisted.",
        )
        write_json(self.output / f"{name}-handoff.json", handoff)
        self.runtime.budget.finish(state.session_id)

    async def chiefs(self, experts, modes):
        results = await asyncio.gather(*(
            self.chief_case(expert, mode) for expert in experts for mode in modes
        ), return_exceptions=True)
        failures = [str(result) for result in results if isinstance(result, BaseException)]
        if failures:
            raise RuntimeError(f"Chief experiment failures (other cases completed): {failures}")


async def main(args):
    output = Path(args.output).resolve()
    if args.mode == "prepare":
        prepare(output, Path(args.run), args.config)
        return
    if not args.live:
        raise ValueError("Real service calls require --live")
    probe = Probe(output)
    try:
        if args.mode == "artifacts":
            await probe.artifacts(args.repeats)
        elif args.mode == "open-packets":
            await probe.open_packets(args.repeats)
        elif args.mode == "matrix":
            await probe.matrix(args.repeats, args.variants)
        else:
            await probe.chiefs(args.experts, args.handoffs)
    finally:
        probe.flush()
        await probe.runtime.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument(
        "--mode", required=True,
        choices=["prepare", "artifacts", "open-packets", "matrix", "chief"],
    )
    parser.add_argument("--output", default="runs/selective_transfer_sample2")
    parser.add_argument("--run", default="runs/mytest49_sample2")
    parser.add_argument("--config", default="config/runtime.yaml")
    parser.add_argument("--repeats", type=int, nargs="+", default=[1, 2, 3])
    parser.add_argument("--variants", choices=VARIANTS, nargs="+", default=list(VARIANTS[:8]))
    parser.add_argument("--experts", nargs="+", default=["expert-full-1"])
    parser.add_argument("--handoffs", choices=["baseline", "bundle"], nargs="+",
                        default=["baseline", "bundle"])
    asyncio.run(main(parser.parse_args()))
