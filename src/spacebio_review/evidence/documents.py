from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urljoin

from lxml import etree, html

from .discovery import parse_date
from .normalization import ConversionError, normalize_doc
from .types import Passage, SourceCandidate, SourceDocument, Visual


def clean_text(value: str) -> str:
    return " ".join(value.split())


def blocked_html(text: str) -> bool:
    lowered = text.lower()
    # Inspect page title/head and short body, not arbitrary phrases in a scientific article.
    try:
        tree = html.fromstring(text)
        title = " ".join(tree.xpath("//title/text()"))
        body = clean_text(tree.text_content())
    except (ValueError, etree.ParserError):
        return True
    markers = (
        "just a moment",
        "access denied",
        "verify you are human",
        "captcha",
        "checking your browser",
        "enable javascript and cookies",
        "sign in to continue",
    )
    return any(m in title.lower() for m in markers) or (
        len(body) < 4000 and any(m in lowered for m in markers)
    )


def add_passage(doc, text, section="", page=None, kind="text", locator=None):
    text = clean_text(text)
    if not text:
        return
    # A long paragraph is split without discarding text; neighbors retain surrounding conditions.
    for start in range(0, len(text), 2400):
        part = text[start : start + 2400]
        pid = f"p{len(doc.passages) + 1}"
        label = locator or (f"page {page} / " if page else "") + (section or "body")
        doc.passages.append(
            Passage(
                passage_id=pid,
                locator=f"{label} / {pid}",
                text=part,
                section=section,
                page=page,
                kind=kind,
            )
        )


def _xml(doc: SourceDocument, content: bytes, base_url: str):
    root = etree.fromstring(content, etree.XMLParser(resolve_entities=False, no_network=True))
    # TEI namespaces and legacy wrapped lowercase TEI both occur in OpenAlex exports.
    for node in root.iter():
        if isinstance(node.tag, str):
            node.tag = etree.QName(node).localname.lower()
    article = root.tag == "article"
    scientific = article or root.tag == "tei" or bool(root.xpath(".//tei"))
    if not scientific:
        doc.limitations.append("XML is not recognized article/TEI full text")
        return
    dates = []
    for node in root.xpath(".//article-meta/pub-date"):
        if node.findtext("year"):
            value = "-".join(
                filter(None, [node.findtext("year"), node.findtext("month"), node.findtext("day")])
            )
            parsed, precision = parse_date(value)
            if parsed:
                dates.append((parsed, precision))
    for value in root.xpath(".//publicationstmt/date/@when"):
        parsed, precision = parse_date(value)
        if parsed:
            dates.append((parsed, precision))
    if dates:
        original_date, _ = min(dates)
        doc.candidate.version_date = max(doc.candidate.version_date or original_date, original_date)
        if doc.candidate.publication_date is None:
            doc.candidate.publication_date, doc.candidate.date_precision = min(dates)
    for node in root.xpath(".//abstract//p"):
        add_passage(doc, "".join(node.itertext()), "Abstract", kind="abstract")
    bodies = root.xpath(".//body") if article else root.xpath(".//text/body | .//text")
    body = bodies[0] if bodies else None
    if body is None:
        doc.availability = "abstract" if doc.passages else "unavailable"
        return
    for node in body.iter():
        if node.tag not in {"p", "table", "caption", "figdesc"}:
            continue
        if any(a.tag in {"table", "caption", "figdesc"} for a in node.iterancestors()):
            continue
        section = "Body"
        for ancestor in node.iterancestors():
            headings = ancestor.xpath("./title | ./head")
            if headings:
                section = clean_text("".join(headings[0].itertext()))
                break
        kind = (
            "table"
            if node.tag == "table"
            else ("caption" if node.tag in {"caption", "figdesc"} else "text")
        )
        if kind == "table":
            text = " ; ".join(
                " | ".join(clean_text("".join(c.itertext())) for c in row)
                for row in node.xpath(".//tr | .//row")
            ) or " ".join(node.itertext())
        else:
            text = " ".join(node.itertext())
        add_passage(doc, text, section, kind=kind)
    for fig in root.xpath(".//fig | .//figure"):
        caption = clean_text(" ".join(fig.xpath(".//caption//text() | .//figdesc//text()")))
        graphic = fig.find(".//graphic")
        url = None
        if graphic is not None:
            href = graphic.get("url") or graphic.get("href")
            href = href or graphic.get("{http://www.w3.org/1999/xlink}href")
            if href and (
                href.startswith("http")
                or Path(href).suffix.lower() in {".jpg", ".jpeg", ".png", ".gif", ".webp"}
            ):
                # JATS graphics can be package-relative; the REST XML path isn't that package.
                if href.startswith("http") or "fullTextXML" not in base_url:
                    url = urljoin(base_url, href)
        doc.visuals.append(
            Visual(
                visual_id=f"fig{len(doc.visuals) + 1}",
                locator=fig.get("id") or "figure",
                caption=caption,
                url=url,
            )
        )
    doc.availability = (
        "full_text" if any(p.kind != "abstract" for p in doc.passages) else "abstract"
    )


def _html(doc: SourceDocument, content: bytes, base_url: str):
    import trafilatura

    text = content.decode("utf-8", errors="replace")
    if blocked_html(text):
        doc.limitations.append("blocked_or_challenge_page")
        return
    extracted = trafilatura.extract(
        text,
        output_format="xml",
        include_tables=True,
        include_images=True,
        include_comments=False,
        favor_precision=True,
    )
    if not extracted:
        doc.limitations.append("no_readable_body; dynamic_render_may_help")
        return
    root = etree.fromstring(extracted.encode())
    section = "Web body"
    for node in root.iter():
        if node.tag == "head":
            section = clean_text(" ".join(node.itertext()))
        if node.tag in {"p", "table", "item"} and not any(
            a.tag in {"p", "table", "item"} for a in node.iterancestors()
        ):
            add_passage(
                doc,
                " ".join(node.itertext()),
                section,
                kind="table"
                if node.tag == "table"
                else ("abstract" if section.strip().casefold() in {"abstract", "摘要"} else "text"),
            )
    # Keep figure originals, never promote a caption to a reading of the actual image.
    tree = html.fromstring(text)
    dates = tree.xpath(
        "//meta[@property='article:published_time' or @name='citation_publication_date']/@content"
    )
    if dates:
        original_date, precision = parse_date(dates[0].replace("/", "-"))
        if original_date:
            doc.candidate.version_date = max(
                doc.candidate.version_date or original_date, original_date
            )
        if doc.candidate.publication_date is None:
            doc.candidate.publication_date, doc.candidate.date_precision = parse_date(
                dates[0].replace("/", "-")
            )
    for fig in tree.xpath("//figure"):
        imgs = fig.xpath(".//img/@src")
        if imgs:
            doc.visuals.append(
                Visual(
                    visual_id=f"fig{len(doc.visuals) + 1}",
                    locator=fig.get("id") or "figure",
                    caption=clean_text(" ".join(fig.xpath(".//figcaption//text()"))),
                    url=urljoin(base_url, imgs[0]),
                )
            )
    if doc.passages:
        # URL entry points can be publisher pages too. Extraction alone cannot prove
        # methods/results coverage, even when discovery classified the URL as a web page.
        scholarly = (
            doc.candidate.kind == "literature"
            or bool(tree.xpath("//meta[starts-with(@name, 'citation_')]"))
            or any(p.kind == "abstract" for p in doc.passages)
        )
        if scholarly:
            doc.availability = (
                "abstract" if all(p.kind == "abstract" for p in doc.passages) else "partial"
            )
            doc.limitations.append(
                "HTML extraction: complete methods/results coverage not guaranteed"
            )
        else:
            doc.availability = "partial"
            doc.limitations.append("Extracted web content; full source coverage not confirmed")


def _docling(doc: SourceDocument, path: Path, output: Path):
    from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling_core.types.doc import PictureItem, TableItem

    options = PdfPipelineOptions()
    options.generate_page_images = True
    options.generate_picture_images = True
    options.images_scale = 1.5
    converter = DocumentConverter(
        format_options={
            # The threaded docling-parse backend can terminate Windows Python with
            # heap corruption on the retained mytest11 PDF; keep the Docling pipeline
            # and use PDFium for page decoding instead.
            InputFormat.PDF: PdfFormatOption(
                pipeline_options=options, backend=PyPdfiumDocumentBackend
            )
        }
    )
    result = converter.convert(path)
    parsed = result.document
    section = "Body"
    for item, _ in parsed.iterate_items():
        page = item.prov[0].page_no if getattr(item, "prov", None) else None
        if isinstance(item, TableItem):
            try:
                text = item.export_to_markdown(doc=parsed)
                if not text.strip():
                    raise ValueError("empty table")
                add_passage(doc, text, section, page, "table")
            except Exception:
                doc.reading_gaps.append(f"unread_table:{section}:page={page}")
        elif isinstance(item, PictureItem):
            picture = item.get_image(parsed)
            vid = f"fig{len(doc.visuals) + 1}"
            image_path = output / f"{vid}.png"
            if picture is not None:
                picture.save(image_path)
            doc.visuals.append(
                Visual(
                    visual_id=vid,
                    locator=f"page {page} / {vid}",
                    page=page,
                    caption=item.caption_text(parsed),
                    path=str(image_path.resolve()) if picture is not None else None,
                    section=section,
                )
            )
            if picture is None:
                doc.reading_gaps.append(f"unavailable_image:{vid}")
        elif hasattr(item, "text"):
            label = str(item.label)
            if label in {"section_header", "title"}:
                section = item.text
            add_passage(doc, item.text, section, page, "caption" if label == "caption" else "text")
    for number, page in parsed.pages.items():
        if page.image is not None:
            image_path = output / f"page-{number}.png"
            page.image.pil_image.save(image_path)
            doc.visuals.append(
                Visual(
                    visual_id=f"page{number}",
                    locator=f"page {number}",
                    page=number,
                    path=str(image_path.resolve()),
                )
            )
    parsed.save_as_json(output / "docling.json")
    status = getattr(result.status, "value", str(result.status))
    doc.availability = "full_text" if status == "success" and doc.passages else "partial"
    if not doc.passages:
        doc.availability = "unavailable"
    if status != "success":
        doc.reading_gaps.append("document_parse_incomplete")
    if getattr(result, "errors", None):
        doc.reading_gaps.append("document_parser_reported_errors")
    if doc.reading_gaps and doc.availability == "full_text":
        doc.availability = "partial"
    doc.limitations.append(
        "Machine document parsing; verify decisive numbers against original pages"
    )


def parse_document(
    source_id: str,
    candidate: SourceCandidate,
    path: Path,
    output: Path,
    content_type: str = "",
    url: str = "",
    legacy_config=None,
) -> SourceDocument:
    output.mkdir(parents=True, exist_ok=True)
    doc = SourceDocument(
        source_id=source_id,
        candidate=candidate.model_copy(deep=True),
        original_path=str(path.resolve()),
        acquired_url=url or None,
    )
    content = path.read_bytes()
    suffix = path.suffix.lower()
    try:
        if suffix == ".doc":
            normalized = normalize_doc(path, output, legacy_config)
            doc.normalized_path = str(normalized.resolve())
            _docling(doc, normalized, output)
            doc.limitations.append("local_DOC_conversion; layout may differ from original")
        elif content.startswith(b"%PDF") or suffix in {".pdf", ".docx"}:
            _docling(doc, path, output)
        elif "html" in content_type or suffix in {".html", ".htm"}:
            _html(doc, content, url)
        elif "xml" in content_type or suffix == ".xml":
            _xml(doc, content, url)
        elif suffix in {".md", ".txt", ".text", ".csv", ""}:
            text = content.decode("utf-8-sig")
            section = "Body"
            for paragraph in re.split(r"\n\s*\n|(?=^#{1,6}\s)", text, flags=re.M):
                if paragraph.lstrip().startswith("#"):
                    heading, _, paragraph = paragraph.partition("\n")
                    section = heading.strip().lstrip("#").strip()
                add_passage(
                    doc,
                    paragraph,
                    section,
                    kind="table" if re.search(r"^\s*\|.*\|", paragraph, re.M) else "text",
                )
            doc.availability = "full_text" if doc.passages else "unavailable"
        else:
            doc.reading_gaps.append("unsupported_document_format:" + suffix)
            doc.limitations.extend(doc.reading_gaps)
    except ConversionError as exc:
        doc.reading_gaps.append(str(exc))
        doc.limitations.append(str(exc))
    except Exception as exc:
        # Preserve acquired original even when a parser/model asset is unavailable.
        doc.availability = "partial" if doc.passages else "unavailable"
        doc.limitations.append(f"parse_failed:{type(exc).__name__}")
        doc.reading_gaps.append(f"parse_failed:{type(exc).__name__}")
    return doc


async def render_public_page(url: str) -> tuple[bytes, str]:
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
            return (await page.content()).encode("utf-8"), page.url
        finally:
            await browser.close()
