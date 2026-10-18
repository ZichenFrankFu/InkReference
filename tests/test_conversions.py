"""Real EPUB/Markdown conversion and synthetic PDF worker lifecycle contracts.

PDF SDK boundary tests substitute a worker deliberately; a successful synthetic
worker is not a MinerU/OCR quality test. Optional live MinerU smoke is separate.
"""

from __future__ import annotations

import io
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

import pytest

import inkreference.conversions as conversion_module
from inkreference.conversions import ConversionStore, markdown_to_text
from inkreference.database import DataError
from inkreference.documents import DocumentStore


def epub_bytes():
    files = [
        ("META-INF/container.xml", '<container><rootfiles><rootfile full-path="OPS/book.opf"/></rootfiles></container>'),
        ("OPS/book.opf", '<package><manifest><item id="b" href="b.xhtml"/><item id="a" href="a.xhtml"/></manifest><spine><itemref idref="a"/><itemref idref="b"/></spine></package>'),
        ("OPS/b.xhtml", '<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>第二章 尾声</h1><p>最后的中文尾标。</p></body></html>'),
        ("OPS/a.xhtml", '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>不要导入标题栏</title></head><body><h1>第一章 初见</h1><p>甲<strong>发现</strong>乙，保留&lt;秘密&gt;与 a*b。</p><p><code>a_b &lt; c &amp; d</code></p><script>不要导入脚本</script>脚本后的正文。</body></html>'),
    ]
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in files:
            archive.writestr(name, text)
    return output.getvalue()


def wait_for(store, identifier, statuses=None, timeout=5):
    statuses = statuses or {"completed", "failed", "cancelled", "interrupted"}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        meta = store.get(identifier)
        if meta["status"] in statuses:
            thread = store._threads.get(identifier)
            if thread is not None and meta["status"] != "running":
                thread.join(timeout=2)
            return meta
        time.sleep(0.01)
    raise AssertionError(f"conversion did not reach {statuses}: {store.get(identifier)}")


def test_real_epub_to_markdown_to_text_and_import_is_idempotent(tmp_path):
    store = ConversionStore(tmp_path)
    raw = epub_bytes()
    job = store.create("参考书.epub", raw, {"title": "版本化参考书", "work_id": "W-SYNTHETIC"})
    assert job["status"] == "queued" and job["outputs"] == {"markdown": False, "text": False}
    assert store.get_original(job["id"]) == (raw, "参考书.epub")
    with pytest.raises(DataError) as caught:
        store.get_output(job["id"], "text")
    assert caught.value.status_code == 409
    store.start(job["id"])
    done = wait_for(store, job["id"])
    assert done["status"] == "completed", done
    assert done["progress"] == 1 and done["completed"] == done["total"] == 3
    markdown = store.get_output(job["id"], "markdown")
    plain = store.get_output(job["id"], "text")
    assert "# 第一章 初见" in markdown and "**发现**" in markdown
    assert "# 第一章" not in plain and "甲发现乙" in plain
    assert "a_b < c & d" in plain and "a*b" in plain
    assert plain.index("第一章") < plain.index("第二章")
    assert plain.endswith("最后的中文尾标。")
    assert "脚本后的正文。" in plain
    assert "不要导入" not in plain
    assert done["text_characters"] == len(plain)
    documents = DocumentStore(tmp_path)
    imported = store.import_to_document(job["id"], documents)
    assert imported["title"] == "版本化参考书" and imported["work_id"] == "W-SYNTHETIC"
    assert documents.get_original(imported["id"])[0] == plain.encode("utf-8")
    assert store.import_to_document(job["id"], documents)["id"] == imported["id"]
    assert len(documents.list_documents()) == 1
    reopened = ConversionStore(tmp_path)
    assert reopened.get_output(job["id"], "text") == plain
    assert reopened.get(job["id"])["imported_document_id"] == imported["id"]


def test_markdown_parser_preserves_literal_prose_code_tables_and_image_alt():
    markdown = """# 标题
正文 *强调*、[标签](https://example.invalid) 与 a*b、x_y、<秘密>。

```python
name = 'a_b*[原文]'
print('<不是HTML删除>')
```

| 角色 | 描述 |
| --- | --- |
| 林舟 | 说“甲&乙” |

![配图文字](image.png)

结束尾标。
"""
    plain = markdown_to_text(markdown)
    for expected in ("标题", "正文 强调、标签", "a*b、x_y、<秘密>", "name = 'a_b*[原文]'",
                     "print('<不是HTML删除>')", "林舟", "说“甲&乙”", "配图文字", "结束尾标。"):
        assert expected in plain
    assert "```" not in plain and "https://example.invalid" not in plain


def test_invalid_epub_fails_with_persistent_error_and_no_fake_output(tmp_path):
    store = ConversionStore(tmp_path)
    job = store.create("损坏.epub", b"not a zip")
    store.start(job["id"])
    result = wait_for(store, job["id"])
    assert result["status"] == "failed" and result["error"]
    assert len(result["errors"]) == 1
    assert result["outputs"] == {"markdown": False, "text": False}
    assert ConversionStore(tmp_path).get(job["id"])["error"] == result["error"]
    with pytest.raises(DataError):
        store.import_to_document(job["id"], DocumentStore(tmp_path))


def test_restart_marks_running_job_interrupted_and_retry_runs_original(tmp_path):
    store = ConversionStore(tmp_path)
    job = store.create("中断.epub", epub_bytes())
    with sqlite3.connect(store.db_path) as connection:
        connection.execute("UPDATE conv_jobs SET status='running',attempt=1 WHERE id=?", (job["id"],))
    reopened = ConversionStore(tmp_path)
    assert reopened.get(job["id"])["status"] == "interrupted"
    assert reopened.get(job["id"])["errors"][0]["attempt"] == 1
    reopened.retry(job["id"])
    done = wait_for(reopened, job["id"])
    assert done["status"] == "completed" and done["attempt"] == 2
    assert done["errors"]


def test_cancel_queued_then_retry_keeps_immutable_original(tmp_path):
    store = ConversionStore(tmp_path)
    raw = epub_bytes()
    job = store.create("取消.epub", raw)
    assert store.cancel(job["id"])["status"] == "cancelled"
    store.retry(job["id"])
    assert wait_for(store, job["id"])["status"] == "completed"
    with sqlite3.connect(store.db_path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE conv_jobs SET original=? WHERE id=?", (b"modified", job["id"]))
    assert store.get_original(job["id"])[0] == raw


@pytest.mark.parametrize("options", [{"python": "untrusted.exe"}, {"tier": []}, {"tier": "advanced"},
                                     {"ocr_mode": "auto"}, {"tier": "flash", "ocr_mode": "ocr"}, []])
def test_conversion_options_cannot_select_programs_or_invalid_modes(tmp_path, options):
    store = ConversionStore(tmp_path)
    with pytest.raises(DataError) as caught:
        store.create("测试.pdf", b"%PDF-synthetic", options)
    assert caught.value.status_code == 422
    assert store.list() == []


def test_pdf_missing_runtime_fails_and_does_not_claim_success(tmp_path, monkeypatch):
    store = ConversionStore(tmp_path)
    monkeypatch.setenv("INKREFERENCE_MINERU_PYTHON", str(tmp_path / "missing-python"))
    job = store.create("本地.pdf", b"%PDF-synthetic")
    store.start(job["id"])
    result = wait_for(store, job["id"])
    assert result["status"] == "failed"
    assert "INKREFERENCE_MINERU_PYTHON" in result["error"]
    assert result["completed"] == 0 and result["progress"] is None and result["total"] is None
    assert result["outputs"] == {"markdown": False, "text": False}


def test_pdf_sdk_boundary_uses_fixed_local_worker_argv_and_all_pages(tmp_path, monkeypatch):
    store = ConversionStore(tmp_path)
    monkeypatch.setattr(store, "_python", lambda: sys.executable)
    captured = {}

    class SDKBoundaryProcess:
        returncode = 0
        pid = 123456789

        def __init__(self, command, **kwargs):
            captured.update(command=command, kwargs=kwargs)
            Path(command[5]).write_text("# 合成SDK边界\n这里不代表真实MinerU结果。\n尾标。", encoding="utf-8")

        def communicate(self, timeout=None):
            return b"", b""

    monkeypatch.setattr(conversion_module.subprocess, "Popen", SDKBoundaryProcess)
    job = store.create("奇怪名字 $(never-run) &.pdf", b"%PDF-synthetic", {"ocr_mode": "ocr"})
    assert job["options"]["tier"] == "standard"
    store.start(job["id"])
    result = wait_for(store, job["id"])
    assert result["status"] == "completed"
    command = captured["command"]
    assert command[:3] == [sys.executable, "-I", "-c"]
    assert command[3] == conversion_module._WORKER
    assert 'page_range=""' in command[3] and "from mineru.parser import parse" in command[3]
    assert "markdown(add_markers=True)" in command[3]
    assert command[-2:] == ["standard", "ocr"]
    assert "never-run" not in " ".join(command)
    assert captured["kwargs"]["shell"] is False
    assert store.get_output(job["id"], "text").endswith("尾标。")


def test_real_worker_process_can_be_cancelled_with_its_process_group(tmp_path, monkeypatch):
    store = ConversionStore(tmp_path)
    monkeypatch.setattr(store, "_python", lambda: sys.executable)
    # A synthetic long-running SDK boundary exercises the real Popen/kill path.
    monkeypatch.setattr(conversion_module, "_WORKER", "import time; time.sleep(30)")
    job = store.create("取消进程.pdf", b"%PDF-synthetic")
    store.start(job["id"])
    deadline = time.monotonic() + 4
    while job["id"] not in store._processes and time.monotonic() < deadline:
        time.sleep(0.01)
    assert job["id"] in store._processes
    process = store._processes[job["id"]]
    assert process.poll() is None
    assert store.cancel(job["id"])["status"] == "cancelled"
    store._threads[job["id"]].join(timeout=3)
    assert process.poll() is not None
    assert not store._threads[job["id"]].is_alive()
    assert store.get(job["id"])["outputs"] == {"markdown": False, "text": False}


def test_empty_pdf_output_is_a_failure_even_if_boundary_returns_normally(tmp_path, monkeypatch):
    store = ConversionStore(tmp_path)
    monkeypatch.setattr(store, "_pdf_markdown", lambda *args: "")
    job = store.create("空输出.pdf", b"%PDF-synthetic")
    store.start(job["id"])
    result = wait_for(store, job["id"])
    assert result["status"] == "failed" and "未生成 Markdown" in result["error"]
    assert result["outputs"] == {"markdown": False, "text": False}


def test_source_metadata_queries_do_not_fetch_originals_or_full_versions(tmp_path, monkeypatch):
    documents = DocumentStore(tmp_path)
    item = documents.upload("大原文.txt", ("正文" * 1000).encode())
    queries = []
    original_connect = documents._connection

    @conversion_module.contextlib.contextmanager
    def observed_connection():
        with original_connect() as connection:
            connection.set_trace_callback(queries.append)
            yield connection

    monkeypatch.setattr(documents, "_connection", observed_connection)
    assert documents.list_documents()[0]["original_bytes"] == len(("正文" * 1000).encode())
    assert documents.get_document(item["id"])["source_characters"] == 2000
    selects = [query for query in queries if query.startswith("SELECT") and "doc_documents" in query]
    assert selects and all("length(original)" in query and "SELECT *" not in query for query in selects)


def test_shutdown_interrupts_worker_and_new_service_can_retry(tmp_path, monkeypatch):
    store = ConversionStore(tmp_path)
    monkeypatch.setattr(store, "_python", lambda: sys.executable)
    monkeypatch.setattr(conversion_module, "_WORKER", "import time; time.sleep(30)")
    job = store.create("关闭中断.pdf", b"%PDF-synthetic")
    store.start(job["id"])
    deadline = time.monotonic() + 4
    while job["id"] not in store._processes and time.monotonic() < deadline:
        time.sleep(0.01)
    assert job["id"] in store._processes
    process = store._processes[job["id"]]
    store.shutdown()
    assert store.get(job["id"])["status"] == "interrupted"
    assert process.poll() is not None
    reopened = ConversionStore(tmp_path)
    monkeypatch.setattr(reopened, "_pdf_markdown", lambda *args: "# 恢复\n恢复正文。")
    reopened.retry(job["id"])
    assert wait_for(reopened, job["id"])["status"] == "completed"


def test_shutdown_notifies_all_workers_before_one_global_join_budget(tmp_path, monkeypatch):
    store = ConversionStore(tmp_path)
    monkeypatch.setattr(store, "_python", lambda: sys.executable)
    monkeypatch.setattr(conversion_module, "_WORKER", "import time; time.sleep(30)")
    jobs = [store.create(f"阻塞-{index}.pdf", b"%PDF-synthetic") for index in range(3)]
    for job in jobs:
        store.start(job["id"])
    deadline = time.monotonic() + 4
    while len(store._processes) < len(jobs) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(store._processes) == 3
    processes = list(store._processes.values())
    started = time.monotonic()
    store.shutdown(timeout=2.5)
    assert time.monotonic() - started < 3.0
    assert all(event.is_set() for event in store._cancellations.values())
    assert all(process.poll() is not None for process in processes)
    assert all(store.get(job["id"])["status"] == "interrupted" for job in jobs)
    assert all(not thread.is_alive() for thread in store._threads.values())


def test_conversion_handoff_retries_same_document_after_commit_link_crash(tmp_path, monkeypatch):
    store = ConversionStore(tmp_path)
    job = store.create("幂等恢复.epub", epub_bytes())
    store.start(job["id"])
    assert wait_for(store, job["id"])["status"] == "completed"
    documents = DocumentStore(tmp_path)
    upload = documents.upload

    def commit_then_interrupt(*args, **kwargs):
        upload(*args, **kwargs)
        raise RuntimeError("synthetic process exit after doc commit, before conversion link")

    monkeypatch.setattr(documents, "upload", commit_then_interrupt)
    with pytest.raises(RuntimeError):
        store.import_to_document(job["id"], documents)
    first = documents.list_documents()[0]
    assert store.get(job["id"])["imported_document_id"] is None
    reopened = ConversionStore(tmp_path)
    reopened_documents = DocumentStore(tmp_path)
    resumed = reopened.import_to_document(job["id"], reopened_documents)
    assert resumed["id"] == first["id"]
    assert len(reopened_documents.list_documents()) == 1
    assert reopened.get(job["id"])["imported_document_id"] == first["id"]
    with sqlite3.connect(reopened_documents.db_path) as connection:
        assert connection.execute("SELECT document_id FROM doc_ingestions WHERE key=?", (f"conv:{job['id']}",)).fetchone()[0] == first["id"]


def test_upload_idempotency_key_cannot_silently_change_original(tmp_path):
    documents = DocumentStore(tmp_path)
    first = documents.upload("原件.txt", "原件甲".encode(), idempotency_key="synthetic-operation")
    assert documents.upload("原件.txt", "原件甲".encode(), idempotency_key="synthetic-operation")["id"] == first["id"]
    with pytest.raises(DataError) as caught:
        documents.upload("原件.txt", "不同原件乙".encode(), idempotency_key="synthetic-operation")
    assert caught.value.status_code == 409
    assert len(documents.list_documents()) == 1
