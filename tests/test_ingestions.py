"""Integrated source -> TXT -> chapter/issue pipeline, using invented fixtures."""
from __future__ import annotations

import io
import sqlite3
import threading
import time
import zipfile

import pytest

from inkreference.conversions import ConversionStore
from inkreference.database import DataError
from inkreference.documents import DocumentStore
from inkreference.ingestions import IngestionStore


def stores(tmp_path):
    documents = DocumentStore(tmp_path)
    conversions = ConversionStore(tmp_path)
    return documents, conversions, IngestionStore(tmp_path, documents, conversions)


def epub_bytes():
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="book.opf"/></rootfiles></container>')
        archive.writestr("book.opf", '<package><manifest><item id="a" href="a.xhtml"/></manifest><spine><itemref idref="a"/></spine></package>')
        archive.writestr("a.xhtml", '<html><body><h1>第一章 初遇</h1><p>星舟启航。</p><p>作者说：求月票！</p></body></html>')
    return output.getvalue()


def finish(store, job, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = store.get(job["id"])
        if result["status"] in {"completed", "failed", "cancelled", "interrupted"}:
            thread = store._threads.get(job["id"])
            if thread is not None:
                thread.join(timeout=1)
            return result
        time.sleep(0.01)
    raise AssertionError(store.get(job["id"]))


def test_txt_pipeline_preserves_findings_and_records_real_stage_progress(tmp_path, monkeypatch):
    documents, _, ingestion = stores(tmp_path)
    observed = []
    update = ingestion._update
    monkeypatch.setattr(ingestion, "_update", lambda identifier, **fields: (observed.append(fields), update(identifier, **fields))[1])
    raw = "第一章 初遇\n星舟\ue001启航。\n作者说：求月票！\n第二章 返航\n尾标。".encode()
    job = ingestion.create("原稿.txt", raw, title="合成参考书")
    ingestion.start(job["id"])
    done = finish(ingestion, job)
    assert done["status"] == "completed", done
    assert done["progress"] == 1
    version = documents.get_version(done["document_id"], done["version_id"])
    assert version["text"] == raw.decode()
    assert len(version["chapters"]) == 2
    items = documents.findings(done["document_id"])["items"]
    assert any(item["kind"] == "missing_glyph" for item in items)
    assert any(item["kind"] == "author_note" for item in items)
    stages = [step["stage"] for step in observed]
    assert stages == ["chapter_parse", "chapter_parse", "scan", "scan", "scan", "completed"]
    assert [step["completed"] for step in observed if step["stage"] == "scan"] == [0, 1, 2]
    assert all(step["total"] == 2 for step in observed if step["stage"] == "scan")
    assert documents.get_original(done["document_id"])[0] == raw


def test_epub_pipeline_automatically_converts_then_parses_and_keeps_binary_origin(tmp_path):
    documents, conversions, ingestion = stores(tmp_path)
    raw = epub_bytes()
    job = ingestion.create("合成.epub", raw)
    ingestion.start(job["id"])
    done = finish(ingestion, job)
    assert done["status"] == "completed", done
    document = documents.get_document(done["document_id"])
    assert document["format"] == "epub"
    assert documents.get_original(document["id"]) == (raw, "合成.epub")
    version = documents.get_version(document["id"])
    assert "星舟启航。" in version["text"]
    assert "<p>" not in version["text"]
    assert documents.chapter_index(document["id"])["chapters"][0]["title"] == "初遇"
    assert conversions.get(job["conversion_id"])["imported_document_id"] == document["id"]


def test_synthetic_pdf_boundary_automatic_import_is_not_an_ocr_quality_test(tmp_path, monkeypatch):
    documents, conversions, ingestion = stores(tmp_path)
    monkeypatch.setattr(conversions, "_pdf_markdown", lambda *args: "# 第一章 初遇\nPDF 合成尾标。")
    job = ingestion.create("合成.pdf", b"%PDF-synthetic", conversion_options={"tier": "flash", "ocr_mode": "txt"})
    ingestion.start(job["id"])
    done = finish(ingestion, job)
    assert done["status"] == "completed", done
    assert documents.get_version(done["document_id"])["text"] == "第一章 初遇\nPDF 合成尾标。"
    assert documents.get_original(done["document_id"])[0] == b"%PDF-synthetic"


def test_replace_append_and_restore_keep_precise_active_source_history(tmp_path):
    documents, _, ingestion = stores(tmp_path)
    first = ingestion.create("甲.txt", "第一章 初遇\n原稿。".encode())
    ingestion.start(first["id"])
    initial = finish(ingestion, first)
    doc_id = initial["document_id"]
    append = ingestion.create("乙.txt", "第二章 出发\n补充。".encode(), mode="append", target_document_id=doc_id, base_version_id=initial["version_id"])
    ingestion.start(append["id"])
    appended = finish(ingestion, append)
    assert appended["status"] == "completed", appended
    assert len(documents.list_sources(doc_id)) == 2
    assert all(source["active"] for source in documents.list_sources(doc_id))
    replace = ingestion.create("丙.txt", "第一章 更新\n新版。".encode(), mode="replace", target_document_id=doc_id, base_version_id=appended["version_id"])
    ingestion.start(replace["id"])
    replaced = finish(ingestion, replace)
    assert replaced["status"] == "completed", replaced
    assert documents.get_version(doc_id)["text"] == "第一章 更新\n新版。"
    sources = documents.list_sources(doc_id)
    assert [(item["filename"], item["active"]) for item in sources] == [("甲.txt", False), ("乙.txt", False), ("丙.txt", True)]
    restored = documents.restore_version(doc_id, replaced["version_id"], appended["version_id"])
    assert "原稿。" in restored["text"] and "补充。" in restored["text"]
    assert [(item["filename"], item["active"]) for item in documents.list_sources(doc_id)] == [("甲.txt", True), ("乙.txt", True), ("丙.txt", False)]
    assert documents.get_source_original(doc_id, sources[2]["id"])[0] == "第一章 更新\n新版。".encode()


def test_repeated_conversion_append_is_idempotent_and_cannot_switch_target(tmp_path):
    documents, conversions, _ = stores(tmp_path)
    first = documents.upload("首稿.txt", "第一章 初遇\n正文。".encode())
    second = documents.upload("他稿.txt", "第一章 另一作品\n别的正文。".encode())
    job = conversions.create("补充.epub", epub_bytes(), {"mode": "append", "target_document_id": first["id"], "base_version_id": first["latest_version_id"]})
    conversions.start(job["id"])
    assert finish(conversions, job)["status"] == "completed"
    committed = conversions.import_to_document(job["id"], documents)
    retried = conversions.import_to_document(job["id"], documents)
    assert retried["latest_version_id"] == committed["latest_version_id"]
    assert len(documents.get_document(first["id"])["versions"]) == 2
    with pytest.raises(DataError) as caught:
        conversions.import_to_document(job["id"], documents, target_document_id=second["id"], base_version_id=second["latest_version_id"])
    assert caught.value.status_code == 409
    assert len(documents.get_document(second["id"])["versions"]) == 1


def test_document_commit_link_crash_then_ingestion_retry_never_duplicates_append(tmp_path, monkeypatch):
    documents, _, ingestion = stores(tmp_path)
    initial = documents.upload("原稿.txt", "第一章 初遇\n正文。".encode())
    job = ingestion.create("补充.txt", "第二章 出发\n尾标。".encode(), mode="append", target_document_id=initial["id"], base_version_id=initial["latest_version_id"])
    append = documents.append

    def commit_then_crash(*args, **kwargs):
        append(*args, **kwargs)
        raise RuntimeError("synthetic process exit after document commit")

    monkeypatch.setattr(documents, "append", commit_then_crash)
    ingestion.start(job["id"])
    assert finish(ingestion, job)["status"] == "failed"
    assert len(documents.get_document(initial["id"])["versions"]) == 2
    monkeypatch.setattr(documents, "append", append)
    ingestion.retry(job["id"])
    retried = finish(ingestion, job)
    assert retried["status"] == "completed", retried
    assert len(documents.get_document(initial["id"])["versions"]) == 2
    assert documents.get_version(initial["id"])["text"].count("第二章") == 1


def test_target_revision_change_during_conversion_fails_without_partial_append(tmp_path, monkeypatch):
    documents, conversions, ingestion = stores(tmp_path)
    initial = documents.upload("原稿.txt", "第一章 初遇\n正文。".encode())
    gate, entered = threading.Event(), threading.Event()

    def convert(*args):
        entered.set()
        assert gate.wait(3)
        return "# 第二章 出发\n尾标。"

    monkeypatch.setattr(conversions, "_pdf_markdown", convert)
    job = ingestion.create("补充.pdf", b"%PDF-synthetic", mode="append", target_document_id=initial["id"], base_version_id=initial["latest_version_id"])
    ingestion.start(job["id"])
    assert entered.wait(2)
    edited = documents.edit_chapter(initial["id"], initial["latest_version_id"], "chapter-1", "手工编辑。")
    gate.set()
    done = finish(ingestion, job)
    assert done["status"] == "failed" and "新版本" in done["error"]
    assert documents.get_version(initial["id"])["id"] == edited["id"]
    assert documents.get_version(initial["id"])["text"].endswith("手工编辑。")
    assert len(documents.list_sources(initial["id"])) == 1


def test_cancel_queued_and_restart_interruption_are_retryable(tmp_path):
    documents, conversions, ingestion = stores(tmp_path)
    job = ingestion.create("原稿.txt", "第一章 初遇\n正文。".encode())
    assert ingestion.cancel(job["id"])["status"] == "cancelled"
    assert documents.list_documents() == []
    with sqlite3.connect(ingestion.db_path) as connection:
        connection.execute("UPDATE ing_jobs SET status='running' WHERE id=?", (job["id"],))
    reopened = IngestionStore(tmp_path, documents, conversions)
    assert reopened.get(job["id"])["status"] == "interrupted"
    reopened.retry(job["id"])
    assert finish(reopened, job)["status"] == "completed"
    assert len(documents.list_documents()) == 1


@pytest.mark.parametrize("filename,options", [("a.md", {}), ("a.docx", {}), ("a.txt", {"mode": "replace"}),
                                            ("a.txt", {"conversion_options": {"tier": "flash"}}),
                                            ("a.pdf", {"conversion_options": {"command": "untrusted"}})])
def test_ingestion_input_validation_writes_no_jobs(tmp_path, filename, options):
    _, conversions, ingestion = stores(tmp_path)
    with pytest.raises(DataError):
        ingestion.create(filename, b"%PDF-synthetic", **options)
    assert ingestion.list() == []
    assert conversions.list() == []


def test_long_chapter_page_edit_preserves_unseen_unicode_prefix_and_tail(tmp_path):
    documents, _, _ = stores(tmp_path)
    body = "甲😀乙" * 80000
    text = "第一章 长章\n" + body + "\n第二章 尾章\n末尾不可丢。"
    item = documents.upload("长章.txt", text.encode(), preserve_text=True)
    version = documents.get_version(item["id"])
    page = documents.get_chapter(item["id"], version["id"], "chapter-1", start=40000, limit=20000)
    assert page["body_text"] is None
    assert len(page["editable_text"]) == 20000
    changed = "😀手工改动😀"
    saved = documents.edit_chapter_slice(item["id"], version["id"], "chapter-1", page["editable_start"], page["editable_end"], changed, page["editable_text"])
    assert saved["text"] == text[:40000] + changed + text[60000:]
    assert saved["text"].endswith("第二章 尾章\n末尾不可丢。")
    with pytest.raises(DataError) as stale:
        documents.edit_chapter_slice(item["id"], version["id"], "chapter-1", 40000, 60000, changed, page["editable_text"])
    assert stale.value.status_code == 409
    with pytest.raises(DataError) as mismatch:
        documents.edit_chapter_slice(item["id"], saved["id"], "chapter-1", 40000, 40010, "变更", "不匹配")
    assert mismatch.value.status_code == 409


def test_cancel_running_conversion_stops_handoff_and_can_retry(tmp_path, monkeypatch):
    documents, conversions, ingestion = stores(tmp_path)
    entered, gate = threading.Event(), threading.Event()

    def delayed(*args):
        entered.set()
        assert gate.wait(3)
        return "# 第一章 初遇\n正文。"

    monkeypatch.setattr(conversions, "_pdf_markdown", delayed)
    job = ingestion.create("等待.pdf", b"%PDF-synthetic")
    ingestion.start(job["id"])
    assert entered.wait(2)
    ingestion.cancel(job["id"])
    gate.set()
    stopped = finish(ingestion, job)
    assert stopped["status"] == "cancelled"
    assert documents.list_documents() == []
    conversions._threads[job["conversion_id"]].join(timeout=1)
    ingestion.retry(job["id"])
    assert finish(ingestion, job)["status"] == "completed"
    assert len(documents.list_documents()) == 1


def test_repeated_replace_key_preserves_first_commit_and_rejects_different_original(tmp_path):
    documents, _, _ = stores(tmp_path)
    initial = documents.upload("原稿.txt", "第一章 原稿\n正文。".encode())
    first = documents.replace(initial["id"], initial["latest_version_id"], "新稿.txt", "第一章 更新\n新版。".encode(), idempotency_key="replace-once")
    retry = documents.replace(initial["id"], initial["latest_version_id"], "新稿.txt", "第一章 更新\n新版。".encode(), idempotency_key="replace-once")
    assert retry["id"] == first["id"]
    assert len(documents.get_document(initial["id"])["versions"]) == 2
    with pytest.raises(DataError) as caught:
        documents.replace(initial["id"], first["id"], "新稿.txt", "不同的源文件".encode(), idempotency_key="replace-once")
    assert caught.value.status_code == 409
    assert documents.get_version(initial["id"])["id"] == first["id"]


def test_control_characters_are_review_candidates_without_changing_whitespace(tmp_path):
    documents, _, ingestion = stores(tmp_path)
    text = "第一章 初遇\r\n\t正文\x00\x01续文。\r\n第二章 尾声\r\n尾标。"
    job = ingestion.create("含控制符.txt", text.encode(), encoding="utf-8")
    ingestion.start(job["id"])
    done = finish(ingestion, job)
    assert done["status"] == "completed", done
    version = documents.get_version(done["document_id"])
    assert version["text"] == text
    findings = documents.findings(done["document_id"])["items"]
    controls = [item for item in findings if item["label"] == "异常控制字符"]
    assert len(controls) == 1 and controls[0]["text"] == "\x00\x01"
    assert controls[0]["removable"]
    cleaned = documents.apply_chapter_cleaning(done["document_id"], version["id"],
        [{"chapter_id": controls[0]["chapter_id"], "proposal_ids": [controls[0]["id"]]}])
    assert cleaned["text"] == text.replace("\x00\x01", "")
    assert "\r\n\t" in cleaned["text"]


def test_shutdown_write_lock_is_bounded_and_restart_recovers(tmp_path):
    documents, conversions, ingestion = stores(tmp_path)
    item = ingestion.create("等待.txt", "第一章 初遇\n正文。".encode())
    with sqlite3.connect(ingestion.db_path) as connection:
        connection.execute("UPDATE ing_jobs SET status='running' WHERE id=?", (item["id"],))
    held, release = threading.Event(), threading.Event()

    def hold_database_lock():
        connection = sqlite3.connect(ingestion.db_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            held.set()
            release.wait(1)
        finally:
            connection.rollback()
            connection.close()

    thread = threading.Thread(target=hold_database_lock)
    thread.start()
    assert held.wait(1)
    try:
        started = time.monotonic()
        ingestion.shutdown(timeout=0.02)
        assert time.monotonic() - started < 0.5
    finally:
        release.set()
        thread.join(timeout=2)
    restarted = IngestionStore(tmp_path, documents, conversions)
    assert restarted.get(item["id"])["status"] == "interrupted"
    restarted.retry(item["id"])
    assert finish(restarted, item)["status"] == "completed"
    assert len(documents.list_documents()) == 1


def test_late_closed_worker_cannot_overwrite_retried_attempt(tmp_path, monkeypatch):
    documents, conversions, previous = stores(tmp_path)
    upload = documents.upload
    old_entered, old_release = threading.Event(), threading.Event()
    new_entered, new_release = threading.Event(), threading.Event()
    counter = [0]
    counter_lock = threading.Lock()

    def delayed_upload(*args, **kwargs):
        with counter_lock:
            counter[0] += 1
            attempt = counter[0]
        if attempt == 1:
            old_entered.set()
            assert old_release.wait(3)
            raise RuntimeError("synthetic late result from closed server")
        new_entered.set()
        assert new_release.wait(3)
        return upload(*args, **kwargs)

    monkeypatch.setattr(documents, "upload", delayed_upload)
    item = previous.create("原稿.txt", "第一章 初遇\n正文。".encode())
    previous.start(item["id"])
    assert old_entered.wait(2)
    assert len(previous.active_threads()) == 1
    previous.shutdown(timeout=0.01)
    current = IngestionStore(tmp_path, documents, conversions)
    current.retry(item["id"])
    assert new_entered.wait(2)
    try:
        old_release.set()
        previous._threads[item["id"]].join(timeout=2)
        assert previous.active_threads() == []
        assert current.get(item["id"])["status"] == "running"
    finally:
        old_release.set()
        new_release.set()
    assert finish(current, item)["status"] == "completed"
    assert len(documents.list_documents()) == 1
    assert current.active_threads() == []
    assert conversions.active_threads() == []


def test_conversion_cancel_between_loop_check_and_read_is_cancelled(tmp_path, monkeypatch):
    documents, conversions, ingestion = stores(tmp_path)
    release = threading.Event()
    job = ingestion.create("等待.pdf", b"%PDF-synthetic")
    original_get = conversions.get
    reads = [0]
    injected = [False]

    def delayed_conversion(*args):
        assert release.wait(3)
        return "# 第一章 初遇\n正文。"

    def read_then_cancel(identifier):
        if threading.current_thread().name.startswith("ingestion-"):
            reads[0] += 1
            # Reads one/two are initial lookup and start()'s return value. Read
            # three follows the ingestion loop's first cancellation check.
            if reads[0] == 3 and not injected[0]:
                injected[0] = True
                ingestion.cancel(job["id"])
        return original_get(identifier)

    monkeypatch.setattr(conversions, "_pdf_markdown", delayed_conversion)
    monkeypatch.setattr(conversions, "get", read_then_cancel)
    ingestion.start(job["id"])
    try:
        done = finish(ingestion, job)
        assert injected[0]
        assert done["status"] == "cancelled", done
        assert documents.list_documents() == []
    finally:
        release.set()
        for worker in conversions.active_threads():
            worker.join(timeout=2)


def test_conversion_cancel_after_queued_read_before_start_is_cancelled(tmp_path, monkeypatch):
    documents, conversions, ingestion = stores(tmp_path)
    job = ingestion.create("等待.pdf", b"%PDF-synthetic")
    start = conversions.start
    injected = [False]

    def start_after_cancel(identifier):
        injected[0] = True
        ingestion.cancel(job["id"])
        return start(identifier)  # The native job has become cancelled: 409.

    monkeypatch.setattr(conversions, "start", start_after_cancel)
    ingestion.start(job["id"])
    done = finish(ingestion, job)
    assert injected[0]
    assert done["status"] == "cancelled", done
    assert documents.list_documents() == []
    assert conversions.active_threads() == []


@pytest.mark.parametrize("kind", ["txt", "pdf"])
def test_committed_version_wins_cancel_and_link_exception(tmp_path, monkeypatch, kind):
    documents, conversions, ingestion = stores(tmp_path)
    if kind == "pdf":
        monkeypatch.setattr(conversions, "_pdf_markdown", lambda *args: "# 第一章 初遇\n正文。")
    job = ingestion.create("原稿." + kind, b"%PDF-synthetic" if kind == "pdf" else "第一章 初遇\n正文。".encode())
    upload = documents.upload

    def commit_then_cancel_and_fail(*args, **kwargs):
        upload(*args, **kwargs)
        ingestion.cancel(job["id"])
        raise RuntimeError("synthetic link failure after durable document commit")

    monkeypatch.setattr(documents, "upload", commit_then_cancel_and_fail)
    ingestion.start(job["id"])
    done = finish(ingestion, job)
    assert done["status"] == "completed", done
    assert done["document_id"] is not None and done["version_id"] is not None
    assert len(documents.list_documents()) == 1
    assert documents.get_version(done["document_id"], done["version_id"])["text"].endswith("正文。")
