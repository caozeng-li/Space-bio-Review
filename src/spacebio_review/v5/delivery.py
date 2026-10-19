"""Publish approved scientific prose first, then optionally translate the public main text."""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

from spacebio_review.agents.diagnostics import digest
from spacebio_review.models.provider import ModelError
from spacebio_review.models.scheduling import ScheduledModel
from spacebio_review.persistence import write_json
from spacebio_review.publication import Renderer, compile_publication
from spacebio_review.publication.document import Section
from spacebio_review.publication.translation import (
    PROMPT,
    TranslationFailure,
    translate_section,
)

from .budget import CompletionReserve, SessionAllowance, exhausted


def _write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


async def deliver_assessment(
    runtime,
    assessment,
    *,
    evidence,
    report_selection,
    contributions,
    author_sources=None,
    admission=None,
    english=False,
    translation_model=None,
    batch_chars=18000,
    root=None,
):
    """Return actual output status; disabled English has neither requests nor output path."""
    output = Path(root) if root is not None else runtime.store.root.parent
    document, audit = compile_publication(
        assessment, evidence=evidence, report_selection=report_selection,
        contributions=contributions, author_sources=author_sources, admission=admission,
    )
    manifest = {
        "assessment": assessment,
        "document": document.model_dump(mode="json"),
        "audit": audit,
        "report_selection": report_selection,
        "contributions": list(contributions),
        "author_sources": author_sources or {},
        "evidence": evidence,
        "admission": admission or {},
    }
    manifest_ref = "publication-" + digest(manifest)
    manifest_path = output / "v5" / "publications" / (manifest_ref + ".json")
    if not manifest_path.exists():
        write_json(manifest_path, manifest)
    fingerprint = digest(
        {
            "manifest": manifest_ref,
            "english": english,
            "translation": translation_model.config.model_dump(mode="json")
            if english and translation_model is not None
            else None,
            "batch_chars": batch_chars,
            "translation_instructions": PROMPT if english else None,
        }
    )
    path = output / "v5" / "delivery.json"
    state = {
        "manifest": str(manifest_path.relative_to(output)),
        "work_fingerprint": fingerprint,
        "scientific": {"status": "completed"},
        "english_enabled": english,
        "translations": {},
        "technical_limits": [],
    }
    def export():
        _write_text(output / "report.zh.md", Renderer(document).render("zh"))
        if document.appendix:
            _write_text(output / "appendix.md", Renderer(document).render("appendix"))
        else:
            (output / "appendix.md").unlink(missing_ok=True)
        missing = (
            [
                section.section_id
                for section in document.main
                if section.section_id not in state["translations"]
            ]
            if english
            else []
        )
        state.update(
            zh={"status": "completed"},
            appendix={"status": "completed" if document.appendix else "not_needed"},
            en={
                "status": "disabled"
                if not english
                else "completed"
                if not missing
                else "partial"
                if state["translations"]
                else "failed"
                if state["technical_limits"]
                else "not_run"
            },
            status="partial" if missing else "completed",
        )
        documents = [
            {"role": "zh", "language": "zh", "path": "report.zh.md", "status": "completed"}
        ]
        if document.appendix:
            documents.append(
                {"role": "appendix", "language": "zh", "path": "appendix.md", "status": "completed"}
            )
        if english and state["translations"]:
            sections = [
                Section.model_validate(state["translations"][section.section_id])
                for section in document.main
                if section.section_id in state["translations"]
            ]
            _write_text(
                output / "report.en.md",
                Renderer(document, "en").render("en", sections, missing_sections=missing),
            )
            documents.append(
                {
                    "role": "en",
                    "language": "en",
                    "path": "report.en.md",
                    "status": state["en"]["status"],
                    "missing_sections": missing,
                }
            )
        else:
            (output / "report.en.md").unlink(missing_ok=True)
        state["paths"] = {row["role"]: row["path"] for row in documents}
        state["publication"] = {
            "status": state["status"],
            "documents": documents,
            "missing_sections": missing,
        }
        write_json(path, state)

    export()
    if not english or state["en"]["status"] == "completed":
        return state
    if translation_model is None:
        state["technical_limits"].append(
            {
                "task": "translation",
                "error": "translation_model_missing",
            }
        )
        export()
        return state

    owner = "translation-" + fingerprint[:24]
    model = ScheduledModel(translation_model, runtime.scheduler, "translation")

    def prepare_requests(requests):
        costs = [
            runtime.budget.measure(
                row["config"], row["messages"], response_schema=row.get("response_schema")
            )
            for row in requests
        ]
        reason = runtime.budget.update_completion(
            owner,
            CompletionReserve(
                calls=len(costs),
                steps=[row["name"] for row in requests],
                input_tokens=sum(cost["input_tokens"] for cost in costs),
                output_tokens=sum(cost["output_reserve"] for cost in costs),
            ),
        )
        if reason:
            raise exhausted(reason)

    try:
        runtime.budget.register(owner, SessionAllowance(completion_calls=0))
        runtime.budget.activate(owner)
        with (
            runtime.budget.scope(owner, purpose="completion"),
            runtime.scheduler.task(owner, "translation", "delivery"),
        ):
            for section in document.main:
                try:
                    remaining = runtime.budget.remaining_seconds(
                        session_id=owner, purpose="completion"
                    )
                    if remaining <= 0:
                        raise exhausted("elapsed_time_budget_exhausted")
                    async with asyncio.timeout(remaining):
                        translated = await translate_section(
                            model,
                            section,
                            batch_chars=batch_chars,
                            artifact_dir=output / "v5" / "translations",
                            prepare_requests=prepare_requests,
                            request_scope=lambda role: runtime.scheduler.task(
                                owner, role, "delivery"
                            ),
                        )
                    state["translations"][section.section_id] = translated.model_dump(mode="json")
                except Exception as exc:
                    state["technical_limits"].append(
                        exc.details
                        if isinstance(exc, TranslationFailure)
                        else {
                            "task": "translation",
                            "section": section.section_id,
                            "error": str(exc) if isinstance(exc, ModelError)
                            else type(exc).__name__,
                        }
                    )
                    cause = exc.__cause__ if isinstance(exc, TranslationFailure) else exc
                    if (
                        isinstance(cause, ModelError) and cause.category == "budget"
                    ) or runtime.budget.remaining_seconds(
                        session_id=owner, purpose="completion"
                    ) <= 0:
                        break
                finally:
                    runtime.budget.update_completion(owner, CompletionReserve())
                    export()
    except Exception as exc:
        state["technical_limits"].append(
            exc.details
            if isinstance(exc, TranslationFailure)
            else {
                "task": "translation",
                "error": str(exc) if isinstance(exc, ModelError) else type(exc).__name__,
            }
        )
        export()
    finally:
        if owner in runtime.budget.data["sessions"]:
            runtime.budget.finish(owner)
    return state
