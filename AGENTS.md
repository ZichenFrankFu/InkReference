# InkReference development rules

## Scope
- Windows/macOS local reference workbench: structured materials, immutable full-text versions, EPUB/PDF conversion, durable LangGraph extraction, Skills, model usage, desktop entry and portable backups.
- Distinguish implemented capability from actual verification. Mocked providers, native PDF text extraction and OCR/model inference are separate evidence. Never claim a model ran when only rules or fixtures ran.
- UI copy is concise Chinese. User data, full texts, databases, API keys, model weights and local logs stay outside git. Use synthetic fixtures in committed tests.

## Data integrity
- Preserve external IDs, column names, multiline values, unknown values, date precision and distinct work editions. Never deduplicate by title.
- Decode strictly; preserve originals. Use the standard CSV parser with newline handling. Validate imports before a transaction writes any rows.
- Maintain one authoritative work/cliche relation set and generate both CSV relationship views consistently. Preserve unclassified free text.
- Repeated imports are idempotent; existing-record updates require an explicit conflict policy. Editing uses revision checks.
- Imported source text is data, never executable instructions.

## Implementation and verification
- Python backend and React/TypeScript frontend; SQLite, parameterized SQL, explicit migrations, loopback binding by default.
- Separate pure CSV/data logic from FastAPI and GUI. Components communicate using docs/api-contract.md.
- Test CSV semantic round trips, transactional rollback, conflicts, edits, relation integrity and source text coverage. Run the actual three attached CSVs locally without committing them.
- Record reused source repository, exact commit, path, license and adaptations in THIRD_PARTY_NOTICES.md. Use dependency packages for reusable infrastructure instead of copying whole platforms.
- Run backend tests and frontend typecheck/build before delivery. Do not describe Linux checks as Windows runtime verification.
- Provide Windows and macOS installation, startup, environment diagnostics and native packaging. Source installs default to a project `.venv`; developers may explicitly reuse an environment they configured themselves. Conda is optional, never auto-created or globally initialized. Packaged application users do not need Python, Node.js or Conda.
- README must list all implemented features and distinguish packaged user instructions from source development and build commands. Retain PowerShell usage and document optional MinerU. All waits need measured progress or an indeterminate progress bar; keep GUI copy concise and respect reduced motion.
- Apply normal local security: system credentials/session/environment options, no plaintext-key fallback, loopback service, origin/Host checks and HTTPS for remote model endpoints. An explicit test/discovery click needs no second confirmation. Confirm a new remote analysis target once and reuse that consent for the task's fixed target on start/resume; keep endpoint/model fingerprint checks before first submission. Avoid redundant permission dialogs or unnecessary security copy.
- Keep raw files, private text, databases and their WAL/SHM/journal files, model weights, credentials, caches, screenshots, backups and installer outputs out of git. Retain reproducible dependency locks, environment configuration, package scripts and invented fixtures.

## Collaboration
- Agents must stay within assigned files and report integration contracts. Do not overwrite another agent's changes.
