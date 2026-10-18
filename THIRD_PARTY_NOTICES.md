# Third-party code and reuse record

## InkOctoBot

- Repository: <https://github.com/ZichenFrankFu/InkOctoBot>
- Exact upstream commit: `51c82b6f01f6a5b5210e1bae9302be257b3b8083`.
- Source: `reference_pipeline/chapter_parser.py` (`cn2int`, numbered chapter regular expressions, `_score_pattern`, selected garble-diagnostic signatures).
- Destination: `backend/inkreference/text_processing.py`.
- Copyright: Copyright (c) 2026 ZichenFrankFu.
- License: MIT; the complete upstream notice is retained at [`third_party/inkoctobot-LICENSE.txt`](third_party/inkoctobot-LICENSE.txt).

Adaptations: functions accept text/configuration explicitly; no global settings, database, API or GUI dependencies. Chinese numerals correctly handle the `万` section and digit-only years. Chapter patterns restrict whitespace to horizontal characters, recognize short works, preserve preface/title/body/tail as exact spans, and replace floor-division fallback with lossless range-based chunking. Diagnostic signatures flag suspected corruption and author notes instead of deleting story content. HTML entities are decoded. Confirmed ASCII ID removal uses non-consuming lookarounds; it is opt-in. Originals and cleaned versions are both returned with hashes and transformation stages.

`backend/inkreference/reuse_check.py` loads the actual upstream `reference_pipeline/chapter_parser.py` and `reference_pipeline/ai_extractor.py` only from an explicitly supplied developer checkout at this commit. It verifies both files against git (allowing only CRLF/LF checkout differences) and records both committed and checkout byte hashes. These old modules are not vendored, bundled, imported by the app, or rewritten as hidden baselines. Its synthetic fixtures call only rule/prompt-building functions, make zero LLM calls, and disable user-defined upstream garble patterns for reproducibility.

## Candidate infrastructure and analysis libraries

React, FastAPI, SQLite, Vite and other declared dependencies retain their own licenses in their distributions. Installation and use of a dependency does not imply that another project's application source was copied.

No Storymap, novel-graphrag, NovelForge, Open Notebook, LangConfig or LangExtract source code is included in Batch 01. LangExtract is an adapter candidate for later real-model evaluation; neither it nor an LLM was run for the Batch 01 reuse checks. No third-party platform was copied wholesale.
