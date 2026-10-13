"""Deterministic Markdown publication, numeric sources and canonical cross references."""

import re

from .publication import EN_LABELS, LABELS
from .publication_document import plain_text, walk_nodes, walk_sections


def escape(text):
    return re.sub(r"([\\`*_{}\[\]<>])", r"\\\1", text)


def appendix_label(index):
    label = ""
    while index:
        index, digit = divmod(index - 1, 26)
        label = chr(65 + digit) + label
    return label


class Renderer:
    def __init__(self, document, language="zh", *, available_main=None):
        self.doc, self.language = document, language
        self.en = language == "en"
        self.labels, self.sections = {}, {}
        self.available_main = available_main
        for i, section in enumerate(document.main, 1):
            self.labels[section.section_id] = str(i)
        for i, topic in enumerate(document.appendix, 1):
            letter = appendix_label(i)
            self.labels[topic.section_id] = letter
            for j, section in enumerate(topic.sections, 1):
                self.labels[section.section_id] = f"{letter}.{j}"
        self.sections = {s.section_id: s for s in walk_sections(document.main + document.appendix)}

    def crossref(self, key, file):
        target = self.doc.targets[key]
        number = self.labels[target.section]
        if target.document == "appendix":
            label = ("Appendix " if self.en else "附录 ") + number
            path = "appendix.md" if file != "appendix" else ""
        else:
            label = f"Section {number}" if self.en else f"第 {number} 节"
            translated = self.en and (
                self.available_main is None or target.section in self.available_main
            )
            filename = "report.en.md" if translated else "report.md"
            path = "" if file == ("en" if translated else "zh") else filename
        if target.paragraph:
            label += f", paragraph {target.paragraph}" if self.en else f"，第 {target.paragraph} 段"
        return f"[{label}]({path}#{target.anchor})"

    def inline(self, nodes, file):
        pieces, i = [], 0
        while i < len(nodes):
            node = nodes[i]
            if node.kind in {"evidence", "crossref"}:
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
                if kind == "evidence":
                    sources = dict.fromkeys(self.doc.locations[key].source for key in refs)
                    value = " ".join(
                        f"[[{self.doc.numbering[sid]}]](#ref-{self.doc.numbering[sid]})"
                        for sid in sources
                    )
                    pieces.append(("" if pieces and pieces[-1][-1:].isspace() else " ") + value)
                else:
                    prefix = "see " if self.en else "参见"
                    pieces.append(
                        (" (" if self.en else "（")
                        + prefix
                        + ("; " if self.en else "；").join(
                            self.crossref(key, file) for key in dict.fromkeys(refs)
                        )
                        + (")" if self.en else "）")
                    )
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

    def blocks(self, nodes, file, *, scope_anchor=None):
        pieces = []
        for node in nodes:
            if node.node_id:
                pieces.append(f'<a id="{node.node_id}"></a>')
            kind = node.kind
            if kind in {"block", "scope"}:
                if kind == "scope":
                    pieces.append("**Scope and limitations**" if self.en else "**适用范围与限制**")
                pieces.append(self.blocks(node.children, file))
                if kind == "block" and scope_anchor:
                    label = "Scope and limitations" if self.en else "适用范围与限制"
                    pieces.append(f"[{label}](#{scope_anchor})")
            elif kind == "paragraph":
                pieces.append(self.inline(node.children, file))
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
            elif kind == "html_block":
                pieces.append(node.text)
            else:
                raise ValueError(f"unsupported_publication_block:{kind}")
        return "\n\n".join(pieces)

    def section(self, section, file, level=2):
        label = self.labels.get(section.section_id, "")
        title = section.title_en if self.en and section.title_en else section.title
        title = (label + ". " if label else "") + title
        parts = [f'<a id="{section.section_id}"></a>', "#" * level + " " + escape(title)]
        scope = next((n.node_id for n in section.blocks if n.kind == "scope"), None)
        parts.append(
            self.blocks(section.blocks, file, scope_anchor=scope if file == "appendix" else None)
        )
        for child in section.sections:
            parts.append(self.section(child, file, level + 1))
            parts.append(f"[返回议题](#{section.section_id})")
        return "\n\n".join(filter(None, parts))

    def navigation(self, file):
        rows = ["## Professional analyses (Chinese)" if self.en else "## 专业分析附录"]
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
        rows = ["## References" if self.en else "## 参考来源"]
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
            for loc in used[sid].values():
                scope = (
                    {
                        "metadata": "Metadata only",
                        "abstract": "Abstract only",
                        "text": "Read text",
                        "full_text": "Full text",
                    }.get(loc.reading_scope, loc.reading_scope)
                    if self.en
                    else {
                        "metadata": "仅元数据",
                        "abstract": "仅摘要",
                        "text": "已读文本",
                        "full_text": "全文",
                    }.get(loc.reading_scope, loc.reading_scope)
                )
                rows.append("- " + escape("；".join(filter(None, [loc.locator, scope]))))
        return "\n\n".join(rows) if used else ""

    def render(self, file, sections=None, *, summary="", missing=()):
        sections = (
            sections
            if sections is not None
            else self.doc.appendix
            if file == "appendix"
            else self.doc.main
        )
        title = (
            "专业分析附录" if file == "appendix" else "Project review" if self.en else "项目评审"
        )
        parts = ["# " + title]
        if file == "appendix":
            parts.extend(["[返回主报告](report.md)", self.navigation(file)])
        elif summary:
            parts.extend(
                [
                    "## Decision summary" if self.en else "## 决策摘要",
                    self.decision_header(),
                    summary,
                ]
            )
        parts.extend(self.section(section, file) for section in sections)
        if file != "appendix":
            parts.append(self.navigation(file))
        parts.append(self.sources(sections))
        if missing:
            parts.append("## Incomplete delivery" if self.en else "## 交付未完成项")
            parts.extend("- " + escape(item) for item in missing)
        return "\n\n".join(filter(None, parts)) + "\n"

    def decision_header(self):
        recommendations = {
            "support": ("支持", "Support"),
            "conditional_support": ("有条件支持", "Conditional support"),
            "revise": ("修订后重评", "Revise and reassess"),
            "not_support": ("不支持", "Do not support"),
            "insufficient_basis": ("依据不足", "Insufficient basis"),
        }
        statuses = {
            "assessed": ("已形成专业评审", "professionally assessed"),
            "partially_assessed": ("仅部分问题形成专业评审", "partially assessed"),
            "unassessed": ("未形成专业评审", "not professionally assessed"),
            "not_applicable": ("不适用", "not applicable"),
        }
        language = int(self.en)
        groups = {}
        for aspect, item in self.doc.coverage.items():
            status = "not_applicable" if item.applicability == "not_applicable" else item.status
            groups.setdefault(status, []).append((EN_LABELS if self.en else LABELS)[aspect])
        rows = [
            ("**Recommendation: " if self.en else "**整体建议：")
            + recommendations[self.doc.recommendation][language]
            + "**",
            "**Actual coverage**" if self.en else "**实际评审覆盖**",
        ]
        rows.extend(
            "- "
            + (", " if self.en else "、").join(aspects)
            + (": " if self.en else "：")
            + statuses[status][language]
            for status, aspects in groups.items()
        )
        return "\n\n".join(rows)
