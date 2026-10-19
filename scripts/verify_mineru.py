"""Real optional MinerU SDK regression: all 12 native PDF pages -> MD -> TXT.

Run in the app environment after installing requirements-mineru.txt. This uses
flash/txt only and neither downloads inference weights nor claims OCR quality.
"""
from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

from inkreference.conversions import ConversionStore
from inkreference.documents import DocumentStore
from inkreference.ingestions import IngestionStore


def main() -> None:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfgen import canvas

    with tempfile.TemporaryDirectory(prefix="inkreference-mineru-check-") as temporary:
        root = Path(temporary)
        source = root / "native-12-pages.pdf"
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        document = canvas.Canvas(str(source))
        for number in range(1, 13):
            document.setFont("Helvetica", 14)
            document.drawString(48, 780, f"InkReference PDF page {number:02d} MARKER_{number:02d}")
            document.setFont("STSong-Light", 14)
            document.drawString(48, 740, f"第{number}章 合成测试")
            document.drawString(48, 700, "保留中文正文与章节结尾。")
            document.showPage()
        document.save()
        store = ConversionStore(root / "project")
        documents = DocumentStore(root / "project")
        ingestions = IngestionStore(root / "project", documents, store)
        try:
            job = store.create(source.name, source.read_bytes(), {"tier": "flash", "ocr_mode": "txt"})
            store.start(job["id"])
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                job = store.get(job["id"])
                if job["status"] not in {"queued", "running"}:
                    break
                time.sleep(.1)
            if job["status"] != "completed":
                raise RuntimeError(job.get("error") or "MinerU conversion timed out")
            markdown = store.get_output(job["id"], "markdown")
            plain = store.get_output(job["id"], "text")
            assert all(f"MARKER_{number:02d}" in plain for number in range(1, 13))
            assert all(f"第{number}章" in plain for number in range(1, 13))
            assert "合成测试" in plain
            assert store.get_original(job["id"])[0] == source.read_bytes()
            # Exercise the same durable pipeline as the upload GUI, including
            # automatic TXT import, chapter parsing and source retention.
            upload = ingestions.create(source.name, source.read_bytes(),
                                       conversion_options={"tier": "flash", "ocr_mode": "txt"})
            ingestions.start(upload["id"])
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                upload = ingestions.get(upload["id"])
                if upload["status"] not in {"queued", "running"}:
                    break
                time.sleep(.1)
            if upload["status"] != "completed":
                raise RuntimeError(upload.get("error") or "PDF upload pipeline timed out")
            version = documents.get_version(upload["document_id"], upload["version_id"])
            assert all(f"MARKER_{number:02d}" in version["text"] for number in range(1, 13))
            assert all(f"第{number}章" in version["text"] for number in range(1, 13))
            assert version["coverage"]["complete"] is True
            assert len(version["chapters"]) >= 12, (version["pattern"], version["text"])
            assert documents.get_original(upload["document_id"])[0] == source.read_bytes()
            assert upload["progress"] == 1
            print(json.dumps({"sdk": "MinerU 4", "tier": "flash", "ocr_mode": "txt", "pages": 12,
                              "all_page_markers": True, "chinese_preserved": True,
                              "upload_pipeline": "completed", "chapters": len(version["chapters"]),
                              "markdown_characters": len(markdown), "txt_characters": len(plain)}, ensure_ascii=False))
        finally:
            ingestions.shutdown()
            store.shutdown()


if __name__ == "__main__":
    main()
