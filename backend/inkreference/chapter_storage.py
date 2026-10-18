"""Recoverable per-book TXT projections of immutable document versions.

SQLite remains authoritative. Chapter files are content-addressed, so a new
manifest can be published atomically without invalidating the previous one.
Only files listed in the previous app-owned manifest are ever removed.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import unicodedata
import uuid
import zipfile
from pathlib import Path
from typing import Any, Iterable

from .chapter_layout import chapter_body_span, chapter_label, normalize_chapter_body

FORMAT = "InkReference-chapters"
FORMAT_VERSION = 1
_RESERVED = re.compile(r"(?i)^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$")


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def safe_name(value: str, *, fallback: str = "未命名作品", max_bytes: int = 160) -> str:
    value = unicodedata.normalize("NFC", str(value))
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", value).strip(" .")
    value = value or fallback
    if value in {".", ".."} or _RESERVED.fullmatch(value):
        value = "_" + value
    while len(value.encode("utf-8")) > max_bytes:
        value = value[:-1]
    return value.rstrip(" .") or fallback


def chapter_text(chapter: dict[str, Any], text: str) -> str:
    """The same canonical presentation used by the merged cleaned TXT export."""
    if chapter.get("gap_range"):
        return f"{chapter_label(chapter)}（缺失，待补充）\n"
    if chapter.get("placeholder"):
        return chapter_label(chapter) + "\n"
    if chapter.get("raw_marker"):
        full = {**chapter, "text": text}
        begin, end = chapter_body_span(full)
        body = normalize_chapter_body(text[begin:end], trim_boundaries=True)
        return chapter_label(chapter) + ("\n" + body if body else "") + "\n"
    body = normalize_chapter_body(text, trim_boundaries=True)
    return body + "\n" if body else ""


def infer_frontmatter(text: str) -> dict[str, Any]:
    """Read explicit, labelled metadata before the first chapter only."""
    head = text[:8192]
    marker = re.search(r"(?m)^\s*第\s*[零〇一二两三四五六七八九十百千万0-9０-９]+\s*[章回节](?!发布时间|更新时间)", head)
    if marker:
        head = head[:marker.start()]
    result: dict[str, Any] = {}
    patterns = {
        "book_title": r"(?:书名|作品名称|title)",
        "author": r"(?:作者|author)",
        "earliest_update_at": r"(?:最早更新时间|首次更新时间|首次发布时间|第一章发布时间|首章发布时间|earliest_update_at)",
    }
    for key, label in patterns.items():
        match = re.search(rf"(?im)^\s*{label}\s*[:：]\s*([^\r\n]{{1,300}})\s*$", head)
        if match:
            value = match.group(1).strip().strip('“”\"')
            if key == "earliest_update_at":
                # Preserve source precision rather than inventing a day/time.
                try:
                    value = validate_metadata({"earliest_update_at": value})["earliest_update_at"]
                except ValueError:
                    continue
            result[key] = value
    return result


def _owned_manifest(folder: Path, document_id: str) -> dict[str, Any] | None:
    path = folder / "metadata.json"
    if not path.is_file() or path.is_symlink():
        return None
    try:
        data = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None
    if (not isinstance(data, dict) or data.get("format") != FORMAT
            or data.get("document_id") != document_id or not isinstance(data.get("version_id"), str)):
        return None
    chapters = data.get("chapters")
    if not isinstance(chapters, list) or data.get("chapter_count") != len(chapters):
        return None
    seen_ids, seen_names = set(), set()
    for order, entry in enumerate(chapters, 1):
        if not isinstance(entry, dict):
            return None
        identity, name, digest = entry.get("id"), entry.get("filename"), entry.get("sha256")
        if (not isinstance(identity, str) or not identity or identity in seen_ids
                or not isinstance(name, str) or name in seen_names
                or not re.fullmatch(r"\d{6,}_[^/\\]+--[0-9a-f]{16}\.txt", name)
                or name != safe_name(name, max_bytes=220)
                or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
                or entry.get("order") != order
                or type(entry.get("bytes")) is not int or entry["bytes"] < 0
                or type(entry.get("characters")) is not int or entry["characters"] < 0):
            return None
        seen_ids.add(identity)
        seen_names.add(name)
    if sha256(json_bytes(chapters)) != data.get("manifest_sha256"):
        return None
    return data


def ensure_folder(root: Path, folder_name: str) -> Path:
    if root.is_symlink():
        raise OSError("章节存储目录不能是符号链接")
    root.mkdir(parents=True, exist_ok=True)
    if folder_name != safe_name(folder_name, max_bytes=220):
        raise OSError("章节目录名称无效")
    folder = root / folder_name
    if folder.is_symlink():
        raise OSError("作品目录不能是符号链接")
    folder.mkdir(exist_ok=True)
    return folder


def manifest_owner(root: Path, folder_name: str) -> str | None:
    folder = root / folder_name
    if folder.is_symlink() or not folder.is_dir():
        return None
    try:
        value = json.loads((folder / "metadata.json").read_bytes())
        if not isinstance(value, dict) or value.get("format") != FORMAT:
            return None
        owner = value.get("document_id")
        return owner if isinstance(owner, str) and owner else None
    except (OSError, ValueError):
        return None


def publish(root: Path, folder_name: str, metadata: dict[str, Any],
            rows: Iterable[tuple[dict[str, Any], str | None]], *, trust_existing: bool = True) -> dict[str, Any]:
    folder = ensure_folder(root, folder_name)
    owner = manifest_owner(root, folder_name)
    if owner not in {None, metadata["document_id"]}:
        raise OSError("作品目录已属于另一本文档")
    previous = _owned_manifest(folder, metadata["document_id"])
    previous_entries = {entry.get("filename"): entry for entry in (previous or {}).get("chapters", [])}
    previous_ids = {entry.get("id"): entry for entry in previous_entries.values()}
    stage = folder / (".staging-" + uuid.uuid4().hex)
    stage.mkdir(mode=0o700)
    entries = []
    staged = []
    try:
        for order, (chapter, text) in enumerate(rows, 1):
            # An incremental save can reuse unchanged entries without loading,
            # encoding or hashing the other chapters' bodies.
            old_entry = previous_ids.get(chapter["id"]) if text is None else None
            if old_entry is not None:
                old_path = folder / old_entry["filename"]
                if old_path.is_symlink() or not old_path.is_file():
                    raise OSError("未修改的章节文件需要重建")
                entries.append({**old_entry, "order": order})
                continue
            if text is None:
                raise OSError("章节文件缓存缺失，需要重建")
            content = chapter_text(chapter, text).encode("utf-8")
            digest = sha256(content)
            name = f"{order:06d}_{safe_name(chapter_label(chapter), max_bytes=120)}--{digest[:16]}.txt"
            target = folder / name
            if target.is_symlink():
                raise OSError("章节文件不能是符号链接")
            entry = {"id": chapter["id"], "order": order, "title": chapter_label(chapter),
                     "filename": name, "sha256": digest, "bytes": len(content),
                     "characters": len(content.decode("utf-8")), "placeholder": bool(chapter.get("placeholder")),
                     "kind": chapter.get("kind", "chapter")}
            entries.append(entry)
            old = previous_entries.get(name)
            if trust_existing and old and old.get("sha256") == digest and target.is_file() and target.stat().st_size == len(content):
                continue
            temporary = stage / name
            with temporary.open("xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            staged.append((temporary, target))
        value = {**metadata, "format": FORMAT, "format_version": FORMAT_VERSION,
                 "encoding": "utf-8", "chapters": entries,
                 "chapter_count": len(entries), "manifest_sha256": sha256(json_bytes(entries))}
        manifest = stage / "metadata.json"
        with manifest.open("xb") as stream:
            stream.write(json_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
        # Existing files keep their old names until metadata.json is replaced.
        for source, destination in staged:
            os.replace(source, destination)
        if (folder / "metadata.json").is_symlink():
            raise OSError("作品元数据不能是符号链接")
        os.replace(manifest, folder / "metadata.json")
        active = {entry["filename"] for entry in entries}
        for old_name in previous_entries.keys() - active:
            if (isinstance(old_name, str) and Path(old_name).name == old_name
                    and re.fullmatch(r"\d{6,}_.+--[0-9a-f]{16}\.txt", old_name)):
                stale = folder / old_name
                if stale.is_file() and not stale.is_symlink():
                    # Only delete unchanged files the app created. User edits
                    # are retained even if a later version no longer uses them.
                    if sha256(stale.read_bytes()) == previous_entries[old_name].get("sha256"):
                        stale.unlink()
        return value
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def verify(root: Path, folder_name: str, document_id: str, version_id: str) -> dict[str, Any]:
    folder = ensure_folder(root, folder_name)
    manifest = _owned_manifest(folder, document_id)
    if not manifest or manifest.get("version_id") != version_id:
        raise OSError("章节文件版本需要重建")
    if sha256(json_bytes(manifest.get("chapters", []))) != manifest.get("manifest_sha256"):
        raise OSError("章节清单校验失败")
    for chapter in manifest["chapters"]:
        name = chapter.get("filename")
        if not isinstance(name, str) or Path(name).name != name:
            raise OSError("章节清单文件名无效")
        path = folder / name
        if path.is_symlink() or not path.is_file() or sha256(path.read_bytes()) != chapter["sha256"]:
            raise OSError(f"章节文件校验失败：{name}")
    return manifest


def archive(metadata: dict[str, Any], rows: Iterable[tuple[dict[str, Any], str]]) -> bytes:
    output = io.BytesIO()
    folder = safe_name(metadata.get("book_title") or metadata.get("title") or "未命名作品")
    entries = []
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target:
        for order, (chapter, text) in enumerate(rows, 1):
            content = chapter_text(chapter, text).encode("utf-8")
            name = f"{order:06d}_{safe_name(chapter_label(chapter), max_bytes=120)}.txt"
            target.writestr(f"{folder}/{name}", content)
            entries.append({"id": chapter["id"], "order": order, "filename": name, "sha256": sha256(content),
                            "title": chapter_label(chapter), "placeholder": bool(chapter.get("placeholder"))})
        target.writestr(f"{folder}/metadata.json", json_bytes({**metadata, "format": FORMAT,
                        "format_version": FORMAT_VERSION, "encoding": "utf-8", "chapters": entries}))
    return output.getvalue()


def validate_metadata(values: dict[str, Any], *, allow_title: bool = True) -> dict[str, Any]:
    if not isinstance(values, dict):
        raise ValueError("作品信息必须为对象")
    allowed = {"author", "earliest_update_at", "book_type"} | ({"title", "book_title"} if allow_title else set())
    if set(values) - allowed:
        raise ValueError("作品信息包含不支持的字段：" + "、".join(sorted(set(values) - allowed)))
    result = {}
    for key, value in values.items():
        key = "title" if key == "book_title" else key
        if key == "earliest_update_at" and (value is None or value == ""):
            result[key] = None
            continue
        if not isinstance(value, str) or re.search(r"[\x00-\x1f\x7f]", value):
            raise ValueError("作品信息需要不含控制字符的文本")
        value = value.strip()
        if len(value) > (300 if key == "author" else 200):
            raise ValueError("作品信息过长")
        if key == "title" and not value:
            raise ValueError("书名不能为空")
        if key == "earliest_update_at":
            match = re.fullmatch(r"(\d{4})(?:[-/](\d{1,2})(?:[-/](\d{1,2}))?)?(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?(?:Z|[+-]\d{2}:?\d{2})?)?", value)
            if not match:
                raise ValueError("最早更新时间需为年份、年月、日期或日期时间")
            from datetime import datetime
            year, month, day, hour, minute, second = [int(item) if item is not None else default
                                                    for item, default in zip(match.groups(), [1, 1, 1, 0, 0, 0])]
            try:
                datetime(year, month, day, hour, minute, second)
            except ValueError as error:
                raise ValueError("最早更新时间无效") from error
        result[key] = value
    return result


def retire_folder(root: Path, folder_name: str, document_id: str) -> None:
    """Remove only verified files from an old title folder; keep user additions."""
    folder = root / folder_name
    if folder.is_symlink():
        return
    previous = _owned_manifest(folder, document_id)
    if not previous:
        return
    for entry in previous.get("chapters", []):
        name = entry.get("filename", "")
        if isinstance(name, str) and Path(name).name == name:
            source = folder / name
            if source.is_file() and not source.is_symlink() and sha256(source.read_bytes()) == entry.get("sha256"):
                source.unlink()
    manifest = folder / "metadata.json"
    if manifest.is_file() and not manifest.is_symlink():
        manifest.unlink()
    try:
        folder.rmdir()
    except OSError:
        pass
