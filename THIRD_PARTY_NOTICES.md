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

## InkOctoBot reference-tab parity

The reference database UI and the actual backend handlers were re-audited at the same exact MIT commit `51c82b6f01f6a5b5210e1bae9302be257b3b8083`. The concrete page/function inventory is retained in [`docs/reference-parity.md`](docs/reference-parity.md); historical comments and unused endpoints are distinguished from rendered controls.

| Upstream source | Adaptation in InkReference |
|---|---|
| `reference_pipeline/chapter_parser.py`: author-note keywords, `detect_aside_paragraphs`, dialogue/tail/fence guards, length-outlier and chapter-gap rules, chapter templates | `backend/inkreference/chapter_preprocessing.py`, `text_processing.py`: reviewable chapter diagnostics and exact codepoint deletion proposals; customizable declarative patterns; immutable versions rather than in-place rewriting |
| `reference_pipeline/volume_detector.py`: explicit volume-marker detection | `backend/inkreference/volumes.py`: version-pinned plans, rule suggestions, validated model/pasted/manual ranges with complete chapter coverage |
| `ui/frontend/src/components/reference/referenceMerge.ts`: character and setting merges, per-chunk section ledger, character-weighted style aggregation and chapter signals | `backend/inkreference/reference_analysis.py`: server-side merging, transactional independent approvals, revisions and idempotent contributions; existing edited scalar facts take precedence |
| `reference_pipeline/nlp_stats.py`: `compute_nlp_style` sentence, vocabulary and punctuation statistics | `backend/inkreference/reference_analysis.py`: local deterministic metrics, computed alongside model/pasted semantic signals; the tokenizer-free implementation explicitly uses Unicode characters |
| `ui/frontend/src/components/reference/AnalysisEditors.tsx`, `CharactersAndSettingsExtraction.tsx`, `PureSettingPanel.tsx`: chronicle hierarchy, rich character/setting fields, style/rhythm and pure-setting data shapes | `backend/inkreference/reference_analysis.py`, `skills.py`: compatible rich reference sections alongside the existing evidence-grounded material extraction |

The frontend is redesigned in `ReferenceAnalysis.tsx`, `ReferenceExtraction.tsx`, `ReferenceEditors.tsx`, `ReferenceDocumentPanels.tsx` and `ChapterWorkbench.tsx` rather than bundling the upstream frontend. `ReferenceLibraryPage.tsx`, `FilesPanel.tsx`, `PreprocessPanel.tsx` and `UnifiedExtractionPanel.tsx` provide the audited business-flow references: continuation uploads, chapter CRUD, inline review, independent extraction sections, and whole-work/segment browsing. The upstream multi-JSON nontransactional writes, auto-commit batch path, aggressive pasted-JSON repair and inferred absolute dates are not required for feature compatibility. The current fixed built-in Skill/Workflow uses version 3.0.0 and preserves prior 1.0.0/1.1.0/2.0.0 declarations for existing snapshots. User data and the upstream global settings/model router are not copied.

The database-management revision additionally audits `storage/reference_schema.py` and `knowledge/reference_db.py`, plus the actual `CharacterCard` behavior in `CharactersAndSettingsExtraction.tsx`. The retained design separates chapter-tagged character facts (`appearance`, `personality`, `experiences`), setting updates, first chapter and in-story time. Existing character experiences take precedence; matching plot-event subjects provide a derived fallback only when experiences are empty. These schema/relationship semantics are adapted in `backend/inkreference/catalog_links.py` and the reference displays to InkReference's own material IDs and version-pinned analysis stores; the upstream DDL is not copied verbatim. `reference_entries`, writing-project prompt injection and the upstream L1/L2/L3 vector index are audited boundaries, not claims of implemented ports.

Pure-setting TXT interchange is a new UTF-8 serialization that preserves all four collections, multiline/marker content and unknown fields; the upstream line-oriented TXT parser is not copied. Analysis history additionally snapshots active section contributions and manual overrides, so restoring a view does not leave a contradictory approval ledger behind. Semantic style judgments remain model-produced/manual data; local Unicode statistics are not represented as an NLP or LLM quality evaluation.

Copyright remains Copyright (c) 2026 ZichenFrankFu. The complete MIT permission and warranty notice is preserved in [`third_party/inkoctobot-LICENSE.txt`](third_party/inkoctobot-LICENSE.txt) and included by the desktop packaging configuration.

## V10 chronicle, language and fixed-stage adaptations

The same audited InkOctoBot MIT commit supplies the design references; no new upstream application codebase is copied. `ui/frontend/src/components/reference/referenceMerge.ts`, `AnalysisEditors.tsx` and `CharactersAndSettingsExtraction.tsx` remain the reference for chronicle events, chapter-linked facts, character experiences and setting evolution. Their mechanisms are adapted into `chronicle_schema.py`, `reference_analysis.py`, `analysis_projection.py` and `ChronicleEditors.tsx`: stable IDs, exact source evidence, independent disclosure/story clocks, causal/plotline links, stage goals/statuses and correction chains are additions in InkReference. Legacy name-based merges and chapter arrays are compatibility behavior, not the identity rule for new entities. Automatic inherited absolute dates are deliberately omitted.

`reference_pipeline/nlp_stats.py` remains the origin of the separation between locally computed statistics and model-produced semantic judgments. The new `language_analysis.py` is a tokenizer-free implementation of writer-facing sentence/paragraph distributions, punctuation frequency and source coverage; it replaces vocabulary-diversity requirements for the new contract. Approved era occurrences, approximate unverified date ranges and character-use frequencies are new InkReference data structures, not a copied or externally validated web dataset.

`fixed_pipeline.py` and updated LangGraph definitions replace the upstream one-call unified-extraction UI with ordered plot, character, setting and language stages. Entity context is adaptively bounded and excludes future factual summaries; stage outputs use a locked schema, models may vary by stage, review is explicit and successful responses are durable. Exact local model-candidate caching is a bounded new InkReference implementation rather than an upstream response service. Chapter-folder projection/metadata, global progress rails and the split content/task pages are local application changes. These adaptations retain the upstream MIT notice; no upstream global model router, vector index, user corpus or executable Skill implementation is bundled.

## Dependency reuse in the workbench

No whole application platform is vendored. Installed packages retain their own distributions and licenses. Versions are recorded in `requirements-lock.txt`, `requirements-mineru.txt` and `frontend/package-lock.json`.

Frontend runtime license notices are retained in `third_party/frontend-LICENSES.txt`. Windows packages include the `third_party` directory alongside this notice; Python distribution metadata is collected by the package build.

| Project | Direct use | License/source |
|---|---|---|
| LangGraph / SQLite checkpoint | Real StateGraph, interrupts and persistent checkpoints | MIT, <https://github.com/langchain-ai/langgraph> |
| React Flow | Retained dependency and legacy workflow canvas/layout compatibility; the current GUI uses fixed stage cards | MIT, <https://github.com/xyflow/xyflow> |
| pywebview | Optional native desktop window | BSD-3-Clause, <https://github.com/r0x0r/pywebview> |
| HTTPX | Model protocols, timeouts and test transports | BSD-3-Clause, <https://github.com/encode/httpx> |
| keyring | Explicit native OS credential backends; no plaintext or third-party fallback | MIT, <https://github.com/jaraco/keyring> |
| markdownify | EPUB HTML to Markdown | MIT, <https://github.com/matthewwithanm/python-markdownify> |
| markdown-it-py | Markdown token parsing for plain TXT output | MIT, <https://github.com/executablebooks/markdown-it-py> |
| openpyxl 3.1.5 | Complete material-library XLSX reading/writing, explicit text cells and long-text auxiliary sheets | MIT, <https://openpyxl.readthedocs.io/> |
| defusedxml 0.7.1 | Defensive XML parsing available to openpyxl for untrusted XLSX imports; archive/entity checks are additionally applied by InkReference | Python Software Foundation License (PSFL), <https://github.com/tiran/defusedxml> |
| MinerU 4.0.12 | Optional PDF parsing SDK, isolated trusted Python process | Apache-2.0 **with additional upstream terms**, <https://github.com/opendatalab/MinerU/blob/master/LICENSE.md> |

PDF conversion uses MinerU. Its runtime/models are installed separately rather than copied into the application. Current SDK documentation: <https://github.com/opendatalab/MinerU/blob/master/docs/en/usage/sdk_api.md>. The default full-document `flash/txt` test extracts native PDF text; it is not an OCR or model-quality evaluation. Full Markdown rendering preserves header-classified content that default rendering may omit.

The material-library exchange uses the installed openpyxl/defusedxml packages rather than copying application code. License names and versions above are confirmed by their installed distribution metadata; their distribution license files remain part of the runtime package metadata collected for desktop builds.

## Historical candidate scope

React, FastAPI, SQLite, Vite and other declared dependencies retain their own licenses in their distributions. Installation and use of a dependency does not imply that another project's application source was copied.

No Storymap, novel-graphrag, NovelForge, Open Notebook, LangConfig or LangExtract source code is included in Batch 01. LangExtract is an adapter candidate for later real-model evaluation; neither it nor an LLM was run for the Batch 01 reuse checks. No third-party platform was copied wholesale.


## V11 prompt and chapter-group review

At the same exact MIT commit `51c82b6f01f6a5b5210e1bae9302be257b3b8083`, this revision re-read these concrete implementations:

- `agents/reference_extractors/skills/chronicle_event_extract/skill.py` and `chronicle_outline_extract/skill.py`: strict JSON, chapter-arc event granularity, explicit time and unknown-date handling.
- `reference_pipeline/ai_extractor.py::build_segment_text_chunks`, `ai_extract_outline_events`, `ai_summarize_outline`: complete-chapter grouping, absolute chapter-range metadata and extraction before chronological organization.
- `reference_pipeline/prompts.py` (`reference.characters`, `reference.settings`, `reference.style`) and `agents/reference_extractors/skills/character_profile/skill.py`: work/volume context, distinct character fact categories, name/alias/relationship fields, concise setting titles with additions/revisions, and deterministic-versus-semantic analysis separation.

`skills.py` adapts these declarative mechanisms to immutable v3 stage prompts and the shared character/snapshot contract. `fixed_pipeline.py` uses saved-volume/whole-chapter groups bounded by all stage model capacities; the upstream overlong-chapter truncation is not copied. `analysis_projection.py` derives the two timeline views from flat events, without copying the upstream epochs/periods GUI or asking a model to rewrite approved events. Existing v2 prompts/schemas remain frozen; source proof and explicit human review remain authoritative. Copyright and the complete upstream MIT notice remain retained as above.

The schema-6 catalog, identity/context bridge, weighted cliche score, reviewed web-research graph, direct embedding/cosine search, vector cache and targeted packaging-metadata repair are new InkReference implementations. The web-research graph reuses installed LangGraph/HTTPX/SQLite and the existing credential/usage layers. Tavily, Brave and SearXNG are user-configured search services; no search-engine application source or third-party corpus is vendored. The semantic matcher calls OpenAI-compatible or Ollama embedding endpoints; it does not port InkOctoBot's L1/L2/L3 index. Dependency metadata and licenses continue to be distributed through the existing packaging path.

The targeted installer repair follows pip's documented [`--ignore-installed` / `--no-deps`](https://pip.pypa.io/en/stable/cli/pip_install/) options and the packaging specification's [installed RECORD requirement](https://packaging.python.org/en/latest/specifications/recording-installed-packages/). It applies to one detected broken dependency at the lockfile version, rather than bypassing normal application dependency resolution. Documentation was consulted; pip application source was not copied.

## V12 intake, narrative controls and adaptive extraction

This revision continues using the exact InkOctoBot MIT audit baseline above; it does not vendor another upstream application or corpus. The existing declarative extraction/fact-accumulation and append-setting ideas are adapted into the independent content-contract 4 in `analysis_contract_v4.py` and the shared character contract. Named phases, inherited relationship editing, chapter/percentage selectors, appended-setting chains, writer-facing language examples/composition and deterministic Chinese-name rankings are InkReference adaptations. Legacy 1/2/3 prompts, schemas and source contributions are retained rather than rewritten.

The automatic/half-automatic/manual intake dialog, request-bound web-model paste protocols, version-bound `volume_research.py` graph, copied prompt handoff, source-status distinctions, model-capacity chunk fitting and pause/terminate semantics are local implementations using the already installed LangGraph, HTTPX, SQLite and credential/model-usage layers. Half-automatic exact/manual matching is explicitly separate from real embedding/cosine search. No third-party search result, paid service credential, private manuscript or generated model response is shipped as application source. The retained MIT license and dependency license collection continue to apply.

The later V12 additions implement Ollama's streamed `/api/pull` and `/api/embed` protocols without vendoring Ollama code or model weights. Independent research A/B candidates, current-catalog score scenarios, editable review copies, chapter-scope union/reuse and extraction A/B groups are new InkReference code. They retain source outputs and per-request usage rather than claiming an upstream evaluation service was ported. Ollama must already be available locally; bare checkpoint download/automatic server deployment and real-model benchmark claims are outside this implementation.

Extraction comparisons additionally isolate their original source scope and initial entity memory with `execution_scope_mode:"comparison"`; they do not reuse ordinary partial execution state as a model comparison. Selection-gated section/material admission, explicit group identity for reused runs and contribution locking are local transaction policies. Content-4 source-coordinate normalization preserves a full-book chapter denominator including placeholders. These mechanisms do not modify the retained upstream license or historical 1/2/3 contracts.

Ollama protocol references (reviewed 2026-10-19): [Embeddings](https://docs.ollama.com/capabilities/embeddings) and the official [API documentation](https://github.com/ollama/ollama/blob/main/docs/api.md). These document the network interface; they are not a bundled runtime or a claim that model weights were downloaded during synthetic verification.
