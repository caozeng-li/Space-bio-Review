"""Change 17-21: read-only historical replay and six fixed local publication requests."""

import argparse
import asyncio
import hashlib
import json
import re
from pathlib import Path
from types import SimpleNamespace

import yaml
from markdown_it import MarkdownIt
from probe_report_citations import Batch, FrozenReader, PreviewOnly, ProbeExecution, ProbeRuntime

from spacebio_review.agents.diagnostics import SubmissionRecorder
from spacebio_review.models.config import load_config
from spacebio_review.v4.config import ReportingConfig, V4Config
from spacebio_review.v4.publication_delivery import Delivery, load_document
from spacebio_review.v4.publication_document import (
    Section,
    summary_input,
    walk_nodes,
    walk_sections,
)
from spacebio_review.v4.publication_input import publication_catalog
from spacebio_review.v4.publication_render import Renderer
from spacebio_review.v4.records import Records, write_json
from spacebio_review.v4.synthesis import Synthesis

HISTORY = Path("runs/mytest20")
LIMITS = (8, 100000, 80000)


class FrozenRecords(Records):
    def __init__(self, output):
        self.history = Records(HISTORY)
        self.read_hashes = {
            str(self.history.path): hashlib.sha256(self.history.path.read_bytes()).hexdigest()
        }
        super().__init__(output)

    def get(self, key):
        if (self.root / (key + ".json")).exists():
            return super().get(key)
        path = self.history.root / (key + ".json")
        self.read_hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        return self.history.get(key)

    def unchanged(self):
        return all(
            hashlib.sha256(Path(path).read_bytes()).hexdigest() == value
            for path, value in self.read_hashes.items()
        )


def frozen_manifest(records, output):
    state = records.history.state
    assessment = records.value(state["assessment"])
    frozen = records.value(state["delivery_input"])
    # The completed run's state supplies its missing publication directory, explicitly.
    labels = {
        v["id"]: v["expertise"]
        for v in yaml.safe_load(Path("config/expertise.yaml").read_text("utf-8"))["experts"]
    }
    for expert in state["package"]["experts"].values():
        expert["profile_labels"] = [labels[p] for p in expert["profile_ids"] if p in labels]
    frozen["publication_catalog"] = publication_catalog(state, assessment["professional_refs"])
    frozen_ref = records.put("delivery-input", frozen, [state["assessment"]])
    ex = SimpleNamespace(records=records)
    delivery = Delivery(ex, output)
    manifest_ref = delivery.assemble(state["assessment"], delivery_input=frozen_ref)
    document = load_document(records, records.value(manifest_ref))
    write_json(
        Path(output) / "fixture.json",
        {
            "history": str(HISTORY),
            "assessment": state["assessment"],
            "original_delivery_input": state["delivery_input"],
            "catalog_recovery": "completed_state_questions_and_expertise_labels",
            "manifest": manifest_ref,
        },
    )
    return manifest_ref, document


def local_links(output, names=None):
    errors, count = [], 0
    paths = [output / name for name in names] if names else output.glob("*.md")
    for path in paths:
        text = path.read_text("utf-8")
        for target in re.findall(r"\]\(([^\s)]+)", text):
            if target.startswith(("https:", "http:", "<")):
                continue
            count += 1
            filename, _, anchor = target.partition("#")
            destination = output / filename if filename else path
            if not destination.exists() or (
                anchor and f'id="{anchor}"' not in destination.read_text("utf-8")
            ):
                errors.append({"file": path.name, "target": target})
    return {"checked": count, "errors": errors}


def previews(output, names):
    parser = MarkdownIt("commonmark", {"html": True}).enable("table").enable("strikethrough")
    css = (
        "body{max-width:960px;margin:32px auto;font:16px/1.8 system-ui;"
        "padding:0 24px;color:#20242b}"
        "a{color:#135daf}h1{font-size:28px}h2{font-size:23px}h3{font-size:19px}"
        "table{border-collapse:collapse}td,th{border:1px solid #bbb;padding:8px}"
    )
    for name in names:
        text = (output / (name + ".md")).read_text("utf-8")
        html = parser.render(text).replace(".md#", ".html#").replace('.md"', '.html"')
        (output / (name + ".html")).write_text(
            '<!doctype html><meta charset="utf-8"><style>' + css + "</style>" + html, "utf-8"
        )


def offline(root):
    output = root / "offline"
    records = FrozenRecords(output)
    manifest, document = frozen_manifest(records, output)
    state = {
        "manifest": manifest,
        "status": "partial",
        "zh": {"status": "body_ready"},
        "en": {"status": "not_run"},
        "appendix": {"status": "completed"},
        "translations": {},
        "technical_limits": [],
        "historical_mode": "structural_replay_without_prose_editing",
    }
    Delivery(SimpleNamespace(records=records), output).export(state, document)
    names = ["report.md", "report.en.md", "appendix.md"]
    previews(output, [Path(name).stem for name in names])
    stats = {
        "mode": "structural_replay_without_prose_editing",
        "api_calls": 0,
        "input_unchanged": records.unchanged(),
        "source_records_read": len(records.read_hashes),
        "sections": len(document.main),
        "topics": len(document.appendix),
        "professional_analyses": sum(
            s.kind == "analysis" for s in walk_sections(document.appendix)
        ),
        "supplementary_analyses": sum(
            s.kind == "supplement" for s in walk_sections(document.appendix)
        ),
        "sources": len(document.sources),
        "precise_locations": len(document.locations),
        "crossrefs": sum(
            n.kind == "crossref" for s in walk_sections(document.main) for n in walk_nodes(s.blocks)
        ),
        "links": local_links(output, names),
        "bytes": {name: (output / name).stat().st_size for name in names},
    }
    write_json(output / "audit.json", stats)
    print(json.dumps(stats, ensure_ascii=False), flush=True)


async def live(batch, case):
    cfg = load_config("config/runtime.yaml")
    for model in cfg.models.values():
        model.retries = 0
    for route in cfg.task_routes.values():
        route.max_truncation_recoveries = 0
    if case != "L1":
        cfg.formal_routes["writer"].max_tokens = 8192 if case == "L2" else 4096
    runtime = ProbeRuntime.from_config(cfg, model_factory=batch.factory)
    runtime.recorder = SubmissionRecorder(batch.output / "submissions")
    records = FrozenRecords(batch.output)
    ex = ProbeExecution(
        runtime, records, V4Config(task_turns=3), {}, ReportingConfig(max_repairs=0)
    )
    manifest, document = frozen_manifest(records, batch.output)
    write_json(
        batch.output / "experiment.json",
        {
            "sample": batch.sample,
            "limits": LIMITS,
            "runtime": cfg,
            "scope": "fixed_formal_or_publication_stage_only",
            "repairs": 0,
            "retries": 0,
        },
    )
    try:
        delivery = Delivery(ex, batch.output)
        if case == "L1":
            assessment = records.value(records.history.state["assessment"])
            final = records.value(assessment["aspect_refs"]["safety"][0])
            refs = final["input_refs"]
            write_json(
                batch.output / "sample.json",
                {
                    "aspect": "safety",
                    "input_refs": refs,
                    "original_final": assessment["aspect_refs"]["safety"][0],
                },
            )
            result = await Synthesis(ex, SimpleNamespace(reader=FrozenReader)).generate(
                records.result_blocks(refs),
                assessment["project"],
                assessment["coverage"]["safety"],
                refs,
                "safety",
                "aspect-final",
            )
            write_json(
                batch.output / "result.json", {"artifact": result, "product": records.value(result)}
            )
        elif case == "L2":
            original = next(s for s in document.main if s.section_id == "aspect-innovation")
            section = Section(
                section_id=original.section_id, title=original.title, blocks=original.blocks[:1]
            )
            write_json(batch.output / "sample.json", section)
            result = await delivery.edit(section)
            write_json(batch.output / "result.json", result)
        elif case == "L3":
            original = next(s for s in document.main if s.section_id == "aspect-feasibility")
            section = Section(
                section_id=original.section_id,
                title=original.title,
                title_en=original.title_en,
                blocks=original.blocks[1:3],
            )
            write_json(batch.output / "sample.json", section)
            result = await delivery.translate(section)
            write_json(batch.output / "result.json", result)
        else:
            # Select existing public sections; no invented scientific premise or review rerun.
            document.main = [s for s in document.main if s.section_id in {"overall", "conditions"}]
            value = records.value(manifest)
            value["document"] = document.model_dump()
            local = records.put("manifest", value)
            write_json(batch.output / "sample.json", summary_input(document))
            result = await delivery.summarize(local)
            write_json(batch.output / "result.json", result)
    except PreviewOnly:
        pass
    except Exception as exc:
        write_json(batch.output / "failure.json", {"type": type(exc).__name__, "error": str(exc)})
        print(f"{batch.sample}: {type(exc).__name__}: {exc}", flush=True)
    finally:
        await runtime.close()
        write_json(
            batch.output / "source-check.json",
            {"unchanged": records.unchanged(), "read_hashes": records.read_hashes},
        )
        print(
            json.dumps(
                {
                    "sample": batch.sample,
                    "requests": len(batch.rows),
                    "input": sum(r.get("input_tokens") or r["input_reserve"] for r in batch.rows),
                    "output": sum(
                        r.get("output_tokens") or r["output_reserve"] for r in batch.rows
                    ),
                }
            ),
            flush=True,
        )


def audit(root):
    output = root / "offline"
    fixture = json.loads((output / "fixture.json").read_text("utf-8"))
    records = FrozenRecords(output)
    document = load_document(records, records.value(fixture["manifest"]))
    requests = json.loads((root / "requests.json").read_text("utf-8"))
    outcomes = {
        "L1-1": "公开表达和引用提交完成；科学推断仍有过强表述，本轮不作语义通过结论",
        "L1-2": "公开表达和引用提交完成；不等于证据支持关系已验证",
        "L2-1": "结构完成，原 scope 写作指令未去除；历史编辑内容验收未通过",
        "L3-1": "绑定和条件保留；金额写成 00,000 yuan，标点边界不稳定",
        "L3-2": "同上；不是无问题的重复通过",
        "L4-1": "结构完成；覆盖状态表达不完整并混用未评/部分评估",
        "L3-contract2": "数值与单位倍率、条件、否定、绑定保留；新版仅真实验证一次",
        "L4-contract2": "公开科学摘要完成；建议标签与覆盖状态改由程序展示",
    }
    rows = []
    bindings = {}
    for request in requests:
        sample = request["sample"]
        rows.append(
            {
                "sample": sample,
                "input": request.get("input_tokens", request["input_reserve"]),
                "output": request.get("output_tokens", request["output_reserve"]),
                "reasoning": request.get("reasoning_tokens"),
                "finish_reason": request.get("finish_reason"),
                "retries": request.get("retries"),
                "outcome": outcomes[sample],
            }
        )
        if sample.startswith("L3"):
            original = Section.model_validate(
                json.loads((root / ("live-" + sample) / "sample.json").read_text("utf-8"))
            )
            result = json.loads((root / ("live-" + sample) / "result.json").read_text("utf-8"))
            translated = Section.model_validate(result["product"]["section"])

            def refs(section):
                return [
                    (i, n.kind, n.target)
                    for i, n in enumerate(walk_nodes(section.blocks))
                    if n.kind in {"evidence", "crossref"}
                ]

            bindings[sample] = refs(original) == refs(translated)
    sample = json.loads((root / "live-L3-contract2" / "result.json").read_text("utf-8"))["product"][
        "section"
    ]
    translated = Section.model_validate(sample)
    text = "局部真实翻译样本：仅可行性前两个论述段，不是完整英文报告。\n\n"
    text += Renderer(document, "en", available_main=set()).render("en", [translated])
    (output / "translation-sample.md").write_text(text, "utf-8")
    summary = json.loads((root / "live-L4-contract2" / "result.json").read_text("utf-8"))[
        "product"
    ]["text"]
    text = "局部真实摘要样本：冻结的总体判断与条件，不是重新运行评审。\n\n"
    text += Renderer(document).render("zh", [], summary=summary)
    (output / "summary-sample.md").write_text(text, "utf-8")
    previews(output, ["translation-sample", "summary-sample"])
    result = {
        "requests": rows,
        "totals": {
            "calls": len(rows),
            "input": sum(r["input"] for r in rows),
            "output": sum(r["output"] for r in rows),
        },
        "translation_bindings_unchanged": bindings,
        "historical_editor_accepted": False,
        "full_review_run": False,
        "links": local_links(output),
        "input_unchanged": records.unchanged(),
    }
    write_json(root / "acceptance.json", result)
    print(json.dumps(result, ensure_ascii=False), flush=True)


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["offline", "audit", "L1", "L2", "L3", "L4"])
    parser.add_argument("--root", default="runs/probe-publication-20261012")
    parser.add_argument("--sample", default="1")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.stage == "offline":
        offline(Path(args.root))
    elif args.stage == "audit":
        audit(Path(args.root))
    else:
        await live(
            Batch(args.root, args.stage + "-" + args.sample, args.execute, limits=LIMITS),
            args.stage,
        )


if __name__ == "__main__":
    asyncio.run(main())
