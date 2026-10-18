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
| Models | `GET/POST /models`; `PUT/DELETE /models/{id}`. `api_key` is input only. Connection, credential and explicit outbound consent contracts are described below. |
| Usage | `GET /usage?run_id=` returns `{items,summary}`. Counts have `actual`, `estimated` or `unknown` sources; costs require explicit prices/currency. |
| Skills | `GET/POST /skills`, `POST /skills/upload` multipart `file`, `GET /skills/{id}/versions/{version}`. JSON, SKILL.md or declarative ZIP; uploaded code is not executable. |
| Workflows | `GET/POST /workflows`, `GET /workflows/{id}/versions/{version}`. Definitions are immutable. `/layout` GET/PUT separately manages `{layout,expected_revision}`. |
| Analysis | `GET/POST /jobs`; POST `{document_id,version_id,profile_id,workflow_id,workflow_version,start:true,remote_consent:false,expected_model_fingerprint?:string}`. Remote creation requires `remote_consent:true` plus the selected public profile's `target_fingerprint` as `expected_model_fingerprint`. Missing confirmation is 428; a changed endpoint/model is 409 with no job or model request. `GET /jobs/{id}` returns real progress, chunks, coverage, evidence, errors and configuration snapshots. |
| Control | `POST /jobs/{id}/cancel`, `/start` or `/resume`; remote start/resume bodies must explicitly include `{remote_consent:true}`. Resume skips completed chunks and uses fixed versions. App restart never sends requests automatically. |
| Review | `PUT /jobs/{id}/results` with `{results,expected_revision}`; `POST /jobs/{id}/approve` with `{expected_revision}`. Approval commits reviewed candidates transactionally and idempotently. |
| Projects | `GET /projects/export` is the full restore-capable ZIP. `GET /projects/tables/export` contains a CSV for every business table. `POST /projects/restore` multipart `file` verifies the ZIP and creates an independent copy, returning `data_dir`. |

## Model configuration and credentials

Profiles include `id,name,provider,connection_mode,base_url,model,context_window,max_output_tokens,max_tokens_parameter,timeout_seconds,credential_mode,key_env,input_price_per_million,output_price_per_million,currency`. `provider` is `ollama` or `openai_compatible`; `connection_mode` is `local` or `remote`:

- `local`: URL hostname must be `localhost` or a literal loopback address; HTTP is allowed for the local inference server.
- `remote`: URL must use HTTPS. A connection test, discovery request or remote analysis execution requires explicit consent for that action.

`credential_mode` is `session`, `vault` or `environment`. Input `api_key` is never returned. Session keys exist only in backend memory; environment mode stores only `key_env`, never its value. Vault mode uses an approved operating-system keyring backend, fails closed when unavailable and does not fall back to plaintext. Vault entries are scoped to the workspace path, so a restored or moved project requires new credential configuration. Profiles return `has_session_key,has_vault_key,key_available,credential_status` (`available|missing|vault_unavailable`) rather than a key.

| Endpoint | Request and behavior |
|---|---|
| `GET /models/security` | Returns `{vault_available,vault_backend,vault_reason,credential_modes}`. No credentials or automatic network lookup. |
| `POST /models/test-draft` | Body contains the current profile draft, optionally `api_key` and `use_saved_key:<profile_id>`; remote requests additionally require `remote_consent:true`. Sends a fixed test message, records actual usage, returns `{ok,model,message,usage,cost,call_id,duration_ms}`. Does not save the draft or send a document. |
| `POST /models/discover` | Same draft and consent contract; performs an explicit provider model-list request, returns `{models:[{id}]}`. No discovery is performed by loading the settings page. |
| `POST /models/{id}/test` | Tests a saved profile; remote requests require `{remote_consent:true}`. |
| `DELETE /models/{id}/credentials` | Removes stored session/vault credentials and the profile's environment-variable reference. Does not change the operating-system environment variable itself. |

Keys are excluded from profile JSON, model snapshots, business-table CSV and full project backups. Backups do retain non-secret configuration metadata and the user's texts/results. Switching to a remote endpoint does not grant consent to future requests; create/start/resume and explicit test/discovery actions each enforce the request's consent.

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

Default Skill result: `{summary:string,characters:[{name,description,evidence}],events:[{name,description,evidence}],cliches:[{name,description,evidence}]}`. Each evidence is a nonempty exact contiguous quote inside its chunk. The service adds absolute `start,end,chunk_id`. Invalid evidence fails the chunk instead of silently becoming a material record.

Run states are `queued,running,paused,failed,cancelling,cancelled,needs_review,approving,approved`; `progress` is completed chunks divided by total chunks (0–1). Cancelling takes effect at the current request boundary and does not imply a provider did not charge the in-flight request. Manual review is revision protected and cannot be overwritten by resuming that job.
