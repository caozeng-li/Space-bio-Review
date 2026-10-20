from __future__ import annotations

import re
from hashlib import sha256
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from lxml import etree, html

from .discovery import parse_date
from .material_limits import MaterialLimits, MaterialTooLarge
from .normalization import ConversionError, normalize_doc
from .types import Passage, SourceCandidate, SourceDocument, SourceMaterial, Visual


def clean_text(value: str) -> str:
    return " ".join(value.split())


def add_material(doc, *, url="", title="", role="main", package_member=""):
    if url:
        parts = urlsplit(url)
        query = parts.query
        if parts.path.lower().endswith(".pdf"):
            query = urlencode(
                [
                    (key, value)
                    for key, value in parse_qsl(query, keep_blank_values=True)
                    if key.casefold() not in {"targetid", "page", "nameddest"}
                ]
            )
        url = urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))
    identity = "\n".join((role, url, package_member or (title if not url else "")))
    material_id = "m-" + sha256(identity.encode()).hexdigest()[:12]
    existing = next((m for m in doc.materials if m.material_id == material_id), None)
    if existing:
        return existing
    material = SourceMaterial(
        material_id=material_id, role=role, title=title, url=url, package_member=package_member
    )
    doc.materials.append(material)
    return material


def _html_materials(doc, tree, base_url):
    for link in tree.xpath(
        "//meta[@name='citation_pdf_url']/@content"
        " | //link[@rel='alternate' and (@type='application/pdf' or @type='text/xml')]/@href"
    ):
        add_material(doc, url=urljoin(base_url, link), title="Article material")
    for anchor in tree.xpath("//a[@href]"):
        href = anchor.get("href", "")
        if not href or href.startswith("#"):
            continue
        label = clean_text(" ".join(anchor.itertext()))
        containers = [anchor, *list(anchor.iterancestors())]
        structure = " ".join(
            node.get("id", "") + " " + node.get("class", "") for node in containers
        )
        if re.search(r"\b(?:ref-list|references?|bibliography)\b", structure, re.I):
            continue
        headings = " ".join(
            " ".join(node.xpath("./h2//text() | ./h3//text() | ./h4//text()"))
            for node in containers
            if node.tag in {"section", "aside"}
        )
        context = " ".join((label, anchor.get("title", ""), structure, headings))
        supplement = bool(
            re.search(
                r"suppl(?:ement)?|supporting\s+(?:information|material)|additional\s+file"
                r"|\bSI\s+(?:Materials|Methods)|\b(?:Fig(?:ure)?|Table)\.?\s*S\d",
                context,
                re.I,
            )
        ) or bool(re.search(r"/suppl(?:ement)?|DCSupplemental|/bin/|SI\.pdf", href, re.I))
        main_download = bool(
            re.match(r"^(?:(?:download|view|read)\s+)?(?:pdf\b|full[ -]text\b)", label, re.I)
        )
        if supplement or main_download:
            add_material(
                doc,
                url=urljoin(base_url, href),
                title=label or Path(href).name,
                role="supplement" if supplement else "main",
            )


def _xml_materials(doc, root, base_url):
    pmcid = doc.candidate.identifiers.get("pmcid")
    for node in root.xpath(".//self-uri"):
        href = node.get("href") or node.get("{http://www.w3.org/1999/xlink}href")
        if href and ("pdf" in (node.get("content-type") or "") or href.endswith(".pdf")):
            if href.startswith("http") or "fullTextXML" not in base_url:
                add_material(doc, url=urljoin(base_url, href), title="Article PDF")
    for node in root.xpath(".//supplementary-material"):
        title = clean_text(" ".join(node.xpath("./label//text() | ./caption//text()")))
        title = title or node.get("id") or "Supplementary material"
        links = node.xpath(".//@*[local-name()='href']")
        for href in links or [""]:
            if href and not href.startswith("http") and "fullTextXML" in base_url:
                url = (
                    f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/supplementaryFiles"
                    if pmcid
                    else ""
                )
                add_material(doc, url=url, title=title, role="supplement", package_member=href)
            else:
                add_material(
                    doc,
                    url=urljoin(base_url, href) if href else "",
                    title=title,
                    role="supplement",
                )


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


def add_passage(
    doc, text, section="", page=None, kind="text", locator=None, *, heading_path=None, unit_id=None
):
    """Retain a native block; the reader, not a character slicer, chooses its range."""
    text = text.strip()
    if not text:
        return
    pid = f"p{len(doc.passages) + 1}"
    label = locator or (f"page {page} / " if page else "") + (section or "body")
    path = list(heading_path) if heading_path is not None else ([section] if section else [])
    doc.passages.append(
        Passage(
            passage_id=pid,
            locator=f"{label} / {pid}",
            text=text,
            section=section,
            heading_path=path,
            page=page,
            kind=kind,
            unit_id=unit_id or pid,
        )
    )
    return pid


def reading_passages(doc):
    """Project native text with callable images from the same material and page."""
    visuals = {visual.visual_id: visual for visual in doc.visuals}
    original_pages = {}
    for material in doc.materials:
        pages = {}
        for visual_id in material.visual_ids:
            visual = visuals.get(visual_id)
            if visual is not None and visual.page is not None:
                pages.setdefault(visual.page, []).append({
                    "visual_id": visual.visual_id, "page": visual.page, "locator": visual.locator,
                })
        for passage_id in material.passage_ids:
            original_pages[passage_id] = pages
    rows = []
    for passage in doc.passages:
        row = passage.model_dump()
        pages = original_pages.get(passage.passage_id, {}).get(passage.page, [])
        if pages:
            row["original_pages"] = pages
        rows.append(row)
    return rows


def _table_parts(headers, groups, caption="", notes=""):
    """Pack complete native row groups, repeating table context on continuation.

    The size target controls row-group packing, never a cut inside a row/cell.
    Merged cells stay in their original group; very large single rows remain whole.
    """
    prefix = "\n\n".join(part for part in (caption.strip(), "\n".join(headers)) if part)
    suffix = notes.strip()
    rows = []
    size = len(prefix) + len(suffix)
    for group in groups:
        group_size = sum(len(row) + 1 for row in group)
        if rows and size + group_size > 8000:
            table = "\n".join(part for part in (prefix, "\n".join(rows)) if part)
            yield "\n\n".join(part for part in (table, suffix) if part)
            rows, size = [], len(prefix) + len(suffix)
        rows.extend(group)
        size += group_size
    if rows or prefix:
        table = "\n".join(part for part in (prefix, "\n".join(rows)) if part)
        yield "\n\n".join(part for part in (table, suffix) if part)


def _cell_text(node):
    return clean_text(" ".join(node.itertext())).replace("|", "\\|")


def _dom_table(node):
    """Expand native rowspan/colspan so every displayed row keeps its labels."""
    row_nodes = [
        row
        for row in node.xpath(".//tr | .//row")
        if not any(a.tag == "tfoot" for a in row.iterancestors())
    ]
    matrix = []
    occupied = {}
    row_ends = []
    header_count = 0
    for index, row in enumerate(row_nodes):
        values = {}
        for (r, column), value in list(occupied.items()):
            if r == index:
                values[column] = value
        column = 0
        end = index + 1
        cells = [c for c in row if c.tag in {"td", "th", "cell"}]
        is_header = bool(cells) and (
            all(c.tag == "th" or c.get("role") == "head" for c in cells)
            or row.get("role") in {"head", "label"}
            or any(a.tag == "thead" for a in row.iterancestors())
        )
        if is_header and header_count == index:
            header_count += 1
        for cell in cells:
            while column in values:
                column += 1
            span_rows = max(1, int(cell.get("rowspan", "1")))
            span_cols = max(1, int(cell.get("colspan", "1")))
            value = _cell_text(cell)
            for r in range(index, index + span_rows):
                for c in range(column, column + span_cols):
                    occupied[r, c] = value
                    if r == index:
                        values[c] = value
            end = max(end, index + span_rows)
            column += span_cols
        matrix.append(values)
        row_ends.append(end)
    if not matrix:
        return [], []
    width = max((max(row, default=-1) + 1 for row in matrix), default=0)
    rendered = ["| " + " | ".join(row.get(c, "") for c in range(width)) + " |" for row in matrix]
    # Markdown needs a header separator; blank headings do not invent column names.
    headers = rendered[:header_count] or ["| " + " | ".join("" for _ in range(width)) + " |"]
    headers.append("| " + " | ".join("---" for _ in range(width)) + " |")
    groups = []
    index = header_count
    while index < len(rendered):
        end = row_ends[index]
        cursor = index
        while cursor < min(end, len(rendered)):
            end = max(end, row_ends[cursor])
            cursor += 1
        end = min(end, len(rendered))
        groups.append(rendered[index:end])
        index = end
    return headers, groups


def _add_table(
    doc, headers, groups, *, caption="", notes="", heading_path=(), page=None, unit_id=None
):
    unit_id = unit_id or f"p{len(doc.passages) + 1}"
    for part in _table_parts(headers, groups, caption, notes):
        add_passage(
            doc,
            part,
            heading_path[-1] if heading_path else "",
            page,
            "table",
            heading_path=heading_path,
            unit_id=unit_id,
        )


def _ancestor_headings(node):
    path = []
    for ancestor in reversed(list(node.iterancestors())):
        headings = ancestor.xpath("./title | ./head")
        if headings:
            path.append(clean_text(" ".join(headings[0].itertext())))
    return path


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
    _xml_materials(doc, root, base_url)
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
    for abstract in root.xpath(".//abstract"):
        unit = f"p{len(doc.passages) + 1}"
        for node in abstract.xpath(".//p"):
            add_passage(
                doc,
                clean_text(" ".join(node.itertext())),
                "Abstract",
                kind="abstract",
                unit_id=unit,
            )
    bodies = root.xpath(".//body") if article else root.xpath(".//text/body | .//text")
    body = bodies[0] if bodies else None
    if body is None:
        doc.availability = "abstract" if doc.passages else "unavailable"
        return
    for node in body.iter():
        if node.tag not in {
            "p",
            "table",
            "caption",
            "figdesc",
            "list",
            "def-list",
            "title",
            "head",
        }:
            continue
        if any(
            a.tag in {"table", "caption", "figdesc", "list", "def-list", "table-wrap-foot"}
            for a in node.iterancestors()
        ):
            continue
        if node.tag in {"caption", "figdesc"} and any(
            a.tag == "table-wrap" or (a.tag == "figure" and a.find("table") is not None)
            for a in node.iterancestors()
        ):
            continue
        path = _ancestor_headings(node)
        section = path[-1] if path else ""
        unit = f"p{len(doc.passages) + 1}"
        if node.tag == "table":
            wrappers = [a for a in node.iterancestors() if a.tag in {"table-wrap", "figure"}]
            wrapper = wrappers[0] if wrappers else node
            caption = clean_text(
                " ".join(
                    wrapper.xpath(
                        "./label//text() | ./caption//text() | ./head//text() | ./figdesc//text()"
                    )
                )
            )
            notes = "\n".join(
                clean_text(" ".join(n.itertext()))
                for n in wrapper.xpath("./table-wrap-foot | ./note")
            )
            _add_table(doc, *_dom_table(node), caption=caption, notes=notes, heading_path=path)
            continue
        if node.tag in {"title", "head"}:
            if node.getparent().tag not in {"sec", "div", "body"}:
                continue
            kind = "heading"
        elif node.tag in {"list", "def-list"}:
            kind = "list"
            unit = f"p{len(doc.passages) + 1}"
        else:
            kind = "caption" if node.tag in {"caption", "figdesc"} else "text"
        text = (
            "\n".join(
                "- " + clean_text(" ".join(n.itertext()))
                for n in node.xpath("./list-item | ./item | ./def-item")
            )
            if kind == "list"
            else clean_text(" ".join(node.itertext()))
        )
        add_passage(doc, text, section, kind=kind, heading_path=path, unit_id=unit)
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
    text = content.decode("utf-8", errors="replace")
    if blocked_html(text):
        doc.limitations.append("blocked_or_challenge_page")
        return
    tree = html.fromstring(text)
    _html_materials(doc, tree, base_url)
    containers = tree.xpath("//main | //article")
    # Consume original DOM blocks once. Article extraction normalizes away table
    # spans and captions, while a tag-only walker loses ordinary div text/tails.
    body = tree.find("body")
    root = containers[0] if containers else (body if body is not None else tree)
    headings = []
    ignored = {"head", "nav", "footer", "script", "style", "noscript", "template"}
    inline = {
        "a", "abbr", "b", "bdi", "bdo", "cite", "code", "data", "del", "dfn", "em",
        "i", "ins", "kbd", "mark", "q", "s", "samp", "small", "span", "strong", "sub",
        "sup", "time", "u", "var", "wbr",
    }

    def inline_text(node):
        if not isinstance(node.tag, str) or node.tag in ignored or node.get("role") == "navigation":
            return ""
        parts = [node.text or ""]
        for child in node:
            parts.append("\n" if child.tag == "br" else inline_text(child))
            parts.append(child.tail or "")
        return "".join(parts)

    def add_text(value, kind="text"):
        path = [value for _, value in headings]
        section = path[-1] if path else ""
        if kind == "text" and section.casefold() in {"abstract", "摘要"}:
            kind = "abstract"
        add_passage(doc, value, section, kind=kind, heading_path=path)

    def walk(node):
        nonlocal headings
        if not isinstance(node.tag, str) or node.tag in ignored or node.get("role") == "navigation":
            return
        if re.fullmatch(r"h[1-6]", node.tag):
            level = int(node.tag[1])
            headings = [(n, value) for n, value in headings if n < level]
            headings.append((level, clean_text(inline_text(node))))
            add_text(headings[-1][1], "heading")
            return
        path = [value for _, value in headings]
        if node.tag == "table":
            caption = clean_text(" ".join(node.xpath("./caption//text()")))
            footers = node.xpath("./tfoot")
            notes = "\n".join(clean_text(" ".join(n.itertext())) for n in footers)
            _add_table(doc, *_dom_table(node), caption=caption, notes=notes, heading_path=path)
        elif node.tag in {"ul", "ol", "dl"}:
            lines = []
            for index, item in enumerate(node.xpath("./li | ./dt | ./dd"), 1):
                marker = f"{index}." if node.tag == "ol" else "-" if node.tag != "dl" else ""
                lines.append(marker + " " + clean_text(" ".join(item.itertext())))
            add_text("\n".join(lines), "list")
        elif node.tag in {"p", "figcaption", "pre", "blockquote"}:
            kind = "caption" if node.tag == "figcaption" else "text"
            value = (
                inline_text(node).strip()
                if node.tag == "pre"
                else clean_text(inline_text(node))
            )
            add_text(value, kind)
        else:
            parts = [node.text or ""]
            for child in node:
                if child.tag in inline:
                    parts.append(inline_text(child))
                elif child.tag == "br":
                    parts.append("\n")
                else:
                    add_text(clean_text("".join(parts)))
                    parts = []
                    walk(child)
                parts.append(child.tail or "")
            add_text(clean_text("".join(parts)))

    walk(root)
    if not doc.passages:
        doc.limitations.append("no_readable_body; dynamic_render_may_help")
    # Keep figure originals, never promote a caption to a reading of the actual image.
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

    options = PdfPipelineOptions()
    options.heading_hierarchy_options.enabled = True
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
    append_docling_document(doc, parsed, output)
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


def _docling_reading_order(parsed):
    """Flatten PDF layout, keeping native parameter areas together.

    Tree traversal visits all children of a list before intervening parameter
    groups. Use Docling's own page/column ordering on layout blocks instead.
    Within a native key-value area, read physical rows with their values.
    Unpaginated documents keep their native tree order.
    """
    from docling.models.postprocessing.reading_order_rb import PageElement, ReadingOrderPredictor
    from docling_core.types.doc import BoundingBox, CoordOrigin

    items = [item for item, _ in parsed.iterate_items()]
    if not items or not all(getattr(item, "prov", None) for item in items):
        return items
    blocks = {}
    for item in items:
        parent = item.parent.resolve(parsed) if item.parent else None
        key = (parent.self_ref if str(getattr(parent, "label", "")) == "key_value_area"
               else item.self_ref, item.prov[0].page_no)
        blocks.setdefault(key, []).append(item)
    elements, children = [], list(blocks.values())
    for i, group in enumerate(children):
        page = parsed.pages[group[0].prov[0].page_no]
        boxes = [item.prov[0].bbox.to_top_left_origin(page.size.height) for item in group]
        box = BoundingBox(l=min(b.l for b in boxes), r=max(b.r for b in boxes),
                          t=min(b.t for b in boxes), b=max(b.b for b in boxes),
                          coord_origin=CoordOrigin.TOPLEFT)
        elements.append(PageElement(
            cid=i, ref=group[0].get_ref(), page_no=page.page_no, page_size=page.size,
            label=group[0].label, **box.to_bottom_left_origin(page.size.height).model_dump(),
        ))
        rows = []
        for item, bounds in sorted(zip(group, boxes), key=lambda pair: pair[1].t):
            row = next((row for row in rows if
                        min(row[0][1].b, bounds.b) - max(row[0][1].t, bounds.t)
                        >= min(row[0][1].height, bounds.height) / 2), None)
            if row is None:
                rows.append([(item, bounds)])
            else:
                row.append((item, bounds))
        children[i] = [item for row in rows for item, _ in sorted(row, key=lambda pair: pair[1].l)]
    return [item for element in ReadingOrderPredictor().predict_reading_order(elements)
            for item in children[element.cid]]


def append_docling_document(doc: SourceDocument, parsed, output: Path):
    """Convert native Docling items, also usable with a retained Docling JSON input."""
    from docling_core.types.doc import PictureItem, TableItem

    output.mkdir(parents=True, exist_ok=True)
    headings = []
    last_page = None
    last_path = None
    unit = None
    table_text_refs = {
        ref.cref for table in parsed.tables for ref in [*table.captions, *table.footnotes]
    }
    for item in _docling_reading_order(parsed):
        page = item.prov[0].page_no if getattr(item, "prov", None) else None
        # Layout order alone is page-local. Explicit native parent relationships
        # remain authoritative across pages and are resolved independently below.
        if page is not None and page != last_page:
            headings, unit = [], None
            last_page = page
        parent = item.parent.resolve(parsed) if getattr(item, "parent", None) else None
        if isinstance(parent, TableItem) or item.self_ref in table_text_refs:
            # Captions/footnotes belong to the table export below, only once.
            continue
        ancestors = []
        ancestor = parent
        while ancestor is not None:
            if str(getattr(ancestor, "label", "")) in {"section_header", "title"}:
                ancestors.append(clean_text(ancestor.text))
            ancestor = (
                ancestor.parent.resolve(parsed) if getattr(ancestor, "parent", None) else None
            )
        path = list(reversed(ancestors)) if ancestors else [value for _, value in headings]
        section = path[-1] if path else ""
        if path != last_path:
            unit = None
        last_path = path
        if isinstance(item, TableItem):
            try:
                grid = item.data.grid
                if not grid:
                    raise ValueError("empty table")
                rows = [
                    "| "
                    + " | ".join(clean_text(cell.text).replace("|", "\\|") for cell in row)
                    + " |"
                    for row in grid
                ]
                header_count = 0
                for row in grid:
                    if not any(cell.column_header for cell in row):
                        break
                    header_count += 1
                headers = rows[:header_count] or ["| " + " | ".join("" for _ in grid[0]) + " |"]
                headers.append("| " + " | ".join("---" for _ in grid[0]) + " |")
                notes = "\n".join(ref.resolve(parsed).text for ref in item.footnotes)
                # grid expands merged cells. Repeating their text retains the
                # row context when a long table continues at a row boundary.
                _add_table(
                    doc,
                    headers,
                    [[row] for row in rows[header_count:]],
                    caption=item.caption_text(parsed),
                    notes=notes,
                    heading_path=path,
                    page=page,
                )
            except Exception:
                doc.reading_gaps.append(f"unread_table:{section}:page={page}")
            unit = None
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
                level = getattr(item, "level", 1)
                headings = [(n, value) for n, value in headings if n < level]
                headings.append((level, clean_text(item.text)))
                path = (
                    [*reversed(ancestors), clean_text(item.text)]
                    if ancestors else [value for _, value in headings]
                )
                section = path[-1]
                last_path = path
                unit = None
            if unit is None:
                unit = f"p{len(doc.passages) + 1}"
            kind = (
                "heading"
                if label in {"section_header", "title"}
                else "list"
                if label == "list_item"
                else "caption"
                if label == "caption"
                else "text"
            )
            add_passage(doc, item.text, section, page, kind, heading_path=path, unit_id=unit)
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


def document_from_docling(parsed, source_id, candidate, output: Path, *, original_path=None):
    """Rebuild derived reading material in a new directory from a native document."""
    doc = SourceDocument(
        source_id=source_id, candidate=candidate.model_copy(deep=True), original_path=original_path
    )
    append_docling_document(doc, parsed, output)
    doc.availability = "partial" if doc.passages or doc.visuals else "unavailable"
    doc.limitations.append("Machine document parsing; original page layout retained")
    return doc


def _markdown(doc, text):
    from marko import Markdown, block
    from marko.ext.footnote import FootnoteDef, FootnoteRef
    from marko.ext.gfm.elements import Table
    from marko.md_renderer import MarkdownRenderer

    # Marko source spans address its CommonMark-normalized newline stream.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    parser = Markdown(extensions=["gfm", "footnote"], renderer=MarkdownRenderer)
    parsed = parser.parse(text)
    headings = []

    def references(element):
        if isinstance(element, FootnoteRef):
            yield element.label
        children = getattr(element, "children", None)
        if isinstance(children, list):
            for child in children:
                yield from references(child)

    attached_notes = {
        label for item in parsed.children if not isinstance(item, FootnoteDef)
        for label in references(item)
    }
    for item in parsed.children:
        if isinstance(item, (block.BlankLine, block.LinkRefDef, block.ThematicBreak)):
            continue
        if isinstance(item, FootnoteDef) and item.label in attached_notes:
            continue
        start, stop = item.source_span
        original = text[start:stop].strip()
        notes = "\n\n".join(
            text[slice(*parsed.footnotes[label].source_span)].strip()
            for label in dict.fromkeys(references(item))
        )
        if isinstance(item, (block.Heading, block.SetextHeading)):
            level = item.level
            value = parser.renderer.render_children(item)
            headings = [(n, value) for n, value in headings if n < level]
            headings.append((level, value))
            unit = f"p{len(doc.passages) + 1}"
            path = [value for _, value in headings]
            value = "\n\n".join(part for part in (value, notes) if part)
            add_passage(doc, value, path[-1], kind="heading", heading_path=path, unit_id=unit)
            continue
        path = [value for _, value in headings]
        section = path[-1] if path else ""
        unit = f"p{len(doc.passages) + 1}"
        if isinstance(item, Table):
            rows = original.splitlines()
            _add_table(
                doc, rows[:2], [[row] for row in rows[2:]],
                notes=notes, heading_path=path, unit_id=unit,
            )
        else:
            kind = "list" if isinstance(item, block.List) else "text"
            value = "\n\n".join(part for part in (original, notes) if part)
            add_passage(doc, value, section, kind=kind, heading_path=path, unit_id=unit)


def parse_document(
    source_id: str,
    candidate: SourceCandidate,
    path: Path,
    output: Path,
    content_type: str = "",
    url: str = "",
    legacy_config=None,
    material_limits=None,
) -> SourceDocument:
    output.mkdir(parents=True, exist_ok=True)
    doc = SourceDocument(
        source_id=source_id,
        candidate=candidate.model_copy(deep=True),
        original_path=str(path.resolve()),
        acquired_url=url or None,
    )
    limits = material_limits or MaterialLimits()
    suffix = path.suffix.lower()
    try:
        if candidate.kind != "application":
            limits.inspect(path, content_type)
        content = path.read_bytes()
        if suffix == ".doc":
            normalized = normalize_doc(path, output, legacy_config)
            doc.normalized_path = str(normalized.resolve())
            if candidate.kind != "application":
                limits.inspect(normalized)
            _docling(doc, normalized, output)
            doc.limitations.append("local_DOC_conversion; layout may differ from original")
        elif content.startswith(b"%PDF") or suffix in {".pdf", ".docx"}:
            _docling(doc, path, output)
        elif "html" in content_type or suffix in {".html", ".htm"}:
            _html(doc, content, url)
        elif "xml" in content_type or suffix == ".xml":
            _xml(doc, content, url)
        elif content_type.startswith("image/") or suffix in {
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".webp",
            ".tif",
            ".tiff",
        }:
            doc.visuals.append(
                Visual(visual_id="fig1", locator=path.name, path=str(path.resolve()))
            )
            doc.availability = "partial"
        elif suffix in {".md", ".txt", ".text", ".csv", ""}:
            text = content.decode("utf-8-sig")
            if suffix == ".md":
                _markdown(doc, text)
            else:
                for paragraph in re.split(r"\n\s*\n", text):
                    add_passage(doc, paragraph)
            doc.availability = "full_text" if doc.passages else "unavailable"
        else:
            doc.reading_gaps.append("unsupported_document_format:" + suffix)
            doc.limitations.extend(doc.reading_gaps)
    except MaterialTooLarge as exc:
        doc.size_exclusion = exc.exclusion
        doc.limitations.append(str(exc))
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
