"""Cheap admission of external originals before document parsing or model reading."""

from pathlib import Path
from zipfile import ZipFile

from lxml import etree, html
from pydantic import BaseModel, ConfigDict, Field


class SizeExclusion(BaseModel):
    metric: str
    actual: int
    limit: int

    def reason(self):
        return f"material_size_limit_exceeded:{self.metric}:{self.actual}>{self.limit}"


class MaterialTooLarge(ValueError):
    def __init__(self, exclusion):
        self.exclusion = exclusion
        super().__init__(exclusion.reason())


class MaterialLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    max_pdf_pages: int | None = Field(default=60, gt=0)
    max_text_chars: int | None = Field(default=120000, gt=0)
    max_file_mb: float | None = Field(default=20, gt=0)

    @property
    def max_bytes(self):
        return int(self.max_file_mb * 1024 * 1024) if self.enabled and self.max_file_mb else None

    def check(self, metric, actual, limit):
        if self.enabled and limit is not None and actual > limit:
            raise MaterialTooLarge(SizeExclusion(metric=metric, actual=actual, limit=limit))

    def inspect(self, path: Path, content_type=""):
        if not self.enabled:
            return
        self.check("file_bytes", path.stat().st_size, self.max_bytes)
        with path.open("rb") as stream:
            pdf = stream.read(4) == b"%PDF"
        suffix = path.suffix.lower()
        if pdf or suffix == ".pdf":
            if self.max_pdf_pages is not None:
                import pypdfium2

                with pypdfium2.PdfDocument(path) as document:
                    self.check("pdf_pages", len(document), self.max_pdf_pages)
            return
        if self.max_text_chars is None:
            return
        if suffix == ".docx":
            count = 0
            with ZipFile(path) as archive:
                for name in archive.namelist():
                    if name.startswith("word/") and name.endswith(".xml"):
                        self.check("file_bytes", archive.getinfo(name).file_size, self.max_bytes)
                        tree = etree.fromstring(
                            archive.read(name),
                            etree.XMLParser(
                                resolve_entities=False,
                                no_network=True,
                            ),
                        )
                        count += sum(
                            len(text) for text in tree.xpath("//*[local-name()='t']/text()")
                        )
        elif "html" in content_type or suffix in {".html", ".htm"}:
            tree = html.fromstring(path.read_bytes())
            for node in tree.xpath("//script|//style"):
                node.drop_tree()
            count = len(tree.text_content())
        elif "xml" in content_type or suffix == ".xml":
            tree = etree.fromstring(
                path.read_bytes(),
                etree.XMLParser(
                    resolve_entities=False,
                    no_network=True,
                ),
            )
            count = sum(len(text) for text in tree.itertext())
        elif suffix in {".txt", ".text", ".md", ".csv", ""} or content_type.startswith("text/"):
            count = len(path.read_text("utf-8-sig"))
        else:
            return
        self.check("text_chars", count, self.max_text_chars)
