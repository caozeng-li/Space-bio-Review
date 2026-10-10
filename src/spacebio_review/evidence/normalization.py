"""Isolated, local legacy Word conversion; originals never modified."""

import os
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import BaseModel, Field


class LegacyDocumentConfig(BaseModel):
    executable: str = "soffice"
    timeout_seconds: float = Field(default=90, gt=0)
    pdf_fallback: bool = True


class ConversionError(Exception):
    pass


def normalize_doc(path: Path, output: Path, config=None, *, runner=None) -> Path:
    config = config or LegacyDocumentConfig()
    executable = shutil.which(config.executable)
    if not executable:
        raise ConversionError("converter_unavailable")
    runner = runner or subprocess.run
    output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="spacebio-doc-") as temp:
        work = Path(temp)
        original = work / "input.doc"
        shutil.copyfile(path, original)
        formats = ["docx", "pdf"] if config.pdf_fallback else ["docx"]
        for fmt in formats:
            # Every conversion has its own user profile; an existing office window
            # cannot swallow this request or share another task's intermediate files.
            argv = [
                executable,
                "-env:UserInstallation=" + (work / "profile").as_uri(),
                "--headless",
                "--convert-to",
                fmt,
                "--outdir",
                str(work),
                str(original),
            ]
            try:
                result = runner(
                    argv,
                    capture_output=True,
                    timeout=config.timeout_seconds,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            except subprocess.TimeoutExpired as exc:
                raise ConversionError("converter_timeout") from exc
            except OSError as exc:
                raise ConversionError("converter_unavailable") from exc
            converted = work / ("input." + fmt)
            if result.returncode == 0 and converted.is_file() and converted.stat().st_size:
                content = converted.read_bytes()
                if not content.startswith(b"PK" if fmt == "docx" else b"%PDF"):
                    continue
                saved = output / ("normalized." + fmt)
                saved.write_bytes(content)
                return saved
    raise ConversionError("conversion_failed_or_damaged_document")
