"""Local real-model assembly of saved mytest40 text; never starts research."""

import argparse
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from spacebio_review.authored_markdown import Node, numbered_blocks, plain_text
from spacebio_review.models.provider import DashScopeModel
from spacebio_review.persistence import write_json
from spacebio_review.prose import cited_ids, compile_authored_body
from spacebio_review.review.evaluation import EvaluationSystem
from spacebio_review.v5.assembly import ManuscriptAssembly
from spacebio_review.v5.assessment_authoring import compile_fields, decision_schema
from spacebio_review.v5.config import load_config
from spacebio_review.v5.records import SessionStore


def visible(body):
    return plain_text([Node.model_validate(node) for node in body["blocks"]])


async def synthesis(run, output):
    """Fresh local execution of just the two report exits, from a frozen decision."""
    from spacebio_review.v5.assessment import AssessmentSubmission
    from spacebio_review.v5.records import SessionTask
    from spacebio_review.v5.session import ResearchRuntime

    runtime = ResearchRuntime.from_config("config/runtime.yaml", output)
    try:
        root = run / "v5"
        artifact = json.loads(
            (
                root
                / "assessments"
                / (
                    "assessment-cdefc0cf17d3d3eeef24e778adb84da70a90d29b2a70e4cbe25f5ba9ef0935c3.json"
                )
            ).read_text("utf-8")
        )
        old = json.loads((root / "sessions" / (artifact["chief_id"] + ".json")).read_text("utf-8"))
        state = runtime.load(
            runtime.create(
                SessionTask(
                    role="chief", objective="依据已有评价撰写综合与摘要", application="局部出口测试"
                ),
                readings=old["evidence"],
            )
        )
        state.assessment_ref, state.research_finished = "assessment-fixture", True
        artifact["chief_id"] = state.session_id
        write_json(runtime.store._path("assessments", state.assessment_ref), artifact)
        runtime.store.save(state)
        submission = AssessmentSubmission.__new__(AssessmentSubmission)
        submission.runtime, submission.store, submission.chief_id = (
            runtime,
            runtime.store,
            state.session_id,
        )
        submission.system = EvaluationSystem.model_validate(
            artifact["assessment"]["evaluation_system"]
        )
        for operation in ("write_overall", "write_summary"):
            await runtime._formal_submission(
                state, runtime.models[state.model_alias], operation=operation, submission=submission
            )
            print(json.dumps({"completed": operation}), flush=True)
        result = submission.result()
        assert result["scientific_status"] == "completed"
        assert result["assessment"]["total_score"] == artifact["assessment"]["total_score"]
        assert result["assessment"]["dimensions"] == artifact["assessment"]["dimensions"]
        assert visible(result["assessment"]["overall"])
        assert visible(result["assessment"]["summary"])
        print(
            json.dumps(
                {
                    "passed": "synthesis",
                    "report_ref": state.report_ref,
                    "usage": runtime.budget.usage(),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    finally:
        await runtime.close()


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, default=Path("runs/mytest40_sample2"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--case", choices=["citations", "decision", "synthesis"], default="citations"
    )
    args = parser.parse_args()
    if args.case == "synthesis":
        await synthesis(args.run, args.output)
        return
    config = load_config("config/runtime.yaml")
    model = DashScopeModel(config.models[config.auxiliary["assembly"]], env_file=".env")
    calls = []

    async def call(state, stage, instruction, value, schema, *, purpose):
        reply = await model.complete(
            [
                {"role": "system", "content": instruction},
                {"role": "user", "content": json.dumps(value, ensure_ascii=False)},
            ],
            response_schema=schema,
        )
        assert reply.finish_reason == "stop", reply.finish_reason
        calls.append(
            {"stage": stage, "input": reply.prompt_tokens, "output": reply.completion_tokens}
        )
        print(json.dumps(calls[-1]), flush=True)
        return json.loads(reply.message["content"])

    store = SessionStore(args.output)
    assembly = ManuscriptAssembly(store, call)
    root = args.run / "v5"

    def load(ref, folder):
        return json.loads((root / folder / (ref + ".json")).read_text("utf-8"))

    def fixture(ref, content=None):
        old = load(ref, "author-texts")
        state = load(old["session_id"], "sessions")
        text = old["content"] if content is None else content
        raw = {
            "author_text_ref": ref,
            "session_id": old["session_id"],
            "content": text,
            "paragraphs": numbered_blocks(text),
            "readings": list(old["aliases"].values()),
        }
        write_json(store._path("author-texts", ref), raw)
        return SimpleNamespace(evidence=state["evidence"]), raw

    try:
        if args.case == "citations":
            nasa = "author-2234d7424d9e58fea2a3dfdae912811dac146d468be58893664b792658a060a9"
            old = load(nasa, "author-texts")
            text = "\n\n".join(
                block["text"] for block in old["paragraphs"] if block["id"] in {"p7", "p12"}
            )
            state, raw = fixture(nasa, text)
            body, _ = await assembly.assemble(state, raw)
            assert visible(body) == visible(compile_authored_body(raw["content"]))
            refs = set(cited_ids(body))
            assert "evidence-e16b9da196bf6f1c" in refs, "NASA metal-fume source not selected"
            assert refs & {"evidence-fefa75586711fab6", "evidence-fc075cf73b4a5220"}
            platform = "author-4d4c9bc0add898f6bc46fe3acfea6c01703bd2d90bd3cf5d0d9957bb24e6fe56"
            state, raw = fixture(platform)
            body, _ = await assembly.assemble(state, raw)
            assert visible(body) == visible(compile_authored_body(raw["content"]))
            assert "evidence-17d79a6bdd43498c" in set(cited_ids(body))
        else:
            artifact = load(
                "assessment-cdefc0cf17d3d3eeef24e778adb84da70a90d29b2a70e4cbe25f5ba9ef0935c3",
                "assessments",
            )
            ref = artifact["last_author_text_ref"]
            values = json.loads(load(ref, "author-texts")["content"])
            sections = ["## 项目理解\n\n" + values["project"], "## 审议范围\n\n" + values["scope"]]
            for name, value in values["dimensions"].items():
                sections.append(f"## {name} score: {value['score']}\n\n{value['body']}")
            sections.append(
                "## safety_veto applies: "
                + str(values["safety_veto"]["applies"]).lower()
                + "\n\n"
                + values["safety_veto"]["reason"]
            )
            sections.append("## 尚未确定事项")
            for item in values["unknowns"]:
                sections.append("### " + item["title"] + "\n\n" + item["text"])
            state, raw = fixture(ref, "\n\n".join(sections))
            system = EvaluationSystem.model_validate(artifact["assessment"]["evaluation_system"])
            schema = decision_schema(system, raw["paragraphs"])
            body, decision = await assembly.assemble(state, raw, decision_schema=schema)
            compiled = compile_fields(decision, body)
            assert {key: item["score"] for key, item in compiled["dimensions"].items()} == {
                key: item["score"] for key, item in values["dimensions"].items()
            }
            assert compiled["safety_veto"]["applies"] == values["safety_veto"]["applies"]
            assert len(compiled["unknowns"]) == len(values["unknowns"])
            for name, value in values["dimensions"].items():
                assert visible(compiled["dimensions"][name]["body"]) == visible(
                    compile_authored_body(value["body"])
                )
            for field in ("project", "scope"):
                assert visible(compiled[field]) == visible(compile_authored_body(values[field]))
            for before, after in zip(values["unknowns"], compiled["unknowns"], strict=True):
                assert before["title"] == after["title"]
                assert visible(after["text"]) == visible(compile_authored_body(before["text"]))
            assert visible(compiled["safety_veto"]["reason"]) == visible(
                compile_authored_body(values["safety_veto"]["reason"])
            )
        print(json.dumps({"passed": args.case, "calls": calls}, ensure_ascii=False), flush=True)
    finally:
        await model.close()


if __name__ == "__main__":
    asyncio.run(main())
