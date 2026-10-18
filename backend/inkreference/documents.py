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
from typing import Any, Iterator
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET

from .database import DataError
from .chapter_preprocessing import AUTHOR_KEYWORDS, annotate_chapters, chapter_proposals, validate_settings
from .text_processing import analyze_text, chunk_text, diagnose_text

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_MEMBER_BYTES = 20 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 2000
SUPPORTED_FORMATS = {".txt", ".md", ".srt", ".docx", ".epub"}
ENCODING_CHOICES = ["utf-8", "gb18030", "utf-16-le", "utf-16-be", "big5"]
# Use the stored exact codepoint count, rather than SQLite length(text), which
# stops at a literal NUL. This does not fetch original BLOBs or full version text.
_CURRENT_CHARACTERS_SQL = ("COALESCE((SELECT CAST(json_extract(payload_json,'$.coverage.characters') AS INTEGER) "
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


def _prepare(source: str, previous: str, options: dict[str, Any], *, normalize: bool = True) -> dict[str, Any]:
    if normalize:
        result = analyze_text(source, remove_random_ids=options["remove_random_ids"],
                              fallback_chars=options["fallback_chars"],
                              heading_formats=options.get("heading_formats"), custom_formats=options.get("custom_formats"))
    else:
        from .text_processing import split_chapters
        result = {"cleaned_text": source, "cleaned_sha256": _sha(source.encode("utf-8")),
                  "diagnostics": diagnose_text(source), "transformations": [],
                  "chapter_analysis": split_chapters(source, fallback_chars=options["fallback_chars"],
                       heading_formats=options.get("heading_formats"), custom_formats=options.get("custom_formats"))}
    text = result["cleaned_text"]
    chapters = result["chapter_analysis"]["chapters"]
    chunks = []
    for chapter_index, chapter in enumerate(chapters):
        chapter["id"] = f"chapter-{chapter_index + 1}"
        for index, chunk in enumerate(chunk_text(chapter["text"], options["chunk_chars"])):
            chunks.append({**chunk, "start": chapter["start"] + chunk["start"],
                           "end": chapter["start"] + chunk["end"],
                           "id": f"{chapter['id']}-chunk-{index + 1}", "chapter_id": chapter["id"],
                           "chapter_index": chapter_index})
    gaps = annotate_chapters(chapters, options)
    reconstructed = "".join(chunk["text"] for chunk in chunks)
    coverage = {**result["chapter_analysis"]["coverage"],
                "chunks_complete": reconstructed == text,
                "chunk_count": len(chunks), "chapter_count": len(chapters)}
    if not coverage["complete"] or not coverage["chunks_complete"]:
        raise DataError(500, "章节或分块覆盖检查失败，未保存不完整版本")
    diff_lines = list(difflib.unified_diff(previous.splitlines(keepends=True), text.splitlines(keepends=True),
                                         fromfile="base", tofile="cleaned", n=2))
    # The full texts remain queryable; cap display-only diff, never source data.
    diff = "".join(diff_lines)
    return {"text": text, "sha256": result["cleaned_sha256"], "chapters": chapters,
            "chunks": chunks, "diagnostics": result["diagnostics"],
            "source_diagnostics": diagnose_text(source), "changes": result["transformations"],
            "diff": diff[:200000], "diff_truncated": len(diff) > 200000,
            "coverage": coverage, "pattern": result["chapter_analysis"]["pattern"],
            "fallback_used": result["chapter_analysis"]["fallback_used"],
            "heading_formats": result["chapter_analysis"]["heading_formats"],
            "candidates": result["chapter_analysis"]["candidates"], "gaps": gaps,
            "options": options}


class DocumentStore:
    """Separate doc_* schema sharing the workbench DB with workflow services."""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "workbench.sqlite3"
        with self._connection() as connection:
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
                    source_text TEXT NOT NULL, version_id TEXT NOT NULL REFERENCES doc_versions(id),
                    created_at TEXT NOT NULL
                );
                CREATE TRIGGER IF NOT EXISTS doc_source_immutable
                BEFORE UPDATE ON doc_sources
                BEGIN SELECT RAISE(ABORT, 'document source is immutable'); END;
                CREATE INDEX IF NOT EXISTS doc_sources_document ON doc_sources(document_id,created_at);
                CREATE INDEX IF NOT EXISTS doc_versions_document ON doc_versions(document_id,version_number);
                CREATE TABLE IF NOT EXISTS doc_ingestions (
                    key TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES doc_documents(id)
                );
                CREATE TRIGGER IF NOT EXISTS doc_original_immutable
                BEFORE UPDATE OF original,original_sha256,source_text,filename,encoding,format ON doc_documents
                BEGIN SELECT RAISE(ABORT, 'document original is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS doc_version_immutable
                BEFORE UPDATE ON doc_versions
                BEGIN SELECT RAISE(ABORT, 'document version is immutable'); END;
            """)

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
               work_id: str | None = None, *, idempotency_key: str | None = None) -> dict[str, Any]:
        if not isinstance(filename, str) or not filename or len(filename) > 255 or re.search(r"[\x00-\x1f]", filename):
            raise DataError(422, "文件名无效")
        # Retain only the display name; never use untrusted names as disk paths.
        filename = filename.replace("\\", "/").rsplit("/", 1)[-1]
        suffix = Path(filename).suffix.lower()
        if suffix not in SUPPORTED_FORMATS:
            raise DataError(422, "支持 TXT、MD、SRT、DOCX 和 EPUB 文件")
        if not isinstance(raw, bytes) or not raw:
            raise DataError(422, "请上传非空文件")
        if len(raw) > MAX_UPLOAD_BYTES:
            raise DataError(413, "文件超过 50 MiB 限制")
        if not isinstance(title, str) or (work_id is not None and not isinstance(work_id, str)):
            raise DataError(422, "标题与作品 ID 必须为字符串")
        if idempotency_key is not None and (not isinstance(idempotency_key, str)
                                            or not 1 <= len(idempotency_key) <= 200):
            raise DataError(422, "导入幂等键必须是不超过 200 字的非空字符串")
        if suffix == ".docx":
            source, used_encoding = _docx_text(raw), "xml"
        elif suffix == ".epub":
            source, used_encoding = _epub_text(raw), "xml"
        else:
            source, used_encoding = _decode(raw, encoding)
        identifier = uuid.uuid4().hex
        now = _now()
        payload = _prepare(source, source, _options({}))
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
                connection.execute("INSERT INTO doc_ingestions VALUES (?,?)", (idempotency_key, identifier))
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
        version = {**payload, "id": identifier, "document_id": document_id,
                   "parent_version_id": parent_id, "version_number": number,
                   "source_text": source, "source_sha256": _sha(source.encode("utf-8")),
                   "created_at": now}
        connection.execute("INSERT INTO doc_versions VALUES (?,?,?,?,?,?)",
                           (identifier, document_id, parent_id, number, _json(version), now))
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
            return document

    def get_version(self, document_id: str, version_id: str | None = None) -> dict[str, Any]:
        with self._connection() as connection:
            document = self._document(connection, document_id)
            row = connection.execute("SELECT payload_json FROM doc_versions WHERE document_id=? AND id=?",
                                     (document_id, version_id or document["latest_version_id"])).fetchone()
            if row is None:
                raise DataError(404, "全文版本不存在")
            return json.loads(row["payload_json"])

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
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            document = self._document(connection, document_id)
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
        return _options({**base.get("options", {}), **changes})

    def chapter_index(self, document_id: str, version_id: str | None = None) -> dict[str, Any]:
        version = self.get_version(document_id, version_id)
        options = self._version_options(version)
        chapters = [dict(chapter) for chapter in version["chapters"]]
        gaps = annotate_chapters(chapters, options)
        return {"document_id": document_id, "version_id": version["id"], "pattern": version.get("pattern"),
                "heading_formats": version.get("heading_formats", []), "candidates": version.get("candidates", []),
                "gaps": gaps, "options": options, "author_keyword_defaults": AUTHOR_KEYWORDS,
                "chapters": [{k: v for k, v in chapter.items() if k not in {"text", "content"}} for chapter in chapters]}

    def get_chapter(self, document_id: str, version_id: str, chapter_id: str,
                    start: int = 0, limit: int = 20000) -> dict[str, Any]:
        if isinstance(start, bool) or not isinstance(start, int) or start < 0 or isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100000:
            raise DataError(422, "章节分页范围无效")
        version = self.get_version(document_id, version_id)
        chapter = dict(self._chapter(version, chapter_id))
        text = chapter["text"]
        body_offset = chapter["body_start"] - chapter["start"]
        metadata = {k: v for k, v in chapter.items() if k not in {"text", "content"}}
        return {**metadata, "chapter": metadata, "document_id": document_id, "version_id": version["id"],
                "text": text[start:start + limit], "start": start, "end": min(start + limit, len(text)),
                "total": len(text), "body_offset": body_offset,
                "body_text": text[body_offset:] if len(text) - body_offset <= 200000 else None,
                "proposals": chapter_proposals(chapter, self._version_options(version)),
                "diagnostics": diagnose_text(text), "offset_unit": "unicode_codepoints"}

    def edit_chapter(self, document_id: str, base_version_id: str, chapter_id: str, body_text: str) -> dict[str, Any]:
        self._valid_text(body_text)
        base = self.get_version(document_id, base_version_id)
        chapter = self._chapter(base, chapter_id)
        # Only the addressed chapter body changes. All headings, prefix,
        # trailing chapters and whitespace outside this span remain exact.
        if chapter["raw_marker"] and body_text and not body_text.startswith("\n"):
            body_text = "\n" + body_text
        if chapter["end"] < len(base["text"]) and not body_text.endswith("\n"):
            body_text += "\n"
        updated = base["text"][:chapter["body_start"]] + body_text + base["text"][chapter["end"]:]
        prepared = _prepare(updated, base["text"], self._version_options(base), normalize=False)
        return self._commit_prepared(document_id, base, prepared, change={"stage": "chapter_edit", "chapter_id": chapter_id, "count": 1})

    @staticmethod
    def _valid_text(text: Any) -> None:
        if not isinstance(text, str):
            raise DataError(422, "章节正文必须为字符串")
        try:
            text.encode("utf-8", errors="strict")
        except UnicodeError:
            raise DataError(422, "文本包含无效 Unicode 字符") from None

    def resegment(self, document_id: str, base_version_id: str,
                  heading_formats: list[str], custom_formats: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        base = self.get_version(document_id, base_version_id)
        options = self._version_options(base, heading_formats=heading_formats,
                                        custom_formats=custom_formats if custom_formats is not None else base["options"].get("custom_formats", []))
        from .text_processing import heading_patterns
        available = {name for name, _ in heading_patterns(options["custom_formats"])}
        if set(heading_formats) - available:
            raise DataError(422, "选中的章节格式不存在")
        prepared = _prepare(base["text"], base["text"], options, normalize=False)
        return self._commit_prepared(document_id, base, prepared, change={"stage": "chapter_resegment", "count": len(prepared["chapters"])})

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
        base = self.get_version(document_id, base_version_id)
        options = self._version_options(base)
        ranges = []
        seen_chapters = set()
        removed = []
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
            for identifier in ids:
                proposal = proposals[identifier]
                ranges.append((chapter["start"] + proposal["start"], chapter["start"] + proposal["end"]))
                removed.append({"chapter_id": chapter_id, "proposal_id": identifier, "kind": proposal["kind"],
                                "characters": proposal["end"] - proposal["start"]})
        if not ranges:
            raise DataError(422, "尚未选择要清理的内容")
        updated = base["text"]
        for start, end in sorted(ranges, reverse=True):
            updated = updated[:start] + updated[end:]
        prepared = _prepare(updated, base["text"], options, normalize=False)
        return self._commit_prepared(document_id, base, prepared,
                                     change={"stage": "reviewed_chapter_cleaning", "count": len(removed), "removed": removed})

    def rename_chapter(self, document_id: str, base_version_id: str, chapter_id: str, title: str) -> dict[str, Any]:
        self._valid_text(title)
        if not title.strip() or len(title) > 100 or "\n" in title or "\r" in title:
            raise DataError(422, "章节标题需为 1 至 100 字的单行文本")
        base = self.get_version(document_id, base_version_id)
        chapter = self._chapter(base, chapter_id)
        marker = chapter["raw_marker"]
        if not marker:
            raise DataError(422, "该段落没有章节标题，请使用新增章节")
        # Preserve the numeral/punctuation prefix and replace title only.
        old_title = chapter["title"]
        if old_title and marker.rstrip().endswith(old_title):
            new_marker = marker[:marker.rfind(old_title)] + title.strip()
        else:
            new_marker = marker.rstrip() + " " + title.strip()
        updated = base["text"][:chapter["start"]] + new_marker + base["text"][chapter["body_start"]:]
        prepared = _prepare(updated, base["text"], self._version_options(base), normalize=False)
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
        base = self.get_version(document_id, base_version_id)
        ranges = [(self._chapter(base, identifier)["start"], self._chapter(base, identifier)["end"]) for identifier in chapter_ids]
        updated = base["text"]
        for start, end in sorted(ranges, reverse=True):
            updated = updated[:start] + updated[end:]
        prepared = _prepare(updated, base["text"], self._version_options(base), normalize=False)
        return self._commit_prepared(document_id, base, prepared, change={"stage": "chapter_delete", "chapter_ids": chapter_ids, "count": len(chapter_ids)})

    def restore_version(self, document_id: str, base_version_id: str, target_version_id: str) -> dict[str, Any]:
        base = self.get_version(document_id, base_version_id)
        target = self.get_version(document_id, target_version_id)
        prepared = _prepare(target["text"], base["text"], self._version_options(target), normalize=False)
        return self._commit_prepared(document_id, base, prepared, source=target["source_text"],
                                     change={"stage": "restore_version", "target_version_id": target_version_id, "count": 1})

    def append(self, document_id: str, base_version_id: str, filename: str, raw: bytes,
               encoding: str = "auto") -> dict[str, Any]:
        # Parse with the same strict upload path without mutating document metadata.
        if not isinstance(filename, str) or not filename or len(filename) > 255 or re.search(r"[\x00-\x1f]", filename):
            raise DataError(422, "文件名无效")
        filename = filename.replace("\\", "/").rsplit("/", 1)[-1]
        suffix = Path(filename).suffix.lower()
        if suffix not in SUPPORTED_FORMATS or not isinstance(raw, bytes) or not raw:
            raise DataError(422, "请上传非空 TXT、MD、SRT、DOCX 或 EPUB 文件")
        if len(raw) > MAX_UPLOAD_BYTES:
            raise DataError(413, "文件超过 50 MiB 限制")
        if suffix == ".docx":
            source, used_encoding = _docx_text(raw), "xml"
        elif suffix == ".epub":
            source, used_encoding = _epub_text(raw), "xml"
        else:
            source, used_encoding = _decode(raw, encoding)
        base = self.get_version(document_id, base_version_id)
        from .text_processing import clean_text
        new_text = clean_text(source)["cleaned_text"]
        separator = "" if not base["text"] or base["text"].endswith("\n") else "\n"
        updated = base["text"] + separator + new_text
        combined_source = base["source_text"] + ("" if not base["source_text"] or base["source_text"].endswith("\n") else "\n") + source
        prepared = _prepare(updated, base["text"], self._version_options(base), normalize=False)
        source_id = uuid.uuid4().hex
        prepared["changes"].insert(0, {"stage": "append_source", "source_id": source_id,
                                       "filename": filename, "count": 1, "appended_characters": len(new_text)})
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            document = self._document(connection, document_id)
            if document["latest_version_id"] != base_version_id:
                raise DataError(409, "全文已有新版本，请刷新后再接续")
            version = self._insert_version(connection, document_id, base_version_id, base["version_number"] + 1,
                                           combined_source, prepared)
            connection.execute("INSERT INTO doc_sources VALUES (?,?,?,?,?,?,?,?,?,?)",
                               (source_id, document_id, filename, suffix[1:], used_encoding, raw, _sha(raw),
                                source, version["id"], _now()))
            connection.execute("UPDATE doc_documents SET latest_version_id=? WHERE id=?", (version["id"], document_id))
            return version

    def list_sources(self, document_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            document = self._document(connection, document_id)
            originals = [{"id": "original", "filename": document["filename"], "format": document["format"],
                          "encoding": document["encoding"], "original_sha256": document["original_sha256"],
                          "original_bytes": len(document["original"]), "created_at": document["created_at"],
                          "version_id": None}]
            originals.extend(dict(row) for row in connection.execute(
                "SELECT id,filename,format,encoding,original_sha256,length(original) AS original_bytes,version_id,created_at "
                "FROM doc_sources WHERE document_id=? ORDER BY created_at,id", (document_id,)))
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
