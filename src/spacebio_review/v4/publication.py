"""Build a reader publication from a frozen review, without research or generation."""

from collections import Counter
from pathlib import PureWindowsPath

from spacebio_review.review.project import REVIEW_ASPECTS

from .generation import render_parts
from .publication_document import (
    Location,
    Node,
    PublicationDocument,
    Section,
    Source,
    Target,
    parse_text,
    walk_nodes,
    walk_sections,
)
from .publication_input import Analysis, Topic, project_input
from .records import statement_registry
from .report_content import block_text

LABELS = dict(zip(REVIEW_ASPECTS, ["科学价值", "创新性", "严谨性", "可行性", "安全性", "项目管理"]))
EN_LABELS = dict(
    zip(
        REVIEW_ASPECTS,
        ["Scientific value", "Innovation", "Rigor", "Feasibility", "Safety", "Management"],
    )
)
STATUSES = {
    "assessed": "已形成专业评审",
    "partially_assessed": "仅部分问题形成专业评审",
    "unassessed": "未形成专业评审",
}


def statement_text(value):
    text = render_parts(value["parts"])
    if value.get("support"):
        text += "\n\n依据说明：" + value["support"]
    if value.get("scope"):
        text += "\n\n适用范围与限制：" + render_parts(value["scope"])
    if value.get("premise_ids"):
        text += "\n\n分析前提：" + " ".join("[@" + k + "]" for k in value["premise_ids"])
    return text


class Builder:
    def __init__(self, records, assessment_ref, frozen):
        self.records, self.cache = records, {}
        self.evidence = dict(frozen.get("evidence", {}))
        self.statements, self.bindings, self.source_ids, self.evidence_ids = {}, {}, {}, {}
        self.analysis_topics = {}
        self.audit = {"origins": {}, "evidence": {}, "sources": {}, "lineage": {}}
        assessment = self.artifact(assessment_ref)
        self.input = project_input(assessment_ref, assessment, frozen)
        self.doc = PublicationDocument(
            main=[],
            appendix=[],
            recommendation=assessment["recommendation"],
            coverage=self.input.coverage,
        )
        for request in frozen.get("request_forms", frozen.get("requests", [])):
            self.evidence.update(request.get("evidence", {}))
            self.statements.update(request.get("statements", {}))
        self.assessment, self.assessment_ref = assessment, assessment_ref

    def artifact(self, ref):
        if ref not in self.cache:
            value = self.records.value(ref)
            self.cache[ref] = value
            self.evidence.update(value.get("evidence", {}))
            self.statements.update(statement_registry(value))
        return self.cache[ref]

    def field(self, value, path, text):
        parts = (value.get("citation_content") or {}).get(path)
        return parse_text(render_parts(parts) if parts is not None else text)

    def add_block(self, section, ref, index):
        value = self.artifact(ref)
        block = value["blocks"][index]
        path = f"/blocks/{index}"
        bid = section.section_id + "-p" + str(index + 1)
        selected = (value.get("statement_content") or {}).get(path, {}).get("selected", [])
        if selected:
            nodes = []
            for i, key in enumerate(selected):
                sid = bid + "-s" + str(i + 1)
                nodes.append(
                    Node(
                        kind="block",
                        node_id=sid,
                        children=parse_text(statement_text(self.statements[key])),
                    )
                )
                self.bindings.setdefault(
                    key,
                    Target(
                        document=self.place(section),
                        section=section.section_id,
                        anchor=sid,
                        paragraph=index + 1,
                    ),
                )
                self.audit["lineage"][key] = {
                    f: self.statements[key].get(f, []) for f in ("based_on", "supersedes")
                }
        else:
            parts = (value.get("citation_content") or {}).get(path)
            nodes = parse_text(block_text(block, parts))
        section.blocks.append(Node(kind="block", node_id=bid, children=nodes))
        self.bindings.setdefault(
            ref + "::" + block["block_id"],
            Target(
                document=self.place(section),
                section=section.section_id,
                anchor=bid,
                paragraph=index + 1,
            ),
        )
        self.audit["origins"][bid] = {"artifact": ref, "path": path}

    @staticmethod
    def place(section):
        return "main" if section.kind == "main" else "appendix"

    def analysis_section(
        self, ref, sid, title, title_en="", kind="main", indices=None, members=None
    ):
        value = self.artifact(ref)
        section = Section(section_id=sid, title=title, title_en=title_en, kind=kind)
        if value.get("scope_note"):
            section.blocks.append(
                Node(
                    kind="scope",
                    node_id=sid + "-scope",
                    children=self.field(value, "/scope_note", value["scope_note"]),
                )
            )
        for index in range(len(value["blocks"])) if indices is None else indices:
            self.add_block(section, ref, index)
        if kind == "analysis":
            for outcome in value.get("scope_outcomes", []):
                explanation = outcome.get("explanation", "")
                if explanation:
                    title = (members or {}).get(outcome["member_id"], "分析范围")
                    section.blocks.extend(parse_text("**" + title + "**\n\n" + explanation))
            for followup in value.get("followups", []):
                for field in ("question", "missing_information", "decision_impact"):
                    if followup.get(field):
                        section.blocks.extend(parse_text(followup[field]))
        return section

    def main_sections(self):
        a = self.assessment
        project = a.get("project", {})
        if project:
            rows = [
                "以下为申请内容，申请自述的首创性与预期成果仍需评审。",
                project.get("objective", ""),
            ]
            for path in project.get("paths", []):
                rows.extend(
                    [
                        "### " + path.get("question", ""),
                        path.get("activity", ""),
                        "预期观测或产物：" + path.get("observation_or_artifact", ""),
                        "申请声称的认识：" + path.get("claimed_knowledge", ""),
                    ]
                )
            self.doc.main.append(
                Section(
                    section_id="project",
                    title="项目理解",
                    title_en="Project understanding",
                    blocks=parse_text("\n\n".join(filter(None, rows))),
                )
            )
        scope = []
        for aspect, item in self.input.coverage.items():
            text = f"{LABELS[aspect]}：{STATUSES[item.status]}；"
            text += (
                "已形成方面综合。"
                if item.assessment_status == "completed"
                else "尚未形成方面综合，不表示申请没有相关材料。"
            )
            scope.append(Node(kind="list_item", children=parse_text(text)))
        self.doc.main.append(
            Section(
                section_id="scope",
                title="实际评审范围",
                title_en="Actual review coverage",
                blocks=[Node(kind="bullet_list", children=scope)]
                + parse_text("各项分析的适用范围和剩余限制见[专业分析附录](appendix.md)。"),
            )
        )
        self.doc.main.append(
            self.analysis_section(self.assessment_ref, "overall", "整体判断", "Overall assessment")
        )
        for aspect in REVIEW_ASPECTS:
            refs = a.get("aspect_refs", {}).get(aspect, [])
            if len(refs) > 1:
                raise ValueError("delivery_requires_one_final_per_aspect")
            for ref in refs:
                self.doc.main.append(
                    self.analysis_section(
                        ref, "aspect-" + aspect, LABELS[aspect], EN_LABELS[aspect]
                    )
                )
        conditions = []
        for timing, label in [
            ("before_support", "支持前"),
            ("implementation_milestone", "实施里程碑"),
            ("recommendation", "一般建议"),
        ]:
            rows = [
                (i, b) for i, b in enumerate(a.get("conditions", [])) if b.get("timing") == timing
            ]
            if rows:
                conditions.extend(parse_text("### " + label))
                for i, block in rows:
                    conditions.extend(self.field(a, f"/conditions/{i}/text", block["text"]))
        if a.get("options"):
            conditions.extend(parse_text("### 替代方案"))
            for i, block in enumerate(a["options"]):
                conditions.extend(self.field(a, f"/options/{i}/text", block["text"]))
        if conditions:
            self.doc.main.append(
                Section(
                    section_id="conditions",
                    title="支持条件与替代方案",
                    title_en="Conditions and alternatives",
                    blocks=conditions,
                )
            )
        gaps = []
        for i, text in enumerate(a.get("unresolved", [])):
            gaps.extend(self.field(a, f"/unresolved/{i}", text))
        labels = {
            "unresolved": "保留未知",
            "answered": "已分析",
            "partial": "部分分析",
            "retain_unknown": "保留未知",
            "external_dependency": "外部依赖",
        }
        for issue in self.input.issues:
            gaps.extend(
                parse_text(
                    "### " + labels.get(issue.status, "尚未完成的分析") + "\n\n" + issue.question
                )
            )
            for field in (
                "decision_impact",
                "dependency",
                "impact",
                "uncertainty",
                "remaining_gap",
            ):
                if text := getattr(issue, field):
                    gaps.extend(parse_text(text))
            if issue.result_refs:
                gaps.extend(
                    parse_text("相关分析：" + " ".join("[@" + k + "]" for k in issue.result_refs))
                )
        for aspect, item in self.input.coverage.items():
            for need in item.external_needs:
                gaps.extend(parse_text(LABELS[aspect] + "的外部依赖：" + need))
        if gaps:
            self.doc.main.append(
                Section(
                    section_id="gaps",
                    title="未决事项和外部依赖",
                    title_en="Open issues and external dependencies",
                    blocks=gaps,
                )
            )

    def appendix_sections(self):
        topics = list(self.input.topics)
        covered = {a.ref for t in topics for a in t.analyses}
        for ref in self.assessment.get("professional_refs", []):
            if ref not in covered:
                topics.append(
                    Topic(
                        title=self.artifact(ref)["title"],
                        analyses=[Analysis(ref=ref, perspective="专业视角未记录")],
                    )
                )
        for i, topic in enumerate(topics, 1):
            parent = Section(section_id=f"topic-{i}", title=topic.title, kind="topic")
            perspectives = Counter(a.perspective for a in topic.analyses)
            for j, analysis in enumerate(topic.analyses, 1):
                title = analysis.perspective
                if perspectives[title] > 1:
                    title += f"：{self.artifact(analysis.ref)['title']}（分析 {j}）"
                section = self.analysis_section(
                    analysis.ref,
                    f"analysis-{i}-{j}",
                    title,
                    kind="analysis",
                    members=topic.members,
                )
                if analysis.purpose:
                    section.blocks.insert(
                        0,
                        Node(kind="paragraph", children=[Node(kind="text", text=analysis.purpose)]),
                    )
                parent.sections.append(section)
                self.analysis_topics[analysis.ref] = parent
            self.doc.appendix.append(parent)

    def resolve_target(self, key):
        if key not in self.bindings:
            parent = None
            if key.startswith("statement-"):
                value = self.statements[key]
                sid = "supplement-" + str(len(self.bindings) + 1)
                section = Section(
                    section_id=sid,
                    title="补充综合分析",
                    kind="supplement",
                    blocks=[
                        Node(
                            kind="block",
                            node_id=sid + "-p1",
                            children=parse_text(statement_text(value)),
                        )
                    ],
                )
                self.bindings[key] = Target(
                    document="appendix", section=sid, anchor=sid + "-p1", paragraph=1
                )
                self.audit["lineage"][key] = {
                    f: value.get(f, []) for f in ("based_on", "supersedes")
                }
            else:
                ref, bid = key.split("::", 1)
                value = self.artifact(ref)
                index = next(i for i, b in enumerate(value["blocks"]) if b["block_id"] == bid)
                # All referenced blocks from one intermediate artifact share one scope/entry.
                existing = self.audit.setdefault("supplements", {}).get(ref)
                if existing:
                    section = next(
                        s for s in walk_sections(self.doc.appendix) if s.section_id == existing
                    )
                    self.add_block(section, ref, index)
                    return self.bindings[key]
                sid = "supplement-" + str(len(self.bindings) + 1)
                section = self.analysis_section(
                    ref, sid, value["title"], kind="supplement", indices=[index]
                )
                self.audit["supplements"][ref] = sid
                inputs = value.get("input_refs", [])
                if inputs and all(r in self.analysis_topics for r in inputs):
                    topics = {self.analysis_topics[r].section_id for r in inputs}
                    if len(topics) == 1:
                        parent = self.analysis_topics[inputs[0]]
                section.title = "补充综合分析：" + section.title
            if parent is None:
                parent = next((s for s in self.doc.appendix if s.section_id == "supplements"), None)
            if parent is None:
                parent = Section(section_id="supplements", title="补充综合分析", kind="topic")
                self.doc.appendix.append(parent)
            parent.sections.append(section)
        return self.bindings[key]

    def bind_reference(self, node):
        key = node.target
        if "::" in key or key.startswith("statement-"):
            target = self.resolve_target(key)
            node.kind, node.target = "crossref", target.anchor
            self.doc.targets[target.anchor] = target
            return
        if key not in self.evidence_ids:
            item = self.evidence[key]
            source_id = item["reference"]["source_id"]
            if source_id not in self.source_ids:
                sid = "source-" + str(len(self.source_ids) + 1)
                self.source_ids[source_id] = sid
                title = item.get("title", "")
                if item.get("kind") == "application":
                    title = PureWindowsPath(title).name
                url = item.get("url", "")
                self.doc.sources[sid] = Source(
                    title=title,
                    kind=item.get("kind", ""),
                    url=url if url.startswith(("https://", "http://")) else "",
                    authors=item.get("authors", []),
                    publication_date=item.get("publication_date", "") or "",
                )
                self.audit["sources"][sid] = source_id
            sid = self.source_ids[source_id]
            eid = "location-" + str(len(self.evidence_ids) + 1)
            self.evidence_ids[key] = eid
            self.doc.locations[eid] = Location(
                source=sid,
                locator=item.get("locator", ""),
                reading_scope=item.get("reading_scope", ""),
            )
            self.audit["evidence"][eid] = {"evidence_id": key, "record": item}
        node.target = self.evidence_ids[key]
        node.kind = "evidence"

    def build(self):
        self.main_sections()
        self.appendix_sections()
        # Resolving a reference may add an appendix entry; process it exactly once.
        processed = set()
        while True:
            pending = [
                n
                for s in walk_sections(self.doc.main + self.doc.appendix)
                for n in walk_nodes(s.blocks)
                if n.kind == "citation" and id(n) not in processed
            ]
            if not pending:
                break
            for node in pending:
                processed.add(id(node))
                self.bind_reference(node)
        for section in walk_sections(self.doc.main + self.doc.appendix):
            for node in walk_nodes(section.blocks):
                if node.kind == "evidence":
                    sid = self.doc.locations[node.target].source
                    self.doc.numbering.setdefault(sid, len(self.doc.numbering) + 1)
        return self.doc, self.audit


def build_publication(records, assessment_ref, frozen=None):
    frozen = frozen or {"assessment": assessment_ref}
    if frozen.get("assessment") != assessment_ref:
        raise ValueError("delivery_snapshot_assessment_mismatch")
    return Builder(records, assessment_ref, frozen).build()
