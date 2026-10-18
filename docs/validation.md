# Batch 01 verification

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

Full export is a table data export, not a project restore format. Use compatible export for CSV reimport. Text processing is an offline reusable module in Batch 01; the GUI does not yet upload or analyze full novels. LangGraph extraction, uploaded Skills, model connections, token/cost accounting and desktop installers remain later work.

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
