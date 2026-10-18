"""Immutable originals and versioned, lossless document preparation.

Only the existing InkReference text_processing helpers are reused. Their
InkOctoBot provenance is documented there and in THIRD_PARTY_NOTICES.md.
Archive/XML parsers below use Python's standard library; no upstream code copied.
Offsets are Unicode code points in the indicated version, never byte offsets.
"""

from __future__ import annotations

import codecs
import contextlib
import difflib
import hashlib
import io
import json
import posixpath
import re
import sqlite3
import stat
import uuid
import zipfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterator
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET

from .database import DataError
from .chapter_layout import (chapter_label, chapter_body_span, chapter_sequence, duplicate_heading_repairs,
                             expand_missing_chapters, normalize_chapter_body, normalize_document_chapter_bodies,
                             renumber_after_deletion)
from .chapter_preprocessing import AUTHOR_KEYWORDS, LEGACY_AUTHOR_KEYWORDS, annotate_chapters, chapter_proposals, chapter_title_span, validate_settings
from .text_processing import analyze_text, chunk_text, diagnose_text, split_chapters

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_MEMBER_BYTES = 20 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 2000
SUPPORTED_FORMATS = {".txt", ".md", ".srt", ".docx", ".epub"}
ENCODING_CHOICES = ["utf-8", "gb18030", "utf-16-le", "utf-16-be", "big5"]
HISTORY_LIMIT = 10
READ_VIEW_LIMIT = 3
READ_MODEL_VERSION = 5
PREPROCESSING_VERSION = 2
# Use the stored exact codepoint count, rather than SQLite length(text), which
# stops at a literal NUL. This does not fetch original BLOBs or full version text.
_CURRENT_CHARACTERS_SQL = ("COALESCE((SELECT characters FROM doc_version_views WHERE version_id=doc_documents.latest_version_id), "
                           "(SELECT CAST(json_extract(payload_json,'$.coverage.characters') AS INTEGER) "
                           "FROM doc_versions WHERE id=doc_documents.latest_version_id),length(source_text))")
_META_COLUMNS = ("id,filename,title,work_id,format,encoding,original_sha256,latest_version_id,created_at,"
                 "length(original) AS original_bytes,length(source_text) AS source_characters,"
                 + _CURRENT_CHARACTERS_SQL + " AS current_characters")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _encoding_error(message: str) -> DataError:
    return DataError(422, {"code": "encoding_required", "message": message,
                           "encoding_choices": ENCODING_CHOICES})


def _decode(raw: bytes, encoding: str) -> tuple[str, str]:
    if not isinstance(encoding, str):
        raise _encoding_error("请选择源文件编码")
    selected = encoding.strip().lower()
    if selected == "auto":
        if raw.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
            selected = "utf-32"
        elif raw.startswith(codecs.BOM_UTF8):
            selected = "utf-8-sig"
        elif raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
            selected = "utf-16"
        else:
            # A frequent UTF-16/no-BOM ambiguity must not become silently
            # cleaned NULs. The caller can select an explicit endian encoding.
            if raw and raw.count(b"\x00") / len(raw) >= 0.2:
                raise _encoding_error("检测到大量空字节，可能是无 BOM 的 UTF-16；请选择编码")
            for candidate in ("utf-8", "gb18030"):
                try:
                    text = raw.decode(candidate, errors="strict")
                    break
                except UnicodeDecodeError:
                    continue
            else:
                raise _encoding_error("无法可靠识别编码；原文件尚未保存，请选择编码后重试")
            # Controls in a mostly-binary file do not justify guessing GB18030.
            controls = sum(ord(ch) < 32 and ch not in "\r\n\t" for ch in text)
            if len(text) >= 16 and controls / len(text) > 0.1:
                raise _encoding_error("内容可能为二进制文件或使用了其他编码，请确认编码")
            return text, candidate
    try:
        codec = codecs.lookup(selected).name
        allowed = {"utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be",
                   "utf-32", "utf-32-le", "utf-32-be", "gb18030", "gbk", "big5"}
        if codec not in allowed:
            raise _encoding_error("不支持该编码，请选择列表中的文本编码")
        text = raw.decode(selected, errors="strict")
        # Valid UTF-16/32 can contain unpaired surrogates only with permissive
        # errors handlers, which are deliberately never used here.
        text.encode("utf-8", errors="strict")
        return text, codec
    except (LookupError, UnicodeError):
        raise _encoding_error(f"文件无法按 {selected} 严格解码，请重新选择编码") from None


def _safe_xml(raw: bytes) -> ET.Element:
    # ElementTree never fetches external resources. Reject entity declarations
    # as well, including UTF-16/32 spellings, before parsing any archive XML.
    declaration_probe = raw.replace(b"\x00", b"").upper()
    # Bare HTML5 doctypes are normal EPUB markup. External/internal DTDs
    # remain unsupported; the parser never fetches an external resource.
    without_html_doctype = re.sub(rb"<!DOCTYPE\s+HTML\s*>", b"", declaration_probe)
    if b"<!DOCTYPE" in without_html_doctype or b"<!ENTITY" in declaration_probe:
        raise DataError(422, "文档含不支持的 XML 实体或 DTD 声明")
    try:
        return ET.fromstring(raw)
    except (ET.ParseError, ValueError, LookupError) as error:
        raise DataError(422, "文档中的 XML 格式无效") from error


def _tag(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1] if isinstance(element.tag, str) else ""


@contextlib.contextmanager
def _safe_zip(raw: bytes) -> Iterator[zipfile.ZipFile]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except (zipfile.BadZipFile, OSError, ValueError) as error:
        raise DataError(422, "文件不是有效的 DOCX/EPUB 压缩包") from error
    try:
        members = archive.infolist()
        if len(members) > MAX_ARCHIVE_MEMBERS or sum(m.file_size for m in members) > MAX_ARCHIVE_BYTES:
            raise DataError(422, "文档解压后的内容超过限制")
        seen = set()
        for member in members:
            path = PurePosixPath(member.filename)
            mode = member.external_attr >> 16
            if (not member.filename or "\\" in member.filename or path.is_absolute()
                    or ".." in path.parts or (path.parts and ":" in path.parts[0])
                    or member.filename in seen or stat.S_ISLNK(mode)):
                raise DataError(422, "文档压缩包含不安全或重复的文件路径")
            seen.add(member.filename)
            if member.flag_bits & 1:
                raise DataError(422, "不支持加密的文档压缩包")
            if (member.file_size > MAX_MEMBER_BYTES
                    or member.file_size > max(1, member.compress_size) * 1000):
                raise DataError(422, "文档压缩内容超过单文件或压缩比限制")
        yield archive
    except (zipfile.BadZipFile, RuntimeError, OSError, NotImplementedError) as error:
        raise DataError(422, "文档压缩内容损坏或使用了不支持的压缩格式") from error
    finally:
        archive.close()


def _read_member(archive: zipfile.ZipFile, name: str) -> bytes:
    try:
        return archive.read(name)
    except KeyError:
        raise DataError(422, f"文档缺少必要文件：{name}") from None


def _docx_text(raw: bytes) -> str:
    with _safe_zip(raw) as archive:
        root = _safe_xml(_read_member(archive, "word/document.xml"))
        bodies = [item for item in root.iter() if _tag(item) == "body"]
        if not bodies:
            raise DataError(422, "DOCX 缺少正文 body")
        paragraphs = []

        def inline_items(element: ET.Element) -> Iterator[ET.Element]:
            # Text boxes can contain nested paragraphs. Their text is emitted
            # when those paragraphs are visited, rather than twice here.
            for child in element:
                if _tag(child) != "p":
                    yield child
                    yield from inline_items(child)

        for paragraph in bodies[0].iter():
            if _tag(paragraph) != "p":
                continue
            fragments = []
            for item in inline_items(paragraph):
                name = _tag(item)
                if name in {"t", "delText"}:
                    fragments.append(item.text or "")
                elif name == "tab":
                    fragments.append("\t")
                elif name in {"br", "cr"}:
                    fragments.append("\n")
            paragraphs.append("".join(fragments))
        return "\n".join(paragraphs)


class _BookHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.rsplit(":", 1)[-1]
        if tag in {"script", "style", "head"}:
            self.hidden += 1
        if not self.hidden and tag in {"br", "p", "div", "section", "li", "h1", "h2", "h3", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.rsplit(":", 1)[-1]
        if tag in {"script", "style", "head"}:
            self.hidden = max(0, self.hidden - 1)
        elif not self.hidden and tag in {"p", "div", "section", "li", "h1", "h2", "h3", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def _archive_path(base: str, href: str) -> str:
    parsed = urlsplit(href)
    if parsed.scheme or parsed.netloc:
        raise DataError(422, "EPUB 正文引用必须指向压缩包内的文件")
    target = posixpath.normpath(posixpath.join(base, unquote(parsed.path)))
    if target.startswith(("/", "../")) or target in {".", ".."} or "\\" in target:
        raise DataError(422, "EPUB 正文引用越过了压缩包边界")
    return target


def epub_spine_sections(raw: bytes) -> list[dict[str, str]]:
    """Read safe visible XHTML fragments in EPUB spine order, without I/O.

    Shared by direct document preparation and the Markdown conversion pipeline.
    Namespace removal makes XHTML work with ordinary HTML/Markdown parsers.
    Script/style/head content is excluded; no external resources are fetched.
    """
    with _safe_zip(raw) as archive:
        container = _safe_xml(_read_member(archive, "META-INF/container.xml"))
        roots = [item.attrib.get("full-path", "") for item in container.iter() if _tag(item) == "rootfile"]
        if not roots or not roots[0]:
            raise DataError(422, "EPUB 缺少书籍目录文件")
        package_path = _archive_path("", roots[0])
        package = _safe_xml(_read_member(archive, package_path))
        manifest = {item.attrib.get("id"): item.attrib.get("href", "")
                    for item in package.iter() if _tag(item) == "item"}
        spines = [item for item in package.iter() if _tag(item) == "spine"]
        if not spines:
            raise DataError(422, "EPUB 缺少阅读顺序 spine")
        sections = []
        for item in spines[0]:
            if _tag(item) != "itemref":
                continue
            href = manifest.get(item.attrib.get("idref"))
            if not href:
                raise DataError(422, "EPUB 阅读顺序引用了缺失的章节")
            path = _archive_path(posixpath.dirname(package_path), href)
            chapter_raw = _read_member(archive, path)
            root = _safe_xml(chapter_raw)
            for element in root.iter():
                element.tag = _tag(element)
            bodies = list(root.iter("body"))
            visible = bodies[0] if bodies else root
            for parent in list(visible.iter()):
                for child in list(parent):
                    if child.tag in {"head", "script", "style"}:
                        # Text after an excluded element still belongs to the
                        # visible document (ElementTree stores it as tail).
                        tail = child.tail or ""
                        index = list(parent).index(child)
                        if index:
                            previous = list(parent)[index - 1]
                            previous.tail = (previous.tail or "") + tail
                        else:
                            parent.text = (parent.text or "") + tail
                        parent.remove(child)
            sections.append({"path": path, "html": ET.tostring(visible, encoding="unicode", method="html")})
        if not sections:
            raise DataError(422, "EPUB 的阅读顺序为空")
        return sections


def _epub_text(raw: bytes) -> str:
    sections = []
    for section in epub_spine_sections(raw):
        parser = _BookHTML()
        parser.feed(section["html"])
        parser.close()
        sections.append("".join(parser.parts))
    return "\n\n".join(sections)


def _options(options: Any) -> dict[str, Any]:
    if not isinstance(options, dict):
        raise DataError(422, "清洗选项必须为对象")
    unknown = set(options) - {"remove_random_ids", "chunk_chars", "fallback_chars", "heading_formats", "custom_formats", "author_keywords", "garble_rules"}
    if unknown:
        raise DataError(422, f"未知清洗选项：{'、'.join(sorted(unknown))}")
    result = {"remove_random_ids": False, "chunk_chars": 3000, "fallback_chars": 3000, **options}
    if not isinstance(result["remove_random_ids"], bool):
        raise DataError(422, "移除疑似 ID 必须明确选择 true 或 false")
    for key in ("chunk_chars", "fallback_chars"):
        if isinstance(result[key], bool) or not isinstance(result[key], int) or not 1 <= result[key] <= 100000:
            raise DataError(422, f"{key} 必须为 1 至 100000 的整数")
    return validate_settings(result)


def _display_diff(previous: str, text: str) -> tuple[str, bool]:
    """Bound review-only diffs; never run a quadratic matcher on an entire book."""
    if previous == text:
        return "", False
    before, after = previous.splitlines(keepends=True), text.splitlines(keepends=True)
    prefix = 0
    shared = min(len(before), len(after))
    while prefix < shared and before[prefix] == after[prefix]:
        prefix += 1
    suffix = 0
    while suffix < shared - prefix and before[-suffix - 1] == after[-suffix - 1]:
        suffix += 1
    begin = max(0, prefix - 2)
    old_end = len(before) - max(0, suffix - 2)
    new_end = len(after) - max(0, suffix - 2)
    old, new = before[begin:old_end], after[begin:new_end]
    # Exact texts and immutable history remain available independently of this
    # bounded display. Very broad edits receive a visibly truncated preview.
    if len(old) + len(new) > 4000 or sum(map(len, old)) + sum(map(len, new)) > 200000:
        rendered = "--- base\n+++ cleaned\n@@ large change; review full version @@\n" + "".join("-" + line for line in old[:20]) + "".join("+" + line for line in new[:20])
        return rendered[:200000], True
    rendered = "".join(difflib.unified_diff(old, new, fromfile="base", tofile="cleaned", n=2))
    return rendered[:200000], len(rendered) > 200000


def _without_ranges(text: str, ranges: list[tuple[int, int]]) -> str:
    """Copy retained spans once, including when thousands of marks are selected."""
    pieces = []
    cursor = 0
    for start, end in sorted(ranges):
        if start >= cursor:
            pieces.append(text[cursor:start])
        cursor = max(cursor, end)
    pieces.append(text[cursor:])
    return "".join(pieces)


def _normalized_slice(text: str, source_start: int) -> tuple[str, list[int]]:
    """Map body presentation codepoints back to the unchanged version span."""
    newline_run = re.compile(r"((?>\r\n|\r|\n))(?:[ \t\u3000]*(?>\r\n|\r|\n))+")
    parts: list[str] = []
    mapping: list[int] = []
    cursor = 0
    for match in newline_run.finditer(text):
        parts.append(text[cursor:match.start()])
        mapping.extend(range(source_start + cursor, source_start + match.start()))
        keep = match.group(1)
        parts.append(keep)
        mapping.extend(range(source_start + match.start(), source_start + match.start() + len(keep)))
        cursor = match.end()
    parts.append(text[cursor:])
    mapping.extend(range(source_start + cursor, source_start + len(text)))
    mapping.append(source_start + len(text))
    return "".join(parts), mapping


def _prepare(source: str, previous: str, options: dict[str, Any], *, normalize: bool = True,
             normalize_layout: bool = False, progress: Callable[[str, int, int], None] | None = None,
             chapter_analysis: dict[str, Any] | None = None) -> dict[str, Any]:
    if progress:
        progress("chapter_parse", 0, 1)
    if normalize:
        result = analyze_text(source, remove_random_ids=options["remove_random_ids"],
                              fallback_chars=options["fallback_chars"],
                              heading_formats=options.get("heading_formats"), custom_formats=options.get("custom_formats"))
    else:
        from .text_processing import split_chapters
        result = {"cleaned_text": source, "cleaned_sha256": _sha(source.encode("utf-8")),
                  "diagnostics": diagnose_text(source), "transformations": [],
                  "chapter_analysis": chapter_analysis if chapter_analysis is not None else split_chapters(
                       source, fallback_chars=options["fallback_chars"], heading_formats=options.get("heading_formats"),
                       custom_formats=options.get("custom_formats"))}
    if normalize_layout:
        normalized = normalize_document_chapter_bodies(result["cleaned_text"], result["chapter_analysis"]["chapters"])
        if normalized != result["cleaned_text"]:
            from .text_processing import split_chapters
            result["transformations"].append({"stage": "body_layout", "count": 1,
                                                "input_characters": len(result["cleaned_text"]),
                                                "output_characters": len(normalized)})
            result["cleaned_text"] = normalized
            result["cleaned_sha256"] = _sha(normalized.encode())
            result["diagnostics"] = diagnose_text(normalized)
            result["chapter_analysis"] = split_chapters(normalized, fallback_chars=options["fallback_chars"],
                heading_formats=options.get("heading_formats"), custom_formats=options.get("custom_formats"))
    if progress:
        progress("chapter_parse", 1, 1)
    text = result["cleaned_text"]
    chapters = result["chapter_analysis"]["chapters"]
    if progress:
        progress("scan", 0, len(chapters))
    chunks = []
    for chapter_index, chapter in enumerate(chapters):
        chapter["id"] = f"chapter-{chapter_index + 1}"
        for index, chunk in enumerate(chunk_text(chapter["text"], options["chunk_chars"])):
            chunks.append({**chunk, "start": chapter["start"] + chunk["start"],
                           "end": chapter["start"] + chunk["end"],
                           "id": f"{chapter['id']}-chunk-{index + 1}", "chapter_id": chapter["id"],
                           "chapter_index": chapter_index})
    proposals_by_id: dict[str, list[dict[str, Any]]] = {}
    gaps = annotate_chapters(chapters, options, proposals_by_id=proposals_by_id,
                             progress=(lambda completed, total: progress("scan", completed, total)) if progress else None)
    reconstructed = "".join(chunk["text"] for chunk in chunks)
    coverage = {**result["chapter_analysis"]["coverage"],
                "chunks_complete": reconstructed == text,
                "chunk_count": len(chunks), "chapter_count": len(chapters)}
    if not coverage["complete"] or not coverage["chunks_complete"]:
        raise DataError(500, "章节或分块覆盖检查失败，未保存不完整版本")
    diff, diff_truncated = _display_diff(previous, text)
    return {"text": text, "sha256": result["cleaned_sha256"], "chapters": chapters,
            "chunks": chunks, "diagnostics": result["diagnostics"],
            "source_diagnostics": diagnose_text(source), "changes": result["transformations"],
            "diff": diff, "diff_truncated": diff_truncated,
            "coverage": coverage, "pattern": result["chapter_analysis"]["pattern"],
            "fallback_used": result["chapter_analysis"]["fallback_used"],
            "heading_formats": result["chapter_analysis"]["heading_formats"],
            "candidates": result["chapter_analysis"]["candidates"], "gaps": gaps,
            "options": options, "_review_proposals": proposals_by_id}


class DocumentStore:
    """Separate doc_* schema sharing the workbench DB with workflow services."""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "workbench.sqlite3"
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS doc_documents (
                    id TEXT PRIMARY KEY, filename TEXT NOT NULL, title TEXT NOT NULL,
                    work_id TEXT, format TEXT NOT NULL, encoding TEXT NOT NULL,
                    original BLOB NOT NULL, original_sha256 TEXT NOT NULL,
                    source_text TEXT NOT NULL, latest_version_id TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS doc_versions (
                    id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES doc_documents(id),
                    parent_version_id TEXT, version_number INTEGER NOT NULL,
                    payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(document_id, version_number)
                );
                CREATE TABLE IF NOT EXISTS doc_sources (
                    id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES doc_documents(id),
                    filename TEXT NOT NULL, format TEXT NOT NULL, encoding TEXT NOT NULL,
                    original BLOB NOT NULL, original_sha256 TEXT NOT NULL,
                    source_text TEXT NOT NULL, version_id TEXT REFERENCES doc_versions(id) ON DELETE SET NULL,
                    created_at TEXT NOT NULL, mode TEXT NOT NULL DEFAULT 'unknown'
                );
                CREATE TRIGGER IF NOT EXISTS doc_source_immutable
                BEFORE UPDATE ON doc_sources
                BEGIN SELECT RAISE(ABORT, 'document source is immutable'); END;
                CREATE INDEX IF NOT EXISTS doc_sources_document ON doc_sources(document_id,created_at);
                CREATE INDEX IF NOT EXISTS doc_versions_document ON doc_versions(document_id,version_number);
                CREATE TABLE IF NOT EXISTS doc_version_views (
                    version_id TEXT PRIMARY KEY REFERENCES doc_versions(id) ON DELETE CASCADE,
                    document_id TEXT NOT NULL, characters INTEGER NOT NULL,
                    summary_json TEXT NOT NULL, index_json TEXT NOT NULL, navigation_json TEXT NOT NULL,
                    layout_version INTEGER NOT NULL DEFAULT 5
                );
                CREATE TABLE IF NOT EXISTS doc_chapter_views (
                    version_id TEXT NOT NULL REFERENCES doc_versions(id) ON DELETE CASCADE,
                    chapter_id TEXT NOT NULL, chapter_order INTEGER NOT NULL,
                    metadata_json TEXT NOT NULL, text TEXT NOT NULL,
                    proposals_json TEXT NOT NULL, diagnostics_json TEXT NOT NULL,
                    PRIMARY KEY(version_id,chapter_id)
                );
                CREATE INDEX IF NOT EXISTS doc_chapter_views_version ON doc_chapter_views(version_id,chapter_order);
                CREATE TABLE IF NOT EXISTS doc_ingestions (
                    key TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES doc_documents(id),
                    source_id TEXT, mode TEXT, original_sha256 TEXT
                );
                CREATE TABLE IF NOT EXISTS doc_version_meta (
                    version_id TEXT PRIMARY KEY REFERENCES doc_versions(id) ON DELETE CASCADE,
                    has_source_provenance INTEGER NOT NULL, active_source_ids_json TEXT
                );
                CREATE TRIGGER IF NOT EXISTS doc_original_immutable
                BEFORE UPDATE OF original,original_sha256,source_text,filename,encoding,format ON doc_documents
                BEGIN SELECT RAISE(ABORT, 'document original is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS doc_version_immutable
                BEFORE UPDATE ON doc_versions
                BEGIN SELECT RAISE(ABORT, 'document version is immutable'); END;
            """)
            connection.execute("BEGIN IMMEDIATE")
            source_columns = {row["name"]: row for row in connection.execute("PRAGMA table_info(doc_sources)")}
            if source_columns["version_id"]["notnull"] or "mode" not in source_columns:
                # Source bytes survive independently of discarded text
                # snapshots. Copy inside SQLite rather than loading BLOBs.
                connection.execute("DROP TRIGGER IF EXISTS doc_source_immutable")
                connection.execute("DROP INDEX IF EXISTS doc_sources_document")
                connection.execute("ALTER TABLE doc_sources RENAME TO doc_sources_legacy")
                connection.execute("""CREATE TABLE doc_sources (
                    id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES doc_documents(id),
                    filename TEXT NOT NULL, format TEXT NOT NULL, encoding TEXT NOT NULL,
                    original BLOB NOT NULL, original_sha256 TEXT NOT NULL, source_text TEXT NOT NULL,
                    version_id TEXT REFERENCES doc_versions(id) ON DELETE SET NULL,
                    created_at TEXT NOT NULL, mode TEXT NOT NULL DEFAULT 'unknown')""")
                mode_column = ",mode" if "mode" in source_columns else ""
                connection.execute("INSERT INTO doc_sources(id,document_id,filename,format,encoding,original,original_sha256,source_text,version_id,created_at" + mode_column + ") "
                                   "SELECT id,document_id,filename,format,encoding,original,original_sha256,source_text,version_id,created_at" + mode_column + " FROM doc_sources_legacy")
                connection.execute("DROP TABLE doc_sources_legacy")
                connection.execute("CREATE INDEX doc_sources_document ON doc_sources(document_id,created_at)")
            # Only pointer/provenance metadata can change. The actual original
            # file, decoded source and original identity remain immutable.
            connection.execute("DROP TRIGGER IF EXISTS doc_source_immutable")
            connection.execute("""CREATE TRIGGER doc_source_immutable
                BEFORE UPDATE OF id,document_id,filename,format,encoding,original,original_sha256,source_text,created_at ON doc_sources
                BEGIN SELECT RAISE(ABORT, 'document source is immutable'); END""")
            ingestion_columns = {row["name"] for row in connection.execute("PRAGMA table_info(doc_ingestions)")}
            for column in ("source_id", "mode", "original_sha256"):
                if column not in ingestion_columns:
                    connection.execute(f"ALTER TABLE doc_ingestions ADD COLUMN {column} TEXT")
            if "active_source_ids_json" not in {row["name"] for row in connection.execute("PRAGMA table_info(doc_version_meta)")}:
                connection.execute("ALTER TABLE doc_version_meta ADD COLUMN active_source_ids_json TEXT")

    @contextlib.contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _document(connection: sqlite3.Connection, identifier: str) -> sqlite3.Row:
        row = connection.execute(f"SELECT *, {_CURRENT_CHARACTERS_SQL} AS current_characters FROM doc_documents WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise DataError(404, "全文文档不存在")
        return row

    @staticmethod
    def _meta(row: sqlite3.Row) -> dict[str, Any]:
        return {key: row[key] for key in ("id", "filename", "title", "work_id", "format", "encoding",
                                          "original_sha256", "latest_version_id", "created_at")} | {
            "original_bytes": row["original_bytes"] if "original_bytes" in row.keys() else len(row["original"]),
            "source_characters": row["source_characters"] if "source_characters" in row.keys() else len(row["source_text"]),
            "current_characters": row["current_characters"]}

    def list_documents(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            return [self._meta(row) for row in connection.execute(f"SELECT {_META_COLUMNS} FROM doc_documents ORDER BY created_at DESC,id")]

    def upload(self, filename: str, raw: bytes, encoding: str = "auto", title: str = "",
               work_id: str | None = None, *, idempotency_key: str | None = None,
               converted_text: str | None = None, preserve_text: bool = False, normalize_layout: bool = False,
               progress: Callable[[str, int, int], None] | None = None) -> dict[str, Any]:
        if not isinstance(filename, str) or not filename or len(filename) > 255 or re.search(r"[\x00-\x1f]", filename):
            raise DataError(422, "文件名无效")
        # Retain only the display name; never use untrusted names as disk paths.
        filename = filename.replace("\\", "/").rsplit("/", 1)[-1]
        suffix = Path(filename).suffix.lower()
        if suffix not in SUPPORTED_FORMATS and not (suffix == ".pdf" and converted_text is not None):
            raise DataError(422, "支持 TXT、MD、SRT、DOCX 和 EPUB 文件；PDF 需先转换为纯文本")
        if not isinstance(raw, bytes) or not raw:
            raise DataError(422, "请上传非空文件")
        if len(raw) > MAX_UPLOAD_BYTES:
            raise DataError(413, "文件超过 50 MiB 限制")
        if not isinstance(title, str) or (work_id is not None and not isinstance(work_id, str)):
            raise DataError(422, "标题与作品 ID 必须为字符串")
        if idempotency_key is not None and (not isinstance(idempotency_key, str)
                                            or not 1 <= len(idempotency_key) <= 200):
            raise DataError(422, "导入幂等键必须是不超过 200 字的非空字符串")
        if converted_text is not None:
            self._valid_text(converted_text)
            if suffix not in {".epub", ".pdf"} or not converted_text.strip():
                raise DataError(422, "转换后的正文仅用于非空 EPUB/PDF 纯文本")
            source, used_encoding = converted_text, "utf-8-converted"
        elif suffix == ".docx":
            source, used_encoding = _docx_text(raw), "xml"
        elif suffix == ".epub":
            source, used_encoding = _epub_text(raw), "xml"
        else:
            source, used_encoding = _decode(raw, encoding)
        identifier = uuid.uuid4().hex
        now = _now()
        payload = _prepare(source, source, _options({}), normalize=not preserve_text, normalize_layout=normalize_layout, progress=progress)
        payload["active_source_ids"] = ["original"]
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if idempotency_key is not None:
                existing = connection.execute(
                    "SELECT document_id FROM doc_ingestions WHERE key=?", (idempotency_key,)).fetchone()
                if existing is not None:
                    document = self._document(connection, existing["document_id"])
                    if document["original_sha256"] != _sha(raw):
                        raise DataError(409, "同一导入幂等键对应了不同原件")
                    return self._meta(document)
            connection.execute("INSERT INTO doc_documents VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                               (identifier, filename, title or Path(filename).stem, work_id, suffix[1:],
                                used_encoding, raw, _sha(raw), source, None, now))
            version = self._insert_version(connection, identifier, None, 1, source, payload)
            connection.execute("UPDATE doc_documents SET latest_version_id=? WHERE id=?", (version["id"], identifier))
            if idempotency_key is not None:
                connection.execute("INSERT INTO doc_ingestions(key,document_id,mode,original_sha256) VALUES (?,?,'new',?)",
                                   (idempotency_key, identifier, _sha(raw)))
            return self._meta(self._document(connection, identifier))

    def create_collection(self, title: str) -> dict[str, Any]:
        """Create an empty reference collection without inventing an uploaded file."""
        if not isinstance(title, str) or not title.strip() or len(title) > 200 or re.search(r"[\x00-\x1f]", title):
            raise DataError(422, "集合名称需为 1 至 200 字")
        identifier, now = uuid.uuid4().hex, _now()
        title = title.strip()
        prepared = _prepare("", "", _options({}), normalize=False)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("INSERT INTO doc_documents VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                               (identifier, title, title, None, "collection", "utf-8", b"", _sha(b""), "", None, now))
            version = self._insert_version(connection, identifier, None, 1, "", prepared)
            connection.execute("UPDATE doc_documents SET latest_version_id=? WHERE id=?", (version["id"], identifier))
            return self._meta(self._document(connection, identifier))

    @staticmethod
    def _insert_version(connection: sqlite3.Connection, document_id: str, parent_id: str | None,
                        number: int, source: str, payload: dict[str, Any]) -> dict[str, Any]:
        now = _now()
        identity = _json({"document_id": document_id, "parent_version_id": parent_id,
                          "sha256": payload["sha256"], "options": payload["options"],
                          "version_number": number})
        identifier = _sha(identity.encode("utf-8"))
        version = {**{k: v for k, v in payload.items() if not k.startswith("_")}, "id": identifier, "document_id": document_id,
                   "parent_version_id": parent_id, "version_number": number,
                   "preprocessing_version": PREPROCESSING_VERSION,
                   "source_text": source, "source_sha256": _sha(source.encode("utf-8")),
                   "created_at": now}
        connection.execute("INSERT INTO doc_versions VALUES (?,?,?,?,?,?)",
                           (identifier, document_id, parent_id, number, _json(version), now))
        DocumentStore._record_version_metadata(connection, version)
        DocumentStore._write_views(connection, version, payload.get("_review_proposals"), payload.get("_review_diagnostics"))
        DocumentStore._prune_history(connection, document_id)
        return version

    def get_document(self, document_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(f"SELECT {_META_COLUMNS} FROM doc_documents WHERE id=?", (document_id,)).fetchone()
            if row is None:
                raise DataError(404, "全文文档不存在")
            document = self._meta(row)
            document["versions"] = [dict(row) for row in connection.execute(
                "SELECT id,parent_version_id,version_number,created_at FROM doc_versions WHERE document_id=? ORDER BY version_number",
                (document_id,))]
            protected = self._protected_versions(connection, document_id, document["latest_version_id"])
            for version in document["versions"]:
                version["protected"] = version["id"] in protected
            document["history_limit"] = HISTORY_LIMIT
            document["read_view_limit"] = READ_VIEW_LIMIT
            return document

    @staticmethod
    def _protected_versions(connection: sqlite3.Connection, document_id: str,
                            current_version_id: str | None = None) -> set[str]:
        """Keep originals and externally pinned analysis versions, not parents.

        Parent IDs are provenance labels, not a request to keep every ancestor.
        The current and retained snapshots carry their active source IDs, so
        removing an ordinary old parent never breaks source reconstruction.
        """
        protected = set()
        initial = connection.execute("SELECT id FROM doc_versions WHERE document_id=? ORDER BY version_number LIMIT 1",
                                     (document_id,)).fetchone()
        if initial:
            protected.add(initial[0])
        if current_version_id is None:
            current = connection.execute("SELECT latest_version_id FROM doc_documents WHERE id=?", (document_id,)).fetchone()
            current_version_id = current[0] if current else None
        if current_version_id:
            protected.add(current_version_id)
        excluded = {"doc_versions", "doc_version_views", "doc_chapter_views", "doc_sources", "doc_version_meta",
                    "reference_volume_plans", "ing_jobs", "conv_imports"}
        # Unknown legacy upload modes still need their original snapshot for
        # a one-time lazy metadata repair. New upload originals never pin a
        # complete book merely because their bytes are preserved.
        protected.update(row[0] for row in connection.execute(
            "SELECT version_id FROM doc_sources WHERE document_id=? AND mode='unknown' AND version_id IS NOT NULL",
            (document_id,)))
        # The workbench's reference and job schemas evolve independently.
        # Discover only scalar version-reference columns, never their payloads.
        tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        for table in tables:
            if table in excluded or table.startswith("sqlite_"):
                continue
            quoted = '"' + table.replace('"', '""') + '"'
            columns = {row[1] for row in connection.execute(f"PRAGMA table_info({quoted})")}
            if table == "reference_analysis_history" and "commits_json" in columns:
                protected.update(row[0] for row in connection.execute(
                    "SELECT DISTINCT json_extract(commit_row.value,'$[7]') FROM reference_analysis_history h, "
                    "json_each(h.commits_json,'$.commits') AS commit_row WHERE h.document_id=?",
                    (document_id,)) if row[0])
            references = columns & {"version_id", "source_version_id", "cleaned_version_id", "text_version_id"}
            for column in references:
                clause = " WHERE document_id=?" if "document_id" in columns else ""
                values = (document_id,) if clause else ()
                protected.update(row[0] for row in connection.execute(f'SELECT DISTINCT "{column}" FROM {quoted}{clause}', values) if row[0])
        return protected

    @staticmethod
    def _prune_history(connection: sqlite3.Connection, document_id: str) -> dict[str, int]:
        protected = DocumentStore._protected_versions(connection, document_id)
        rows = connection.execute("SELECT v.id,v.parent_version_id,m.has_source_provenance AS sources "
                                  "FROM doc_versions v LEFT JOIN doc_version_meta m ON m.version_id=v.id "
                                  "WHERE v.document_id=? ORDER BY v.version_number DESC", (document_id,)).fetchall()
        keep = {row["id"] for row in rows[:HISTORY_LIMIT]} | protected
        # Pre-migration snapshots without materialized source provenance may
        # still walk their parents. Keep only those exceptional legacy chains.
        by_id = {row["id"]: row for row in rows}
        pending = [identity for identity in keep if identity in by_id and not by_id[identity]["sources"]]
        while pending:
            parent = by_id[pending.pop()]["parent_version_id"]
            if parent in by_id and parent not in keep:
                keep.add(parent)
                if not by_id[parent]["sources"]:
                    pending.append(parent)
        removed = [row["id"] for row in rows if row["id"] not in keep]
        connection.executemany("DELETE FROM doc_versions WHERE id=?", ((identity,) for identity in removed))
        # Volume plans are small derived per-version records rather than pins.
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "reference_volume_plans" in tables:
            connection.execute("DELETE FROM reference_volume_plans WHERE document_id=? AND version_id NOT IN "
                               "(SELECT id FROM doc_versions WHERE document_id=?)", (document_id, document_id))
        cached = [row["id"] for row in rows[:READ_VIEW_LIMIT]]
        if cached:
            placeholders = ",".join("?" for _ in cached)
            for table in ("doc_chapter_views", "doc_version_views"):
                # Historical read models rebuild lazily if the user opens one.
                version_filter = ("document_id=?" if table == "doc_version_views" else
                                  "version_id IN (SELECT id FROM doc_versions WHERE document_id=?)")
                connection.execute(f"DELETE FROM {table} WHERE {version_filter} AND version_id NOT IN ({placeholders})",
                                   (document_id, *cached))
        return {"removed_versions": len(removed), "retained_versions": len(rows) - len(removed)}

    @staticmethod
    def _record_version_metadata(connection: sqlite3.Connection, version: dict[str, Any]) -> None:
        connection.execute("INSERT OR REPLACE INTO doc_version_meta VALUES (?,?,?)",
                           (version["id"], int("active_source_ids" in version),
                            _json(version["active_source_ids"]) if "active_source_ids" in version else None))
        for change in version.get("changes", []):
            stage = change.get("stage")
            if stage not in {"append_source", "replace_source"} or not change.get("source_id"):
                continue
            mode = "replace" if stage == "replace_source" else "append"
            connection.execute("UPDATE doc_sources SET mode=? WHERE id=? AND mode='unknown'", (mode, change["source_id"]))
            if change.get("ingestion_key"):
                connection.execute("UPDATE doc_ingestions SET source_id=?,mode=?,original_sha256=? WHERE key=? AND mode IS NULL",
                                   (change["source_id"], mode, change.get("original_sha256"), change["ingestion_key"]))

    @staticmethod
    def _limit_read_views(connection: sqlite3.Connection, document_id: str, requested_version_id: str) -> None:
        cached = {requested_version_id}
        cached.update(row[0] for row in connection.execute(
            "SELECT id FROM doc_versions WHERE document_id=? ORDER BY version_number DESC LIMIT ?",
            (document_id, READ_VIEW_LIMIT - 1)))
        placeholders = ",".join("?" for _ in cached)
        for table in ("doc_chapter_views", "doc_version_views"):
            clause = ("document_id=?" if table == "doc_version_views" else
                      "version_id IN (SELECT id FROM doc_versions WHERE document_id=?)")
            connection.execute(f"DELETE FROM {table} WHERE {clause} AND version_id NOT IN ({placeholders})",
                               (document_id, *cached))

    def get_version(self, document_id: str, version_id: str | None = None) -> dict[str, Any]:
        with self._connection() as connection:
            if version_id is None:
                document = connection.execute("SELECT latest_version_id FROM doc_documents WHERE id=?", (document_id,)).fetchone()
                if document is None:
                    raise DataError(404, "全文文档不存在")
                version_id = document["latest_version_id"]
            row = connection.execute("SELECT payload_json FROM doc_versions WHERE document_id=? AND id=?",
                                     (document_id, version_id)).fetchone()
            if row is None:
                raise DataError(404, "全文版本不存在")
            version = json.loads(row["payload_json"])
            marker = connection.execute("SELECT has_source_provenance,active_source_ids_json FROM doc_version_meta WHERE version_id=?", (version["id"],)).fetchone()
            if marker is None or marker["has_source_provenance"] and marker["active_source_ids_json"] is None:
                self._record_version_metadata(connection, version)
            return version

    @staticmethod
    def version_summary(version: dict[str, Any]) -> dict[str, Any]:
        """A navigation response never carries copies of the entire book."""
        heavy = {"text", "source_text", "chapters", "chunks", "diff", "diagnostics", "source_diagnostics"}
        result = {key: value for key, value in version.items() if key not in heavy and not key.startswith("_")}
        result.update(characters=version.get("coverage", {}).get("characters", len(version.get("text", ""))),
                      chapter_count=version.get("coverage", {}).get("chapter_count", len(version.get("chapters", []))),
                      diagnostics_count=len(version.get("diagnostics", [])),
                      source_diagnostics_count=len(version.get("source_diagnostics", [])),
                      placeholder_count=sum(gap.get("missing_count", 0) for gap in version.get("gaps", [])))
        result["issue_chapter_count"] = result["placeholder_count"] + sum(
            any(issue.get("kind") != "gap" for issue in chapter.get("issues", []))
            for chapter in version.get("chapters", []))
        return result

    @staticmethod
    def _write_views(connection: sqlite3.Connection, version: dict[str, Any],
                     proposals_by_id: dict[str, list[dict[str, Any]]] | None = None,
                     diagnostics_by_id: dict[str, list[dict[str, Any]]] | None = None) -> None:
        """Persist small read models beside immutable analysis snapshots.

        They are derived exclusively from the addressed snapshot. A restored or
        pre-upgrade project can rebuild them without altering source versions.
        """
        options = DocumentStore._version_options(version)
        chapters = [dict(chapter) for chapter in version.get("chapters", [])]
        cache = {} if proposals_by_id is None else dict(proposals_by_id)
        gaps = annotate_chapters(chapters, options, proposals_by_id=cache)
        expanded = expand_missing_chapters(chapters, gaps=gaps)
        # The missing chapters already have dedicated presentation items.
        # Keep authoritative gaps in the snapshot/index metadata while avoiding
        # a second warning on the real chapter immediately after a gap.
        for chapter in expanded["chapters"]:
            if not chapter.get("placeholder"):
                chapter["issues"] = [issue for issue in chapter.get("issues", []) if issue.get("kind") != "gap"]
                chapter["issue_count"] = int(bool(chapter["issues"]))
        index = {"document_id": version["document_id"], "version_id": version["id"],
                 "pattern": version.get("pattern"), "heading_formats": version.get("heading_formats", []),
                 "candidates": version.get("candidates", []), "gaps": gaps, "options": options,
                 "author_keyword_defaults": AUTHOR_KEYWORDS,
                 "placeholder_count": expanded["placeholder_count"],
                 "truncated_missing_count": expanded["truncated_missing_count"],
                 "issue_chapter_count": sum(bool(chapter.get("issues")) for chapter in expanded["chapters"]),
                 "chapters": [{k: v for k, v in chapter.items() if k not in {"text", "content"}}
                              for chapter in expanded["chapters"]]}
        summary = DocumentStore.version_summary(version)
        summary["placeholder_count"] = sum(gap["missing_count"] for gap in gaps)
        summary["issue_chapter_count"] = summary["placeholder_count"] + sum(
            bool(chapter.get("issues")) for chapter in expanded["chapters"] if not chapter.get("placeholder"))
        navigation = {**summary,
                      "chapters": [{k: v for k, v in chapter.items() if k not in {"text", "content"}} for chapter in chapters],
                      "chunks": [{k: v for k, v in chunk.items() if k not in {"text", "content"}}
                                 for chunk in version.get("chunks", [])],
                      "diagnostics": version.get("diagnostics", []),
                      "source_diagnostics": version.get("source_diagnostics", [])}
        connection.execute("INSERT OR REPLACE INTO doc_version_views VALUES (?,?,?,?,?,?,?)",
                           (version["id"], version["document_id"], summary["characters"],
                            _json(summary), _json(index), _json(navigation), READ_MODEL_VERSION))
        rows = []
        for order, chapter in enumerate(expanded["chapters"]):
            metadata = {k: v for k, v in chapter.items() if k not in {"text", "content"}}
            text = chapter.get("text", "")
            rows.append((version["id"], chapter["id"], order, _json(metadata), text,
                         _json(cache.get(chapter["id"], [])), _json(diagnostics_by_id[chapter["id"]] if diagnostics_by_id is not None and chapter["id"] in diagnostics_by_id else diagnose_text(text))))
        connection.executemany("INSERT OR REPLACE INTO doc_chapter_views VALUES (?,?,?,?,?,?,?)", rows)

    def _view(self, document_id: str, version_id: str | None, column: str) -> dict[str, Any]:
        if column not in {"summary_json", "index_json", "navigation_json"}:
            raise ValueError("unsupported read model")
        with self._connection() as connection:
            if version_id is None:
                document = connection.execute("SELECT latest_version_id FROM doc_documents WHERE id=?", (document_id,)).fetchone()
                if document is None:
                    raise DataError(404, "全文文档不存在")
                version_id = document["latest_version_id"]
            row = connection.execute(f"SELECT {column} FROM doc_version_views WHERE version_id=? AND document_id=? AND layout_version=?",
                                     (version_id, document_id, READ_MODEL_VERSION)).fetchone()
            if row is None:
                # Serialize only the one-time lazy rebuild. Recheck after the
                # lock so simultaneous summary/index reads do not rescan or
                # contend while upgrading a deferred read transaction.
                connection.execute("BEGIN IMMEDIATE")
                concurrent = connection.execute(f"SELECT {column} FROM doc_version_views WHERE version_id=? AND document_id=? AND layout_version=?", (version_id, document_id, READ_MODEL_VERSION)).fetchone()
                if concurrent is not None:
                    return json.loads(concurrent[column])
                stored = connection.execute("SELECT payload_json FROM doc_versions WHERE id=? AND document_id=?",
                                            (version_id, document_id)).fetchone()
                if stored is None:
                    raise DataError(404, "全文版本不存在")
                self._write_views(connection, json.loads(stored["payload_json"]))
                self._limit_read_views(connection, document_id, version_id)
                row = connection.execute(f"SELECT {column} FROM doc_version_views WHERE version_id=?", (version_id,)).fetchone()
            return json.loads(row[column])

    def get_version_summary(self, document_id: str, version_id: str | None = None) -> dict[str, Any]:
        return self._view(document_id, version_id, "summary_json")

    def get_version_navigation(self, document_id: str, version_id: str | None = None) -> dict[str, Any]:
        return self._view(document_id, version_id, "navigation_json")

    def chapter_previews(self, document_id: str, version_id: str, *, preview_chars: int = 200) -> list[dict[str, Any]]:
        """Bounded chapter heads for volume planning, without full snapshots."""
        if type(preview_chars) is not int or not 0 <= preview_chars <= 1000:
            raise DataError(422, "章节预览长度需为 0 至 1000")
        self.get_version_summary(document_id, version_id)
        with self._connection() as connection:
            return [{"id": row["chapter_id"], "title": row["title"] or "", "text": row["preview"]}
                    for row in connection.execute(
                        "SELECT chapter_id,json_extract(metadata_json,'$.title') AS title,substr(text,1,?) AS preview "
                        "FROM doc_chapter_views WHERE version_id=? AND COALESCE(json_extract(metadata_json,'$.placeholder'),0)=0 "
                        "ORDER BY chapter_order", (preview_chars, version_id))]

    def rebuild_read_views(self, document_id: str, version_id: str | None = None) -> dict[str, Any]:
        """Atomically rebuild one derived read model from its immutable snapshot."""
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            document = connection.execute("SELECT latest_version_id FROM doc_documents WHERE id=?", (document_id,)).fetchone()
            if document is None:
                raise DataError(404, "全文文档不存在")
            selected = version_id or document["latest_version_id"]
            stored = connection.execute("SELECT payload_json FROM doc_versions WHERE document_id=? AND id=?", (document_id, selected)).fetchone()
            if stored is None:
                raise DataError(404, "全文版本不存在")
            version = json.loads(stored["payload_json"])
            connection.execute("DELETE FROM doc_chapter_views WHERE version_id=?", (selected,))
            connection.execute("DELETE FROM doc_version_views WHERE version_id=?", (selected,))
            self._write_views(connection, version)
            self._limit_read_views(connection, document_id, selected)
            summary = connection.execute("SELECT summary_json FROM doc_version_views WHERE version_id=?", (selected,)).fetchone()
            return json.loads(summary["summary_json"])

    def create_version(self, document_id: str, base_version_id: str, options: dict[str, Any],
                       manual_text: str | None = None) -> dict[str, Any]:
        selected = _options(options)
        if manual_text is not None and not isinstance(manual_text, str):
            raise DataError(422, "手动编辑文本必须为字符串")
        base = self.get_version(document_id, base_version_id)
        working_text = base["text"] if manual_text is None else manual_text
        try:
            working_text.encode("utf-8", errors="strict")
        except UnicodeError:
            raise DataError(422, "手动文本包含无效 Unicode 字符") from None
        prepared = _prepare(working_text, base["text"], selected)
        prepared["active_source_ids"] = base.get("active_source_ids", self._active_source_ids(document_id, base))
        if manual_text is not None and manual_text != base["text"]:
            prepared["changes"].insert(0, {"stage": "manual_edit", "count": 1,
                                           "input_characters": len(base["text"]),
                                           "output_characters": len(manual_text),
                                           "input_sha256": base["sha256"],
                                           "output_sha256": _sha(manual_text.encode("utf-8"))})
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            document = self._document(connection, document_id)
            if document["latest_version_id"] != base_version_id:
                raise DataError(409, "全文已有新版本，请刷新后再保存")
            version = self._insert_version(connection, document_id, base_version_id,
                                           base["version_number"] + 1, base["source_text"], prepared)
            connection.execute("UPDATE doc_documents SET latest_version_id=? WHERE id=?", (version["id"], document_id))
            return version

    def create_literal_version(self, document_id: str, base_version_id: str, text: str) -> dict[str, Any]:
        """Pin assembled reference entries without applying document cleaning.

        Source/append provenance remains the existing immutable upload chain;
        the assembled text is explicitly recorded as a reference-source version.
        """
        self._valid_text(text)
        base = self.get_version(document_id, base_version_id)
        prepared = _prepare(text, base["text"], self._version_options(base), normalize=False)
        return self._commit_prepared(document_id, base, prepared,
                                     change={"stage": "reference_source", "count": 1})

    def _commit_prepared(self, document_id: str, base: dict[str, Any], prepared: dict[str, Any],
                         *, change: dict[str, Any] | None = None, source: str | None = None) -> dict[str, Any]:
        if change:
            prepared["changes"].insert(0, change)
        prepared.setdefault("active_source_ids", base.get("active_source_ids", self._active_source_ids(document_id, base)))
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            document = connection.execute("SELECT latest_version_id FROM doc_documents WHERE id=?", (document_id,)).fetchone()
            if document is None:
                raise DataError(404, "全文文档不存在")
            if document["latest_version_id"] != base["id"]:
                raise DataError(409, "全文已有新版本，请刷新后再保存")
            version = self._insert_version(connection, document_id, base["id"], base["version_number"] + 1,
                                           base["source_text"] if source is None else source, prepared)
            connection.execute("UPDATE doc_documents SET latest_version_id=? WHERE id=?", (version["id"], document_id))
            return version

    @staticmethod
    def _chapter(version: dict[str, Any], chapter_id: str) -> dict[str, Any]:
        for chapter in version["chapters"]:
            if chapter["id"] == chapter_id:
                return chapter
        raise DataError(404, "章节不存在")

    @staticmethod
    def _version_options(base: dict[str, Any], **changes: Any) -> dict[str, Any]:
        options = dict(base.get("options", {}))
        if base.get("preprocessing_version", 1) < PREPROCESSING_VERSION and options.get("author_keywords") == LEGACY_AUTHOR_KEYWORDS:
            options["author_keywords"] = list(AUTHOR_KEYWORDS)
        return _options({**options, **changes})

    def chapter_index(self, document_id: str, version_id: str | None = None) -> dict[str, Any]:
        return self._view(document_id, version_id, "index_json")

    def get_chapter(self, document_id: str, version_id: str, chapter_id: str,
                    start: int = 0, limit: int = 20000, *, body_only: bool = False) -> dict[str, Any]:
        if isinstance(start, bool) or not isinstance(start, int) or start < 0 or isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100000:
            raise DataError(422, "章节分页范围无效")
        # Ensure a legacy snapshot is materialized only once. The existence
        # check is small; body reads do not deserialize the chapter directory.
        with self._connection() as connection:
            exists = connection.execute("SELECT 1 FROM doc_version_views WHERE version_id=? AND document_id=? AND layout_version=?", (version_id, document_id, READ_MODEL_VERSION)).fetchone()
        if exists is None:
            self.get_version_summary(document_id, version_id)
        with self._connection() as connection:
            row = connection.execute("SELECT metadata_json,text,proposals_json,diagnostics_json FROM doc_chapter_views WHERE version_id=? AND chapter_id=?", (version_id, chapter_id)).fetchone()
            if row is None:
                raise DataError(404, "章节不存在")
            metadata = json.loads(row["metadata_json"])
            text = row["text"]
            body_offset = metadata["body_start"] - metadata["start"]
            body_start, body_end = chapter_body_span({**metadata, "text": text})
            response = {**metadata, "chapter": metadata, "document_id": document_id, "version_id": version_id,
                        "proposals": json.loads(row["proposals_json"]),
                        "diagnostics": json.loads(row["diagnostics_json"]), "offset_unit": "unicode_codepoints"}
            title_start, title_end = chapter_title_span(metadata)
            response.update(title_source_start=title_start, title_source_end=title_end,
                            title_proposals=[proposal for proposal in response["proposals"]
                                             if proposal["start"] >= title_start and proposal["end"] <= title_end],
                            heading_proposals=[proposal for proposal in response["proposals"] if proposal.get("scope") == "heading"],
                            structured_issues=[{"id": f"structure-{metadata['id']}-{issue['kind']}",
                                                "kind": issue["kind"], "label": issue["label"],
                                                "reason": "章节正文篇幅与本作品其他章节差异较大" if issue["kind"] == "length" else issue["label"],
                                                "location": "heading"}
                                               for issue in metadata.get("issues", []) if issue["kind"] in {"length", "duplicate", "gap"}])
            if body_only:
                actual_start = min(body_end, max(start, body_start))
                actual_end = min(actual_start + limit, body_end)
                expected = text[actual_start:actual_end]
                displayed, offsets = _normalized_slice(expected, actual_start)
                return {**response, "text": displayed, "start": actual_start, "end": actual_end,
                        "total": body_end, "source_total": len(text), "body_offset": body_start,
                        "body_total": body_end - body_start, "editable_start": actual_start,
                        "editable_end": actual_end, "editable_text": displayed, "expected_text": expected,
                        "body_text": None,
                        "source_offset_map": offsets, "next_start": actual_end if actual_end < body_end else None,
                        "body_only": True}
            return {**response, "text": text[start:start + limit], "start": start,
                    "end": min(start + limit, len(text)), "total": len(text), "body_offset": body_offset,
                    "body_total": len(text) - body_offset, "editable_start": max(start, body_offset),
                    "editable_end": min(start + limit, len(text)),
                    "editable_text": text[max(start, body_offset):min(start + limit, len(text))],
                    "body_text": text[body_offset:] if len(text) - body_offset <= 200000 else None}

    def _assert_current(self, document_id: str, base_version_id: str) -> None:
        with self._connection() as connection:
            document = connection.execute("SELECT latest_version_id FROM doc_documents WHERE id=?", (document_id,)).fetchone()
            if document is None:
                raise DataError(404, "全文文档不存在")
            if document["latest_version_id"] != base_version_id:
                raise DataError(409, "全文已有新版本，请刷新后再保存")

    @staticmethod
    def _new_chapter_marker(chapter: dict[str, Any], title: str, options: dict[str, Any]) -> str:
        DocumentStore._valid_text(title)
        title = title.strip()
        if len(title) > 80 or "\n" in title or "\r" in title or (not title and chapter.get("number") is None):
            raise DataError(422, "章节标题需为不超过 80 字的单行文本")
        marker = chapter.get("raw_marker") or ""
        if not marker:
            raise DataError(422, "该段落没有章节标题，请使用新增章节")
        left, right = chapter_title_span(chapter)
        if left == right and title:
            marker = marker[:left] + " " + title + marker[right:]
        else:
            marker = marker[:left] + title + marker[right:]
        parsed = split_chapters(marker + "\n", heading_formats=options.get("heading_formats") or [chapter["pattern"]],
                                custom_formats=options.get("custom_formats", []))["chapters"]
        if (len(parsed) != 1 or parsed[0].get("raw_marker") != marker
                or parsed[0].get("number") != chapter.get("number") or parsed[0].get("title") != title):
            raise DataError(422, "该标题未能识别为独立章节；请移除标题末尾的句号、问号等标点，或在「分卷与章节识别」中调整格式后重试。修改尚未保存。")
        return marker

    def _incremental_chapter_edit(self, document_id: str, base: dict[str, Any], chapter: dict[str, Any],
                                  start: int, end: int, replacement: str, *, title: str | None = None) -> dict[str, Any]:
        """Refresh only the edited chapter; retain downstream chunk identities."""
        options = self._version_options(base)
        marker = self._new_chapter_marker(chapter, title, options) if title is not None else None
        changed_text = chapter["text"][:start] + replacement + chapter["text"][end:]
        marker_delta = 0
        if marker is not None:
            marker_delta = len(marker) - len(chapter["raw_marker"])
            changed_text = marker + changed_text[len(chapter["raw_marker"]):]
        updated = base["text"][:chapter["start"]] + changed_text + base["text"][chapter["end"]:]
        delta = len(replacement) - (end - start)
        delta += marker_delta
        chapters = []
        changed = None
        for item in base["chapters"]:
            current = dict(item)
            if item["id"] == chapter["id"]:
                current["text"] = changed_text
                if marker is not None:
                    current.update(raw_marker=marker, title=title.strip(), body_start=item["body_start"] + marker_delta)
                current["content"] = current["text"][current["body_start"] - current["start"]:]
                current["end"] += delta
                changed = current
            elif item["start"] >= chapter["end"]:
                for field in ("start", "end", "body_start"):
                    current[field] += delta
            chapters.append(current)
        assert changed is not None
        # Small precomputed diagnostics do not carry any source text or full
        # version. Invalidating one chapter keeps unchanged evidence exact.
        self.get_version_summary(document_id, base["id"])
        with self._connection() as connection:
            cached = connection.execute("SELECT chapter_id,proposals_json,diagnostics_json FROM doc_chapter_views WHERE version_id=?", (base["id"],)).fetchall()
        proposals = {row["chapter_id"]: json.loads(row["proposals_json"]) for row in cached if row["chapter_id"] != chapter["id"]}
        diagnostics = {row["chapter_id"]: json.loads(row["diagnostics_json"]) for row in cached if row["chapter_id"] != chapter["id"]}
        diagnostics[chapter["id"]] = diagnose_text(changed["text"])
        gaps = annotate_chapters(chapters, options, proposals_by_id=proposals)
        chunks = []
        changed_index = next(index for index, item in enumerate(chapters) if item["id"] == chapter["id"])
        for item in base["chunks"]:
            if item["chapter_id"] == chapter["id"]:
                continue
            current = dict(item)
            if item["start"] >= chapter["end"]:
                current["start"] += delta
                current["end"] += delta
            chunks.append(current)
        for index, chunk in enumerate(chunk_text(changed["text"], options["chunk_chars"])):
            chunks.append({**chunk, "start": changed["start"] + chunk["start"],
                           "end": changed["start"] + chunk["end"],
                           "id": f"{changed['id']}-chunk-{index + 1}", "chapter_id": changed["id"],
                           "chapter_index": changed_index})
        chunks.sort(key=lambda item: item["start"])
        if "".join(item["text"] for item in chapters) != updated or "".join(item["text"] for item in chunks) != updated:
            raise DataError(500, "正文覆盖检查失败，未保存不完整版本")
        whole_diagnostics = []
        for item in chapters:
            # Legacy versions may have no read model. Only those missing
            # cached entries need scanning, rather than every normal save.
            if item["id"] not in diagnostics:
                diagnostics[item["id"]] = diagnose_text(item["text"])
            whole_diagnostics.extend({**finding, "start": item["start"] + finding["start"],
                                      "end": item["start"] + finding["end"]} for finding in diagnostics[item["id"]])
        whole_diagnostics.sort(key=lambda item: (item["start"], item["code"]))
        diff, truncated = _display_diff(base["text"], updated)
        return {"text": updated, "sha256": _sha(updated.encode()), "chapters": chapters, "chunks": chunks,
                "diagnostics": whole_diagnostics, "source_diagnostics": base.get("source_diagnostics", []),
                "changes": [], "diff": diff, "diff_truncated": truncated,
                "coverage": {**base["coverage"], "characters": len(updated), "covered_characters": len(updated),
                             "reconstructed_sha256": _sha(updated.encode()), "contiguous": True, "complete": True,
                             "chunks_complete": True, "chunk_count": len(chunks), "chapter_count": len(chapters)},
                "pattern": base.get("pattern"), "fallback_used": base.get("fallback_used", False),
                "heading_formats": base.get("heading_formats", []), "candidates": base.get("candidates", []),
                "gaps": gaps, "options": options, "_review_proposals": proposals,
                "_review_diagnostics": diagnostics}

    def _fill_missing(self, document_id: str, base: dict[str, Any], chapter_id: str,
                      body_text: str, title: str | None = None) -> dict[str, Any]:
        index = self.chapter_index(document_id, base["id"])
        missing = next((item for item in index["chapters"] if item["id"] == chapter_id and item.get("placeholder")), None)
        if missing is None or missing.get("gap_range"):
            raise DataError(404, "缺失章节占位项不存在，请刷新目录")
        title = "" if title is None else title.strip()
        self._valid_text(title)
        if len(title) > 80 or "\n" in title or "\r" in title:
            raise DataError(422, "章节标题需为不超过 80 字的单行文本")
        next_chapter = self._chapter(base, missing["before_chapter_id"])
        position = next_chapter["start"]
        style = "\r\n" if "\r\n" in next_chapter["text"] else "\n"
        heading = chapter_label({"kind": "chapter", "number": missing["number"], "title": title})
        body = normalize_chapter_body(body_text, trim_boundaries=True)
        inserted = heading + style + body + style
        prefix = base["text"][:position]
        if prefix and not prefix.endswith(("\n", "\r")):
            inserted = style + inserted
        updated = prefix + inserted + base["text"][position:]
        options = self._version_options(base)
        # The canonical replacement may differ from an explicitly selected
        # source format (e.g. Chapter N). Add 第N章 without discarding settings.
        if options["heading_formats"] and "第N章" not in options["heading_formats"]:
            options["heading_formats"] = [*options["heading_formats"], "第N章"]
        prepared = _prepare(updated, base["text"], options, normalize=False)
        heading_start = position + (len(style) if prefix and not prefix.endswith(("\n", "\r")) else 0)
        recognized = next((chapter for chapter in prepared["chapters"]
                           if chapter["start"] == heading_start and chapter.get("number") == missing["number"]
                           and chapter.get("raw_marker", "").rstrip("\r") == heading
                           and chapter.get("title") == title), None)
        if recognized is None or len(prepared["chapters"]) != len(base["chapters"]) + 1:
            raise DataError(422, "该标题未能识别为独立章节；请移除标题末尾的句号、问号等标点，或在「分卷与章节识别」中调整格式后重试。正文尚未保存。")
        return self._commit_prepared(document_id, base, prepared,
                                     change={"stage": "fill_missing_chapter", "chapter_id": chapter_id,
                                             "number": missing["number"], "title": title, "count": 1})

    def edit_chapter(self, document_id: str, base_version_id: str, chapter_id: str, body_text: str,
                     *, title: str | None = None) -> dict[str, Any]:
        self._valid_text(body_text)
        self._assert_current(document_id, base_version_id)
        base = self.get_version(document_id, base_version_id)
        if chapter_id.startswith("missing-before-"):
            return self._fill_missing(document_id, base, chapter_id, body_text, title)
        chapter = self._chapter(base, chapter_id)
        # Keep the exact source separator outside the editable body. In
        # particular, a CRLF heading must never become CRCRLF after saving.
        start, end = chapter_body_span(chapter)
        replacement = normalize_chapter_body(body_text, trim_boundaries=True)
        if replacement and chapter.get("raw_marker") and start == chapter["body_start"] - chapter["start"] and not chapter["text"][:start].endswith(("\n", "\r")):
            replacement = "\n" + replacement
        prepared = self._incremental_chapter_edit(document_id, base, chapter, start, end, replacement, title=title)
        return self._commit_prepared(document_id, base, prepared,
                                     change={"stage": "chapter_edit", "chapter_id": chapter_id, "count": 1})

    def edit_chapter_slice(self, document_id: str, base_version_id: str, chapter_id: str,
                           start: int, end: int, text: str, expected_text: str, *, title: str | None = None) -> dict[str, Any]:
        """Replace one raw source span displayed by the body-only page editor."""
        self._valid_text(text)
        self._valid_text(expected_text)
        self._assert_current(document_id, base_version_id)
        base = self.get_version(document_id, base_version_id)
        chapter = self._chapter(base, chapter_id)
        body_offset = chapter["body_start"] - chapter["start"]
        if (isinstance(start, bool) or not isinstance(start, int) or isinstance(end, bool) or not isinstance(end, int)
                or not body_offset <= start <= end <= len(chapter["text"])):
            raise DataError(422, "正文分页编辑范围无效")
        if chapter["text"][start:end] != expected_text:
            raise DataError(409, "正文内容已变化，请刷新后再保存")
        # A title-only save of a legacy blank-line view must not silently
        # rewrite the source body merely because the editor displays one line.
        unchanged_body_view = title is not None and text == _normalized_slice(expected_text, start)[0]
        replacement = expected_text if unchanged_body_view else normalize_chapter_body(text)
        # Legacy callers can include the heading separator in the requested
        # span. Reintroduce its exact token when an edit removes it.
        body_start, body_end = chapter_body_span(chapter)
        if chapter.get("raw_marker") and start == body_offset and start < body_start:
            # raw_marker can end with the CR half of CRLF in old snapshots.
            # Match the existing separator instead of stacking CR + CRLF.
            replacement = re.sub(r"^(?:[ \t\u3000]*(?>\r\n|\r|\n))+", "", replacement)
            replacement = chapter["text"][body_offset:body_start] + replacement
        elif (replacement and chapter.get("raw_marker") and start == body_offset
              and not chapter["text"][:start].endswith(("\n", "\r"))
              and not replacement.startswith(("\n", "\r"))):
            replacement = "\n" + replacement
        if end > body_end and chapter["end"] < len(base["text"]):
            replacement = re.sub(r"(?:(?>\r\n|\r|\n)[ \t\u3000]*)+$", "", replacement)
            replacement += chapter["text"][body_end:end]
        prepared = self._incremental_chapter_edit(document_id, base, chapter, start, end, replacement, title=title)
        return self._commit_prepared(document_id, base, prepared,
                                     change={"stage": "chapter_page_edit", "chapter_id": chapter_id,
                                             "start": start, "end": end, "count": 1})

    def findings(self, document_id: str, version_id: str | None = None, *,
                 chapter_id: str | None = None, start: int = 0, limit: int | None = None) -> dict[str, Any]:
        if (type(start) is not int or start < 0 or
                (limit is not None and (type(limit) is not int or not 1 <= limit <= 10000))):
            raise DataError(422, "问题清单分页范围无效")
        summary = self.get_version_summary(document_id, version_id)
        with self._connection() as connection:
            query = "SELECT metadata_json,proposals_json,text FROM doc_chapter_views WHERE version_id=?"
            parameters: tuple = (summary["id"],)
            if chapter_id is not None:
                query += " AND chapter_id=?"
                parameters += (chapter_id,)
            rows = connection.execute(query + " ORDER BY chapter_order", parameters).fetchall()
        if chapter_id is not None and not rows:
            raise DataError(404, "章节不存在")
        items = []
        issue_chapters = 0
        for row in rows:
            chapter = json.loads(row["metadata_json"])
            proposals = json.loads(row["proposals_json"])
            issue_chapters += bool(chapter.get("issues"))
            for proposal in proposals:
                items.append({**proposal, "chapter_id": chapter["id"], "chapter_index": chapter.get("index", 0),
                              "chapter_title": chapter.get("display_label") or chapter_label(chapter),
                              "context_before": row["text"][max(0, proposal["start"] - 40):proposal["start"]],
                              "context_after": row["text"][proposal["end"]:proposal["end"] + 40]})
        total = len(items)
        return {"document_id": document_id, "version_id": summary["id"], "offset_unit": "unicode_codepoints",
                "items": items[start:None if limit is None else start + limit], "total": total,
                "start": start, "limit": limit, "issue_chapter_count": issue_chapters,
                "next_start": start + limit if limit is not None and start + limit < total else None}

    def formatted_export(self, document_id: str, version_id: str | None = None) -> str:
        """Final TXT: one heading, a body without empty lines, explicit gaps."""
        summary = self.get_version_summary(document_id, version_id)
        with self._connection() as connection:
            rows = connection.execute("SELECT metadata_json,text FROM doc_chapter_views WHERE version_id=? ORDER BY chapter_order",
                                      (summary["id"],)).fetchall()
        sections = []
        for row in rows:
            chapter = json.loads(row["metadata_json"])
            chapter["text"] = row["text"]
            if chapter.get("gap_range"):
                # Hostile million-chapter gaps remain explicit and bounded.
                sections.append(f"{chapter_label(chapter)}（缺失，待补充）")
            elif chapter.get("placeholder"):
                sections.append(chapter_label(chapter))
            elif chapter.get("raw_marker"):
                begin, end = chapter_body_span(chapter)
                body = normalize_chapter_body(chapter["text"][begin:end], trim_boundaries=True)
                sections.append(chapter_label(chapter) + ("\n" + body if body else ""))
            else:
                body = normalize_chapter_body(chapter["text"], trim_boundaries=True)
                if body:
                    sections.append(body)
        return "\n".join(sections) + ("\n" if sections else "")

    @staticmethod
    def _valid_text(text: Any) -> None:
        if not isinstance(text, str):
            raise DataError(422, "章节正文必须为字符串")
        try:
            text.encode("utf-8", errors="strict")
        except UnicodeError:
            raise DataError(422, "文本包含无效 Unicode 字符") from None

    def _resegment_candidate(self, document_id: str, base_version_id: str,
                             heading_formats: list[str], custom_formats: list[dict[str, Any]] | None,
                             repair_duplicates: bool) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        self._assert_current(document_id, base_version_id)
        base = self.get_version(document_id, base_version_id)
        options = self._version_options(base, heading_formats=heading_formats,
                                        custom_formats=custom_formats if custom_formats is not None else base["options"].get("custom_formats", []))
        from .text_processing import heading_patterns
        available = {name for name, _ in heading_patterns(options["custom_formats"])}
        if set(heading_formats) - available:
            raise DataError(422, "选中的章节格式不存在")
        parsed = split_chapters(base["text"], heading_formats=heading_formats,
                                custom_formats=options["custom_formats"], fallback_chars=options["fallback_chars"])
        initial_chapters = [{**chapter, "id": f"chapter-{index + 1}"} for index, chapter in enumerate(parsed["chapters"])]
        repairs = duplicate_heading_repairs(initial_chapters)
        updated = base["text"]
        if repair_duplicates and repairs:
            pieces, position = [], 0
            for repair in repairs:
                pieces.extend((updated[position:repair["start"]], repair["replacement"]))
                position = repair["end"]
            pieces.append(updated[position:])
            updated = "".join(pieces)
        prepared = _prepare(updated, base["text"], options, normalize=False, normalize_layout=True,
                            chapter_analysis=parsed if updated == base["text"] else None)

        def directory(chapters):
            return [{"chapter_id": item["id"], "number": item.get("number"), "title": item.get("title", ""),
                     "kind": item.get("kind"), "start": item["start"], "end": item["end"],
                     "body_characters": len(item.get("content", "").strip())} for item in chapters]

        def counts(chapters):
            sequence = chapter_sequence(chapters)
            return {"chapter_count": len(chapters),
                    "empty_chapter_count": sum(item.get("kind") == "chapter" and not item.get("content", "").strip() for item in chapters),
                    "duplicate_chapter_count": len(sequence["duplicates"])}

        old_directory, new_directory = directory(base["chapters"]), directory(prepared["chapters"])
        changes = []
        for index in range(max(len(old_directory), len(new_directory))):
            before = old_directory[index] if index < len(old_directory) else None
            after = new_directory[index] if index < len(new_directory) else None
            if before != after:
                changes.append({"kind": "changed" if before and after else "added" if after else "removed",
                                "before": before, "after": after})
        token = _sha(_json({"document_id": document_id, "base_version_id": base_version_id,
                            "options": options, "repair_duplicates": repair_duplicates,
                            "candidate_sha256": prepared["sha256"], "directory": new_directory}).encode())
        preview = {"document_id": document_id, "base_version_id": base_version_id, "preview_token": token,
                   "conflict": bool(changes), "old": counts(base["chapters"]), "new": counts(prepared["chapters"]),
                   "changes": changes[:500], "change_count": len(changes), "changes_truncated": len(changes) > 500,
                   "duplicate_repairs": [{key: value for key, value in repair.items() if key not in {"replacement", "start", "end"}}
                                         for repair in repairs[:500]], "duplicate_repair_count": len(repairs),
                   "duplicate_repairs_truncated": len(repairs) > 500, "repair_duplicates": repair_duplicates}
        return base, prepared, preview

    def preview_resegment(self, document_id: str, base_version_id: str,
                          heading_formats: list[str], custom_formats: list[dict[str, Any]] | None = None,
                          *, repair_duplicates: bool = False) -> dict[str, Any]:
        # Candidate payloads are request-local. No second whole-book snapshot
        # stays resident or enters version history merely for opening a dialog.
        return self._resegment_candidate(document_id, base_version_id, heading_formats, custom_formats,
                                         repair_duplicates)[2]

    def resegment(self, document_id: str, base_version_id: str,
                  heading_formats: list[str], custom_formats: list[dict[str, Any]] | None = None,
                  *, conflict_policy: str | None = None, preview_token: str | None = None,
                  repair_duplicates: bool = False, require_confirmation: bool = False) -> dict[str, Any]:
        if conflict_policy not in {None, "keep_old", "use_new"}:
            raise DataError(422, "请选择保留旧版或以新版为准")
        base, prepared, preview = self._resegment_candidate(document_id, base_version_id, heading_formats,
                                                            custom_formats, repair_duplicates)
        if preview_token is not None and preview_token != preview["preview_token"]:
            raise DataError(409, {"code": "chapter_preview_changed", "message": "识别结果已变化，请重新预览", "preview": preview})
        if require_confirmation and preview["conflict"] and (conflict_policy is None or preview_token is None):
            raise DataError(409, {"code": "chapter_resegment_conflict", "message": "章节目录有变化，请选择保留旧版或以新版为准", "preview": preview})
        if conflict_policy == "keep_old":
            self._assert_current(document_id, base_version_id)
            return base
        if prepared["sha256"] == base["sha256"] and prepared["options"] == self._version_options(base):
            self._assert_current(document_id, base_version_id)
            return base
        return self._commit_prepared(document_id, base, prepared,
                                     change={"stage": "chapter_resegment", "count": len(prepared["chapters"]),
                                             "duplicate_repairs": preview["duplicate_repairs"] if repair_duplicates else [],
                                             "previous_chapter_count": preview["old"]["chapter_count"]})

    def update_preprocess_settings(self, document_id: str, base_version_id: str,
                                   author_keywords: list[str] | None = None,
                                   garble_rules: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        base = self.get_version(document_id, base_version_id)
        changes = {}
        if author_keywords is not None:
            changes["author_keywords"] = author_keywords
        if garble_rules is not None:
            changes["garble_rules"] = garble_rules
        prepared = _prepare(base["text"], base["text"], self._version_options(base, **changes), normalize=False)
        return self._commit_prepared(document_id, base, prepared, change={"stage": "preprocess_settings", "count": 1})

    def apply_chapter_cleaning(self, document_id: str, base_version_id: str,
                               selections: list[dict[str, Any]]) -> dict[str, Any]:
        if not isinstance(selections, list) or not selections:
            raise DataError(422, "请选择要清理的标注")
        self._assert_current(document_id, base_version_id)
        base = self.get_version(document_id, base_version_id)
        options = self._version_options(base)
        ranges = []
        seen_chapters = set()
        removed = []
        heading_checks = []
        for selection in selections:
            if not isinstance(selection, dict) or not isinstance(selection.get("chapter_id"), str):
                raise DataError(422, "清理选择无效")
            chapter_id = selection["chapter_id"]
            if chapter_id in seen_chapters:
                raise DataError(422, "清理章节不能重复")
            seen_chapters.add(chapter_id)
            chapter = self._chapter(base, chapter_id)
            ids = selection.get("proposal_ids")
            if not isinstance(ids, list) or any(not isinstance(identifier, str) for identifier in ids) or len(ids) != len(set(ids)):
                raise DataError(422, "清理标注选择无效")
            proposals = {proposal["id"]: proposal for proposal in chapter_proposals(chapter, options) if proposal["removable"]}
            if set(ids) - set(proposals):
                raise DataError(409, "清理标注已变化，请刷新后重试")
            heading_ranges = []
            for identifier in ids:
                proposal = proposals[identifier]
                if proposal.get("scope") == "heading":
                    left, right = chapter_title_span(chapter)
                    if not left <= proposal["start"] <= proposal["end"] <= right:
                        raise DataError(422, "该标注涉及章节序号，请在标题中手动修复")
                    heading_ranges.append((proposal["start"], proposal["end"]))
                ranges.append((chapter["start"] + proposal["start"], chapter["start"] + proposal["end"]))
                removed.append({"chapter_id": chapter_id, "proposal_id": identifier, "kind": proposal["kind"],
                                "scope": proposal.get("scope", "body"), "characters": proposal["end"] - proposal["start"]})
            if heading_ranges:
                heading_checks.append((chapter, _without_ranges(chapter["raw_marker"], heading_ranges)))
        if not ranges:
            raise DataError(422, "尚未选择要清理的内容")
        updated = _without_ranges(base["text"], ranges)
        prepared = _prepare(updated, base["text"], options, normalize=False)
        for chapter, marker in heading_checks:
            new_start = chapter["start"] - sum(end - start for start, end in ranges if end <= chapter["start"])
            if not any(item["start"] == new_start and item.get("number") == chapter.get("number")
                       and item.get("raw_marker") == marker for item in prepared["chapters"]):
                raise DataError(422, "清理后的标题未能识别为独立章节，请保留该标记并手动修改标题。内容尚未保存。")
        return self._commit_prepared(document_id, base, prepared,
                                     change={"stage": "reviewed_chapter_cleaning", "count": len(removed), "removed": removed})

    def rename_chapter(self, document_id: str, base_version_id: str, chapter_id: str, title: str) -> dict[str, Any]:
        self._assert_current(document_id, base_version_id)
        base = self.get_version(document_id, base_version_id)
        chapter = self._chapter(base, chapter_id)
        prepared = self._incremental_chapter_edit(document_id, base, chapter,
                                                   len(chapter["raw_marker"]), len(chapter["raw_marker"]), "", title=title)
        return self._commit_prepared(document_id, base, prepared, change={"stage": "chapter_rename", "chapter_id": chapter_id, "count": 1})

    def insert_chapter(self, document_id: str, base_version_id: str, after_chapter_id: str | None,
                       heading: str, body_text: str) -> dict[str, Any]:
        self._valid_text(heading)
        self._valid_text(body_text)
        if not heading.strip() or len(heading) > 160 or "\n" in heading or "\r" in heading:
            raise DataError(422, "新章节标题需为单行文本")
        base = self.get_version(document_id, base_version_id)
        offset = self._chapter(base, after_chapter_id)["end"] if after_chapter_id is not None else 0
        prefix, suffix = base["text"][:offset], base["text"][offset:]
        inserted = ("\n" if prefix and not prefix.endswith("\n") else "") + heading.strip() + "\n" + body_text
        if suffix and not inserted.endswith("\n"):
            inserted += "\n"
        updated = prefix + inserted + suffix
        prepared = _prepare(updated, base["text"], self._version_options(base), normalize=False)
        heading_start = offset + (1 if prefix and not prefix.endswith("\n") else 0)
        recognized = any(chapter["start"] == heading_start and chapter["raw_marker"].strip() == heading.strip()
                         for chapter in prepared["chapters"])
        if not recognized:
            raise DataError(422, "新标题未匹配当前章节格式，请使用例如「第一章 标题」，或先调整章节格式")
        return self._commit_prepared(document_id, base, prepared, change={"stage": "chapter_insert", "count": 1, "after_chapter_id": after_chapter_id})

    def delete_chapters(self, document_id: str, base_version_id: str, chapter_ids: list[str]) -> dict[str, Any]:
        if not isinstance(chapter_ids, list) or not chapter_ids or any(not isinstance(identifier, str) for identifier in chapter_ids):
            raise DataError(422, "请选择要删除的章节")
        if len(chapter_ids) != len(set(chapter_ids)):
            raise DataError(422, "删除章节不能重复")
        self._assert_current(document_id, base_version_id)
        base = self.get_version(document_id, base_version_id)
        for identifier in chapter_ids:
            self._chapter(base, identifier)
        try:
            updated, renumbered = renumber_after_deletion(base["text"], base["chapters"], set(chapter_ids),
                                                         base["options"].get("custom_formats", []))
        except (ValueError, TimeoutError):
            raise DataError(422, "章节编号无法安全调整，请检查当前章节格式；内容尚未保存") from None
        prepared = _prepare(updated, base["text"], self._version_options(base), normalize=False)
        return self._commit_prepared(document_id, base, prepared, change={"stage": "chapter_delete", "chapter_ids": chapter_ids,
                                                                      "count": len(chapter_ids), "renumbered": renumbered})

    def restore_version(self, document_id: str, base_version_id: str, target_version_id: str) -> dict[str, Any]:
        base = self.get_version(document_id, base_version_id)
        target = self.get_version(document_id, target_version_id)
        prepared = _prepare(target["text"], base["text"], self._version_options(target), normalize=False)
        prepared["active_source_ids"] = target.get("active_source_ids", self._active_source_ids(document_id, target))
        return self._commit_prepared(document_id, base, prepared, source=target["source_text"],
                                     change={"stage": "restore_version", "target_version_id": target_version_id, "count": 1})

    def _active_source_ids(self, document_id: str, version: dict[str, Any]) -> list[str]:
        """Resolve legacy versions without changing their immutable payloads."""
        if "active_source_ids" in version:
            return list(version["active_source_ids"])
        source_ids = []
        cursor = version
        while cursor:
            replacement = False
            for change in cursor.get("changes", []):
                if change.get("stage") in {"append_source", "replace_source"} and change.get("source_id"):
                    source_ids.append(change["source_id"])
                    replacement = replacement or change["stage"] == "replace_source"
                elif change.get("stage") == "restore_version":
                    restored = self.get_version(document_id, change["target_version_id"])
                    return self._active_source_ids(document_id, restored) + list(reversed(source_ids))
            if replacement:
                return list(reversed(source_ids))
            parent = cursor.get("parent_version_id")
            cursor = self.get_version(document_id, parent) if parent else None
        return ["original", *reversed(source_ids)]

    def _ingestion_record(self, key: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM doc_ingestions WHERE key=?", (key,)).fetchone()
            if row is None:
                return None
            document_id = row["document_id"]
            record = dict(row)
            if record["mode"] is None:
                # Old stores did not persist upload receipts separately. Only
                # a replay of that key pays this one-time migration cost; normal
                # startup, navigation and every subsequent save avoid it.
                for item in connection.execute("SELECT json_extract(payload_json,'$.changes') AS changes FROM doc_versions "
                                               "WHERE document_id=? ORDER BY version_number", (document_id,)):
                    change = next((value for value in json.loads(item["changes"] or "[]") if value.get("ingestion_key") == key), None)
                    if change is not None:
                        record.update(source_id=change.get("source_id"), mode="replace" if change.get("stage") == "replace_source" else "append",
                                      original_sha256=change.get("original_sha256"))
                        break
                if record["mode"] is None:
                    original = connection.execute("SELECT original_sha256 FROM doc_documents WHERE id=?", (document_id,)).fetchone()
                    record.update(mode="new", original_sha256=original[0])
                connection.execute("UPDATE doc_ingestions SET source_id=?,mode=?,original_sha256=? WHERE key=?",
                                   (record["source_id"], record["mode"], record["original_sha256"], key))
                if record["source_id"]:
                    connection.execute("UPDATE doc_sources SET mode=? WHERE id=? AND mode='unknown'",
                                       (record["mode"], record["source_id"]))
            if record["mode"] == "new":
                selected = connection.execute("SELECT id FROM doc_versions WHERE document_id=? ORDER BY version_number LIMIT 1", (document_id,)).fetchone()
                version_id = selected[0]
            else:
                selected = connection.execute("SELECT version_id FROM doc_sources WHERE document_id=? AND id=?", (document_id, record["source_id"])).fetchone()
                version_id = selected[0] if selected and selected[0] else connection.execute(
                    "SELECT latest_version_id FROM doc_documents WHERE id=?", (document_id,)).fetchone()[0]
            return {**record, "version_id": version_id}

    def ingestion_result(self, key: str) -> dict[str, Any] | None:
        """Resolve a durable receipt without deserializing a book's history."""
        record = self._ingestion_record(key)
        return {"document_id": record["document_id"], "version_id": record["version_id"]} if record else None

    def append(self, document_id: str, base_version_id: str, filename: str, raw: bytes,
               encoding: str = "auto", *, idempotency_key: str | None = None,
               converted_text: str | None = None, preserve_text: bool = False, normalize_layout: bool = False,
               progress: Callable[[str, int, int], None] | None = None) -> dict[str, Any]:
        return self._update_source(document_id, base_version_id, filename, raw, encoding, mode="append",
                                   idempotency_key=idempotency_key, converted_text=converted_text,
                                   preserve_text=preserve_text, normalize_layout=normalize_layout, progress=progress)

    def replace(self, document_id: str, base_version_id: str, filename: str, raw: bytes,
                encoding: str = "auto", *, idempotency_key: str | None = None,
                converted_text: str | None = None, preserve_text: bool = True, normalize_layout: bool = False,
                progress: Callable[[str, int, int], None] | None = None) -> dict[str, Any]:
        return self._update_source(document_id, base_version_id, filename, raw, encoding, mode="replace",
                                   idempotency_key=idempotency_key, converted_text=converted_text,
                                   preserve_text=preserve_text, normalize_layout=normalize_layout, progress=progress)

    def _update_source(self, document_id: str, base_version_id: str, filename: str, raw: bytes,
                       encoding: str, *, mode: str, idempotency_key: str | None,
                       converted_text: str | None, preserve_text: bool, normalize_layout: bool,
                       progress: Callable[[str, int, int], None] | None) -> dict[str, Any]:
        if not isinstance(filename, str) or not filename or len(filename) > 255 or re.search(r"[\x00-\x1f]", filename):
            raise DataError(422, "文件名无效")
        filename = filename.replace("\\", "/").rsplit("/", 1)[-1]
        suffix = Path(filename).suffix.lower()
        if (suffix not in SUPPORTED_FORMATS and not (suffix == ".pdf" and converted_text is not None)) or not isinstance(raw, bytes) or not raw:
            raise DataError(422, "请上传支持的非空文本；PDF 需先转换为纯文本")
        if len(raw) > MAX_UPLOAD_BYTES:
            raise DataError(413, "文件超过 50 MiB 限制")
        if idempotency_key is not None and (not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 200):
            raise DataError(422, "导入幂等键必须为 1 至 200 字的字符串")
        if converted_text is not None:
            self._valid_text(converted_text)
            if suffix not in {".epub", ".pdf"} or not converted_text.strip():
                raise DataError(422, "转换后的正文仅用于非空 EPUB/PDF 纯文本")
            source, used_encoding = converted_text, "utf-8-converted"
        elif suffix == ".docx":
            source, used_encoding = _docx_text(raw), "xml"
        elif suffix == ".epub":
            source, used_encoding = _epub_text(raw), "xml"
        else:
            source, used_encoding = _decode(raw, encoding)
        if idempotency_key:
            existing = self._ingestion_record(idempotency_key)
            if existing:
                if (existing["document_id"] != document_id or existing["original_sha256"] != _sha(raw)
                        or existing["mode"] != mode):
                    raise DataError(409, "同一导入幂等键对应了不同原件或操作")
                return self.get_version(existing["document_id"], existing["version_id"])
        base = self.get_version(document_id, base_version_id)
        from .text_processing import clean_text
        new_text = source if preserve_text else clean_text(source)["cleaned_text"]
        if mode == "append":
            separator = "" if not base["text"] or base["text"].endswith("\n") else "\n"
            updated = base["text"] + separator + new_text
            combined_source = base["source_text"] + ("" if not base["source_text"] or base["source_text"].endswith("\n") else "\n") + source
        else:
            updated, combined_source = new_text, source
        prepared = _prepare(updated, base["text"], self._version_options(base), normalize=False, normalize_layout=normalize_layout, progress=progress)
        source_id = uuid.uuid4().hex
        prepared["active_source_ids"] = ([*self._active_source_ids(document_id, base), source_id]
                                         if mode == "append" else [source_id])
        prepared["changes"].insert(0, {"stage": f"{mode}_source", "source_id": source_id,
                                       "filename": filename, "count": 1, "appended_characters": len(new_text) if mode == "append" else 0,
                                       "original_sha256": _sha(raw), "ingestion_key": idempotency_key})
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if idempotency_key:
                previous = connection.execute("SELECT document_id FROM doc_ingestions WHERE key=?", (idempotency_key,)).fetchone()
                if previous is not None:
                    raise DataError(409, "上传操作正在提交，请刷新后重试")
            document = self._document(connection, document_id)
            if document["latest_version_id"] != base_version_id:
                raise DataError(409, "全文已有新版本，请刷新后重新选择更新或补充")
            version = self._insert_version(connection, document_id, base_version_id, base["version_number"] + 1,
                                           combined_source, prepared)
            connection.execute("INSERT INTO doc_sources VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                               (source_id, document_id, filename, suffix[1:], used_encoding, raw, _sha(raw),
                                source, version["id"], _now(), mode))
            connection.execute("UPDATE doc_documents SET latest_version_id=? WHERE id=?", (version["id"], document_id))
            if idempotency_key:
                connection.execute("INSERT INTO doc_ingestions VALUES (?,?,?,?,?)",
                                   (idempotency_key, document_id, source_id, mode, _sha(raw)))
            return version

    def list_sources(self, document_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            document = connection.execute(f"SELECT {_META_COLUMNS} FROM doc_documents WHERE id=?", (document_id,)).fetchone()
            if document is None:
                raise DataError(404, "全文文档不存在")
            cached = connection.execute("SELECT active_source_ids_json FROM doc_version_meta WHERE version_id=?",
                                        (document["latest_version_id"],)).fetchone()
        active = set(json.loads(cached[0])) if cached and cached[0] is not None else None
        if active is None:
            # Only a pre-metadata project needs the one-time legacy lineage
            # read. Ordinary source navigation never loads a book or BLOB.
            version = self.get_version(document_id, document["latest_version_id"])
            active = set(self._active_source_ids(document_id, version))
            with self._connection() as connection:
                connection.execute("UPDATE doc_version_meta SET active_source_ids_json=? WHERE version_id=?",
                                   (_json(sorted(active)), version["id"]))
        with self._connection() as connection:
            originals = [] if document["format"] == "collection" else [
                {"id": "original", "filename": document["filename"], "format": document["format"],
                 "encoding": document["encoding"], "original_sha256": document["original_sha256"],
                 "original_bytes": document["original_bytes"], "created_at": document["created_at"], "version_id": None, "mode": "new"}]
            originals.extend(dict(row) for row in connection.execute(
                "SELECT id,filename,format,encoding,original_sha256,length(original) AS original_bytes,version_id,created_at,mode "
                "FROM doc_sources WHERE document_id=? ORDER BY created_at,id", (document_id,)))
            for source in originals:
                if source["version_id"] and source.get("mode") == "unknown":
                    row = connection.execute(
                        "SELECT json_extract(change.value,'$.stage') AS stage,json_extract(change.value,'$.ingestion_key') AS ingestion_key FROM doc_versions, "
                        "json_each(doc_versions.payload_json,'$.changes') AS change "
                        "WHERE document_id=? AND doc_versions.id=? AND json_extract(change.value,'$.source_id')=? LIMIT 1",
                        (document_id, source["version_id"], source["id"])).fetchone()
                    source["mode"] = "replace" if row and row["stage"] == "replace_source" else "append"
                    connection.execute("UPDATE doc_sources SET mode=? WHERE id=? AND mode='unknown'", (source["mode"], source["id"]))
                    if row and row["ingestion_key"]:
                        connection.execute("UPDATE doc_ingestions SET source_id=?,mode=?,original_sha256=? WHERE key=? AND mode IS NULL",
                                           (source["id"], source["mode"], source["original_sha256"], row["ingestion_key"]))
                source["active"] = source["id"] in active
                source["superseded"] = source["id"] not in active
                source["derived_format"] = "txt" if source["encoding"] == "utf-8-converted" else None
            return originals

    def get_source_original(self, document_id: str, source_id: str) -> tuple[bytes, str]:
        if source_id == "original":
            return self.get_original(document_id)
        with self._connection() as connection:
            self._document(connection, document_id)
            row = connection.execute("SELECT original,original_sha256,filename FROM doc_sources WHERE document_id=? AND id=?",
                                     (document_id, source_id)).fetchone()
            if row is None:
                raise DataError(404, "接续原件不存在")
            raw = bytes(row["original"])
            if _sha(raw) != row["original_sha256"]:
                raise DataError(500, "接续原件校验失败")
            return raw, row["filename"]

    def get_original(self, document_id: str) -> tuple[bytes, str]:
        with self._connection() as connection:
            document = self._document(connection, document_id)
            raw = bytes(document["original"])
            if _sha(raw) != document["original_sha256"]:
                raise DataError(500, "原文件校验失败")
            return raw, document["filename"]
