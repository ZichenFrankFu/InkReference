"""Check a three-file catalog without changing inputs or reporting their contents."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from inkreference.database import DataError, DataStore
from inkreference.library_exchange import (
    commit_library_import, export_library, parse_library_files, preview_library_import,
)


def table_values(batch) -> dict:
    return {table.table: table.rows for table in batch.tables}


def raw_values(store: DataStore) -> dict:
    # Read persisted input rather than the GUI projection, which intentionally
    # recomputes the one derived score. Fixed table names are schema-owned.
    with store.connect() as connection:
        connection.execute("BEGIN")
        return {table: [json.loads(row[0]) for row in connection.execute(
            f"SELECT values_json FROM {table} ORDER BY sort_order,id")]
            for table in ("works", "characters", "cliches")}


def canonical_values(store: DataStore, original: dict) -> dict:
    expected = {table: [dict(row) for row in rows] for table, rows in original.items()}
    for row in expected.get("cliches", []):
        row["cliché评分"] = str(store.get_record("cliches", row["clichéID"])["computed"]["cliche_score"])
    return expected


def verify(paths: list[Path]) -> dict:
    originals = [(path.name, path.read_bytes()) for path in paths]
    checksums = [hashlib.sha256(content).digest() for _, content in originals]
    with tempfile.TemporaryDirectory(prefix="InkReference-catalog-") as directory:
        root = Path(directory)
        store = DataStore(root / "source.db")
        batch = parse_library_files(originals)
        preview = preview_library_import(store, batch)
        if preview["errors"]:
            raise ValueError("; ".join(preview["errors"]))
        expected = table_values(batch)
        commit_library_import(store, batch, "error")
        if raw_values(store) != expected:
            raise AssertionError("Raw import changed fields or record order")
        canonical_expected = canonical_values(store, expected)
        formats = {}
        for format, extension in (("db", "db"), ("csv", "zip"), ("xlsx", "xlsx")):
            target = DataStore(root / f"target-{format}.db")
            exported = export_library(store, format)
            imported = parse_library_files([(f"catalog.{extension}", exported)])
            result = preview_library_import(target, imported)
            if result["errors"]:
                raise AssertionError(f"{format} reimport validation failed")
            if table_values(imported) != canonical_expected:
                raise AssertionError(f"{format} changed canonical fields or record order")
            commit_library_import(target, imported, "error")
            again = parse_library_files([(f"catalog.{extension}", export_library(target, format))])
            if table_values(again) != canonical_expected:
                raise AssertionError(f"{format} changed canonical values after a second export")
            with target.connect() as connection:
                if connection.execute("PRAGMA foreign_key_check").fetchall():
                    raise AssertionError(f"{format} foreign key integrity failed")
            formats[format] = {"canonical_export_fields_and_order_preserved": True, "second_canonical_export_preserved": True}
        repeated = parse_library_files(originals)
        preview_library_import(store, repeated)
        outcome = commit_library_import(store, repeated, "skip")
        if outcome["imported"] or outcome["updated"] or outcome["skipped"] != sum(len(rows) for rows in expected.values()):
            raise AssertionError("Repeated import changed the catalog")
        if raw_values(store) != expected:
            raise AssertionError("Repeated import changed raw fields or record order")
    if [hashlib.sha256(path.read_bytes()).digest() for path in paths] != checksums:
        raise AssertionError("Input bytes changed")
    return {"status": "passed", "raw_import_fields_preserved": True, "raw_import_order_preserved": True,
            "canonical_export_fields_and_order_preserved": True, "derived_fields_recomputed": ["cliché评分"],
            "formats": formats, "repeated_import_idempotent": True,
            "input_bytes_unchanged": True, "model_calls": 0}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs=3, type=Path, help="Complete works, characters and cliches CSV set, in any order")
    parser.add_argument("--output", type=Path, help="Optional local verification result")
    args = parser.parse_args()
    try:
        result = verify(args.files)
    except (OSError, ValueError, AssertionError, DataError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
