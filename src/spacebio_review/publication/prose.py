"""Parse authored Markdown while retaining the already bound citation identities."""

import re

from markdown_it import MarkdownIt

from spacebio_review.agents.diagnostics import digest
from spacebio_review.prose import cited_ids as cited_ids
from spacebio_review.prose import prose_type as prose_type

from .document import Node


def prose_nodes(prose):
    blocks = []
    for paragraph in prose["paragraphs"]:
        references = {}
        marker = "@[publication-" + digest(paragraph)[:20] + ":"
        text = ""
        for part in paragraph["parts"]:
            text += part["text"]
            for key in part["cites"]:
                token = marker + str(len(references)) + "]"
                references[token] = key
                text += token

        def citation(state, silent):
            if not state.src.startswith(marker, state.pos):
                return False
            end = state.src.find("]", state.pos + len(marker)) + 1
            value = state.src[state.pos:end]
            if value not in references:
                return False
            if not silent:
                token = state.push("publication_citation", "", 0)
                token.meta = {"target": references[value]}
            state.pos = end
            return True

        parser = MarkdownIt("commonmark", {"html": True}).enable(["table", "strikethrough"])
        parser.inline.ruler.before("text", "publication_citation", citation)
        blocks.extend(_nodes(parser.parse(text), references))
    return blocks


def _literal_nodes(text, references):
    """Code and raw HTML keep literal text, with citations as separate tree nodes."""
    pattern = "(" + "|".join(re.escape(key) for key in references) + ")"
    return [
        Node(kind="citation", target=references[value])
        if value in references else Node(kind="literal_text", text=value)
        for value in re.split(pattern, text) if value
    ]


def _nodes(tokens, references):
    roots, stack = [], []
    for token in tokens:
        if token.nesting == -1:
            stack.pop()
            continue
        parent = stack[-1].children if stack else roots
        if token.type == "inline":
            parent.extend(_nodes(token.children or [], references))
            continue
        kind = token.type.removesuffix("_open")
        attrs = dict(token.attrs or {})
        if kind == "heading":
            attrs["level"] = int(token.tag[1:])
        if kind == "fence":
            attrs["info"] = token.info
        if kind == "publication_citation":
            node = Node(kind="citation", target=token.meta["target"])
        else:
            node = Node(kind=kind, text=token.content if not token.nesting else "", attrs=attrs)
            if token.children:
                node.children = _nodes(token.children, references)
            elif references and any(key in node.text for key in references):
                node.children = _literal_nodes(node.text, references)
                node.text = ""
        parent.append(node)
        if token.nesting == 1:
            stack.append(node)
    return roots
