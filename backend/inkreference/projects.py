"""Portable, checked project snapshots. Restores always create a new copy."""
from __future__ import annotations

import csv
from contextlib import closing
import hashlib
import io
import json
import re
import shutil
import sqlite3
import tempfile
import uuid
import zipfile
from pathlib import Path, PurePosixPath

from .database import DataError

DATABASES = ("inkreference.sqlite3", "workbench.sqlite3", "skills.sqlite3", "workflow_definitions.sqlite3", "checkpoints.sqlite3")
MAX_BACKUP_BYTES = 512 * 1024 * 1024
MAX_EXPANDED_BYTES = 2 * 1024 * 1024 * 1024


def _safe_member(name: str) -> bool:
    path = PurePosixPath(name)
    return (
        not path.is_absolute() and path.as_posix() == name and "\\" not in name and ":" not in name
        and all(part not in {"", ".", ".."} and part.rstrip(" .") == part
                and not re.search(r"[\x00-\x1f]", part)
                and not re.fullmatch(r"(?i)(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part)
                for part in path.parts)
        and (name in DATABASES or (len(path.parts) >= 3 and path.parts[0] == "imports"))
    )


class ProjectStore:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)

    def export(self) -> bytes:
        output = io.BytesIO()
        entries = []
        with tempfile.TemporaryDirectory(prefix="inkreference-backup-") as temporary:
            root = Path(temporary)
            for name in DATABASES:
                source = self.data_dir / name
                if source.is_file():
                    with closing(sqlite3.connect(str(source))) as connection, closing(sqlite3.connect(str(root / name))) as destination:
                        connection.backup(destination)
            imports = self.data_dir / "imports"
            if imports.is_dir():
                for source in imports.rglob("*"):
                    if source.is_file() and not source.is_symlink():
                        relative = source.relative_to(self.data_dir)
                        if not _safe_member(relative.as_posix()):
                            continue
                        destination = root / relative
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(source, destination)
            with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
                total = 0
                for source in sorted(root.rglob("*")):
                    if not source.is_file():
                        continue
                    raw = source.read_bytes()
                    total += len(raw)
                    if total > MAX_EXPANDED_BYTES:
                        raise DataError(413, "项目超过当前备份大小上限。")
                    name = source.relative_to(root).as_posix()
                    entries.append({"path": name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
                    archive.writestr(name, raw)
                archive.writestr("manifest.json", json.dumps({"format": "InkReference-project", "version": 1, "files": entries}, ensure_ascii=False))
        return output.getvalue()

    def restore(self, raw: bytes) -> dict:
        if len(raw) > MAX_BACKUP_BYTES:
            raise DataError(413, "备份文件不能超过 512 MB。")
        destination = self.data_dir / "restored_projects" / uuid.uuid4().hex
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                infos = archive.infolist()
                names = [item.filename for item in infos]
                if len(names) != len(set(names)) or len(names) > 10000:
                    raise DataError(422, "备份包含重复文件或文件数量过多。")
                if sum(item.file_size for item in infos) > MAX_EXPANDED_BYTES:
                    raise DataError(413, "备份展开后超过 2 GB。")
                info = archive.getinfo("manifest.json")
                if info.file_size > 4 * 1024 * 1024:
                    raise DataError(422, "无效的备份清单。")
                manifest = json.loads(archive.read(info))
                if not isinstance(manifest, dict) or manifest.get("format") != "InkReference-project" or manifest.get("version") != 1:
                    raise DataError(422, "不支持的项目备份格式。")
                entries = manifest["files"]
                if (not isinstance(entries, list) or not all(isinstance(entry, dict) and isinstance(entry.get("path"), str) for entry in entries)
                        or len({entry["path"] for entry in entries}) != len(entries)
                        or set(names) != {"manifest.json", *(entry["path"] for entry in entries)}):
                    raise DataError(422, "备份清单与文件不一致。")
                if not any(entry["path"] == "inkreference.sqlite3" for entry in entries):
                    raise DataError(422, "备份缺少素材数据库。")
                destination.mkdir(parents=True, mode=0o700)
                for entry in entries:
                    name = entry["path"]
                    if not _safe_member(name):
                        raise DataError(422, "备份包含无效的文件路径。")
                    raw_file = archive.read(name)
                    if len(raw_file) != entry["bytes"] or hashlib.sha256(raw_file).hexdigest() != entry["sha256"]:
                        raise DataError(422, "备份校验失败。")
                    target = destination / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(raw_file)
                    if name in DATABASES:
                        with closing(sqlite3.connect(str(target))) as connection:
                            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                                raise DataError(422, "备份数据库完整性校验失败。")
        except DataError:
            shutil.rmtree(destination, ignore_errors=True)
            raise
        except (ValueError, KeyError, TypeError, zipfile.BadZipFile, sqlite3.DatabaseError, RuntimeError, OSError) as exc:
            shutil.rmtree(destination, ignore_errors=True)
            raise DataError(422, "备份文件损坏或格式无效。") from exc
        return {"restored": True, "restart_required": True, "data_dir": str(destination.resolve()), "files": len(entries)}

    def export_tables(self) -> bytes:
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            for name in DATABASES[:-1]:
                path = self.data_dir / name
                if not path.is_file():
                    continue
                with closing(sqlite3.connect(str(path))) as connection:
                    connection.execute("BEGIN")
                    tables = connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
                    for (table,) in tables:
                        quoted = '"' + table.replace('"', '""') + '"'
                        rows = connection.execute(f"SELECT * FROM {quoted}")
                        csv_text = io.StringIO(newline="")
                        writer = csv.writer(csv_text)
                        writer.writerow([column[0] for column in rows.description])
                        for row in rows:
                            writer.writerow([value.hex() if isinstance(value, bytes) else value for value in row])
                        archive.writestr(f"{Path(name).stem}/{table}.csv", csv_text.getvalue().encode("utf-8-sig"))
            archive.writestr("manifest.json", json.dumps({"format": "InkReference-business-tables", "blob_encoding": "hex", "restore_format": False}))
        return output.getvalue()
