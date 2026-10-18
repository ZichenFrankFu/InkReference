"""Reproducible rule-only comparison against an explicit InkOctoBot checkout."""

from __future__ import annotations

import hashlib
import importlib.util
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

from .text_processing import analyze_text, chunk_text, clean_text

SOURCE_COMMIT = "51c82b6f01f6a5b5210e1bae9302be257b3b8083"
SOURCE_REPOSITORY = "https://github.com/ZichenFrankFu/InkOctoBot"

# Synthetic fixture: no uploaded novel content enters this module or the repo.
SYNTHETIC_STORY = (
    "题记：港口旧钟停在六点。\n"
    "第一章 铜钥匙\n林桐在失物箱找到一把铜钥匙。管理员说：&quot;留到明天。&quot;\n"
    "第二章 白手套\n送信人把信藏进白手套。信纸背面画着那座旧钟。\n"
    "第三章 身份\n林桐用铜钥匙打开钟室。管理员递来值班证，他曾是钟室的守卫。\n"
    "第四章 归还\n送信人归还借走的钟摆，铜钥匙留在失物箱中。全文尾标。"
)


def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Cannot load source module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _case(name: str, source: str, expected: str, old: str, new: str, **metadata: Any) -> dict[str, Any]:
    return {"name": name, "source_characters": len(source), "expected_characters": len(expected),
            "old_characters": len(old), "new_characters": len(new),
            "old_defect_reproduced": old != expected, "new_pass": new == expected,
            "old_has_tail": old.endswith(expected[-20:]), "new_has_tail": new.endswith(expected[-20:]),
            **metadata}


def evaluate_reuse(source_repo: str | Path) -> dict[str, Any]:
    """Load trusted local source, require its exact clean reference revision.

    This developer CLI helper executes only the two named Python modules; never
    expose source_repo as an uploaded-file or application input.
    """
    root = Path(source_repo).resolve()
    commit = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    if commit != SOURCE_COMMIT:
        raise ValueError(f"Expected source commit {SOURCE_COMMIT}; got {commit}")
    paths = ["reference_pipeline/chapter_parser.py", "reference_pipeline/ai_extractor.py"]
    source_hashes = {}
    for path in paths:
        committed = subprocess.check_output(["git", "-C", str(root), "show", f"{SOURCE_COMMIT}:{path}"])
        # A clean Windows checkout may use core.autocrlf. Python normalizes
        # source newlines too; tolerate only that platform newline difference.
        if (root / path).read_bytes().replace(b"\r\n", b"\n") != committed.replace(b"\r\n", b"\n"):
            raise ValueError(f"Source file differs from reference commit: {path}")
        source_hashes[path] = hashlib.sha256(committed).hexdigest()
    parser = _load_module(root / paths[0], "_inkreference_old_chapter_parser")
    extractor = _load_module(root / paths[1], "_inkreference_old_ai_extractor")
    # Isolate original built-ins from any local user's settings file.
    parser._load_user_garbled_patterns = lambda: []
    cases = []
    no_heading = "甲" * 3490 + "结尾必须完整保留尾标甲乙"
    old = "".join(chapter["content"] for chapter in parser.detect_chapters(no_heading)["chapters"])
    new_result = analyze_text(no_heading)
    new = "".join(chapter["text"] for chapter in new_result["chapter_analysis"]["chapters"])
    cases.append(_case("unheaded_tail", no_heading, no_heading, old, new))
    entities = "他说：&quot;灯亮了&quot;，甲&amp;乙。"
    cases.append(_case("html_entities", entities, '他说："灯亮了"，甲&乙。',
                       parser.strip_deletable_garbled(entities), clean_text(entities)["cleaned_text"]))
    random_id = "左3Abq5FQkXVhYkEy8u3y右"
    cases.append(_case("random_id_neighbors", random_id, "左右", parser.strip_deletable_garbled(random_id),
                       clean_text(random_id, remove_random_ids=True)["cleaned_text"],
                       new_default_preserves_id=clean_text(random_id)["cleaned_text"] == random_id))
    long_body = "甲" * 32980 + "超长单章结尾必须保留。"
    old_chunks = extractor.build_segment_text_chunks([{"title": "", "content": long_body}])
    old_long = "".join(chunk["text"] for chunk in old_chunks)
    old_lost_tail = not old_long.endswith(long_body[-20:])
    new_long = "".join(chunk["text"] for chunk in chunk_text(long_body, 3000))
    case = _case("oversized_single_chapter", long_body, long_body, old_long, new_long,
                 old_defect="prompt builder truncates body and inserts omission marker", new_chunk_count=len(chunk_text(long_body)))
    # Old adds formatting, so mismatch alone is insufficient evidence here.
    case["old_defect_reproduced"] = old_lost_tail
    cases.append(case)
    synthetic = analyze_text(SYNTHETIC_STORY)
    passed = all(case["old_defect_reproduced"] and case["new_pass"] for case in cases)
    return {"source_repository": SOURCE_REPOSITORY, "source_commit": commit,
            "source_files_sha256": source_hashes,
            "checkout_files_sha256": {path: hashlib.sha256((root / path).read_bytes()).hexdigest() for path in paths},
            "source_comparison": "git blob equality after CRLF-to-LF normalization only",
            "reference_settings": "built-in garble patterns only; user patterns disabled",
            "comparison": "rule-only preservation checks; no model calls", "model_calls": 0,
            "cases": cases, "all_expected_comparisons_pass": passed,
            "synthetic_sample": {"original_characters": len(SYNTHETIC_STORY),
                                 "cleaned_characters": len(synthetic["cleaned_text"]),
                                 "chapters": sum(chapter["kind"] == "chapter" for chapter in synthetic["chapter_analysis"]["chapters"]),
                                 "coverage": synthetic["chapter_analysis"]["coverage"]},
            "limitations": ["No real LLM or LangExtract evaluation was run.",
                            "Character-bound chunks are a preservation primitive, not token-aware inference chunks.",
                            "No Windows desktop runtime was tested by this script."]}
