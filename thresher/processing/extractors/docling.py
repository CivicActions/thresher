"""Docling document extraction via subprocess isolation.

Runs docling conversion in a child process to prevent native memory leaks
from accumulating in the main runner process. The child process uses
posix_spawn/vfork+exec, not fork(), ensuring native memory from libpdfium,
ONNX runtime, and PyTorch is fully reclaimed by the OS when the child exits.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger("thresher.extractors.docling")

# Worker script executed in subprocess
_AUDIO_EXTENSIONS = frozenset({".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac"})
_VIDEO_EXTENSIONS = frozenset({".mp4", ".avi", ".mov"})
_MEDIA_EXTENSIONS = _AUDIO_EXTENSIONS | _VIDEO_EXTENSIONS

_WORKER_SCRIPT = """
import json
import logging
import sys
from pathlib import Path

# Suppress all logging in subprocess
logging.disable(logging.CRITICAL)

def main():
    args_path = sys.argv[1]
    with open(args_path, "r") as f:
        args = json.load(f)

    input_path = args["input_path"]
    output_path = args["output_path"]
    error_path = args["error_path"]
    max_pages = args.get("max_pages", 500)
    ocr_enabled = args.get("ocr_enabled", True)
    ocr_lang = args.get("ocr_lang", ["eng"])

    try:
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.datamodel.pipeline_options import PdfPipelineOptions, TesseractOcrOptions
        from docling.datamodel.base_models import InputFormat

        AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac"}
        VIDEO_EXTS = {".mp4", ".avi", ".mov"}
        suffix = Path(input_path).suffix.lower()

        if suffix in AUDIO_EXTS or suffix in VIDEO_EXTS:
            # Audio/video extraction via ASR pipeline
            from docling.datamodel import asr_model_specs
            from docling.datamodel.pipeline_options import AsrPipelineOptions
            from docling.document_converter import AudioFormatOption
            from docling.pipeline.asr_pipeline import AsrPipeline

            asr_options = AsrPipelineOptions()
            asr_options.asr_options = asr_model_specs.WHISPER_TURBO

            converter = DocumentConverter(
                allowed_formats=[InputFormat.AUDIO],
                format_options={
                    InputFormat.AUDIO: AudioFormatOption(
                        pipeline_cls=AsrPipeline,
                        pipeline_options=asr_options,
                    ),
                },
            )
        else:
            # Document/image extraction
            pipeline_options = PdfPipelineOptions()
            pipeline_options.do_ocr = ocr_enabled
            if ocr_enabled:
                pipeline_options.ocr_options = TesseractOcrOptions(lang=ocr_lang)

            converter = DocumentConverter(
                allowed_formats=[
                    InputFormat.PDF, InputFormat.DOCX, InputFormat.XLSX,
                    InputFormat.PPTX, InputFormat.HTML, InputFormat.IMAGE,
                    InputFormat.ASCIIDOC, InputFormat.MD, InputFormat.CSV,
                ],
                format_options={
                    InputFormat.PDF: PdfFormatOption(
                        pipeline_options=pipeline_options,
                    ),
                },
            )

        result = converter.convert(input_path, max_num_pages=max_pages)
        doc = result.document

        # Export markdown
        markdown = doc.export_to_markdown()

        # Serialize document for cache
        doc_json = doc.model_dump_json()

        output = {"markdown": markdown, "document_json": doc_json}
        with open(output_path, "w") as f:
            json.dump(output, f)
    except Exception as e:
        with open(error_path, "w") as f:
            f.write(str(e))
        sys.exit(1)

main()
"""


def extract_with_docling(
    file_path: Path,
    timeout: int = 600,
    max_pages: int = 500,
    ocr_enabled: bool = True,
    ocr_lang: list[str] | None = None,
) -> tuple[str, str | None]:
    """Extract document content using docling in a subprocess.

    Args:
        file_path: Path to the document file
        timeout: Maximum seconds for conversion
        max_pages: Maximum pages to process
        ocr_enabled: Whether to run OCR on scanned pages and images
        ocr_lang: Tesseract language codes (e.g. ["eng"])

    Returns:
        Tuple of (markdown_text, document_json_or_none)

    Raises:
        TimeoutError: If conversion exceeds timeout
        RuntimeError: If conversion fails
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        args_path = tmpdir_path / "args.json"
        output_path = tmpdir_path / "output.json"
        error_path = tmpdir_path / "error.txt"

        args = {
            "input_path": str(file_path),
            "output_path": str(output_path),
            "error_path": str(error_path),
            "max_pages": max_pages,
            "ocr_enabled": ocr_enabled,
            "ocr_lang": ocr_lang or ["eng"],
        }
        args_path.write_text(json.dumps(args))

        proc = subprocess.Popen(
            [sys.executable, "-c", _WORKER_SCRIPT, str(args_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=False,
        )

        try:
            returncode = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise TimeoutError(f"Docling conversion timed out after {timeout}s: {file_path}")

        if returncode != 0:
            error_msg = "Unknown error"
            if error_path.exists():
                error_msg = error_path.read_text().strip()
            raise RuntimeError(f"Docling conversion failed for {file_path}: {error_msg}")

        output = json.loads(output_path.read_text())
        return output["markdown"], output.get("document_json")
