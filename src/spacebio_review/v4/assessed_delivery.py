"""Authored publication and resumable translation of the completed public tree."""

import json
from pathlib import Path

from spacebio_review.agents.diagnostics import digest

from .assessment import synthesize
from .publication_compile import compile_publication, compile_review_result
from .publication_document import Section
from .publication_render import Renderer
from .publication_translation import translate_section
from .records import write_json
from .report_authoring import author_report


async def deliver(caller, assessment_ref, frozen_ref, output, *, translate=True, reporting=None):
    records, output = caller.records, Path(output)
    authored_ref = await author_report(caller, frozen_ref, assessment_ref)
    document, provenance = compile_publication(
        records.value(assessment_ref),
        records.value(authored_ref)["sections"],
        records.value(frozen_ref),
    )
    manifest_ref = records.put(
        "manifest",
        {
            "format_version": document.format_version,
            "assessment": assessment_ref,
            "frozen_review": frozen_ref,
            "authored_report": authored_ref,
            "document": document.model_dump(),
            "audit": provenance,
        },
        [assessment_ref, frozen_ref, authored_ref],
    )
    return await _publish_document(
        caller, document, manifest_ref, output, translate=translate, reporting=reporting
    )


async def deliver_review_result(caller, result_ref, output, *, translate=True, reporting=None):
    """Deliver the complete two-step candidate without calling a second scientific author."""
    records = caller.records
    document, provenance = compile_review_result(records, result_ref)
    manifest_ref = records.put(
        "manifest",
        {
            "format_version": document.format_version,
            "review_result": result_ref,
            "document": document.model_dump(),
            "audit": provenance,
        },
        [result_ref],
    )
    return await _publish_document(
        caller, document, manifest_ref, output, translate=translate, reporting=reporting
    )


async def _publish_document(caller, document, manifest_ref, output, *, translate, reporting):
    records, output = caller.records, Path(output)
    fingerprint = digest([manifest_ref, caller.config.model_dump(mode="json")])
    state = {
        "format_version": "delivery-3",
        "manifest": manifest_ref,
        "work_fingerprint": fingerprint,
        "status": "partial",
        "translations": {},
        "technical_limits": [],
    }
    path = output / "delivery.json"
    if path.exists():
        previous = json.loads(path.read_text("utf-8"))
        if previous.get("work_fingerprint") == fingerprint:
            state["translations"] = previous.get("translations", {})

    def export():
        output.mkdir(parents=True, exist_ok=True)
        for role, name in (("zh", "report.md"), ("appendix", "appendix.md")):
            if role == "appendix" and not document.appendix:
                (output / name).unlink(missing_ok=True)
                continue
            temporary = output / (name + ".tmp")
            temporary.write_text(Renderer(document).render(role), "utf-8")
            temporary.replace(output / name)
        missing = [s.section_id for s in document.main if s.section_id not in state["translations"]]
        state.update(
            zh={"status": "completed"},
            appendix={"status": "completed" if document.appendix else "not_needed"},
            en={
                "status": "completed"
                if not missing
                else "partial"
                if state["translations"]
                else "not_run"
            },
        )
        state["status"] = (
            "completed" if not missing else "local_completed" if not translate else "partial"
        )
        documents = [
            {
                "role": role,
                "language": language,
                "path": filename,
                "status": state[role]["status"],
                "missing_sections": missing if role == "en" else [],
            }
            for role, language, filename in (
                ("zh", "zh", "report.md"),
                ("appendix", "zh", "appendix.md"),
                ("en", "en", "report.en.md"),
            )
        ]
        state["paths"] = {
            row["role"]: row["path"]
            for row in documents
            if row["status"] not in {"not_run", "not_needed"}
        }
        state["publication"] = {
            "format_version": document.format_version,
            "status": state["status"],
            "documents": documents,
            "missing_sections": missing,
        }
        if state["translations"]:
            sections = [
                Section.model_validate(state["translations"][s.section_id])
                for s in document.main
                if s.section_id in state["translations"]
            ]
            text = Renderer(document, "en").render("en", sections)
            temporary = output / "report.en.md.tmp"
            temporary.write_text(text, "utf-8")
            temporary.replace(output / "report.en.md")
        else:
            (output / "report.en.md").unlink(missing_ok=True)
        write_json(path, state)

    export()
    if translate:
        for section in document.main:
            if section.section_id not in state["translations"]:
                try:
                    translated = await translate_section(
                        caller,
                        section,
                        batch_chars=reporting.material_batch_chars if reporting else 18000,
                    )
                except Exception as exc:
                    state["technical_limits"].append(
                        f"{section.section_id}: {type(exc).__name__}: {exc}"
                    )
                    export()
                    break
                state["translations"][section.section_id] = translated.model_dump()
                export()
    return records.put("delivery", state, [manifest_ref])


async def run_downstream(caller, frozen_ref, output, *, translate=True, reporting=None):
    assessment_ref = await synthesize(caller, frozen_ref)
    delivery_ref = await deliver(
        caller, assessment_ref, frozen_ref, output, translate=translate, reporting=reporting
    )
    return assessment_ref, delivery_ref
