"""Verify private v8 inputs without committing them or modifying their bytes."""
from __future__ import annotations

import argparse
import csv
import io
import json
from pathlib import Path
import sys
import tempfile
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from inkreference.csvio import COMPATIBLE_NAMES, commit_import, export_zip, parse_files, preview_import
from inkreference.database import DataStore


def rows(content: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"), newline="")))


def verify(input_dir: Path) -> dict:
    files = [(name, (input_dir / name).read_bytes()) for name in COMPATIBLE_NAMES.values()]
    with tempfile.TemporaryDirectory(prefix="InkReference-v8-") as temporary:
        store = DataStore(Path(temporary) / "first.db")
        batch = parse_files(files)
        preview = preview_import(store, batch)
        if preview["errors"]:
            raise ValueError(f"Import validation failed: {preview['errors']}")
        first = commit_import(store, batch, "error")
        expected = {"works": 536, "characters": 1213, "cliches": 138, "work_cliches": 1155}
        if first["stats"] != expected:
            raise AssertionError(f"Expected original v8 counts {expected}, got {first['stats']}")
        exported = export_zip(store, "compatible")
        comparisons = {}
        with zipfile.ZipFile(io.BytesIO(exported)) as archive:
            for filename, original in files:
                # Includes record order, every field, multiline strings and unknown values.
                comparisons[filename] = rows(original) == rows(archive.read(filename))
                if not comparisons[filename]:
                    raise AssertionError(f"CSV field round trip changed: {filename}")
            second_files = [(name, archive.read(name)) for name in COMPATIBLE_NAMES.values()]
        second_store = DataStore(Path(temporary) / "second.db")
        second_batch = parse_files(second_files)
        second_preview = preview_import(second_store, second_batch)
        if second_preview["errors"]:
            raise AssertionError(f"Exported CSVs failed reimport: {second_preview['errors']}")
        second = commit_import(second_store, second_batch, "error")
        if second["stats"] != expected:
            raise AssertionError("Relationship counts changed after reimport")
        repeated = parse_files(files)
        preview_import(store, repeated)
        repeated_result = commit_import(store, repeated, "skip")
        if repeated_result["imported"] or repeated_result["updated"] or repeated_result["skipped"] != 1887:
            raise AssertionError("Repeated import was not idempotent")
        with zipfile.ZipFile(io.BytesIO(export_zip(second_store, "compatible"))) as archive:
            for filename, original in files:
                if rows(original) != rows(archive.read(filename)):
                    raise AssertionError(f"Fields changed after export and reimport: {filename}")
        return {
            "status": "passed", "stats": first["stats"],
            "csv_all_fields_and_order_preserved": comparisons,
            "reimport_stats": second["stats"],
            "repeated_import": repeated_result,
            "original_files_modified": False,
            "model_calls": 0,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", required=True, type=Path, help="Directory containing the three original v8 CSVs")
    parser.add_argument("--output", type=Path, help="Optional private JSON verification result")
    args = parser.parse_args()
    try:
        result = verify(args.input_dir)
    except (OSError, ValueError, AssertionError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
