# Verification record

## Editable chapter titles, visible findings and reviewed repairs (2026-10-18)

- Final Linux/Python 3.12 regression: **625 passed, 1 Windows Credential Locker case skipped**. The final chapter/repair/HTTP-boundary/audit selection also passed all 50 checks. TypeScript, the ten Unicode helper checks and the production build passed. Current-commit Windows/macOS source and frozen-runtime results are linked on the PR.
- The focused chapter-edit, repair-engine and independent audit suites passed **48 checks** on Linux/Python 3.12. Source repair checks use temporary synthetic checkouts; they do not patch the development worktree. Full-suite and current-commit platform results are recorded separately below when complete.
- Independent combined-edit checks save a CRLF/non-BMP chapter page at source offset 32000 together with its title, retain the unseen tail and next chapter, and verify exact immutable history/original bytes, chunk coverage and a stale-base 409. A second chain removes a selected title solicitation and a separate author-note span while preserving the chapter number, story, quoted dialogue containing the same phrase and next chapter.
- The chapter browser regression passed **10 groups** against the production frontend and local API, with no JavaScript page errors. It covers shared title/body saving, dirty drafts after failures, title-only changes, title-only batch cleaning, visible deletion, 22-chapter dialog pagination buttons, 390/800 px layouts, dark appearance, exact hidden-character marks and locating a diagnostic beyond the first 20000-character page. A focus race found during that test was fixed by waiting until the editor and returned page address the same source range.
- Appearance/navigation browser checks passed **10 groups**, with no JavaScript page errors: light/dark/system switching, refresh persistence, real same-origin second-window synchronization, model-entry consolidation, retained backups, quick-repair entry and layouts at 390/800/1440 px. Contrast probes cover twelve pages/dialogs; visible enabled text passed the 4.45:1 probe threshold. Those probes are targeted browser checks, not a complete accessibility certification.
- Quick-repair's twelve real-browser/HTTP groups use a temporary synthetic source root: actual file application, checks, rollback, restart recovery, automatic restoration after syntax failure, concurrent-file SHA rejection, input preservation, clipboard fallback, packaged-mode cache rebuild/export and narrow/dark presentation. No running project source was patched during browser tests, and no LLM was called.
- Three final browser checks verify activation after repair: a frontend-only fixture patch runs the actual TypeScript/Vite build, exposes “刷新界面” even without a backend restart requirement, and performs a real reload. Packaged-mode index repair exposes “刷新内容” and reloads the current view. Neither action reloads automatically or discards another page's unsaved draft without the existing unload protection.
- HTTP-boundary regressions verify a synthetic unexpected failure returns a generic 500 while recording its exception type, frame locations, route template and timing; its private exception text, query/header values never enter the response or generated diagnostic. A pending repair worker retains the workspace lease until it exits after bounded shutdown.
- Independent interrupted-repair simulation initially reproduced a recovery defect: after only the first of two files was written, restart exposed a rollback action that rejected the still-original second file. Recovery now accepts each verified old/new hash, validates all backups before restoring, and preserves any unknown subsequent edit. The same simulation passed after the fix, restoring both exact originals and retaining a visible recent-operation record.
- Repair-engine tests cover actual CRLF byte replacement, diff-only preview, Python syntax failures without executing patched code, multi-file recovery, stale preview/apply/rollback, path/symlink restrictions, secret and home-path redaction, two-hour session expiry, explicit cross-session plan rebinding and packaged-mode boundaries. A temporary frontend checkout also runs the real installed TypeScript/Vite tools and rebuilds after rollback. These checks establish local source/build behavior, not the semantic correctness of arbitrary model patches; no commercial model was called.
- The source-launch smoke now also generates a diagnostic prompt, rebuilds a real document's chapter cache and verifies identical text/version afterward. These same operations are included in frozen Windows/macOS CI. The private v8 field/order round trip and four exact-commit InkOctoBot preservation comparisons passed again; originals remain excluded from git.

## Fast chapter workbench and guided cleaning (2026-10-18)

- Final local regression: **575 passed, 1 Windows Credential Locker case skipped** on Linux/Python 3.12. TypeScript checking and the production build passed. The intentional duplicate-ZIP-member fixture emits one warning. Native platform results belong to the PR's current-commit Actions checks.
- Opening the text workspace now reads a cached version summary, a chapter directory and one body page. It does not load immutable full-book snapshots, analysis history or all findings. Chapter mutations return compact summaries; unchanged chapters reuse their diagnostics. LangGraph and other workspace services initialize when needed. Regression checks forbid full-book reads/scans on the summary, directory and chapter-page paths and verify startup/shutdown without importing LangGraph.
- A same-machine Linux/Python 3.12 comparison against commit `fa5d5c6` used the same synthetic 200-chapter document (398,704 Unicode codepoints; 1,192,524 UTF-8 bytes). Directory reads improved from 261.3 to 1.38 ms, chapter reads from 26.3 to 1.05 ms and one-chapter saves from 363.9 to 85.6 ms. Building the persisted read models increased initial upload from 449 to 539 ms. These are local measurements, not timing guarantees for Windows/macOS or arbitrary books.
- Real Chromium with the production UI and local API passed 13 chapter-flow checks using a separate 1,200-chapter, 984,787-codepoint synthetic document: first editable body at 498 ms, input at 31 ms and save/render at 353 ms. The directory rendered at most 30 rows. Checks covered direct editing, heading/body separation, five missing items (12–16), filling chapter 14, actual marked snippets grouped per chapter, select-all, add/rename/delete, unsaved-edit guards and draft retention after a 409. Widths from 390 to 1,440 px had no horizontal overflow or clipped buttons. These measurements establish Linux browser/API behavior, not native desktop interaction.
- An independent 200-chapter browser run reached an editable body in 947 ms and completed a save in 172 ms (92 ms HTTP save, 2,278-byte response). It also checked fixed per-work upload targets, appending chapter 201 and final-step TXT export containing both the edit and appended chapter. Neither large-book run eagerly fetched all findings or full version snapshots; neither reported JavaScript errors.
- Ten document-flow checks and nine material-library checks cover fixed-target upload, the ordered source/structure/body tabs, lazy history, final-only export, dirty-state protection, DB/CSV/XLSX selection, meaningful related materials and narrow layouts. Six editor checks cover exact CRLF/Unicode offsets, undo, finding navigation, synthetic composition events and 390/640/900 px widths. Synthetic composition events do not establish behavior with a real operating-system IME.
- Four additional fragment-selection browser checks use two independently marked author-note spans in one chapter. After selecting all and retaining one span, both group checkboxes become partially selected, the count remains one chapter, the POST includes exactly one proposal ID and only that span is removed. The retained span, surrounding story and immutable old version remain exact.
- Independent save audits cover CRLF, CR, LF, non-BMP characters and short pages, checking exact unchanged tails, contiguous chapter/chunk coverage, correct summary lengths and unchanged original bytes. New regressions also protect against unrecognized titles silently moving a chapter into another body, duplicate gap counts and blank lines in final TXT. Original uploads and immutable historical versions remain preserved.
- A supplementary long-page harness initially returned a 404 and its temporary document could not subsequently be located. That run lacked live database identity traces, so its cause is unresolved and it is excluded from acceptance evidence. Two fresh runs with absolute data paths, unique ports, live SQL checks and awaited shutdown passed third-page saves while retaining the next chapter and old version. Independent post-exit SQL confirmed the saved documents/versions were still present; no application data-loss defect was reproduced in those instrumented runs.
- The 18-check analysis/review browser regression passed again after lazy initialization, including actual LangGraph execution against a synthetic local HTTP provider, independent approvals, edits, history, project backup/restore and token usage. Its three provider calls are protocol fixtures, not real LLM output. The source-launch smoke also passed health, frontend, SQLite, all three library export formats and the asynchronous TXT upload pipeline.

## Complete library exchange and direct text workflow (2026-10-18)

- Local Linux / Python 3.12 regression: **511 passed, 1 skipped**. The skipped case requires actual Windows Credential Locker. New HTTP integration checks exercise all three library formats, complete-set and version diagnostics, import-token separation, transactional relationship preservation, asynchronous upload progress, replacement/continuation, stale edits and work-deletion races.
- The actual private v8 triplet again retained all CSV fields and row order: 536 works, 1,213 characters, 138 cliches and 1,155 work/cliche relations. DB, CSV ZIP and XLSX export/import into fresh stores retained every original v8 value. Reimport skipped 1,887 rows without changing them. Private inputs and generated databases are not committed.
- A real local API and production Chromium frontend passed **14 text-workflow checks**: non-destructive TXT intake including NUL/control marks, automatic headings, cross-chapter findings/select-all, direct dirty editing and saving, explicit cleaning, exact saved TXT export, update/append and active source history, real EPUB conversion, and installed MinerU native-text PDF intake. At an artificial 450 ms uncached chapter delay, reader geometry moved **0 px** and retained the prior read-only body until the next body arrived. A 50,000-character chapter stayed fully editable without redundant paging; a 240,000-character Unicode chapter's page edit retained the unseen tail. Eight widths from 390 to 1,440 px had no page overflow or toolbar overlap. No LLM calls or browser JavaScript errors occurred in this flow.
- Material-library Chromium layout checks cover 390, 540, 720, 800, 960, 1,100 and 1,440 px, including related-card meanings and the narrow detail overlay. Missing-file checklist, adding omitted files and format selection were checked with synthetic preview responses; actual format/commit behavior is covered separately by real HTTP tests.
- Four additional real-browser checks cover source records, volume planning and unsaved-volume protection. Ten widths from 390 to 1,600 px had no document/panel overflow or button overlap.
- The analysis/review production-browser regression passed **18 checks** with five narrative tabs and five setting-collection tabs. Independent approvals, manual edits, prompt/paste, history, four-section TXT interchange, live project backup/restore and usage accounting still pass. Its three model requests use an explicitly synthetic local HTTP provider, not an actual LLM.
- Real MinerU 4.0.12 `flash/txt` converted a generated 12-page Chinese PDF both through ConversionStore and through the new upload pipeline. All twelve page markers, Chinese headings, complete source coverage and original PDF bytes remained present; automatic parsing produced twelve chapters plus a preface. This is native-text PDF extraction, **not OCR**. The reproducible optional check is `python scripts/verify_mineru.py`.
- The real source-launch smoke exercises all DB/CSV/XLSX exports and TXT upload/marking, and now runs those operations in frozen Windows/macOS CI too. Frontend source fingerprints force a rebuild after source changes; missing dependencies or build failures stop startup before an old interface can launch. Environment tests cover native command paths; local checks do not establish interactive Windows/macOS window behavior.
- Explicit SQLite closing, bounded shutdown, late-worker suppression and workspace ownership through ingestion/conversion workers are covered by regressions. Deterministic cancellation checks cover conversion reads, queued starts and a document commit preceding a cancelled linking attempt: a saved version wins cancellation and remains visible. The InkOctoBot exact-commit preservation comparison still passes all four rule-only cases. The standalone frontend helper checks ten CRLF/Unicode editing boundaries. Real system IME input, scanned-PDF OCR and commercial-model output quality remain separate real-machine acceptance items.

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
