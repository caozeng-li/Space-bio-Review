"""Deterministic Markdown publication with shared numeric source references."""

import re

from .publication_document import EN_LABELS, LABELS, plain_text, walk_nodes, walk_sections


def escape(text):
    return re.sub(r"([\\`*_{}\[\]<>])", r"\\\1", text)


def appendix_label(index):
    label = ""
    while index:
        index, digit = divmod(index - 1, 26)
        label = chr(65 + digit) + label
    return label


class Renderer:
    def __init__(self, document, language="zh"):
        self.doc, self.language = document, language
        self.en = language == "en"
        self.labels = {}
        for i, topic in enumerate(document.appendix, 1):
            letter = appendix_label(i)
            self.labels[topic.section_id] = letter
            for j, section in enumerate(topic.sections, 1):
                self.labels[section.section_id] = f"{letter}.{j}"

    def inline(self, nodes, file):
        pieces, i = [], 0
        while i < len(nodes):
            node = nodes[i]
            if node.kind == "evidence":
                kind, refs = node.kind, []
                while i < len(nodes) and nodes[i].kind == kind:
                    refs.append(nodes[i].target)
                    i += 1
                    if (
                        i + 1 < len(nodes)
                        and nodes[i].kind == "text"
                        and nodes[i].text.isspace()
                        and nodes[i + 1].kind == kind
                    ):
                        i += 1
                sources = dict.fromkeys(self.doc.locations[key].source for key in refs)
                value = " ".join(
                    f"[[{self.doc.numbering[sid]}]](#ref-{self.doc.numbering[sid]})"
                    for sid in sources
                )
                pieces.append(("" if pieces and pieces[-1][-1:].isspace() else " ") + value)
                continue
            children = self.inline(node.children, file)
            kind = node.kind
            if kind == "text":
                pieces.append(escape(node.text))
            elif kind in {"softbreak", "hardbreak"}:
                pieces.append("\n" if kind == "softbreak" else "  \n")
            elif kind in {"strong", "em", "s"}:
                mark = {"strong": "**", "em": "*", "s": "~~"}[kind]
                pieces.append(mark + children + mark)
            elif kind == "code_inline":
                mark = "`" * (max((len(s) for s in re.findall(r"`+", node.text)), default=0) + 1)
                pieces.append(mark + " " + node.text + " " + mark)
            elif kind == "link":
                pieces.append(f"[{children}]({node.attrs['href']})")
            elif kind == "image":
                pieces.append(f"![{escape(plain_text(node.children))}]({node.attrs['src']})")
            elif kind == "html_inline":
                pieces.append(node.text)
            else:
                raise ValueError(f"unsupported_publication_inline:{kind}")
            i += 1
        return "".join(pieces)

    def blocks(self, nodes, file):
        pieces = []
        for node in nodes:
            if node.node_id:
                pieces.append(f'<a id="{node.node_id}"></a>')
            kind = node.kind
            if kind == "paragraph":
                pieces.append(self.inline(node.children, file))
            elif kind == "label_heading":
                labels = {
                    "before_support": ("支持前需明确", "Before a support decision"),
                    "implementation_milestone": ("实施里程碑", "Implementation milestones"),
                    "recommendation": ("一般建议", "Recommendations"),
                    "support_scope": ("支持范围", "Scope of support"),
                }
                pieces.append("### " + labels[str(node.attrs["label"])][int(self.en)])
            elif kind == "heading":
                pieces.append(
                    "#" * int(node.attrs["level"]) + " " + self.inline(node.children, file)
                )
            elif kind in {"bullet_list", "ordered_list"}:
                start = int(node.attrs.get("start", 1))
                rows = []
                for i, item in enumerate(node.children, start):
                    prefix = f"{i}. " if kind == "ordered_list" else "- "
                    text = self.blocks(item.children, file)
                    rows.append(prefix + text.replace("\n", "\n" + " " * len(prefix)))
                pieces.append("\n".join(rows))
            elif kind == "blockquote":
                pieces.append(
                    "\n".join("> " + line for line in self.blocks(node.children, file).splitlines())
                )
            elif kind in {"fence", "code_block"}:
                mark = "`" * max(
                    3, 1 + max((len(m) for m in re.findall(r"`+", node.text)), default=0)
                )
                pieces.append(
                    mark
                    + str(node.attrs.get("info", ""))
                    + "\n"
                    + node.text.rstrip("\n")
                    + "\n"
                    + mark
                )
            elif kind == "table":
                rows = []
                for group in node.children:
                    for row in group.children:
                        cells = [
                            self.inline(cell.children, file).replace("|", "\\|")
                            for cell in row.children
                        ]
                        rows.append("| " + " | ".join(cells) + " |")
                        if group.kind == "thead":
                            rows.append("| " + " | ".join("---" for _ in cells) + " |")
                pieces.append("\n".join(rows))
            elif kind == "hr":
                pieces.append("---")
            elif kind == "grade_line":
                row = self.doc.grades[str(node.attrs["aspect"])]
                empty = "Not rated" if self.en else "未评分"
                grade, confidence = row["grade"] or empty, row["confidence"] or empty
                pieces.append(
                    f"Rating: {grade}; confidence: {confidence}."
                    if self.en
                    else f"评分：{grade}；置信度：{confidence}。"
                )
            elif kind == "grade_table":
                rows = [
                    (
                        "Rating A-E: excellent, good, moderate, weak, very weak. "
                        "Confidence A-E describes confidence in the assessment, "
                        "not project success."
                    )
                    if self.en
                    else (
                        "评分 A-E：优秀、良好、中等、较弱、很弱。"
                        "置信度 A-E 表示对判断的把握由高到低，不表示项目成功概率；"
                        "依据不足的维度不评分。"
                    ),
                    "| Dimension | Rating | Confidence |"
                    if self.en
                    else "| 维度 | 评分 | 置信度 |",
                    "| --- | --- | --- |",
                ]
                for aspect, row in self.doc.grades.items():
                    empty = "Not rated" if self.en else "未评分"
                    rows.append(
                        f"| {(EN_LABELS if self.en else LABELS)[aspect]} "
                        f"| {row['grade'] or empty} | {row['confidence'] or empty} |"
                    )
                rows.insert(1, "")
                pieces.append("\n".join(rows))
            elif kind == "recommendation":
                pieces.append(self.decision_header())
            elif kind == "html_block":
                pieces.append(node.text)
            else:
                raise ValueError(f"unsupported_publication_block:{kind}")
        return "\n\n".join(pieces)

    def section(self, section, file, level=2):
        title = section.title_en if self.en and section.title_en else section.title
        parts = [f'<a id="{section.section_id}"></a>', "#" * level + " " + escape(title)]
        parts.append(self.blocks(section.blocks, file))
        for child in section.sections:
            parts.append(self.section(child, file, level + 1))
            parts.append(f"[返回议题](#{section.section_id})")
        return "\n\n".join(filter(None, parts))

    def navigation(self, file):
        if not self.doc.appendix:
            return ""
        rows = ["## Topic index (Chinese appendix)" if self.en else "## 专题论证索引"]
        for topic in self.doc.appendix:
            path = "" if file == "appendix" else "appendix.md"
            label = self.labels[topic.section_id]
            # Translate no appendix prose implicitly; its language is explicit in the heading.
            rows.append(f"- [{label}. {escape(topic.title)}]({path}#{topic.section_id})")
            for section in topic.sections:
                rows.append(
                    f"  - [{self.labels[section.section_id]}. {escape(section.title)}]"
                    f"({path}#{section.section_id})"
                )
        return "\n".join(rows)

    def sources(self, sections):
        used = {}
        for section in walk_sections(sections):
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
                title = f"[{title}](<{source.url}>)"
            details = "; ".join(filter(None, [", ".join(source.authors), source.publication_date]))
            if details:
                title += ". " + escape(details)
            rows.extend([f'<a id="ref-{number}"></a>', f"[{number}] {title}（{kind}）"])
        return "\n\n".join(rows) if used else ""

    def render(self, file, sections=None):
        sections = (
            sections
            if sections is not None
            else self.doc.appendix
            if file == "appendix"
            else self.doc.main
        )
        title = (
            "专题论证附录" if file == "appendix" else "Project review" if self.en else "项目评审"
        )
        parts = ["# " + title]
        if file == "appendix":
            parts.extend(["[返回主报告](report.md)", self.navigation(file)])
        elif self.doc.appendix:
            parts.append(
                "[Topic appendix (Chinese)](appendix.md)"
                if self.en
                else "[专题论证附录](appendix.md)"
            )
        parts.extend(self.section(section, file) for section in sections)
        if file != "appendix":
            parts.append(self.navigation(file))
        parts.append(self.sources(sections))
        return "\n\n".join(filter(None, parts)) + "\n"

    def decision_header(self):
        recommendations = {
            "support": ("支持", "Support"),
            "conditional_support": ("有条件支持", "Conditional support"),
            "revise": ("修订后重评", "Revise and reassess"),
            "not_support": ("不支持", "Do not support"),
            "insufficient_basis": ("依据不足", "Insufficient basis"),
        }
        return (
            ("**Recommendation: " if self.en else "**整体建议：")
            + recommendations[self.doc.recommendation][int(self.en)]
            + "**"
        )
