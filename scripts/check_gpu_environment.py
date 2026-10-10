"""Small local CUDA and production PDF-parser smoke check; no remote model calls."""

import logging
from pathlib import Path
from tempfile import TemporaryDirectory

import torch
import torchvision
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.utils.accelerator_utils import decide_device
from reportlab.pdfgen.canvas import Canvas

from spacebio_review.evidence.documents import parse_document
from spacebio_review.evidence.types import SourceCandidate


def main():
    logging.basicConfig(level=logging.INFO)
    assert torch.cuda.is_available(), "CUDA unavailable in this Python environment"
    print("torch:", torch.__version__, "torchvision:", torchvision.__version__, flush=True)
    print("GPU:", torch.cuda.get_device_name(0), flush=True)
    device = decide_device(PdfPipelineOptions().accelerator_options.device)
    assert device.startswith("cuda"), device
    matrix = torch.eye(32, device=device)
    assert torch.equal(matrix @ matrix, matrix)
    boxes = torch.tensor([[0, 0, 10, 10], [1, 1, 9, 9]], device=device, dtype=torch.float32)
    scores = torch.tensor([0.9, 0.8], device=device)
    assert torchvision.ops.nms(boxes, scores, 0.5).tolist() == [0]
    del matrix, boxes, scores
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()
    with TemporaryDirectory(prefix="spacebio-gpu-") as folder:
        root = Path(folder)
        pdf = root / "probe.pdf"
        canvas = Canvas(str(pdf))
        canvas.setFont("Helvetica-Bold", 18)
        canvas.drawString(60, 760, "SpaceBioReview GPU parser check")
        canvas.setFont("Helvetica", 12)
        canvas.drawString(60, 720, "A one-page local document used to verify layout inference.")
        canvas.drawString(60, 690, "The project parser must retain this sentence.")
        canvas.save()
        doc = parse_document(
            "gpu-smoke",
            SourceCandidate(title="GPU parser check", kind="application"),
            pdf,
            root / "parsed",
        )
        assert doc.availability == "full_text", (doc.availability, doc.reading_gaps)
        assert any("retain this sentence" in p.text for p in doc.passages)
        peak = torch.cuda.max_memory_allocated()
        assert peak > 0, "PDF parser did not allocate CUDA tensors"
        print("PDF:", doc.availability, "passages:", len(doc.passages), flush=True)
        print("Parser peak CUDA allocation MiB:", round(peak / 1024**2, 1), flush=True)
    print("PASS CUDA computation, torchvision CUDA op and production PDF parsing", flush=True)


if __name__ == "__main__":
    main()
