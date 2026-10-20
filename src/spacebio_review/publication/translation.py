"""Translate coherent Markdown, retaining source identities for the renderer."""

from contextlib import nullcontext
from pathlib import Path

from spacebio_review.agents.diagnostics import digest
from spacebio_review.authored_markdown import MarkdownRenderer, compile_blocks, walk_nodes
from spacebio_review.persistence import write_json

PROMPT = """你是科学项目评审报告的译者。中文评审已完成，请将给定 Markdown 完整翻译为
正式、自然的英文，直接输出译文 Markdown。保留原有标题层级、段落、列表和表格结构，
保留对象、条件、否定、判断强度、研究阶段、数字和单位。以完整句段组织英文表达。
evidence: 链接是固定来源身份，原样保留链接目标和标记，不改成数字编号；程序会统一编号。
只翻译本次正文，不重新评审、不增加建议、开场说明、文档总标题或参考文献列表。
"""


class TranslationFailure(Exception):
    def __init__(self, details):
        self.details = details
        super().__init__(details["error"])


def markdown_batches(section, limit):
    """Prefer the whole report; only long reports split at authored chapter boundaries."""
    renderer = MarkdownRenderer()
    blocks = [node.model_copy(deep=True) for node in section.blocks]
    for node in walk_nodes(blocks):
        if node.kind == "evidence":
            node.kind = "citation"
    chapters, current = [], []
    for block in blocks:
        if block.kind == "heading" and int(block.attrs["level"]) <= 2 and current:
            chapters.append(renderer.blocks(current))
            current = []
        current.append(block)
    if current:
        chapters.append(renderer.blocks(current))
    batches, current = [], ""
    for chapter in chapters:
        if current and len(current) + len(chapter) + 2 > limit:
            batches.append(current)
            current = ""
        current = "\n\n".join(filter(None, [current, chapter]))
    if current:
        batches.append(current)
    return batches


async def translate_section(
    model,
    section,
    *,
    batch_chars=18000,
    artifact_dir=None,
    prepare_requests=None,
    request_scope=None,
):
    translated = section.model_copy(deep=True)
    translated.title = section.title_en or section.title
    # Scores and system labels are generated from the same data in both languages.
    if all(node.kind == "grade_table" for node in section.blocks):
        return translated
    translated.blocks = []
    artifacts = Path(artifact_dir) if artifact_dir else None
    for index, markdown in enumerate(markdown_batches(section, batch_chars), 1):
        messages = [{"role": "system", "content": PROMPT}, {"role": "user", "content": markdown}]
        key = digest({"model": model.config.model_dump(mode="json"), "messages": messages})
        saved = {"section_id": section.section_id, "batch_index": index, "input": markdown}
        try:
            if prepare_requests:
                prepare_requests(
                    [
                        {
                            "name": "translation:author",
                            "config": model.config,
                            "messages": messages,
                        }
                    ]
                )
            with request_scope("translation") if request_scope else nullcontext():
                reply = await model.complete(messages)
            content = reply.message.get("content") or ""
            saved.update(
                content=content,
                finish_reason=reply.finish_reason,
                usage={
                    "input_tokens": reply.prompt_tokens,
                    "output_tokens": reply.completion_tokens,
                },
            )
            if reply.finish_reason != "stop" or not content.strip():
                raise ValueError("incomplete_translation:" + str(reply.finish_reason))
            blocks = compile_blocks(content)
            for node in walk_nodes(blocks):
                if node.kind == "citation":
                    node.kind = "evidence"
            translated.blocks.extend(blocks)
            saved["status"] = "completed"
        except Exception as exc:
            details = {
                "task": "translation",
                "section": section.section_id,
                "batch": key,
                "batch_index": index,
                "error": str(exc),
                "error_type": type(exc).__name__,
            }
            saved.update(status="failed", failure=details)
            raise TranslationFailure(details) from exc
        finally:
            if artifacts:
                write_json(artifacts / (key + ".json"), saved)
    return translated
