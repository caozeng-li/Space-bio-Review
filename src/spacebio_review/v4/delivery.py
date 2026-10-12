"""Frozen final report, original expert appendix and resumable main-report translation."""

import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from spacebio_review.agents.contracts import SubmissionContract
from spacebio_review.agents.diagnostics import digest
from spacebio_review.agents.types import TaskResult
from spacebio_review.context import product_material
from spacebio_review.review.project import REVIEW_ASPECTS

from .records import write_json


class TextDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1)


class TranslationDraft(TextDraft):
    title: str = Field(min_length=1)


LABELS = dict(
    zip(
        REVIEW_ASPECTS,
        ["科学价值", "创新性", "严谨性", "可行性", "安全性", "项目管理"],
        strict=True,
    )
)
EN_LABELS = dict(
    zip(
        REVIEW_ASPECTS,
        ["Scientific value", "Innovation", "Rigor", "Feasibility", "Safety", "Management"],
        strict=True,
    )
)
STATUSES = {
    "assessed": "已形成专业评审",
    "partially_assessed": "仅部分问题形成专业评审",
    "unassessed": "未形成专业评审",
}
STOP_REASONS = {
    "completion_call_reserve": "已结束探索，保留额度用于研究提交、综合与交付",
    "completion_time_reserve": "已结束探索，保留时间用于综合与交付",
    "completion_input_reserve": "已结束探索，保留输入额度用于完成输出",
    "completion_output_reserve": "已结束探索，保留输出额度用于完成输出",
    "embedding_call_budget_exhausted": "向量化请求额度耗尽",
    "research_round_limit": "达到补充研究轮次上限",
    "model_call_budget_exhausted": "模型请求预算耗尽",
    "elapsed_time_budget_exhausted": "运行时间预算耗尽",
    "input_token_budget_exhausted": "输入额度耗尽",
    "output_token_budget_exhausted": "输出额度耗尽",
}


class Delivery:
    def __init__(self, execution, output):
        self.ex, self.records, self.output = execution, execution.records, Path(output)

    def assemble(self, assessment_ref, *, delivery_input=None):
        assessment = self.records.value(assessment_ref)
        frozen = (
            self.records.value(delivery_input)
            if delivery_input
            else {"assessment": assessment_ref, "requests": [], "coverage": assessment["coverage"]}
        )
        if frozen["assessment"] != assessment_ref:
            raise ValueError("delivery_snapshot_assessment_mismatch")
        finals = [r for a in REVIEW_ASPECTS for r in assessment["aspect_refs"].get(a, [])]
        if any(len(rows) != 1 for rows in assessment["aspect_refs"].values()):
            raise ValueError("delivery_requires_one_final_per_aspect")
        refs = list(dict.fromkeys([assessment_ref, *finals, *assessment["professional_refs"]]))
        evidence = dict(frozen.get("evidence", {}))
        for ref in refs:
            evidence.update(self.records.value(ref).get("evidence", {}))
        for req in frozen.get("request_forms", frozen.get("requests", [])):
            evidence.update(req.get("evidence", {}))
        sources = {}

        def source_keys(key, visiting=None):
            visiting = set() if visiting is None else visiting
            if key in visiting:
                return []
            if "::" in key:
                visiting.add(key)
                artifact = self.records.value(key.split("::", 1)[0])
                evidence.update(artifact.get("evidence", {}))
                block = self.records.block(key)
                keys = [
                    *block.get("evidence_refs", []),
                    *block.get("result_refs", []),
                    *artifact.get("scope_links", []),
                ]
                result = [s for k in keys for s in source_keys(k, visiting)]
                visiting.remove(key)
                return sorted(set(result))
            item = evidence[key]
            identity = digest({"reference": item["reference"], "version": item["material_version"]})
            sources.setdefault(identity, item)
            return [identity]

        def section(sid, title, title_en, paragraphs):
            return {
                "section_id": sid,
                "title": title,
                "title_en": title_en,
                "text": "\n\n".join(p for p in paragraphs if p),
            }

        def argument(ref):
            value = self.records.value(ref)
            blocks = []
            for block in value["blocks"]:
                text = block["text"]
                if "[@" not in text:
                    text += "".join(
                        "[@" + k + "]"
                        for k in [*block.get("evidence_refs", []), *block.get("result_refs", [])]
                    )
                blocks.append(text)
            return [value.get("scope_note", ""), *blocks]

        main, appendix = [], []
        project = assessment.get("project", {})
        if project:
            rows = [
                "以下是对申请内容的理解，申请自述的首创性与预期成果仍需评审。",
                project.get("objective", ""),
            ]
            for path in project.get("paths", []):
                rows.append(
                    path.get("question", "")
                    + "："
                    + path.get("activity", "")
                    + "\n"
                    + "预期观测或产物："
                    + path.get("observation_or_artifact", "")
                    + "\n"
                    + "申请声称的认识："
                    + path.get("claimed_knowledge", "")
                )
            main.append(section("project", "项目理解", "Project understanding", rows))
        scope = []
        for aspect in REVIEW_ASPECTS:
            item = assessment["coverage"][aspect]
            scope.append(
                f"- {LABELS[aspect]}：{STATUSES[item['status']]}；"
                + (
                    "已形成方面综合。"
                    if item.get("assessment_status") == "completed"
                    else "尚未形成方面综合，不表示申请没有相关材料。"
                )
                + item.get("reason", "")
                + (" 综合限制：" + item["synthesis_limit"] if item.get("synthesis_limit") else "")
            )
        main.append(section("scope", "实际评审范围", "Actual review coverage", ["\n".join(scope)]))
        main.append(section("overall", "整体判断", "Overall assessment", argument(assessment_ref)))
        for aspect in REVIEW_ASPECTS:
            for ref in assessment["aspect_refs"].get(aspect, []):
                main.append(
                    section("aspect-" + aspect, LABELS[aspect], EN_LABELS[aspect], argument(ref))
                )
        conditions = []
        for timing, label in [
            ("before_support", "支持前"),
            ("implementation_milestone", "实施里程碑"),
            ("recommendation", "一般建议"),
        ]:
            rows = [
                b["text"] for b in assessment.get("conditions", []) if b.get("timing") == timing
            ]
            if rows:
                conditions.extend(["### " + label, *rows])
        if assessment.get("options"):
            conditions.extend(["### 替代方案", *[b["text"] for b in assessment["options"]]])
        if conditions:
            main.append(
                section(
                    "conditions", "支持条件与替代方案", "Conditions and alternatives", conditions
                )
            )
        gaps = []
        if assessment.get("unresolved"):
            gaps.extend(["### 综合保留的未知", *assessment["unresolved"]])
        groups = {}
        for req in frozen.get("requests", []):
            status, outcome = req["status"], req.get("outcome", {})
            group = (
                "已分析的范围与剩余未知"
                if status in {"answered", "partial", "retain_unknown"}
                else "外部依赖"
                if status == "external_dependency"
                else "尚未完成的分析"
            )
            line = req["request"]["question"] + "\n" + req.get("reason", "")
            line += "\n判断影响：" + req["request"].get("decision_impact", "")
            for field in ("dependency", "impact", "uncertainty", "remaining_gap"):
                if outcome.get(field):
                    line += "\n" + outcome[field]
            line += "".join("[@" + r + "]" for r in outcome.get("result_refs", []))
            if status == "deferred":
                line += "\n未执行原因：" + STOP_REASONS.get(
                    req.get("reason"), req.get("reason", "")
                )
            groups.setdefault(group, []).append(line)
        for aspect, item in frozen.get("coverage", {}).items():
            if item.get("external_needs"):
                groups.setdefault("外部依赖", []).append(
                    LABELS[aspect] + "：" + "；".join(item["external_needs"])
                )
        for title, rows in groups.items():
            gaps.extend(["### " + title, *rows])
        if gaps:
            main.append(
                section("gaps", "未决事项和外部依赖", "Open issues and external dependencies", gaps)
            )
        for ref in sorted(assessment["professional_refs"]):
            value = self.records.value(ref)
            rows = argument(ref)
            outcomes = value.get("scope_outcomes", [])
            if outcomes:
                rows.extend(
                    [
                        "### 成员分析范围",
                        *[
                            f"- {o['member_id']} / {o['status']}：{o['explanation']}"
                            for o in outcomes
                        ],
                    ]
                )
            if value.get("followups"):
                rows.append("### 原始后续问题")
                for followup in value["followups"]:
                    rows.append(
                        followup["question"]
                        + "\n"
                        + followup["missing_information"]
                        + "\n"
                        + followup["decision_impact"]
                        + "\n"
                        + followup["information_route"]
                    )
            rows.append(f"[原始产物与引用快照](v4/{ref}.json)")
            appendix.append(section(ref, "专家原文：" + value["title"], "", rows))
        index = [
            f"- [{self.records.value(r)['title']}](appendix.md#{r})"
            for r in sorted(assessment["professional_refs"])
        ]
        index += [
            f"- [内部综合产物 {i}](v4/{r}.json)"
            for i, r in enumerate(assessment.get("internal_refs", []), 1)
        ]
        if index:
            main.append(section("index", "论证与附录索引", "Arguments and appendix index", index))
        # Collect every scalar citation before allocating any source number.
        all_sections = main + appendix
        for row in all_sections:
            row["sources"] = sorted(
                {
                    s
                    for key in re.findall(r"(?<!\\)\[@([^]]+)\]", row["text"])
                    for s in source_keys(key)
                }
            )
        numbering = {k: i for i, k in enumerate(sorted(sources), 1)}
        for row in all_sections:
            row["text"] = re.sub(
                r"(?<!\\)\[@([^]]+)\]",
                lambda m: "".join(f"[{numbering[k]}]" for k in source_keys(m[1])),
                row["text"],
            )
            # Multiple argument citations may resolve to the same original sources.
            row["text"] = re.sub(
                r"(?:\[\d+\]){2,}",
                lambda m: "".join(dict.fromkeys(re.findall(r"\[\d+\]", m[0]))),
                row["text"],
            )
        manifest = {
            "assessment": assessment_ref,
            "delivery_input": delivery_input,
            "artifacts": refs,
            "main_sections": main,
            "appendix_sections": appendix,
            "sources": sources,
            "numbering": numbering,
            "recommendation": assessment["recommendation"],
            "coverage": assessment["coverage"],
            "paths": {"zh": "report.md", "en": "report.en.md", "appendix": "appendix.md"},
        }
        return self.records.put(
            "manifest", manifest, refs + ([delivery_input] if delivery_input else [])
        )

    async def summarize(self, manifest_ref):
        manifest = self.records.value(manifest_ref)
        selected = [
            s
            for s in manifest["main_sections"]
            if s["section_id"] in {"overall", "scope", "conditions", "gaps"}
        ]
        return await self.ex.call(
            "writer/summary/" + manifest_ref,
            "writer",
            "写简短中文决策摘要，以冻结的整体建议、支持条件、最新缺口处置和实际覆盖为依据。"
            "保留决定性限定、替代方案和未评范围，不新增事实或条件，不重新表决、不提出研究请求。"
            "actual_coverage 是实际完成范围的唯一依据，优先于论证正文对覆盖的自述。"
            "摘要必须明示哪些方面仅部分评估、哪些方面未评；不能把未评称为申请缺材料，"
            "也不能自行宣布未评或尚未研究的缺口不影响判断。"
            "不要生成来源编号。",
            SubmissionContract(TextDraft, TextDraft, version="v4.1-summary-2"),
            materials=[
                product_material(
                    "fixed-decision",
                    {
                        "recommendation": manifest["recommendation"],
                        "sections": selected,
                        "actual_coverage": manifest["coverage"],
                    },
                )
            ],
        )

    async def run(self, manifest_ref, *, selected_sections=None):
        manifest = self.records.value(manifest_ref)
        state = {
            "manifest": manifest_ref,
            "status": "partial",
            "paths": manifest["paths"],
            "zh": {"sections": manifest["main_sections"], "status": "body_ready"},
            "en": {"sections": [], "status": "not_run"},
            "appendix": {"sections": manifest["appendix_sections"], "status": "completed"},
            "technical_limits": [],
        }
        self.export(state, manifest)
        summary = await self.summarize(manifest_ref)
        if summary.status == "completed" and summary.product:
            state["zh"].update(summary=summary.product["text"], status="completed")
        else:
            state["technical_limits"].append("summary: " + str(summary.reason))
        translatable = (
            [
                {
                    "section_id": "summary",
                    "title": "决策摘要",
                    "title_en": "Decision summary",
                    "text": state["zh"]["summary"],
                    "sources": [],
                }
            ]
            if state["zh"].get("summary")
            else []
        )
        translatable += [
            s
            for s in manifest["main_sections"]
            if selected_sections is None or s["section_id"] in selected_sections
        ]
        # Preserve the summary and truthful progress even if the outer time budget
        # cancels the first translation before it returns.
        state["en"]["status"] = "partial"
        self.export(state, manifest)
        for section in translatable:
            translated = await self.translate(section)
            if translated.status == "completed" and translated.product:
                state["en"]["sections"].append(
                    {
                        **section,
                        "title": section.get("title_en") or translated.product["title"],
                        "text": translated.product["text"],
                    }
                )
            else:
                state["technical_limits"].append(
                    section["section_id"] + ": " + str(translated.reason)
                )
            self.export(state, manifest)
        complete = len(state["en"]["sections"]) == len(translatable)
        state["en"]["status"] = (
            ("completed" if selected_sections is None else "selected_completed")
            if complete
            else "partial"
        )
        if all(state[k]["status"] == "completed" for k in ("zh", "en", "appendix")):
            state["status"] = "completed"
        self.export(state, manifest)
        return self.records.put("delivery", state, [manifest_ref])

    async def translate(self, section):
        chunks, group, size = [], [], 0
        for paragraph in section["text"].split("\n\n"):
            if group and size + len(paragraph) > self.ex.reporting.material_batch_chars:
                chunks.append("\n\n".join(group))
                group, size = [], 0
            group.append(paragraph)
            size += len(paragraph)
        if group:
            chunks.append("\n\n".join(group))
        outputs = []
        for i, text in enumerate(chunks):
            chunk = {**section, "text": text, "chunk": i + 1, "chunks": len(chunks)}
            key = "writer/en/" + digest(chunk)
            result = await self.ex.call(
                key,
                "writer",
                "将冻结的中文正文翻译为英文，忠实保持条件、否定、数字、引用编号、Markdown链接和判断强度。"
                "不重新评审、不增加事实或研究请求；title 为英文标题，text 只含当前分片正文。"
                "保留链接目标不变，不重复其他分片的范围说明。",
                SubmissionContract(
                    TranslationDraft, TranslationDraft, version="v4.1-translation-2"
                ),
                materials=[product_material("accepted-chinese-section", chunk)],
            )
            if result.status != "completed" or not result.product:
                return result
            outputs.append(result.product)
        return TaskResult(
            task_id="writer/en/" + digest(section),
            agent_id="writer",
            status="completed",
            reason="translated_frozen_section",
            product={"title": outputs[0]["title"], "text": "\n\n".join(o["text"] for o in outputs)},
        )

    def export(self, state, manifest):
        self.output.mkdir(parents=True, exist_ok=True)
        for language in ("zh", "en", "appendix"):
            current = state[language]
            text = (
                "# "
                + {"zh": "项目评审", "en": "Project review", "appendix": "专家原文附录"}[language]
                + "\n\n"
            )
            if language == "en":
                text += "[Original expert appendix](appendix.md)\n\n"
            elif language == "zh":
                text += "[专家原文附录](appendix.md)\n\n"
            else:
                text += "[返回主报告](report.md)\n\n"
            if current.get("summary"):
                text += "## 决策摘要\n\n" + current["summary"] + "\n\n"
            used = set()
            for row in current["sections"]:
                if language == "appendix":
                    text += f'<a id="{row["section_id"]}"></a>\n\n'
                text += "## " + row["title"] + "\n\n" + row["text"] + "\n\n"
                used.update(row.get("sources", []))
            text += "## 来源 / Sources\n\n"
            for key in sorted(used, key=lambda k: manifest["numbering"][k]):
                v = manifest["sources"][key]
                text += (
                    f"[{manifest['numbering'][key]}] {v.get('title', '')} — "
                    f"{v.get('locator', '')}; {v.get('url', '')}\n\n"
                )
            if state["technical_limits"]:
                text += "## 交付未完成项 / Incomplete delivery\n\n" + "\n".join(
                    "- " + t for t in state["technical_limits"]
                )
            path = self.output / manifest["paths"][language]
            temp = path.with_suffix(".tmp")
            temp.write_text(text, "utf-8")
            temp.replace(path)
        write_json(self.output / "delivery.json", state)
