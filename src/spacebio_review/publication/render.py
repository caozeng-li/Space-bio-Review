"""Deterministic Markdown publication with shared numeric source references."""

from spacebio_review.authored_markdown import MarkdownRenderer, escape

from .document import walk_nodes


class Renderer(MarkdownRenderer):
    def __init__(self, document, language="zh"):
        super().__init__()
        self.doc, self.language = document, language
        self.en = language == "en"
        self.system = document.evaluation_system
        self.criteria = {item.id: item for item in self.system.criteria}

    def inline_special(self, nodes, index, file, *, table=False):
        if nodes[index].kind != "evidence":
            return super().inline_special(nodes, index, file, table=table)
        if nodes[index].children:
            label = self.inline(nodes[index].children, file, table=table)
            source = self.doc.locations[nodes[index].target].source
            return f"{label} [{self.doc.numbering[source]}]", index + 1
        refs = []
        cursor = index
        while (
            cursor < len(nodes)
            and nodes[cursor].kind == "evidence"
            and not nodes[cursor].children
        ):
            refs.append(nodes[cursor].target)
            cursor += 1
            if (
                cursor + 1 < len(nodes)
                and nodes[cursor].kind == "text"
                and nodes[cursor].text.isspace()
                and nodes[cursor + 1].kind == "evidence"
                and not nodes[cursor + 1].children
            ):
                cursor += 1
        sources = dict.fromkeys(self.doc.locations[key].source for key in refs)
        value = " ".join(f"[{self.doc.numbering[sid]}]" for sid in sources)
        previous = nodes[index - 1] if index else None
        spaced = previous and previous.kind == "text" and previous.text[-1:].isspace()
        return ("" if spaced else " ") + value, cursor

    def block_special(self, node, file):
        if node.kind == "grade_table":
            return self.grade_table()
        return super().block_special(node, file)

    def section(self, section, file):
        title = section.title_en if self.en and section.title_en else section.title
        return "\n\n".join(filter(None, ["## " + escape(title) if title else "",
                                            self.blocks(section.blocks, file)]))

    def sources(self, sections):
        used = {}
        for section in sections:
            for node in walk_nodes(section.blocks):
                if node.kind == "evidence":
                    loc = self.doc.locations[node.target]
                    used.setdefault(loc.source, {})[(loc.locator, loc.reading_scope)] = loc
        rows = ["## References" if self.en else "## 参考文献与申请材料"]
        for sid in sorted(used, key=self.doc.numbering.__getitem__):
            source, number = self.doc.sources[sid], self.doc.numbering[sid]
            kind = (
                ("Application material" if self.en else "申请材料")
                if source.kind == "application"
                else ("External source" if self.en else "外部资料")
            )
            title = escape(source.title)
            if source.url:
                url = source.url.replace("\\", "/")
                title = f"[{title}](<{url}>)"
            authors = ", ".join(source.authors[:3])
            if len(source.authors) > 3:
                authors += ", et al." if self.en else "，等"
            details = "; ".join(filter(None, [authors, source.publication_date]))
            if details:
                title += ". " + escape(details)
            rows.append(
                f"[{number}] {title} ({kind})" if self.en else f"[{number}] {title}（{kind}）"
            )
        return "\n\n".join(rows) if used else ""

    def render(self, file, sections=None, *, missing_sections=()):
        sections = sections if sections is not None else self.doc.main
        parts = ["# Project review" if self.en else "# 项目评审"]
        if self.en and missing_sections:
            missing_titles = "; ".join(
                escape(section.title_en or section.title)
                for section in self.doc.main
                if section.section_id in missing_sections
            )
            parts.append(
                "> Partial English report. Sections not delivered: " + missing_titles
                + ". See the [Chinese report](report.zh.md) for the complete report."
            )
        parts.extend(self.section(section, file) for section in sections)
        parts.append(self.sources(sections))
        return "\n\n".join(filter(None, parts)) + "\n"

    def decision_header(self):
        if self.system.score_grades:
            if self.doc.safety_veto:
                label = "Not passed (safety veto)" if self.en else "不通过（安全否决）"
            else:
                grade = next(
                    item
                    for item in self.system.score_grades
                    if item.recommendation == self.doc.recommendation
                )
                label = grade.label_en if self.en else grade.label
            return ("**Final outcome: " if self.en else "**最终结论：") + label + "**"
        recommendations = {
            "support": ("支持", "Support"),
            "conditional_support": ("有条件支持", "Conditional support"),
            "revise": ("当前方案尚不足以支持", "The current proposal does not yet justify support"),
            "not_support": ("不支持", "Do not support"),
            "insufficient_basis": ("依据不足", "Insufficient basis"),
        }
        return (
            ("**Overall conclusion: " if self.en else "**整体结论：")
            + recommendations[self.doc.recommendation][int(self.en)]
            + "**"
        )

    def grade_table(self):
        if self.system.score_grades:
            rows = [
                "| Criterion | Score | Maximum |" if self.en else "| 评价指标 | 得分 | 满分 |",
                "| --- | ---: | ---: |",
            ]
            for item in self.system.criteria:
                label = item.label_en if self.en else item.label
                rows.append(f"| {label} | {self.doc.scores[item.id]} | {item.max_score} |")
            maximum = sum(item.max_score for item in self.system.criteria)
            rows.append(
                f"| {'Total' if self.en else '总分'} | {self.doc.total_score} | {maximum} |"
            )
            rows.extend(
                [
                    "",
                    ("Score grade: " if self.en else "分数等级：") + str(self.doc.score_grade),
                    "",
                    self.decision_header(),
                ]
            )
            return "\n".join(rows)
        rows = [
            (
                "Rating A-E: excellent, good, moderate, weak, very weak. "
                "Confidence A-E describes confidence in the assessment, not project success."
            )
            if self.en
            else (
                "评分 A-E：优秀、良好、中等、较弱、很弱。"
                "置信度 A-E 表示对判断的把握由高到低，不表示项目成功概率；"
                "依据不足的维度不评分。"
            ),
            "",
            "| Dimension | Rating | Confidence |" if self.en else "| 维度 | 评分 | 置信度 |",
            "| --- | --- | --- |",
        ]
        for item in self.system.criteria:
            row = self.doc.grades[item.id]
            empty = "Not rated" if self.en else "未评分"
            label = item.label_en if self.en else item.label
            rows.append(f"| {label} | {row['grade'] or empty} | {row['confidence'] or empty} |")
        rows.extend(["", self.decision_header()])
        return "\n".join(rows)
