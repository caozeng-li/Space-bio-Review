"""Change 47 local acceptance. This entry point never runs a complete review."""

import argparse
import asyncio
import hashlib
import json
import re
from collections import Counter
from copy import deepcopy
from html.parser import HTMLParser
from pathlib import Path

from markdown_it import MarkdownIt

from spacebio_review.agents.context import TokenEstimator
from spacebio_review.agents.contracts import example_for
from spacebio_review.agents.diagnostics import digest
from spacebio_review.agents.skills import SkillLibrary
from spacebio_review.models.config import load_config
from spacebio_review.models.provider import ModelError
from spacebio_review.review.project import REVIEW_ASPECTS, load_preparation_config
from spacebio_review.v4.assessed_delivery import deliver
from spacebio_review.v4.assessment import assessment_contract, assessment_request, synthesize
from spacebio_review.v4.assessment_input import (
    ReviewArchive,
    build_evaluation_context,
    freeze_review,
)
from spacebio_review.v4.budget import Budget
from spacebio_review.v4.config import ReportingConfig, V4Config
from spacebio_review.v4.manuscript_call import ManuscriptCall, ManuscriptConfig
from spacebio_review.v4.publication_compile import compile_publication
from spacebio_review.v4.publication_document import walk_nodes
from spacebio_review.v4.publication_render import Renderer
from spacebio_review.v4.records import Records, write_json
from spacebio_review.v4.report_authoring import PROMPT as AUTHOR_PROMPT
from spacebio_review.v4.report_authoring import (
    author_contract,
    author_materials,
    sized_writing_plan,
)

A_CASES = ("A24-1", "A24-2", "A24-3", "A23-1")
DELIVERY_CASES = {"A24-1", "A23-1"}
LIMITS = {
    "A": {"requests": 10, "input": 350000, "output": 80000},
    "B": {"requests": 40, "input": 480000, "output": 160000},
    "C": {"requests": 4, "input": 120000, "output": 24000},
    "D": {"requests": 5, "input": 180000, "output": 40000},
}
TOTAL_LIMITS = {key: sum(group[key] for group in LIMITS.values()) for key in LIMITS["A"]}
EMBEDDING_LIMITS = {"requests": 40, "input": 200000}


def read(path):
    return json.loads(Path(path).read_text("utf-8"))


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def totals(rows):
    """Unknown provider usage stays unknown; reservations still protect the budget."""
    known = {
        key: all(row.get("usage_known", {}).get(key, row["usage"] == "provider") for row in rows)
        for key in ("input", "output")
    }
    accounted = {key: sum(row[key] for row in rows) for key in ("input", "output")}
    return {
        "requests": len(rows),
        **{key: value if known[key] else None for key, value in accounted.items()},
        "accounted": accounted,
        "unknown_usage_requests": sum(
            not all(
                row.get("usage_known", {}).get(k, row["usage"] == "provider")
                for k in ("input", "output")
            )
            for row in rows
        ),
    }


class ProbeBudget(Budget):
    """Experiment group caps on the production ledger, including all prior revisions."""

    def __init__(self, root, group, case, revision="initial"):
        if group not in LIMITS:
            raise ValueError("unknown_probe_group")
        super().__init__(
            Records(root),
            V4Config(
                max_model_calls=TOTAL_LIMITS["requests"],
                max_embedding_calls=EMBEDDING_LIMITS["requests"],
                max_input_tokens=TOTAL_LIMITS["input"] + EMBEDDING_LIMITS["input"],
                max_output_tokens=TOTAL_LIMITS["output"],
                completion_reserve_calls=0,
                completion_reserve_seconds=0,
                max_seconds=7 * 86400,
            ),
            ReportingConfig(),
        )
        self.group, self.case, self.revision = group, case, revision
        if any("probe" not in row for row in self.data["requests"]):
            raise ValueError("probe_budget_requires_its_own_root")

    @staticmethod
    def check(rows, caps, estimate, output, label):
        amounts = {"requests": len(rows) + 1, "input": estimate, "output": output}
        for key in ("input", "output"):
            amounts[key] += sum(row[key] for row in rows)
        for key, cap in caps.items():
            if amounts[key] > cap:
                raise ModelError(
                    f"probe_{label}_{key}_limit", category="budget", request_sent=False
                )

    def reserve(self, config, messages=None, tools=None, response_schema=None, texts=None):
        if getattr(config, "retries", 0):
            raise ModelError("probe_retries_must_be_zero", request_sent=False)
        estimate = (
            TokenEstimator(None, getattr(config, "image_tokens", 0)).measure(
                messages, tools or [], response_schema=response_schema
            )["input_tokens"]
            if messages is not None
            else sum(len(text.encode("utf-8")) for text in texts or [])
        )
        output = getattr(config, "max_tokens", 0)
        kind = "embedding" if texts is not None else "model"
        all_rows = [row for row in self.data["requests"] if row["kind"] == kind]
        if kind == "embedding":
            if self.group != "B":
                raise ModelError("probe_embedding_only_in_B", request_sent=False)
            self.check(all_rows, EMBEDDING_LIMITS, estimate, output, "embedding")
        else:
            self.check(all_rows, TOTAL_LIMITS, estimate, output, "total")
            rows = [row for row in all_rows if row["probe"]["group"] == self.group]
            self.check(rows, LIMITS[self.group], estimate, output, self.group)
            case_rows = [
                row
                for row in rows
                if row["probe"]["case"] == self.case and row["probe"]["revision"] == self.revision
            ]
            if self.group == "B":
                self.check(
                    case_rows,
                    {"requests": 10, "input": 120000, "output": 40000},
                    estimate,
                    output,
                    "B_case",
                )
            elif self.group == "A":
                cap = 4 if self.case in DELIVERY_CASES else 1
                self.check(case_rows, {"requests": cap}, estimate, output, "A_case")
            elif self.group in {"C", "D"}:
                self.check(case_rows, {"requests": 1}, estimate, output, "single_sample")
        ticket = super().reserve(config, messages, tools, response_schema, texts)
        self.data["requests"][ticket]["probe"] = {
            "group": self.group,
            "case": self.case,
            "revision": self.revision,
        }
        self.save()
        return ticket

    def settle(self, ticket, usage):
        super().settle(ticket, usage)
        self.data["requests"][ticket]["usage_known"] = {
            "input": getattr(usage, "prompt_tokens", None) is not None,
            "output": self.data["requests"][ticket]["kind"] == "embedding"
            or getattr(usage, "completion_tokens", None) is not None,
        }
        self.save()

    def report(self):
        rows = self.data["requests"]
        return {
            "limits": LIMITS,
            "total_limits": TOTAL_LIMITS,
            "models": totals([row for row in rows if row["kind"] == "model"]),
            "embedding": totals([row for row in rows if row["kind"] == "embedding"]),
            "groups": {
                group: totals(
                    [
                        row
                        for row in rows
                        if row["kind"] == "model" and row["probe"]["group"] == group
                    ]
                )
                for group in LIMITS
            },
            "requests": rows,
            "unknown_usage_note": "Accounted totals retain estimates/reservations when provider "
            "usage is unavailable. They are not reported as measured actual token use.",
        }


def evaluation_context():
    runtime = load_config("config/runtime.yaml")
    skill = SkillLibrary(runtime.skills_dir).get(runtime.evaluation_skill)
    return build_evaluation_context(
        skill,
        runtime.evaluation_core,
        load_preparation_config("config/preparation.yaml").evaluation,
    )


def fixture(case):
    if case not in A_CASES:
        raise ValueError("unknown_A_case")
    root = Path("runs/mytest23_complete" if case == "A23-1" else "runs/mytest24")
    archive = ReviewArchive(root)
    application = archive.application()
    selected = archive.selected()
    frozen = freeze_review(archive, selected, application, evaluation_context())
    paths = [archive.path, *(archive.root / (ref + ".json") for ref in selected)]
    for document in application:
        folder = root / "sources" / document["source_id"]
        paths.extend([folder / "source.json", folder / "original.md"])
    original_manifest = archive.state["metadata"]["manifest"]["files"]
    for path, expected in original_manifest.items():
        if not Path(path).is_file() or file_hash(path) != expected:
            raise ValueError("historical_original_missing_or_changed:" + path)
        paths.append(Path(path))
    files = {str(path.resolve()): file_hash(path) for path in paths}
    return frozen, files


def paragraph(text, cites=()):
    return {"paragraphs": [{"parts": [{"text": text, "cites": list(cites)}]}]}


def forecast(caller, frozen):
    """Synthetic writer allocation, never used as evidence or an expected scientific answer."""
    assessment = example_for(assessment_contract(frozen).schema())
    ids = list(frozen["materials"]["opinions"])
    cited = list(frozen["materials"]["application"])[::3]
    for key in ("project", "scope", "overall", "support_scope", "alternatives"):
        assessment[key] = paragraph("项目材料、相关依据及判断范围。" * 28, cited[:2])
    for key in REVIEW_ASPECTS:
        assessment["dimensions"][key]["body"] = paragraph(
            "本维度的关键依据、适用条件、科学价值与知识局限。" * 28, cited[:2]
        )
    assessment["appendix_topics"] = [
        {
            "title": f"Scientific topic {i + 1}",
            "focus": "相关研究的原始依据、方法条件与可比范围。" * 6,
            "opinion_ids": ids[i::6],
            "main_sections": [REVIEW_ASPECTS[i]],
        }
        for i in range(6)
    ]
    previews = []
    for keys in sized_writing_plan(caller, frozen, assessment):
        materials = author_materials(frozen, assessment, keys)
        tokens = (
            caller.config.summary_tokens if keys == ["summary"] else caller.config.author_tokens
        )
        request = caller.request(
            AUTHOR_PROMPT,
            materials,
            author_contract(keys, materials["materials"]["evidence"]),
            tokens,
        )
        previews.append({"keys": keys, "capacity": caller.preview(request), "request": request})
    return previews


def preview(output, budget_root):
    if (output / "cases").exists() or (output / "matrix-preview.json").exists():
        raise ValueError("preserve_previous_preview_use_an_explicit_revision")
    config = ManuscriptConfig.load()
    if config.model.retries or config.model.tools:
        raise ValueError("A_probe_requires_zero_retries_and_no_tools")
    rows = {}
    for case in A_CASES:
        records = Records(output / "preview" / case)
        caller = ManuscriptCall(records, config)
        frozen, files = fixture(case)
        prompt, materials = assessment_request(frozen)
        request = caller.request(
            prompt, materials, assessment_contract(frozen), config.assessment_tokens
        )
        authors = forecast(caller, frozen) if case in DELIVERY_CASES else []
        value = {
            "case": case,
            "frozen": frozen,
            "frozen_ref": records.put("frozen-review", frozen),
            "source_hashes": files,
            "manuscript_config": config.model_dump(mode="json"),
            "assessment_request": request,
            "assessment_capacity": caller.preview(request),
            "author_forecast": authors,
            "forecast_note": "Assessment request is exact. Author forecasts use synthetic topic "
            "allocations, not predicted answers; actual requests are previewed before dispatch.",
        }
        write_json(output / "preview" / case / "fixture.json", value)
        rows[case] = {
            "requests": 1 + len(authors),
            "assessment_input_estimate": value["assessment_capacity"]["input_tokens"],
            "author_input_forecast": sum(row["capacity"]["input_tokens"] for row in authors),
            "output_capacity_sum": config.assessment_tokens
            + sum(row["request"]["model"]["max_tokens"] for row in authors),
            "all_requests_fit": value["assessment_capacity"]["fits"]
            and all(row["capacity"]["fits"] for row in authors),
        }
    ledger = budget_root / "v4/budget.json"
    spent = read(ledger)["requests"] if ledger.exists() else []
    prior = totals([row for row in spent if row.get("probe", {}).get("group") == "A"])
    matrix = {
        "group": "A",
        "limits": LIMITS,
        "total_limits": TOTAL_LIMITS,
        "cases": rows,
        "prior_A_usage": prior,
        "input_forecast": sum(
            row["assessment_input_estimate"] + row["author_input_forecast"] for row in rows.values()
        ),
        "requests_forecast": sum(row["requests"] for row in rows.values()),
        "output_capacity_sum": sum(row["output_capacity_sum"] for row in rows.values()),
        "output_note": "Sum of per-request maximum capacities, not predicted use. The ledger "
        "reserves each maximum before dispatch and never exceeds the shared output cap.",
    }
    matrix["fits"] = (
        all(row["all_requests_fit"] for row in rows.values())
        and matrix["input_forecast"] + prior["accounted"]["input"] <= LIMITS["A"]["input"]
        and matrix["requests_forecast"] + prior["requests"] <= LIMITS["A"]["requests"]
    )
    write_json(output / "matrix-preview.json", matrix)
    return matrix


class RenderedMain(HTMLParser):
    """Observe rendered HTML structure without judging or rewriting scientific prose."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags, self.anchors, self.links, self.headings, self.text = Counter(), [], [], [], []
        self.heading, self.link = None, None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags[tag] += 1
        if attrs.get("id"):
            self.anchors.append(attrs["id"])
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.heading = {"level": int(tag[1]), "text": ""}
        if tag == "a" and "href" in attrs:
            self.link = {"target": attrs["href"], "text": ""}

    def handle_data(self, data):
        self.text.append(data)
        if self.heading is not None:
            self.heading["text"] += data
        if self.link is not None:
            self.link["text"] += data

    def handle_endtag(self, tag):
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"} and self.heading is not None:
            self.headings.append(self.heading)
            self.heading = None
        if tag == "a" and self.link is not None:
            self.links.append(self.link)
            self.link = None

    def observations(self):
        citations = [row for row in self.links if re.fullmatch(r"#ref-\d+", row["target"])]
        targets = {row["target"][1:] for row in self.links if row["target"].startswith("#")}
        return {
            "headings": self.headings,
            "html_paragraphs": self.tags["p"],
            "tables": self.tags["table"],
            "table_rows": self.tags["tr"],
            "ordered_lists": self.tags["ol"],
            "list_items": self.tags["li"],
            "visible_numeric_citations": len(citations),
            "citation_labels_match_targets": all(
                row["text"] == "[" + row["target"].removeprefix("#ref-") + "]" for row in citations
            ),
            "missing_anchor_targets": sorted(targets - set(self.anchors)),
            "duplicate_anchor_ids": sorted(
                key for key, count in Counter(self.anchors).items() if count > 1
            ),
            "raw_alias_tokens_observed": sorted(
                set(re.findall(r"\b[ERO]\d+\b", "".join(self.text)))
            ),
        }


def main_preview(assessment, frozen):
    projected = deepcopy(assessment)
    projected["appendix_topics"] = []
    document, _ = compile_publication(projected, {"summary": {"paragraphs": []}}, frozen)
    document.main = [section for section in document.main if section.section_id != "summary"]
    renderer = Renderer(document)
    markdown = (
        "\n\n".join(
            filter(
                None,
                [
                    "<!-- preview-only: existing assessment main text; "
                    "not a completed delivery -->",
                    "# 综合主文预览",
                    renderer.decision_header(),
                    *(renderer.section(section, "main") for section in document.main),
                    renderer.sources(document.main),
                ],
            )
        )
        + "\n"
    )
    body = MarkdownIt("commonmark", {"html": True}).enable("table").render(markdown)
    html = (
        '<!doctype html>\n<html lang="zh-CN"><head><meta charset="utf-8">'
        "<title>综合主文预览</title></head><body>\n" + body + "</body></html>\n"
    )
    rendered = RenderedMain()
    rendered.feed(body)
    observed = rendered.observations()
    observed["prose_paragraphs_by_section"] = {
        section.section_id: sum(node.kind == "paragraph" for node in walk_nodes(section.blocks))
        for section in document.main
    }
    observed["condition_groups"] = [
        node.attrs["label"]
        for section in document.main
        if section.section_id == "conditions"
        for node in section.blocks
        if node.kind == "label_heading"
    ]
    observed["bibliography_sources"] = len(document.sources)
    observed["rendered_sections"] = [section.section_id for section in document.main]
    return markdown, html, observed


def render_main(output, case):
    folder = output / "cases" / case
    result_path = folder / "result.json"
    result = read(result_path)
    records = Records(folder)
    assessment = records.value(result["assessment"])
    frozen = records.value(result["frozen_ref"])
    inputs = [
        result_path,
        records.root / (result["assessment"] + ".json"),
        records.root / (result["frozen_ref"] + ".json"),
    ]
    hashes = {str(path.resolve()): file_hash(path) for path in inputs}
    markdown, html, observations = main_preview(assessment, frozen)
    (folder / "assessment-main-preview.md").write_text(markdown, encoding="utf-8")
    (folder / "assessment-main-preview.html").write_text(html, encoding="utf-8")
    metadata = {
        "case": case,
        "preview_only": True,
        "official_delivery_completed": False,
        "model_requests": 0,
        "assessment_ref": result["assessment"],
        "frozen_ref": result["frozen_ref"],
        "source_hashes": hashes,
        "inputs_unchanged": all(file_hash(path) == expected for path, expected in hashes.items()),
        "omitted_sections": ["summary", "appendix_topics"],
        "note": "Local rendering of the received assessment only. No generated summary, "
        "appendix, semantic correction or publication completion. Observations describe HTML "
        "structure; citation labels and anchors do not establish scientific support.",
        "observations": observations,
    }
    write_json(folder / "assessment-main-preview.json", metadata)
    return metadata


async def live(output, budget_root, case, stage, *, revision="initial", assessment_review=None):
    if stage == "author" and (case not in DELIVERY_CASES or not assessment_review):
        raise ValueError("only_fixed_delivery_cases_after_explicit_manual_assessment_review")
    matrix = read(output / "matrix-preview.json")
    if not matrix["fits"]:
        raise ValueError("matrix_does_not_fit_review_plan_before_calls")
    planned = read(output / "preview" / case / "fixture.json")
    frozen, files = fixture(case)
    if digest(frozen) != digest(planned["frozen"]) or files != planned["source_hashes"]:
        raise ValueError("frozen_case_changed")
    records = Records(output / "cases" / case)
    budget = ProbeBudget(budget_root, "A", case, revision)
    config = ManuscriptConfig.load()
    if config.model_dump(mode="json") != planned["manuscript_config"]:
        raise ValueError("manuscript_configuration_changed_after_preview")
    caller = ManuscriptCall(records, config, budget=budget, env_file=".env")
    prompt, materials = assessment_request(frozen)
    request = caller.request(
        prompt, materials, assessment_contract(frozen), config.assessment_tokens
    )
    if digest(request) != digest(planned["assessment_request"]):
        raise ValueError("assessment_request_changed_after_preview")
    ref = records.put("frozen-review", frozen)
    result_path = output / "cases" / case / "result.json"
    result = read(result_path) if result_path.exists() else {"case": case, "frozen_ref": ref}
    before = len(budget.data["requests"])
    attempt = {"stage": stage, "revision": revision}
    try:
        if stage == "assessment":
            result["assessment"] = await synthesize(caller, ref)
            write_json(
                result_path.with_name("assessment.json"), records.value(result["assessment"])
            )
        elif stage == "author":
            if not result.get("assessment"):
                raise ValueError("assessment_must_exist_before_authoring")
            assessment = records.value(result["assessment"])
            plan = sized_writing_plan(caller, frozen, assessment)
            if len(plan) > 3:
                raise ValueError("actual_author_plan_exceeds_fixed_two_appendix_batches")
            actual = []
            for keys in plan:
                data = author_materials(frozen, assessment, keys)
                tokens = config.summary_tokens if keys == ["summary"] else config.author_tokens
                author_wire = caller.request(
                    AUTHOR_PROMPT,
                    data,
                    author_contract(keys, data["materials"]["evidence"]),
                    tokens,
                )
                actual.append(
                    {"keys": keys, "request": author_wire, "capacity": caller.preview(author_wire)}
                )
            write_json(result_path.with_name("author-preview.json"), actual)
            result["manual_assessment_review"] = assessment_review
            result["delivery"] = await deliver(
                caller, result["assessment"], ref, result_path.parent, translate=False
            )
        else:
            raise ValueError("unknown_local_stage")
        attempt["status"] = "completed"
    except Exception as exc:
        attempt.update(status="failed", reason=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        budget.save()
        attempt["new_requests"] = len(budget.data["requests"]) - before
        attempt["cache_or_local_replay"] = attempt["new_requests"] == 0
        result.setdefault("attempts", []).append(attempt)
        result["source_hashes"] = files
        write_json(result_path, result)
        write_json(budget_root / "usage.json", budget.report())
        print(
            json.dumps(
                {"case": case, "attempt": attempt, "usage": budget.report()["models"]},
                ensure_ascii=False,
            )
        )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "stage", choices=("preview", "assessment", "author", "render-main", "usage")
    )
    parser.add_argument("--output", type=Path, default=Path("runs/probe-review-quality-20261013"))
    parser.add_argument("--case", choices=A_CASES, default="A24-1")
    parser.add_argument("--revision", default="initial")
    parser.add_argument(
        "--assessment-review", help="Human assessment review before fixed delivery."
    )
    args = parser.parse_args()
    if Path(args.revision).name != args.revision or args.revision in {".", ".."}:
        raise ValueError("revision_must_be_a_single_directory_name")
    root = args.output.resolve()
    output = root / "revisions" / args.revision
    if args.stage == "preview":
        print(json.dumps(preview(output, root), ensure_ascii=False, indent=2))
    elif args.stage == "render-main":
        print(json.dumps(render_main(output, args.case), ensure_ascii=False, indent=2))
    elif args.stage == "usage":
        report = ProbeBudget(root, "A", args.case, args.revision).report()
        write_json(root / "usage.json", report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        asyncio.run(
            live(
                output,
                root,
                args.case,
                args.stage,
                revision=args.revision,
                assessment_review=args.assessment_review,
            )
        )


if __name__ == "__main__":
    main()
