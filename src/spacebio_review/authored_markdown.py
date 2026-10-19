"""Shared Markdown nodes, compilation and rendering, independent of report layout."""

import re

from markdown_it import MarkdownIt
from pydantic import BaseModel, ConfigDict, Field


class Node(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str
    text: str = ""
    target: str = ""
    node_id: str = ""
    attrs: dict[str, str | int] = Field(default_factory=dict)
    children: list["Node"] = Field(default_factory=list)
    origin: dict[str, str] = Field(default_factory=dict)


def walk_nodes(nodes):
    for node in nodes:
        yield node
        yield from walk_nodes(node.children)


def plain_text(nodes):
    parts = []
    for node in nodes:
        if node.kind in {"citation", "crossref"}:
            continue
        if node.kind in {"softbreak", "hardbreak"}:
            parts.append("\n")
        else:
            parts.append(node.text + plain_text(node.children))
            if node.kind in {"paragraph", "heading", "block", "scope", "list_item"}:
                parts.append("\n\n")
    return "".join(parts)


def markdown_parser():
    return MarkdownIt("commonmark", {"html": True}).enable(["table", "strikethrough"])


def _groups(tokens):
    """Each top-level Markdown structure is one selectable original-text block."""
    group, depth = [], 0
    for token in tokens:
        group.append(token)
        depth += token.nesting
        if depth == 0:
            yield group
            group = []


def numbered_blocks(markdown):
    lines = markdown.splitlines(keepends=True)
    return [
        {
            "id": f"p{index}",
            "text": "".join(lines[group[0].map[0] : group[0].map[1]]).rstrip("\r\n"),
        }
        for index, group in enumerate(_groups(markdown_parser().parse(markdown)), 1)
    ]


def _nodes(tokens, *, references=True):
    roots, stack = [], []
    for token in tokens:
        if token.nesting == -1:
            stack.pop()
            continue
        parent = stack[-1].children if stack else roots
        if token.type == "inline":
            parent.extend(_nodes(token.children or [], references=references))
            continue
        kind = token.type.removesuffix("_open")
        attrs = dict(token.attrs or {})
        target = ""
        content = token.content if not token.nesting else ""
        if kind == "heading":
            attrs["level"] = int(token.tag[1:])
        elif kind in {"bullet_list", "ordered_list"}:
            attrs["marker"] = token.markup
        elif kind == "fence":
            attrs["info"] = token.info
        if references and kind == "link" and str(attrs.get("href", "")).startswith("evidence:"):
            target = attrs.pop("href")[len("evidence:"):]
            kind = "citation"
        node = Node(
            kind=kind,
            text=content,
            target=target,
            attrs=attrs,
        )
        if token.children:
            node.children = _nodes(
                token.children, references=references and kind != "image",
            )
        if kind == "citation" and plain_text(node.children) in {target, "来源"}:
            node.children = []
        parent.append(node)
        if token.nesting == 1:
            stack.append(node)
    return roots


def compile_blocks(markdown, origin=None):
    blocks = []
    for index, group in enumerate(_groups(markdown_parser().parse(markdown)), 1):
        nodes = _nodes(group)
        for node in nodes:
            node.origin = {**(origin or {}), "paragraph_id": f"p{index}"}
        blocks.extend(nodes)
    return blocks


def escape(text):
    return re.sub(r"([\\`*_{}\[\]<>#~|])", r"\\\1", text)


def _paragraph_start(text):
    text = re.sub(r"(?m)^([ \t]{0,3})([-+])(?=\s)", r"\1\\\2", text)
    text = re.sub(r"(?m)^([ \t]{0,3}\d{1,9})([.)])(?=\s)", r"\1\\\2", text)
    return re.sub(r"(?m)^([ \t]{0,3})([=-])([=-]*[ \t]*)$", r"\1\\\2\3", text)


def _destination(value):
    return "<" + str(value).replace("<", "%3C").replace(">", "%3E").replace("|", "%7C") + ">"


def _title(attrs):
    if "title" not in attrs:
        return ""
    value = str(attrs["title"]).replace("\\", "\\\\").replace('"', '\\"')
    return f' "{value}"'


class MarkdownRenderer:
    """Render scientific structure; report layout subclasses only add layout."""

    def inline_special(self, nodes, index, file, *, table=False):
        node = nodes[index]
        if node.kind != "citation":
            return None
        label = self.inline(node.children, file, table=table)
        return f"[{label}](evidence:{node.target})", index + 1

    def inline(self, nodes, file=None, *, table=False):
        pieces, index = [], 0
        while index < len(nodes):
            special = self.inline_special(nodes, index, file, table=table)
            if special is not None:
                value, index = special
                pieces.append(value)
                continue
            node = nodes[index]
            children = self.inline(node.children, file, table=table)
            kind = node.kind
            if kind == "text":
                pieces.append(escape(node.text))
            elif kind == "literal_text":
                pieces.append(node.text)
            elif kind in {"softbreak", "hardbreak"}:
                pieces.append("\n" if kind == "softbreak" else "  \n")
            elif kind in {"strong", "em", "s"}:
                mark = {"strong": "**", "em": "*", "s": "~~"}[kind]
                pieces.append(mark + children + mark)
            elif kind == "code_inline":
                value = node.text.replace("|", "\\|") if table else node.text
                mark = "`" * (max((len(s) for s in re.findall(r"`+", value)), default=0) + 1)
                pad = " " if value.startswith("`") or value.endswith("`") else ""
                if value.startswith(" ") and value.endswith(" ") and value.strip():
                    pad = " "
                pieces.append(mark + pad + value + pad + mark)
            elif kind == "link":
                pieces.append(
                    f"[{children}]({_destination(node.attrs['href'])}{_title(node.attrs)})"
                )
            elif kind == "image":
                alt = escape(plain_text(node.children))
                pieces.append(f"![{alt}]({_destination(node.attrs['src'])}{_title(node.attrs)})")
            elif kind == "html_inline":
                pieces.append(node.text.replace("|", "\\|") if table else node.text)
            else:
                raise ValueError(f"unsupported_markdown_inline:{kind}")
            index += 1
        return "".join(pieces)

    def block_special(self, node, file):
        return None

    def blocks(self, nodes, file=None):
        pieces = []
        for node in nodes:
            special = self.block_special(node, file)
            if special is not None:
                pieces.append(special)
                continue
            kind = node.kind
            if kind == "paragraph":
                pieces.append(_paragraph_start(self.inline(node.children, file)))
            elif kind == "heading":
                pieces.append(
                    "#" * int(node.attrs["level"]) + " " + self.inline(node.children, file)
                )
            elif kind in {"bullet_list", "ordered_list"}:
                start, rows = int(node.attrs.get("start", 1)), []
                for index, item in enumerate(node.children, start):
                    marker = str(node.attrs.get("marker", "." if kind == "ordered_list" else "-"))
                    prefix = f"{index}{marker} " if kind == "ordered_list" else marker + " "
                    value = self.blocks(item.children, file)
                    rows.append(prefix + value.replace("\n", "\n" + " " * len(prefix)))
                pieces.append("\n".join(rows))
            elif kind == "blockquote":
                pieces.append(
                    "\n".join("> " + line for line in self.blocks(node.children, file).splitlines())
                )
            elif kind in {"fence", "code_block"}:
                character = "~" if "`" in str(node.attrs.get("info", "")) else "`"
                mark = character * max(
                    3,
                    1 + max((len(m) for m in re.findall(character + "+", node.text)), default=0),
                )
                value = node.text if node.text.endswith("\n") else node.text + "\n"
                pieces.append(mark + str(node.attrs.get("info", "")) + "\n" + value + mark)
            elif kind == "table":
                rows = []
                for group in node.children:
                    for row in group.children:
                        cells = [
                            self.inline(cell.children, file, table=True) for cell in row.children
                        ]
                        rows.append("| " + " | ".join(cells) + " |")
                        if group.kind == "thead":
                            alignments = {"left": ":---", "right": "---:", "center": ":---:"}
                            bars = [
                                alignments.get(
                                    str(cell.attrs.get("style", "")).removeprefix("text-align:"),
                                    "---",
                                )
                                for cell in row.children
                            ]
                            rows.append("| " + " | ".join(bars) + " |")
                pieces.append("\n".join(rows))
            elif kind == "hr":
                pieces.append("---")
            elif kind == "html_block":
                pieces.append(node.text)
            else:
                raise ValueError(f"unsupported_markdown_block:{kind}")
        return "\n\n".join(pieces)
