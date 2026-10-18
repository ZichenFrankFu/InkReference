"""Compare preserved source at a fixed revision; writes only explicit output."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from inkreference.reuse_check import evaluate_reuse  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-repo", required=True, type=Path, help="Trusted InkOctoBot checkout at the recorded commit")
    parser.add_argument("--output", type=Path, help="Optional local JSON result (keep outside git)")
    args = parser.parse_args()
    result = evaluate_reuse(args.source_repo)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if result["all_expected_comparisons_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
