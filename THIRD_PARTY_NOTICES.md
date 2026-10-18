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

## InkOctoBot extraction declarations (workbench batches)

The same MIT upstream commit `51c82b6f01f6a5b5210e1bae9302be257b3b8083` supplies the declarations adapted into `backend/inkreference/skills.py`:

- `reference_pipeline/prompts.py`, `reference.unified` / `reference.characters`: single structured JSON extraction and character facts.
- `agents/reference_extractors/skills/chronicle_event_extract/skill.py`: objective event descriptions and explicit unknown dates.
- `agents/reference_extractors/skills/character_profile/skill.py`: structured character facts.
- `agents/reference_extractors/skills/shuangdian_extract/skill.py`: source text spans as evidence.

Adaptations retain only declarative prompts/schema, remove global model routing and BaseSkill execution, avoid forced date inference, and unify summary/characters/events/cliches with exact contiguous evidence. Raw inputs remain data. The engine validates source spans and requires explicit human approval before materialization. The upstream MIT notice remains in `third_party/inkoctobot-LICENSE.txt`.

## Dependency reuse in the workbench

No whole application platform is vendored. Installed packages retain their own distributions and licenses. Versions are recorded in `requirements-lock.txt`, `requirements-mineru.txt` and `frontend/package-lock.json`.

| Project | Direct use | License/source |
|---|---|---|
| LangGraph / SQLite checkpoint | Real StateGraph, interrupts and persistent checkpoints | MIT, <https://github.com/langchain-ai/langgraph> |
| React Flow | Workflow canvas navigation, node dragging and layout | MIT, <https://github.com/xyflow/xyflow> |
| pywebview | Optional native desktop window | BSD-3-Clause, <https://github.com/r0x0r/pywebview> |
| HTTPX | Model protocols, timeouts and test transports | BSD-3-Clause, <https://github.com/encode/httpx> |
| markdownify | EPUB HTML to Markdown | MIT, <https://github.com/matthewwithanm/python-markdownify> |
| markdown-it-py | Markdown token parsing for plain TXT output | MIT, <https://github.com/executablebooks/markdown-it-py> |
| MinerU 4.0.12 | Optional PDF parsing SDK, isolated trusted Python process | Apache-2.0 **with additional upstream terms**, <https://github.com/opendatalab/MinerU/blob/master/LICENSE.md> |

PDF conversion uses MinerU. Its runtime/models are installed separately rather than copied into the application. Current SDK documentation: <https://github.com/opendatalab/MinerU/blob/master/docs/en/usage/sdk_api.md>. The default full-document `flash/txt` test extracts native PDF text; it is not an OCR or model-quality evaluation. Full Markdown rendering preserves header-classified content that default rendering may omit.

## Historical candidate scope

React, FastAPI, SQLite, Vite and other declared dependencies retain their own licenses in their distributions. Installation and use of a dependency does not imply that another project's application source was copied.

No Storymap, novel-graphrag, NovelForge, Open Notebook, LangConfig or LangExtract source code is included in Batch 01. LangExtract is an adapter candidate for later real-model evaluation; neither it nor an LLM was run for the Batch 01 reuse checks. No third-party platform was copied wholesale.
