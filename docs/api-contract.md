# InkReference API contract

All JSON endpoints are under `/api`; errors use `{detail: string | object}`. Tables are `works`, `characters`, `cliches`. All CSV values remain strings.

## Local HTTP boundary

The launcher accepts loopback interfaces only. Every request must use a loopback Host; API reads and writes reject foreign or `null` Origin values and browser `Sec-Fetch-Site: cross-site` requests. Frontend requests use the same origin by default. An explicitly configured loopback `INKREFERENCE_DEV_ORIGIN` is the development exception. API and HTML responses use `Cache-Control: no-store`; the production page has a self-only Content Security Policy, including `connect-src`, and no external scripts or telemetry.

These controls protect the local browser boundary; the application is not a network multi-user service. Workspace databases and downloaded backups contain source text and results and are not encrypted archives.

## Common types
- `Record`: `{id: string, values: Record<string,string>, revision: number}`.
- `TableMeta`: `{key: string, label: string, id_field: string, columns: string[], count: number}`.
- `GET /api/meta`: `{tables: TableMeta[], version: string}`.
- `GET /api/stats`: `{works: number, characters: number, cliches: number, work_cliches: number}`.
- `GET /api/health`: `{status: "ok", version: string}`.

## Browse/edit
- `GET /api/records/{table}?q=&offset=0&limit=50&work_id=&cliche_id=` → `{items: Record[],total:number,offset:number,limit:number}`.
- `GET /api/records/{table}/{id}` → `Record`.
- `GET /api/records/{table}/{id}/relations` → `{works: Record[],characters:Record[],cliches:Record[]}`.
- `POST /api/records/{table}` body `{values: Record<string,string>}` → Record, 201.
- `PATCH /api/records/{table}/{id}` body `{values: Record<string,string>,expected_revision:number}` → Record. Immutable IDs. 409 on revision conflict.
- `DELETE /api/records/{table}/{id}?expected_revision=N&cascade=false` → `{deleted:number}`; 409 if related records block deletion. `cascade=true` is only sent after a GUI confirmation describing related records.

## Import/export
- `POST /api/import/preview`, multipart field `files` (multiple CSVs) → `{batch_id:string, tables:[{table:string, filename:string, rows:number,new:number,existing:number}],errors:string[],warnings:string[],stats:{works:number,characters:number,cliches:number,work_cliches:number}}`. Validation errors remain reviewable in this response, and commit is disabled if errors are nonempty.
- `POST /api/import/commit` body `{batch_id:string,policy:"skip"|"update"|"error"}` → `{imported:number,updated:number,skipped:number,stats:{...}}`. One atomic transaction, rejects a changed database snapshot with 409. Preview tokens expire after two hours or a server restart (404); originals are private app data outside git. File limits: 32 files, 32 MB per file, 128 MB total.
- `GET /api/export?mode=compatible|full` → ZIP attachment. Compatible contains v8's three named CSVs; full contains `works`, `characters`, `cliches` with `_record_id`/`_revision`, the `work_cliches` relation table and `manifest.json`. For workbench-wide business tables, use `/api/studio/projects/tables/export` instead. Neither CSV ZIP is a restore-capable project backup.
- `GET /api/export/{table}` → UTF-8 BOM CSV attachment.

Frontend use relative `/api`, native file input and downloadable response blobs. No real user data is bundled into the app; show an actionable empty state with import button. Backend module `inkreference.api:app`, core `inkreference.database` and `inkreference.csvio`.

## Reference workbench

All endpoints below use `/api/studio`. Lists return `{items:[...]}`. Timestamps are UTC; source spans use Unicode codepoints, never UTF-16, bytes or tokens. `/openapi.json` describes request schemas. CDN-based Swagger/ReDoc pages are disabled so this local application does not load external documentation scripts.

| Resource | Endpoints and behavior |
|---|---|
| Full texts | `GET/POST /documents`; POST multipart `file,title,encoding,work_id`. TXT/MD/SRT/DOCX/EPUB, 50 MiB. Originals remain immutable. |
| Versions | `GET /documents/{id}`, `GET /documents/{id}/versions/{vid}?include_text=false`; `POST /documents/{id}/versions` with `base_version_id,options,manual_text?`, 409 on a stale base. |
| Reading | Version suffix `/text?start=0&limit=20000&kind=cleaned\|source` pages text; `/diff` returns the bounded diff; `/download` downloads TXT. `/documents/{id}/original/download` returns original bytes. |
| Models | `GET/POST /models`; `PUT/DELETE /models/{id}`. `api_key` is input only. Connection and credential contracts are described below. |
| Usage | `GET /usage?run_id=` returns `{items,summary}`. Counts have `actual`, `estimated` or `unknown` sources; costs require explicit prices/currency. |
| Skills | `GET/POST /skills`, `POST /skills/upload` multipart `file`, `GET /skills/{id}/versions/{version}`. JSON, SKILL.md or declarative ZIP; uploaded code is not executable. |
| Workflows | `GET/POST /workflows`, `GET /workflows/{id}/versions/{version}`. Definitions are immutable. `/layout` GET/PUT separately manages `{layout,expected_revision}`. |
| Analysis | `GET/POST /jobs`; POST `{document_id,version_id,profile_id,workflow_id,workflow_version,start:true,remote_consent:false,expected_model_fingerprint?:string}`. Remote creation requires `remote_consent:true` plus the selected public profile's `target_fingerprint` as `expected_model_fingerprint`. Missing confirmation is 428; a changed endpoint/model is 409 with no job or model request. `GET /jobs/{id}` returns real progress, chunks, coverage, evidence, errors and configuration snapshots. |
| Control | `POST /jobs/{id}/cancel`, `/start` or `/resume`. A remote task uses its already confirmed fixed target, so start/resume need no renewed consent body. Resume skips completed chunks and uses fixed versions. App restart never sends requests automatically. |
| Review | `PUT /jobs/{id}/results` with `{results,expected_revision}`; `POST /jobs/{id}/approve` with `{expected_revision}`. Approval commits reviewed candidates transactionally and idempotently. |
| Projects | `GET /projects/export` is the full restore-capable ZIP. `GET /projects/tables/export` contains a CSV for every business table. `POST /projects/restore` multipart `file` verifies the ZIP and creates an independent copy, returning `data_dir`. |

## Model configuration and credentials

Profiles include `id,name,provider,connection_mode,base_url,model,context_window,max_output_tokens,max_tokens_parameter,timeout_seconds,credential_mode,key_env,input_price_per_million,output_price_per_million,currency`. `provider` is `ollama` or `openai_compatible`; `connection_mode` is `local` or `remote`:

- `local`: URL hostname must be `localhost` or a literal loopback address; HTTP is allowed for the local inference server.
- `remote`: URL must use HTTPS. Clicking test/discovery executes that explicit user operation without a second confirmation. New analysis confirms the target once; an existing task keeps that fixed target on start/resume.

`credential_mode` is `session`, `vault` or `environment`. Input `api_key` is never returned. Session keys exist only in backend memory; environment mode stores only `key_env`, never its value. Vault mode uses an approved operating-system keyring backend, fails closed when unavailable and does not fall back to plaintext. Vault entries are scoped to the workspace path, so a restored or moved project requires new credential configuration. Profiles return `has_session_key,has_vault_key,key_available,credential_status` (`available|missing|vault_unavailable`) rather than a key.

| Endpoint | Request and behavior |
|---|---|
| `GET /models/security` | Returns `{vault_available,vault_backend,vault_reason,credential_modes}`. No credentials or automatic network lookup. |
| `POST /models/test-draft` | Body contains the current profile draft, optionally `api_key` and `use_saved_key:<profile_id>`; `remote_consent` is accepted for older clients but no second consent is required for this explicit action. Sends a fixed test message, records actual usage, returns `{ok,model,message,usage,cost,call_id,duration_ms}`. Does not save the draft or send a document. |
| `POST /models/discover` | Same draft contract; performs an explicit provider model-list request, returns `{models:[{id}]}`. No discovery is performed by loading the settings page. |
| `POST /models/{id}/test` | Tests a saved profile; no repeated consent is required for this explicit action. |
| `DELETE /models/{id}/credentials` | Removes stored session/vault credentials and the profile's environment-variable reference. Does not change the operating-system environment variable itself. |

Keys are excluded from profile JSON, model snapshots, business-table CSV and full project backups. Backups do retain non-secret configuration metadata and the user's texts/results. Credentials are bound to their service endpoint rather than silently reused after an endpoint change. New remote analysis requires target confirmation and the fingerprint check above; existing tasks retain the already confirmed immutable target. Explicit test/discovery requests do not require an extra confirmation dialog.

## EPUB and MinerU PDF conversion

Conversion endpoints also use `/api/studio`:

| Endpoint | Request and behavior |
|---|---|
| `GET /conversions` | Returns `{items:[Conversion]}` with metadata, output availability and progress; does not load original BLOBs. |
| `POST /conversions` | Multipart `file` (EPUB or PDF, up to 50 MiB) and `options` JSON string; persists the original then starts a background conversion. Options: `{tier:"flash"\|"standard",ocr_mode:"txt"\|"ocr",title?:string,work_id?:string}`. OCR requires `standard`. `work_id`, when present, must exist. |
| `GET /conversions/{id}` | Metadata: `id,filename,kind,original_sha256,options,status,stage,progress,completed,total,error,attempt,created_at,updated_at,outputs,imported_document_id`. `progress` can be `null` when the backend cannot measure a stage. |
| `POST /conversions/{id}/start`, `/cancel`, `/retry` | Explicit task controls. Cancellation terminates the MinerU process tree. Restarted in-flight conversions become interrupted and wait for user action. |
| `GET /conversions/{id}/output?format=markdown\|text` | Returns `{text,format}` after successful conversion. |
| `GET /conversions/{id}/download?format=md\|txt` | Downloads a UTF-8 BOM attachment. |
| `GET /conversions/{id}/original/download` | Downloads the immutable original file. |
| `POST /conversions/{id}/import` | Imports the finished plain text into the document library, returning the document. An idempotency key prevents duplicate documents if the process exits around the import commit. |

EPUB follows validated spine order and converts HTML with markdownify. Plain text is rendered from markdown-it-py tokens. PDF uses a trusted, separately configurable Python process with the MinerU 4 SDK; uploaded options cannot choose an executable. Default `flash/txt` reads an existing text layer; `standard/ocr` requires the corresponding MinerU models. Markdown uses full-block rendering to retain short source lines that MinerU may classify as headers.

Legacy 1.0 Skill result: `{summary:string,characters:[{name,description,evidence}],events:[{name,description,evidence}],cliches:[{name,description,evidence}]}`. The HTTP 1.1 default adds the rich reference sections described below. Each evidence is a nonempty exact contiguous quote inside its chunk. The service adds absolute `start,end,chunk_id`. Invalid evidence fails the chunk instead of silently becoming a material record.

Run states are `queued,running,paused,failed,cancelling,cancelled,needs_review,approving,approved`; `progress` is completed chunks divided by total chunks (0–1). Cancelling takes effect at the current request boundary and does not imply a provider did not charge the in-flight request. Manual review is revision protected and cannot be overwritten by resuming that job.

## Reference database chapter workspace

The routes in this section are relative to `/api/studio`. Document metadata includes `source_characters` (initial decoded upload length) and `current_characters` (latest text version length); append/edit/collection preparation updates the latter. List/detail metadata queries do not fetch original BLOBs. A document version is immutable. Every chapter mutation supplies its current `base_version_id`; a stale base returns 409 and writes neither a new version nor an append source. Chapter IDs identify positions **within the addressed version**, independently of parsed chapter numbers. Duplicate numbered chapters remain separate records.

| Endpoint | Request and response |
|---|---|
| `GET /documents/{id}/versions/{vid}/chapters` | Returns `{document_id,version_id,pattern,heading_formats,candidates,gaps,options,author_keyword_defaults,chapters}`. Chapter metadata includes `id,index,number,title,raw_marker,kind,pattern,start,end,body_start,char_count,issues,proposal_count,proposal_ids,enabled_proposal_ids`. No chapter body is included. |
| `GET /documents/{id}/versions/{vid}/chapters/{cid}?start=0&limit=20000` | Returns metadata (also under `chapter`), `text,start,end,total,body_offset,body_text,proposals,diagnostics,offset_unit`. `limit` is 1–100000. `body_text` is available through 200000 codepoints; longer bodies remain readable through pages. |
| `POST /documents/{id}/append` | Multipart `base_version_id,file,encoding` (`auto` by default). Strictly parses TXT/MD/SRT/DOCX/EPUB and appends after the current edited text. Returns a new version. Each upload retains its own immutable bytes, hash and decoded source; it does not replace the initial original. |
| `GET /documents/{id}/sources` | `{items:[{id,filename,format,encoding,original_sha256,original_bytes,version_id,created_at}]}`. Initial original uses ID `original`; later IDs identify append uploads. |
| `GET /documents/{id}/sources/{source_id}/download` | Exact original bytes for that source, after SHA-256 verification. |
| `POST /documents/{id}/chapters/edit` | `{base_version_id,chapter_id,body_text}` → new version. Edits the body, preserving its heading and every exterior source span. Heading/next-chapter line boundaries are retained. |
| `POST /documents/{id}/chapters/rename` | `{base_version_id,chapter_id,title}` → new version; retains a numbered heading's prefix. |
| `POST /documents/{id}/chapters/insert` | `{base_version_id,after_chapter_id:null|string,heading,body_text}` → new version. `null` inserts at the beginning; use a heading matching the selected formats. |
| `POST /documents/{id}/chapters/delete` | `{base_version_id,chapter_ids:[string]}` → new version without those exact chapter spans. |
| `POST /documents/{id}/chapters/restore` | `{base_version_id,target_version_id}` → a new version containing the selected historical text/settings. Existing history remains immutable. |
| `POST /documents/{id}/chapters/resegment` | `{base_version_id,heading_formats:[string],custom_formats:[{name,format?,regex?,enabled?}]}` → new version. Empty `heading_formats` auto-detects; an explicit list uses only those named formats. Literal templates use one `N` placeholder, e.g. `第N章` or `卷N`; advanced regex uses captures for chapter number and title. Custom heading regex has a two-second scan timeout. Text is unchanged. |
| `POST /documents/{id}/chapters/settings` | `{base_version_id,author_keywords?:[string],garble_rules?:[{name,pattern,literal?:boolean,enabled?:boolean}]}` → new version, retaining the text. An empty keyword array deliberately disables keyword matching. `literal:true` matches exact watermark text; otherwise `pattern` is a regular expression, with a 250 ms per-chapter scan timeout. Invalid/slow rules return 422 before committing. |
| `POST /documents/{id}/chapters/clean` | `{base_version_id,selections:[{chapter_id,proposal_ids:[string]}]}` → new version containing only the selected removals. The server regenerates and validates candidate IDs against that version. Unknown, changed or non-removable IDs return 409. Unselected ranges remain exact. |

All ranges are half-open Unicode **codepoint** ranges `[start,end)`. The chapter index's `start,end,body_start` are absolute within the version text. A chapter-page response's `start,end,total` and all proposal/diagnostic offsets are local to that chapter; `body_offset` is the local start of the body. Pagination never makes a proposal's offsets page-relative. JavaScript must use codepoints (`Array.from(text)`) rather than UTF-16 offsets. The body begins immediately after the heading and can include its line break.

A proposal is `{id,start,end,text,text_truncated,kind,label,reason,enabled,removable}`. `text` is a preview capped at 2000 codepoints; the full range remains readable through chapter pages. `enabled` is the suggested review selection, **not** evidence of an applied deletion. Changing the selection in the GUI has no write effect until `/chapters/clean` succeeds. Author notes and scrape-noise candidates are removable; suspected replacement characters, private-use glyphs and mojibake remain diagnostic annotations with `removable:false`. Missing source character information is not repaired by deleting a placeholder. The single chapter reader can therefore show proposed removals in red and let a user retain each span, without a second original/cleaned text column.

Issue kinds are `author_note,missing_glyph,mojibake,noise,length,gap,duplicate`; entries include `label,count,severity`. Gap detection compares parsed numbers within each heading pattern. Outliers use relative chapter-body length. Neither missing numbers nor repeated headings cause source text to be discarded or renumbered. Prefaces, headings, whitespace and final partial fallback chunks remain covered exactly.

## Version-specific volume plans

| Endpoint | Request and response |
|---|---|
| `GET /documents/{id}/versions/{vid}/volumes` | `{document_id,version_id,revision,chapter_count,volumes,method}`. An unsaved plan has revision 0 and `method:"chapter_markers"`; saved plans use `method:"saved"`. |
| `PUT /documents/{id}/versions/{vid}/volumes` | `{expected_revision,volumes:[{title,start_chapter,end_chapter}]}` → updated plan. Every chapter must belong to exactly one continuous range, from 1 to the final chapter; 409 on stale revision. |
| `POST /documents/{id}/versions/{vid}/volumes/detect` | `{volumes,method:"chapter_markers"}` derived from source volume markers; does not save the suggestion. |
| `GET /documents/{id}/versions/{vid}/volumes/prompt` | `{prompt,chapter_count}` for user-directed copying to another model. Includes the complete chapter listing. |
| `POST /documents/{id}/versions/{vid}/volumes/paste` | `{content}` → `{volumes}`. Accepts JSON (optionally fenced), validates complete ranges and does not save. |
| `POST /documents/{id}/versions/{vid}/volumes/model` | `{profile_id,remote_consent?,expected_model_fingerprint?}` → `{volumes,method:"model",usage,call_id}`. Explicit model operation with the same remote target checks and token ledger; save separately through PUT. |

Volume range numbers are **one-based chapter-list ordinals**, including a preface/fallback segment if present. They are not a work's potentially duplicated or missing printed chapter numbers. Plans are keyed by `(document_id,version_id)` and have their own revision independent of the source version or analysis revision.

## Rich reference analysis and section review

`GET /documents/{id}/reference` returns `{document_id,revision,structure_type,sections,updated_at,commits}`. The initial analysis revision is 0. `structure_type` is `narrative` or `setting_collection`. Object sections are `plot,style,rhythm,opening`; array sections are `characters,settings,setting_features,raw_entries`. Plot uses a nested `epochs → periods → events` timeline; characters retain appearance/personality/experience facts by chapter; settings retain category/title/content and chronological updates. `raw_entries` have `{title,content}`. Ratio metrics and chapter information density are numbers in `[0,1]`.

| Endpoint | Request and behavior |
|---|---|
| `PUT /documents/{id}/reference` | `{structure_type,expected_revision}` → updated reference state. |
| `PUT /documents/{id}/reference/{section}` | `{data,expected_revision}` → validated, edited section and updated reference state. The revision belongs to this work-level reference state, not the job. |
| `GET /documents/{id}/reference/history` | `{items:[{revision,structure_type,updated_at}]}`. |
| `POST /documents/{id}/reference/restore` | `{target_revision,expected_revision}` → a new reference revision containing the historical sections. The contribution ledger remains retained for idempotency. |
| `POST /documents/{id}/reference/transform` | `{kind:"summarize"|"translate",profile_id?,payload,remote_consent?,expected_model_fingerprint?}`. `payload.prompt_only:true` returns `{kind,prompt,source}` without a provider call. Summary supports `granularity:"chapter"|"major_event"|"volume"|"book"` and optional `text`; translation supports `entry_index` or explicit `text`, and `target_language`. A model response returns `{kind,prompt,content,call_id,usage}` plus validated plot `data` for summary. These are review previews; section/entry saving is separate. |
| `POST /reference-collections` | `{title}` → empty collection document metadata with `format:"collection"`, empty immutable version, and a `setting_collection` reference state. Does not fabricate an uploaded TXT file. |
| `POST /documents/{id}/reference/prepare-source` | `{expected_revision}` → `{document,version,reference_revision}`. Creates/reuses a fixed literal text version assembled from raw entries, existing settings and characters for subsequent extraction; HTML entity strings, CRLF and Unicode characters are retained exactly. Repeating preparation with unchanged entries reuses that version. Upload/append originals remain unchanged. Empty collections cannot start extraction. |
| `GET /documents/{id}/reference/export` | Plain UTF-8 text attachment containing raw entries in the length-prefixed `InkReference entries v1` format, preserving multiline contents and marker-like text. |
| `POST /documents/{id}/reference/import` | `{text,expected_revision,mode?:"merge"|"replace",title?:string}` → updated reference state. Round-trips the exported format; ordinary text becomes one raw entry. |
| `GET /jobs/{run_id}/chunks/{chunk_id}/prompt` | `{run_id,chunk_id,text,prompt}` for that task's immutable source chunk and pinned Skill. Does not invoke a model. |
| `POST /jobs/{run_id}/chunks/{chunk_id}/paste` | `{content,expected_revision}` → updated job. Accepts fenced/plain JSON, validates it and any supplied exact source evidence; stores a human-reviewed candidate without a model call. `expected_revision` is the **job** revision (minimum 1). Committed chunks cannot be replaced. |
| `POST /jobs/{run_id}/chunks/{chunk_id}/start` | Starts only that source chunk through LangGraph. Other chunks retain their progress. Uses the task's fixed model and already confirmed target. |
| `POST /jobs/{run_id}/chunks/{chunk_id}/commit` | `{sections:[string],expected_revision}` → reference state. Approves selected available sections independently; `expected_revision` is the **work-level reference** revision (minimum 0). Repeating the same `(run_id,chunk_id,section)` contribution is idempotent. |

The HTTP default workflow/Skill is now `default@1.1.0` / `unified-reference@1.1.0`. A new job can use `{document_id,version_id,workflow_id:"default",workflow_version:"1.1.0",manual:true,start:false}` without a model profile. Manual jobs support prompt copying and response pasting; they never send model requests automatically. For provider jobs, `manual:false` (default) still requires a profile and the existing remote target check. The core Python runner's historical `1.0.0` default remains for backwards-compatible direct callers; explicit HTTP snapshots identify the actual version used.

The 1.1 output retains `summary,characters,events,cliches` and adds optional `reference:{plot,characters,settings,style,rhythm,opening,setting_features}`. The schema's authoritative declarations remain in `/skills/unified-reference/versions/1.1.0`; old `1.0.0` definitions remain immutable. Model-derived rich character/setting/event/feature facts need contiguous, nonempty original `evidence`. A native upstream-shaped human paste may instead contain `events,characters,settings,style` directly and is kept as an explicitly reviewed reference candidate; it does not silently create v8 material facts lacking evidence. Supplied evidence must still be exact. Core v8 material approval (`/jobs/{id}/approve`) and work-level section approval (`/chunks/{id}/commit`) remain separate operations.

Section commit records retain `source_version_id`. Character/setting merges preserve existing scalar edits and union chapter fact lists; style metrics aggregate by source character weight, with separately saved manual style/rhythm/opening overrides retained. Source text edits never change an existing job's fixed text, prompts, chunk ranges or model snapshot; create another task to analyze another source version.
