"""Direct, shallow scientific decisions and Markdown fields for the chief."""

from __future__ import annotations

from .assembly import array, choices, record


def section_index(nodes):
    from spacebio_review.authored_markdown import Node, plain_text

    result, stack = {}, []
    for value in nodes:
        node = Node.model_validate(value)
        pid = node.origin["paragraph_id"]
        if node.kind == "heading":
            level = int(node.attrs["level"])
            while stack and result[stack[-1]]["level"] >= level:
                stack.pop()
            result[pid] = {
                "section_id": pid,
                "title": plain_text(node.children).strip(),
                "level": level,
                "body_ids": [],
            }
            for parent in stack:
                result[parent]["body_ids"].append(pid)
            stack.append(pid)
        else:
            for parent in stack:
                result[parent]["body_ids"].append(pid)
    return result


def authored_sections(paragraphs):
    from spacebio_review.authored_markdown import compile_blocks

    nodes = []
    texts = {block["id"]: block["text"] for block in paragraphs}
    for block in paragraphs:
        for node in compile_blocks(block["text"]):
            node.origin["paragraph_id"] = block["id"]
            nodes.append(node.model_dump(mode="json"))
    return [
        {**section, "body": "\n\n".join(texts[pid] for pid in section["body_ids"])}
        for section in section_index(nodes).values()
    ]


def decision_schema(system, paragraphs, *, candidates=()):
    """Select whole authored sections; their internal boundaries belong to the program."""
    sections = [
        section["section_id"] for section in authored_sections(paragraphs) if section["body_ids"]
    ]
    if not sections:
        raise ValueError("assessment_markdown_sections_required")
    blocks = choices(sections)
    dimensions = {}
    for criterion in system.criteria:
        fields = {"body": blocks}
        if system.score_grades:
            fields["score"] = {"type": "integer", "minimum": 0, "maximum": criterion.max_score}
        else:
            grade = {"anyOf": [choices("ABCDE"), {"type": "null"}]}
            fields.update(grade=grade, confidence=grade)
        dimensions[criterion.id] = record(fields)
    fields = {
        "project": {**blocks, "description": "仅项目理解或项目概况这一章节，不汇集其他评价章节。"},
        "scope": {**blocks, "description": "仅审议范围这一章节，说明本次评价的对象与范围。"},
        "dimensions": record(dimensions),
        "unknowns": array(blocks),
    }
    if system.score_grades:
        fields["safety_veto"] = record({"applies": {"type": "boolean"}, "reason": blocks})
    else:
        fields.update(
            support_scope=blocks,
            recommendation=choices(
                ["support", "conditional_support", "revise", "not_support", "insufficient_basis"]
            ),
        )
    if candidates:
        fields.update(
            main_sections={**array(choices(candidates)), "minItems": 1},
            appendix_sections=array(choices(candidates)),
        )
    return record(fields)


def compile_fields(values, body):
    """Place exact authored blocks into report fields; scalar values are copied."""
    from spacebio_review.prose import select_authored_body

    sections = section_index(body["blocks"])

    def select(section):
        return select_authored_body(body, sections[section]["body_ids"])

    result = {}
    for key, value in values.items():
        if key in {"project", "scope", "support_scope"}:
            result[key] = select(value)
        elif key == "dimensions":
            result[key] = {
                criterion: {**item, "body": select(item["body"])}
                for criterion, item in value.items()
            }
        elif key == "unknowns":
            result[key] = [
                {"title": sections[section]["title"], "text": select(section)} for section in value
            ]
        elif key == "safety_veto":
            result[key] = {**value, "reason": select(value["reason"])}
        elif key not in {"main_sections", "appendix_sections"}:
            result[key] = value
    return result


def field_sources(values, author_text_ref):
    def source(path):
        return {"author_text_ref": author_text_ref, "field_path": path}

    return {
        key: {criterion: source(f"dimensions.{criterion}.body") for criterion in value}
        if key == "dimensions"
        else source("safety_veto.reason" if key == "safety_veto" else key)
        for key, value in values.items()
        if key not in {"main_sections", "appendix_sections"}
    }
