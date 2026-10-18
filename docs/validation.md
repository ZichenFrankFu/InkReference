# Verification record

## Complete library exchange and direct text workflow (2026-10-18)

- Local Linux / Python 3.12 regression: **507 passed, 1 skipped**. The skipped case requires actual Windows Credential Locker. New HTTP integration checks exercise all three library formats, complete-set and version diagnostics, import-token separation, transactional relationship preservation, asynchronous upload progress, replacement/continuation, stale edits and work-deletion races.
- The actual private v8 triplet again retained all CSV fields and row order: 536 works, 1,213 characters, 138 cliches and 1,155 work/cliche relations. DB, CSV ZIP and XLSX export/import into fresh stores retained every original v8 value. Reimport skipped 1,887 rows without changing them. Private inputs and generated databases are not committed.
- A real local API and production Chromium frontend passed **14 text-workflow checks**: non-destructive TXT intake including NUL/control marks, automatic headings, cross-chapter findings/select-all, direct dirty editing and saving, explicit cleaning, exact saved TXT export, update/append and active source history, real EPUB conversion, and installed MinerU native-text PDF intake. At an artificial 450 ms uncached chapter delay, reader geometry moved **0 px** and retained the prior read-only body until the next body arrived. A 50,000-character chapter stayed fully editable without redundant paging; a 240,000-character Unicode chapter's page edit retained the unseen tail. Eight widths from 390 to 1,440 px had no page overflow or toolbar overlap. No LLM calls or browser JavaScript errors occurred in this flow.
- Material-library Chromium layout checks cover 390, 540, 720, 800, 960, 1,100 and 1,440 px, including related-card meanings and the narrow detail overlay. Missing-file checklist, adding omitted files and format selection were checked with synthetic preview responses; actual format/commit behavior is covered separately by real HTTP tests.
- Four additional real-browser checks cover source records, volume planning and unsaved-volume protection. Ten widths from 390 to 1,600 px had no document/panel overflow or button overlap.
- The analysis/review production-browser regression passed **18 checks** with five narrative tabs and five setting-collection tabs. Independent approvals, manual edits, prompt/paste, history, four-section TXT interchange, live project backup/restore and usage accounting still pass. Its three model requests use an explicitly synthetic local HTTP provider, not an actual LLM.
- Real MinerU 4.0.12 `flash/txt` converted a generated 12-page Chinese PDF both through ConversionStore and through the new upload pipeline. All twelve page markers, Chinese headings, complete source coverage and original PDF bytes remained present; automatic parsing produced twelve chapters plus a preface. This is native-text PDF extraction, **not OCR**. The reproducible optional check is `python scripts/verify_mineru.py`.
- The real source-launch smoke exercises all DB/CSV/XLSX exports and TXT upload/marking, and now runs those operations in frozen Windows/macOS CI too. Frontend source fingerprints force a rebuild after source changes; missing dependencies or build failures stop startup before an old interface can launch. Environment tests cover native command paths; local checks do not establish interactive Windows/macOS window behavior.
- Explicit SQLite closing, bounded shutdown, late-worker suppression and workspace ownership through ingestion/conversion workers are covered by regressions. The InkOctoBot exact-commit preservation comparison still passes all four rule-only cases. The standalone frontend helper checks ten CRLF/Unicode editing boundaries. Real system IME input, scanned-PDF OCR and commercial-model output quality remain separate real-machine acceptance items.

The records below describe earlier batches and their historical GUI controls.

## Reference-database parity and blue chapter workbench (2026-10-18)

- Reviewed InkOctoBot commit `51c82b6f01f6a5b5210e1bae9302be257b3b8083`, including the seven narrative tabs, five setting-collection tabs, chapter preprocessing and independent extraction sections. The actual feature map and remaining differences are recorded in [reference-parity.md](reference-parity.md).
- Latest local regression: **400 passed, 1 skipped** on Linux / Python 3.12. The skip requires the actual Windows Credential Locker. TypeScript type checking and the production Vite build passed. These local results are not Windows/macOS runtime evidence; platform results are linked from the PR's Actions checks.
- Real Chromium against the production frontend and local API: **23 chapter-workbench checks passed**, with no JavaScript page errors or model calls. Checks covered import and automatic headings, amber/violet issue labels, cached chapter changes with **0 px layout movement**, inline red removal toggles, selected cleaning, undo, body editing and unsaved-edit protection, rename/insert/delete, two-file append, original byte downloads, historical restore, invalid heading/regex atomicity, volume rule/manual/paste validation, 390 px layout and 210,000-codepoint coverage.
- The analysis browser flow passed **18 checks on each of two fresh workspaces**: seven narrative/five collection tabs, credential-free manual tasks, prompt/pasted JSON, independent section commits, editing an uncommitted section after another approval, structured editors and style views, summary/translation previews, one-chunk LangGraph execution, literal collection snapshots, four-section TXT round trips, token accounting, narrow layout, live project export and checked restore. The three provider calls per run use a local synthetic HTTP server, not a real LLM; no commercial-model quality claim is made.
- The original three private CSVs again preserved all fields and record order: 536 works, 1,213 characters, 138 cliches, 1,155 relations; repeated import skipped 1,887 records and changed none. Original inputs were not modified. The upstream four text-preservation comparisons also passed without model calls.
- New integrity checks cover exact Unicode proposal offsets, immutable append sources, chapter/version conflicts, custom regex timeouts, literal collection snapshots (including entities and CRLF), four-section TXT round trips, transactional rejection, independent approval ledgers, and history restoration of active contributions/manual overrides. Sources, screenshots and runtime databases remain untracked.
- A supplementary shutdown check initially found an inconsistent workbench database from the browser harness, which sent SIGINT without waiting for the Python child to exit. Those artifacts are not used as backup evidence. The harness now awaits shutdown, and the new reference/volume stores explicitly close transaction connections. Two complete fresh browser runs subsequently passed live backup/restore, post-exit integrity checks for every database present, and portable restoration of document sources, review ledgers and history. An independent 15-collection API stress run also retained database integrity before and after exit. The committed API regression covers restored append originals, version text, volume plans, metadata, section approvals and analysis history.

Older sections below record earlier batches and their narrower verification scope.

## Local checks completed

- Python 3.12: 79 tests passed, covering transactional CSV imports, rollback, CRUD, revision conflicts, API validation, text preservation, export behavior and real launcher HTTP responses after TCP clients disconnect.
- TypeScript type checking and the Vite production build passed.
- The three private v8 inputs imported as 536 works, 1,213 characters, 138 cliches and 1,155 work/cliche relations. Compatible export preserved record order and every CSV field exactly after standard CSV parsing. Export, reimport into a new database and a second export preserved those values and relationships again.
- Reimporting the same inputs with `skip` produced 0 inserts, 0 updates and 1,887 skipped records.
- The exact upstream InkOctoBot commit reproduced four text-loss boundaries. The adapted pure functions retained the expected text in all four cases; the synthetic four-chapter sample had complete, contiguous coverage. No LLM calls were made.
- Chromium browser checks against a real local API passed with synthetic data: empty state, multi-file preview/commit, multiline work editing, related-character navigation, character create/delete, ZIP download and narrow-screen layout. No JavaScript page errors were observed. User CSV content and local screenshots are not committed.

## Reproduce private v8 verification

Keep the three original CSVs outside the repository, then run:

```powershell
.\.venv\Scripts\python.exe scripts\verify_v8.py --input-dir "D:\writing\v8" --output artifacts\v8-roundtrip.json
```

This command targets the original v8 dataset and asserts its known row/relation counts. It uses temporary databases, does not alter the inputs and does not commit their contents.

General regression checks use the synthetic fixtures:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
Push-Location frontend
npm run typecheck
npm run build
Pop-Location
```

## Scope of evidence

The development host is Linux. The included GitHub Actions workflow runs backend tests on Windows/Python 3.12 and Linux/Python 3.11, parses the scripts with Windows PowerShell, builds the frontend, runs the Windows setup script, and checks launcher HTTP health with a Chinese data-directory path. Its actual run status should be checked on the PR. A Windows runner pass does not establish interactive installation, browser opening or desktop GUI behavior on a user's PC.

The records below preserve Batch 01 evidence. Workbench batches now add full-text processing, real LangGraph execution, uploaded declarative Skills, model protocols, usage, backups and desktop/package entry points. Table CSV export remains distinct from the full restore-capable project ZIP.

## Synthetic GUI verification detail

Browser checks used headless Chromium against a real FastAPI server and temporary SQLite data. No API responses were mocked and no user CSV contents were used in screenshots. The committed synthetic fixtures contain 3 works (including two editions with the same title), 3 characters, 2 cliches and 3 work/cliche relations.

The first browser pass verified:

1. A new database displays the actionable empty state, with no fabricated records.
2. Uploading all three CSV fixtures displays file-level new/existing counts; a single confirmation imports the complete batch.
3. Editing a work preserves Chinese text and embedded newlines.
4. The related-material tab navigates from a work to its character and allows saving a character edit.
5. A new character can be created, then removed through the deletion confirmation.
6. Compatible ZIP export initiates a real browser download.
7. The 390 px layout keeps navigation, tables and a character/cliche detail panel usable. The browser reported no JavaScript page errors.

A production-build pass additionally verified a server-side revision change produces a conflict notice without losing the local draft; the reload action fetches the current record. The full ZIP and single-table CSV downloads use the expected attachment names. Cliche descriptions remain editable while their immutable ID prefix is retained. Searching for one work filters the list to one result.

Screenshots are local verification artifacts under `artifacts/ui-*.png`; they are excluded from git. TypeScript checking and Vite production compilation passed again after text contrast, font-size and modal focus improvements. These checks establish browser/API behavior on Linux, not interactive Windows verification.

## Workbench batches: local verification

- Python 3.12: **253 tests passed**, including real SQLite transactions, source coverage, immutable uploads/versions, EPUB conversion, durable LangGraph interruptions, retries, failed-request accounting, exact evidence, manual revision protection and idempotent materialization. Approval recovery also reconciles a main-library commit that preceded an interrupted workbench confirmation, restoring the exact committed review without duplicate inserts.
- Real competing processes verify exclusive workspace ownership, normal/exception/crash release, and delayed release while an existing model worker is still finishing.
- Real subprocesses verify conversion cancellation/close, process-tree cleanup, interrupted restart and retry. A conversion-to-document transaction key prevents duplicates even when document commit precedes conversion-link commit.
- The original private v8 CSV semantic round trip was rerun: 536 works, 1,213 characters, 138 cliches, 1,155 relations. All fields and row order remain identical; reimport skips 1,887 rows with no inserts/updates.
- TypeScript and production Vite build passed. Headless Chromium against a real local API completed CSV import, model configuration, HTTP provider fixture + usage, full-text import, LangGraph extraction + explicit approval, React Flow, real MinerU conversion, TXT download, project backup and the 390px layout. No JavaScript page errors. Model output in this browser check came from an explicitly synthetic local HTTP fixture, not a real LLM.
- **Real MinerU 4.0.12** was installed independently and used through ConversionStore on a generated 12-page PDF with Chinese and unique per-page markers. `flash/txt` -> full Markdown -> TXT retained all 12 markers and Chinese, including page 12. Default Markdown rendering omitted header-classified content in this fixture; full rendering fixes that boundary. This is native PDF text extraction, **not OCR or an LLM-quality evaluation**.
- Windows desktop lifecycle tests use a fake renderer. GitHub Actions has also built the portable ZIP and Inno Setup installer in a named Windows Conda environment and passed the frozen EXE HTTP/UI/database smoke check ([run 39102048363](https://github.com/ZichenFrankFu/InkReference/actions/runs/39102048363)). Native window interaction, actual Setup install/uninstall and scanned-page OCR remain separate real-machine acceptance items; consult the PR checks for the latest commit.

Reproduce the optional native-PDF integration (after installing MinerU in the app environment):

```powershell
conda run --no-capture-output -n InkReference python scripts/verify_mineru.py
```

If MinerU runs in a separate environment, set `INKREFERENCE_MINERU_PYTHON` to its Python executable and ensure ReportLab is available in the environment running this verification script (MinerU's installed dependencies include it). This generates invented pages in a temporary directory, makes no model API calls and requests no inference weights.

## Model configuration and local privacy revision

- Python 3.12: **314 passed, 1 skipped**. The skipped test writes, reads and deletes a newly generated credential through actual Windows Credential Locker and runs on Windows CI. Linux checks exercise a synthetic vault plus fail-closed native-backend selection.
- Regression coverage includes literal loopback versus HTTPS targets, endpoint-bound credentials, safe draft tests and model discovery, no secret in application databases/usage/backup, credential clearing, blocked redirects/environment proxies, Host/Origin/Fetch Metadata isolation, no-cache/CSP and validation errors that omit rejected secret inputs.
- A real API race test changes the endpoint or model in another simulated tab after consent was shown. The stale target fingerprint produces 409, zero model requests and zero new tasks. Missing initial remote confirmation produces 428. The current revision resumes the immutable target without asking again.
- LangGraph execution explicitly disables LangSmith tracing even when inherited tracing environment variables are enabled. The regression checks that model execution sees tracing disabled and creates no trace posts.
- Headless Chromium against the production frontend and real FastAPI/SQLite services passed **13 checks**: CSV import; unsaved draft test; explicit model discovery; GUI model save; provider HTTP/usage; document import; LangGraph/evidence/approval; workflow canvas; real MinerU conversion; TXT download; backup; 390px backup and model-settings layouts. No page errors. The local HTTP model returns clearly synthetic content; no commercial model was called.
- Separate mocked-interface browser checks passed 10 model-settings cases and 9 navigation/consent/draft cases. These distinguish GUI behavior from backend security enforcement.
- Windows packaging includes the explicit native keyring backend. Its CI smoke additionally saves and deletes an invented credential through the frozen EXE API. Native desktop interaction, real commercial-model quality and OCR remain separate acceptance items.

## Cross-platform runtime and simpler interaction

- Python 3.12 on Linux: **352 passed, 1 Windows-only test skipped**. This includes 30 environment-selection tests, source launch in paths with Chinese/spaces, Python and Node diagnostics, explicit existing-Conda selection without environment creation, macOS data/log location compatibility, and Windows/macOS packaging contracts.
- The real shell launcher (`bash scripts/open.sh --browser --no-open`) passed HTTP health, built-frontend and SQLite checks through `scripts/verify_launch.py`. `doctor --json` and execution in the explicitly selected environment were also run locally.
- TypeScript/production build passed. 11 simulated-interface browser checks verify direct connection tests/model discovery, one remote-analysis confirmation, direct resume, draft protection, progress and 390px layouts. The existing 13-check real FastAPI/SQLite browser flow passed again, including the local synthetic HTTP provider, LangGraph approval, actual MinerU native-text PDF and exports; no JavaScript errors.
- macOS PyObjC 12.2.2 dependencies were checked as CPython 3.12 universal2 wheels targeting macOS 10.13, before constraining them for Darwin only. The application bundle declares macOS 13+ and builds independently for arm64/x86_64. Local Linux packaging-contract tests are not macOS execution evidence.
- CI now runs Windows, macOS arm64, macOS Intel and Linux backend checks, source `.venv` installation/reuse/launch on both desktop systems, and explicit reuse of a developer-managed interpreter. Packaging runs without Conda, extracts both macOS ZIPs, verifies their ad-hoc signatures and probes the extracted executable's HTTP/UI/database. Windows also checks actual credential storage through the frozen API. Consult the new PR for actual run results.
- Packaged applications include Python/frontend resources. Source installers still require supported Python and Node, while packaged end users do not need Python, Node or Conda. macOS distributions are not Developer ID signed/notarized; interactive desktop behavior and downloaded-app Gatekeeper behavior require real-machine acceptance.
