# InkReference API contract

All JSON endpoints are under `/api`; errors use `{detail: string | object}`. Tables are `works`, `characters`, `cliches`. All CSV values remain strings.

## Local HTTP boundary

The launcher accepts loopback interfaces only. Every request must use a loopback Host; API reads and writes reject foreign or `null` Origin values and browser `Sec-Fetch-Site: cross-site` requests. Frontend requests use the same origin by default. An explicitly configured loopback `INKREFERENCE_DEV_ORIGIN` is the development exception. API and HTML responses use `Cache-Control: no-store`; the production page has a self-only Content Security Policy, including `connect-src`, and no external scripts or telemetry.

These controls protect the local browser boundary; the application is not a network multi-user service. Workspace databases and downloaded backups contain source text and results and are not encrypted archives.

## Local diagnostics and reviewed repairs

Endpoints are relative to `/api/studio/repair`. These actions never call a model provider. The user copies a generated prompt into a model of their choice and pastes the returned JSON. Source mode can modify existing project source files; packaged mode previews and exports a plan without modifying the executable. Cache rebuilding is a separate local operation available in either mode when a document service is present.

| Endpoint | Request and response |
|---|---|
| `GET /capabilities` | `{mode:"source"\|"packaged",can_apply_source,can_rebuild_cache,source_root_label,protocol,topics,limits}`. No credential or model request. |
| `POST /prompt` | `{description,topic?,frontend_events?:[object],context?}` → `{session_id,prompt,diagnostics,source_files:[{path,sha256}],truncated,capabilities}`. Description is 1–6000 characters; optional context is at most 12000. Topics are `chapters,upload,models,library,workflow,app`. |
| `POST /preview` | `{reply,session_id?}` → `{preview_id,session_id,summary,files:[{path,diff,old_sha256,new_sha256,additions,deletions,unverified}],checks,can_apply,restart_required,expires_at}`. The optional current local session ID explicitly rebinds an exported plan to a fresh local diagnostic session; every path, original file hash and replacement is still checked. Preview writes no source. |
| `POST /apply` | `{preview_id}` → asynchronous operation metadata. Source mode only; rechecks all original hashes before saving. |
| `GET /operations` | `{operations:[Operation,...]}`. Up to twelve most recent operations, without source contents or file payloads, allowing recovery controls to survive a restart. |
| `GET /operations/{operation_id}` | `{operation_id,kind,status,stage,progress,checks,rollback_available,restart_required,frontend_rebuilt,...}`. `status` is `running\|succeeded\|failed\|rolled_back`; progress is 0–100. |
| `POST /rollback` | `{operation_id}` → asynchronous recovery. Verifies every backup hash first; each source file must still match either the saved old or new hash. Unrelated subsequent edits cause 409 before recovery writes. |
| `POST /export` | `{preview_id}` → JSON attachment `InkReference-repair.json`. A source checkout uses a fresh local diagnostic session before previewing an exported plan. |
| `POST /cache-rebuild` | `{document_id,version_id?:string\|null}` → asynchronous operation. Rebuilds derived chapter read models from the fixed immutable version without changing its text, history or original bytes. |

The model response is one JSON object (a single fenced JSON block is also accepted):

```json
{
  "protocol": "inkreference.repair.v1",
  "session_id": "SESSION_ID_FROM_PROMPT",
  "summary": "Correct the selected behavior",
  "files": [
    {
      "path": "frontend/src/App.tsx",
      "sha256": "64_LOWERCASE_HEXADECIMAL_CHARACTERS_FROM_PROMPT",
      "edits": [
        {"before": "exact unique original source", "after": "replacement source"}
      ]
    }
  ]
}
```

Each file provides either `edits` (1–100 nonempty, unique `before` matches) or the complete replacement `content`. The original hash is the SHA-256 of the complete original file bytes, including its line endings. A plan contains 1–12 files, each at most 1000000 UTF-8 bytes; the reply is at most 2000000 characters. Sessions and previews expire after two hours and are not retained across application restarts. Duplicate JSON fields, unsupported keys/commands, duplicate paths, non-UTF-8 source and stale hashes are rejected. Missing or ambiguous replacements and stale sessions return 409.

Allowed paths are existing `.py` files under `backend/inkreference`, `.ts/.tsx/.js/.jsx/.css/.json` files under `frontend/src`, and `.py/.ps1/.sh` files under `scripts`. Absolute/traversing paths, symlinks, hidden paths, user-data/build/dependency directories, dependency manifests and the repair engine itself are excluded. The engine does not execute model-supplied shell or installation commands. Python syntax and JSON structure are checked without importing patched code. Frontend edits use fixed local TypeScript and Vite commands rather than package lifecycle scripts, build into a temporary directory, then publish the new frontend. Shell and PowerShell source edits are saved but not executed. Checks establish syntax/build consistency, not correctness of arbitrary model changes.

Local diagnostics record request route/method/status/duration and exception types/stack locations; request bodies, headers, source texts and exception messages are not automatically collected. Generated prompts include redacted frontend errors, environment/dependency versions, Git metadata and bounded relevant project source excerpts. Secrets, URL credentials/query values and home-directory usernames are redacted. Successful requests stay in memory; a bounded recent-error record and repair manifests/backups stay in the private `quick-repairs` data directory. Source backups preserve exact original bytes and permit recovery after restart. New source must be reviewed before applying; Python/backend/script changes require restarting the application, while a successful frontend rebuild can be refreshed.

## Common types
- `Record`: `{id: string, values: Record<string,string>, revision: number}`.
- `TableMeta`: `{key: string, label: string, id_field: string, columns: string[], count: number}`.
- `GET /api/meta`: `{tables: TableMeta[], version: string, schema_version:6}`.
- `GET /api/stats`: `{works: number, characters: number, cliches: number, work_cliches: number}`.
- `GET /api/health`: `{status: "ok", version: string}`.

## Browse/edit
- `GET /api/records/{table}?q=&offset=0&limit=50&work_id=&cliche_id=&category=&seen=&role=&character_trait=&plot_mechanism=&has_text=&has_analysis=&sort_by=&sort_direction=asc` → `{items:Record[],total,offset,limit,facets:{categories,roles,works:[{id,title}],character_traits,plot_mechanisms},summary:{matches,...counts}}`. Filters run before pagination. `seen` accepts stored yes/no values (`是` / `否`), or `unknown` for any other value. `role` filters the character's exact `角色定位`; `character_trait` and `plot_mechanism` filter a cliche's exact classification values below, independently and together. `work_id` filters direct work associations; it does not select a work by title. `has_text` and `has_analysis` are booleans applicable to works only. An analysis exists only when its reference revision is greater than zero. `sort_by` is an ID or declared column; direction is `asc|desc`.
- `GET /api/records/{table}/{id}` → `Record`.
- `GET /api/records/{table}/{id}/relations` → `{works: RelatedRecord[],characters:RelatedRecord[],cliches:RelatedRecord[],edges:Edge[]}`. Each `RelatedRecord` adds `relationship` (first edge) and `relationships` (all meanings for that same record): `{id,type,label,description,direction,metadata,source}`. Direction is `outgoing|incoming`; `source` identifies `library_links`, legacy `character_work` / `work_cliches`, or `explicit`. Cards are deduplicated by target; the individual relationship meanings remain separate.
- `POST /api/records/{table}` body `{values: Record<string,string>}` → Record, 201.
- `PATCH /api/records/{table}/{id}` body `{values: Record<string,string>,expected_revision:number}` → Record. Immutable IDs. 409 on revision conflict.
- `DELETE /api/records/{table}/{id}?expected_revision=N&cascade=false` → `{deleted:number}`; 409 if related records block deletion. `cascade=true` is only sent after a GUI confirmation describing related records.

Only direct declared edges are shown. Sharing a work does not infer a new relationship; cliche-to-cliche edges are forbidden. Modern association JSON retains target IDs, structured relationship content and unknown object fields; `library_links` is its transactional query projection. Characters can belong to multiple works and do not inherit ownership from a single `work_id`. Work editions remain distinct by ID. The visible “看过” column maps to `用户是否完整体验过`; CRUD also accepts `看过` as its input alias. Removed legacy fields are preserved in the migration archive rather than shown as current editable columns.

Cliches have two independent string fields appended in schema 5: `人设塑造` is one of `强大,智谋,利他,共情,利己,执念,反差,宿命`; `剧情机制` is one of `信息反转,机械降神,顿悟突破,代价交换`. Each field accepts exactly one value or the empty string. Lists, combined labels and unknown values return 422 on input. The GUI formats the two selected values as separate tags such as `人设塑造·强大` and `剧情机制·信息反转`; these composite labels are not stored values. Schema-4 records and inputs without the fields receive empty strings, without guessing classifications. Record details show associations only in their associations tab; their JSON remains present in the API and portable files.

### Typed material associations

An explicit relation has `{id,source_table,source_id,target_table,target_id,relation_type,description,metadata,revision}`. Both table keys are among `works,characters,cliches`. `relation_type` contains the user-facing relationship meaning; arbitrary structured extensions are retained in `metadata`. Unknown input relation fields are folded into this object. The source and target must exist, cannot be the same record, and cannot both be cliches. Two different relation types may connect the same endpoints; duplicate endpoint/type tuples under different IDs are rejected with both IDs identified. Modern work/cliche associations come from the three main tables' declared JSON links; `work_cliches` is a derived compatibility projection. Additional explicit relations remain in their own table.

- `GET /api/relations` → `{items: Relation[]}`.
- `POST /api/relations` body `{values:{source_table,source_id,target_table,target_id,relation_type,description?,id?,metadata?}}` → Relation, 201; an omitted ID is generated.
- `PATCH /api/relations/{id}` body `{values:{...patch},expected_revision:number}` → Relation. IDs are immutable.
- `DELETE /api/relations/{id}?expected_revision=N` → `{deleted:1}`.

Revision checks and tombstones prevent stale edits and ID-reuse conflicts. Associated material deletion requires `cascade=true`; confirmed deletion removes references and typed links while retaining independent characters when a work is removed. A work with linked text remains protected until the text link is changed. SQLite endpoint triggers protect explicit polymorphic links; modern JSON references are validated and their query projection rebuilt within the material transaction.

## Complete material-library exchange

DB, CSV and XLSX carry the same catalog dataset. Format version 1 / exchange schema version 6 uses the three core tables `works,characters,cliches`; optional `material_relations`, `legacy_values`, `catalog_field_archive` and `character_contexts` tables are included only when they contain data. The app's internal SQLite migration version is also 6 but is a separately managed contract. Schema 3, 4 and 5 portable packages remain importable through migration; schema 4's two absent cliche classifications become empty strings. Original schema-specific columns and hashes are checked before the normalized current view is compared or written.

Modern core columns are exposed by `/api/meta` and `/api/library/formats`. Works use `作品ID`, characters use `人物ID`, cliches use a separate `clichéID`. Association columns hold JSON arrays of IDs or structured objects; object fields and relationship content are preserved. `library_links` and `work_cliches` are derived indexes/compatibility views and are not required extra files in a new package. Optional `material_relations` columns are `id,source_table,source_id,target_table,target_id,relation_type,description,metadata_json`; `legacy_values` columns are `table_key,record_id,values_json` and retain the visible pre-migration field snapshots, including legacy relationship fields rendered from their authoritative relation table. Modern edits do not rewrite that migration snapshot.

| Endpoint | Contract |
|---|---|
| `GET /api/library/formats` | `{default:"db",schema_version:6,tables,formats,legacy_csv,guidance}`. Each table declaration includes its exact columns, suggested filename and `required` flag; only the three core tables are always required. |
| `POST /api/library/import/preview` | Multipart `files`. Accepts one portable `.db` / `.sqlite` / `.sqlite3`, a complete CSV ZIP, the three raw modern CSVs together, a complete set of exported CSVs plus manifest, one `.xlsx`, or a complete legacy v8 triplet. Raw CSV classification uses exact headers rather than filenames. Returns `{batch_id,tables,errors,warnings,stats,format,schema_version,bundle_id,legacy,required_tables,guidance}`. Required entries identify `table,label,filename,present`; errors disable commit. |
| `POST /api/library/import/commit` | `{batch_id,policy:"skip"\|"update"\|"error"}` → `{imported,updated,skipped,relations_imported,relations_updated,relations_skipped,stats}`. One transaction covers every material, declared relationship and retained auxiliary row. Material revisions and relation snapshots must still match preview, otherwise 409. |
| `GET /api/library/export?format=db\|csv\|xlsx` | Defaults to DB. Downloads `InkReference-library.db`, `InkReference-library-csv.zip`, or `InkReference-library.xlsx`; all three contain the same core/actually-present auxiliary tables and manifest. |
| `GET /api/library/export/legacy` | A v8 CSV ZIP built from the original migration archive. If a modern record lacks its archived v8 fields, returns 422 instead of inventing or dropping unsupported data. This is an explicit legacy snapshot export, not a conversion of current edits to guessed v8 values. |

Raw modern CSV intake needs all three tables and no manifest. Both schema-4 and schema-5 raw headers are accepted, including empty tables; the legacy CSV API also accepts schema-4 headers. An exported bundle has a shared `bundle_id`, exact per-table columns, row counts and SHA-256 hashes. Its manifest-declared auxiliary files must also be present. DB stores `_manifest`; CSV ZIP stores `manifest.json`; XLSX stores a hidden `_manifest`. Exported CSV/XLSX rows append `_bundle_id` and `_schema_version`; deleting the manifest from such files is an error. Optional tables absent from the manifest are not required. All three core tables, including empty tables with headers, remain present.

XLSX adds hidden `_long_text` when needed to preserve strings exceeding Excel's UTF-16 cell limit, CR characters, XML-incompatible characters, XML escape-like text or reserved literal markers. Data cells are text, including formula-like strings; actual imported formulas/errors/date objects are rejected with sheet/cell locations. Numeric cells produce a warning when converted to text because lost leading zeros cannot be recovered.

CSV uses UTF-8 BOM with strict UTF-8/GB18030 import decoding. NUL control characters cause guidance to use DB/XLSX rather than emitting an unreadable CSV. Missing files/sheets, duplicate table selections, missing columns, batch mismatches, dangling target IDs and contradictory relationship declarations name the affected files and IDs. Declared objects on both sides are checked; conflicting relationship content is not silently resolved by picking one file. Edited manifest hashes/row counts produce review warnings. Portable imports preserve business values and external IDs; revisions remain local. Full text, analysis and history use the full project backup rather than catalog exchange.

Limits are 32 selected files and 128 MiB total compressed/raw input, 256 ZIP entries and 256 MiB expanded data, 200000 rows per table and 32 MiB per field. A DB is opened read-only with schema restrictions; only ordinary portable data tables are accepted, without triggers, views or virtual tables. Raw workspace SQLite files use project backup/restore. Preview tokens expire after two hours or restart (404); originals remain private application data outside git.

### Legacy CSV APIs

These endpoints remain for existing integrations; the GUI uses the complete exchange endpoints above. Legacy single-table import does not provide the new all-table guidance or typed association format.

- `POST /api/import/preview`, multipart field `files` (multiple CSVs) → `{batch_id:string, tables:[{table:string, filename:string, rows:number,new:number,existing:number}],errors:string[],warnings:string[],stats:{works:number,characters:number,cliches:number,work_cliches:number}}`. Validation errors remain reviewable in this response, and commit is disabled if errors are nonempty.
- `POST /api/import/commit` body `{batch_id:string,policy:"skip"|"update"|"error"}` → `{imported:number,updated:number,skipped:number,stats:{...}}`. One atomic transaction, rejects a changed database snapshot with 409. Preview tokens expire after two hours or a server restart (404); originals are private app data outside git. File limits: 32 files, 32 MB per file, 128 MB total.
- `GET /api/export?mode=compatible|full` → ZIP attachment. Compatible uses the historical three filenames with the current stored columns; it is not the explicit v8 migration-snapshot export above. The full variant contains `works`, `characters`, `cliches` with `_record_id`/`_revision`, `work_cliches`, `material_relations` (including revision) and a legacy `manifest.json`. This legacy full export is for inspection; its manifest differs from the new complete exchange format. For workbench-wide business tables, use `/api/studio/projects/tables/export` instead. Neither CSV ZIP is a restore-capable project backup.
- `GET /api/export/{table}` → UTF-8 BOM CSV attachment.

Frontend use relative `/api`, native file input and downloadable response blobs. No real user data is bundled into the app; show an actionable empty state with import button. Backend module `inkreference.api:app`; material modules are `inkreference.database`, `inkreference.library_exchange` and legacy `inkreference.csvio`.

## Reference workbench

### Catalog links and work-level reference views

These routes are relative to `/api/studio`. `works.id` is the catalog identity; a text's `doc_documents.work_id` is an explicit link rather than a title match. A work may have multiple linked texts. Rich analysis stays in `reference_analysis` and its contribution ledger instead of being copied into catalog CSV fields.

| Endpoint | Request and response |
|---|---|
| `GET /catalog/works?q=&offset=0&limit=100` | Paged choices `{items:[{id,title,revision}],total,offset,limit}`; limit 1–200. |
| `GET /documents/{id}/catalog` | `{document_id,work_id,work,missing_work,latest_version_id,completion}`. Completion, when present, contains its fixed `version_id,completed_at`. |
| `PUT /documents/{id}/catalog` | `{expected_work_id:string\|null,expected_version_id,work_id?:string\|null,new_work?:{title},complete?:boolean}`. Both expected fields are required. Select an existing work or create a new work, never both. `complete:true` requires a work and records the current cleaned version. Stale version or link returns 409. A null work without `new_work` detaches the link and clears completion. |
| `GET /catalog/works/{work_id}` | `{work_id,title,revision,documents,counts}`. Linked documents carry current text counts, latest/completed version IDs and compact analysis metadata/counts. Does not return their original bytes or full texts. |
| `GET /catalog/works/{work_id}/analysis?document_id=...&chapter=...` | `{work_id,document_id,latest_version_id,reference,projection,chapter}`. The document must explicitly link to this work, otherwise 409. `reference` retains revision, rich sections and source-version contribution ledger; `projection` contains plot, characters and settings for display. Detail metadata exposes `source_version_ids` and `stale` when any approved contribution refers to an older text version. |

An optional nonnegative `chapter` restricts facts with a recognized chapter anchor to that chapter or earlier; unknown anchors remain visible. Character appearance, personality, experiences and setting updates retain `{chapter,text}`. A character's own experiences take precedence even when the selected chapter filters them out; only an originally empty experiences list uses exact-name plot-subject matches as a derived fallback. The projection never modifies stored facts or infers a story date. `first_chapter` remains separate from in-story `time_marker/time_markers`, and approved contributions retain their source text version.

All endpoints below use `/api/studio`. Lists return `{items:[...]}`. Timestamps are UTC; source spans use Unicode codepoints, never UTF-16, bytes or tokens. `/openapi.json` describes request schemas. CDN-based Swagger/ReDoc pages are disabled so this local application does not load external documentation scripts.

| Resource | Endpoints and behavior |
|---|---|
| Full texts | `GET /documents` lists metadata. The GUI uploads through `/ingestions` below: TXT/PDF/EPUB, 50 MiB. Legacy `POST /documents` still accepts multipart `file,title,encoding,work_id` with TXT/MD/SRT/DOCX/EPUB for existing integrations. Originals remain immutable. |
| Versions | `GET /documents/{id}`, `GET /documents/{id}/versions/{vid}?summary=true` returns lightweight metadata/counts without chapter/chunk/diagnostic arrays. `chapter_count` counts actual immutable source segments; `placeholder_count` counts all missing chapter numbers. `issue_chapter_count` counts actual affected chapters once each plus missing chapters, without double-counting their following gap anchor. `include_text=false` retains span-only chapter/chunk metadata for compatibility; omit both flags for the full immutable snapshot. `POST /documents/{id}/versions` accepts `base_version_id,options,manual_text?`, 409 on a stale base. |
| Reading | Version suffix `/text?start=0&limit=20000&kind=cleaned\|source` pages text; `/diff` returns the bounded legacy diff. `/download?format=clean` downloads the final formatted TXT; `format=raw` (default) preserves the exact saved version text. Original/source-download APIs remain for compatibility; the GUI exposes final export after “完成清洗”. |
| Models | `GET/POST /models`; `PUT/DELETE /models/{id}`. `api_key` is input only. Connection and credential contracts are described below. |
| Usage | `GET /usage?run_id=` returns `{items,summary}`. Counts have `actual`, `estimated` or `unknown` sources; costs require explicit prices/currency. |
| Skills | `GET/POST /skills`, `POST /skills/upload` multipart `file`, `GET /skills/{id}/versions/{version}`. JSON, SKILL.md or declarative ZIP; uploaded code is not executable. |
| Workflows | `GET/POST /workflows`, `GET /workflows/{id}/versions/{version}`. Definitions are immutable. `/layout` GET/PUT separately manages `{layout,expected_revision}`. |
| Analysis | `GET/POST /jobs`; `GET /jobs?summary=true&document_id=...&version_id=...&limit=100` and `GET /jobs/{id}?summary=true` omit chunk/results payloads for progress polling. POST `{document_id,version_id,profile_id,workflow_id,workflow_version,start:true,remote_consent:false,expected_model_fingerprint?:string,expected_stage_fingerprints?:{stage:string}}`. Remote creation requires `remote_consent:true` plus the selected public profile's `target_fingerprint` as `expected_model_fingerprint`. Missing confirmation is 428; a changed endpoint/model is 409 with no job or model request. `GET /jobs/{id}` returns real progress, chunks, coverage, evidence, errors and configuration snapshots. |
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

The GUI presents one upload pipeline. Format-specific settings appear with the selected PDF/EPUB upload; conversion is not a separate navigation tab. Backend conversion resources remain available under `/api/studio` for the pipeline and existing integrations:

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

### Durable upload pipeline

The following endpoints are relative to `/api/studio`. The GUI accepts TXT, PDF and EPUB. TXT is strictly decoded; EPUB/PDF first pass through the existing conversion service to plain text, then use the same chapter parser and issue scan. Uploading does not apply diagnostic deletion proposals. The global upload creates a new work (`mode:new`); uploading for the selected existing work pins its target and offers `replace` (“更新正文”, replace all current text) or `append` (“补充章节”, add after the current ending), without a second destination selector. The work list shows only titles and character counts in ten-thousand-character units, rounded to one decimal place. Immutable sources remain; ordinary version history follows the bounded retention rules below.

| Endpoint | Request and response |
|---|---|
| `GET /ingestions` | `{items:Ingestion[]}`. Metadata only, without original BLOBs. |
| `POST /ingestions` | Multipart `file,title?,encoding?,work_id?,mode?,target_document_id?,base_version_id?,options?,author?,earliest_update_at?,book_type?`. `mode` is `new\|replace\|append`; replace/append require the current target document and base version. `options` is a JSON string, `{tier:"flash"\|"standard",ocr_mode:"txt"\|"ocr"}` for PDF only. New-book metadata is validated before starting; replace/append metadata edits use the separate guarded endpoint. Starts a durable worker and returns its task metadata, 201. |
| `GET /ingestions/{id}` | Task includes `status,stage,progress,completed,total,error,attempt,document_id,version_id,conversion_id,options`. `progress:null` indicates an indeterminate stage; PDF/EPUB conversion stages forward the actual conversion progress. |
| `POST /ingestions/{id}/cancel` | Cancels pending work and the associated conversion process. A document commit that already succeeded remains committed and completed. |
| `POST /ingestions/{id}/retry` | Explicitly retries a failed/cancelled/interrupted task using its immutable source and target. Idempotency checks reconcile a prior successful document commit before repeating work. |

An application restart marks running imports interrupted and requires an explicit retry; it does not submit a model request. Stages cover decoding/conversion, chapter parsing and issue scanning. A stale replace/append target returns 409 and does not overwrite a newer version. Originals and target configuration are immutable; source filename/hash/bytes remain provenance even though original download is absent from the GUI.

### Chapter operations

The routes in this section are relative to `/api/studio`. Document metadata includes `source_characters` (initial decoded upload length) and `current_characters` (latest text version length); append/edit/collection preparation updates the latter. List/detail metadata queries do not fetch original BLOBs. A document version is immutable. Every chapter mutation supplies its current `base_version_id`; a stale base returns 409 and writes neither a new version nor an append source. Chapter IDs identify positions **within the addressed version**, independently of parsed chapter numbers. Duplicate numbered chapters remain separate records until the user accepts a conservative duplicate-heading repair. The text workspace is named “文本清理”; progress occupies one fixed region below its title, and chapter-format/body-marking settings are collapsed by default.

| Endpoint | Request and response |
|---|---|
| `GET /documents/{id}/versions/{vid}/chapters` | Returns `{document_id,version_id,pattern,heading_formats,candidates,gaps,options,author_keyword_defaults,chapters}` and placeholder/count metadata. Chapters include `id,index,display_index,number,title,display_label,raw_marker,kind,pattern,start,end,body_start,char_count,issues,issue_count,proposal_count,proposal_ids,enabled_proposal_ids,first_body_issue_start`. `display_label` is `第X章 标题`; no chapter body is included. |
| `GET /documents/{id}/versions/{vid}/chapters/{cid}?start=0&limit=20000&body_only=true` | GUI reading mode returns only the body, with repeated empty lines collapsed: `text,editable_text,expected_text,source_offset_map,start,end,total,source_total,body_offset,body_total,editable_start,editable_end,next_start,body_only`, plus chapter metadata, proposals and diagnostics. Title marks are separately exposed as `title_source_start,title_source_end,title_proposals,heading_proposals`; `structured_issues` gives concrete structural reasons rather than artificial body spans. `expected_text` is the exact original slice for saving; mapping entries are chapter-local source codepoint offsets for each displayed codepoint boundary. `body_text` is null. `next_start` is the next source offset, not a display character count. |
| `GET /documents/{id}/versions/{vid}/chapters/{cid}?start=0&limit=20000` | Compatibility mode returns the full chapter span including its heading, with the previous `body_text,editable_text` fields. `limit` is 1–100000. Bodies above 200000 codepoints remain readable/editable through pages. |
| `GET /documents/{id}/versions/{vid}/findings?chapter_id=...&offset=0&limit=1000` | `{document_id,version_id,offset_unit,items,total,start,limit,next_start,issue_chapter_count}`. Optional chapter filter and proposal pagination. Items retain `chapter_id,chapter_index,chapter_title`; `total` counts individual proposals, while `issue_chapter_count` counts affected chapters once each. Used on demand by the batch-cleaning dialog, which displays actual matched text beside each selection. |
| `POST /documents/{id}/append` | Multipart `base_version_id,file,encoding` (`auto` by default). Strictly parses TXT/MD/SRT/DOCX/EPUB and appends after the current edited text. Returns a new version. Each upload retains its own immutable bytes, hash and decoded source; it does not replace the initial original. |
| `GET /documents/{id}/sources` | `{items:[{id,filename,format,encoding,original_sha256,original_bytes,version_id,created_at}]}`. Initial original uses ID `original`; later IDs identify subsequent uploads. Source `mode` is independent of its nullable version link; metadata comes from compact provenance rather than original BLOBs. |
| `GET /documents/{id}/sources/{source_id}/download` | Exact original bytes for that source, after SHA-256 verification. |
| `POST /documents/{id}/chapters/edit` | `{base_version_id,chapter_id,body_text,title?}` → new version. Edits the body and, when supplied, its pure title in one transaction while preserving the numbered heading prefix and exterior source spans. A virtual missing `chapter_id` materializes a new `第N章 title` at its pinned position; `title` is optional, single-line and at most 80 characters. The new heading must remain independently recognized; otherwise 422 is returned before committing either title or body. Previous versions retain the blank virtual item. |
| `POST /documents/{id}/chapters/edit-slice` | `{base_version_id,chapter_id,start,end,text,expected_text,title?}` → new version. Replaces only the addressed chapter-local codepoint range within the body and optionally changes the pure title, atomically. The original slice must equal `expected_text`; 409 on mismatch. The optional title obeys the same single-line/80-character/heading-recognition checks. Unseen parts of long chapters are preserved. |
| `POST /documents/{id}/chapters/rename` | `{base_version_id,chapter_id,title}` → new version; retains a numbered heading's prefix and separator. The title is 1–80 characters on one line. A title that stops the chapter being independently recognized returns 422, retaining the previous version. |
| `POST /documents/{id}/chapters/insert` | `{base_version_id,after_chapter_id:null\|string,heading,body_text}` → new version. `null` inserts at the beginning; use a heading matching the selected formats. |
| `POST /documents/{id}/chapters/delete` | `{base_version_id,chapter_ids:[string]}` → new version without those exact chapter spans. Later chapter numbers shift by the number of deleted logical chapters in their original numbering run; existing missing numbers and volume restarts are retained. Deleting one duplicate while the same logical number remains does not shift again. Untouched bodies and heading punctuation/numeral style remain intact; change metadata includes `renumbered`. |
| `POST /documents/{id}/chapters/restore` | `{base_version_id,target_version_id}` → a new version containing the selected historical text/settings. Existing history remains immutable. |
| `POST /documents/{id}/chapters/resegment/preview` | `{base_version_id,heading_formats:[string],custom_formats:[{name,format?,regex?,enabled?}],repair_duplicates?:boolean}` → `{document_id,base_version_id,preview_token,conflict,old,new,changes,change_count,changes_truncated,duplicate_repairs,repair_duplicates}`. Preview is request-local and creates no version. Old/new counts include actual, empty and duplicate chapters; changes include before/after directory entries and are capped at 500. |
| `POST /documents/{id}/chapters/resegment` | Same selection plus `conflict_policy?:"keep_old"\|"use_new",preview_token?`. A changed directory requires an explicit policy and matching token; otherwise 409 `chapter_resegment_conflict`. A changed token yields 409 `chapter_preview_changed`. `keep_old` retains the original version; `use_new` replaces the directory in a new derived version, without merging old/new chapter arrays. Empty `heading_formats` auto-detects; explicit names select only those formats. Templates use one `N`; regex has a two-second scan timeout. Optional duplicate repair only removes adjacent empty same-number heading copies; two nonempty bodies are never automatically merged. Reapplying an unchanged parse reuses the existing version. Originals and retained earlier versions stay exact. |
| `POST /documents/{id}/chapters/settings` | `{base_version_id,author_keywords?:[string],garble_rules?:[{name,pattern,literal?:boolean,enabled?:boolean}]}` → new version, retaining the text. Default non-body keywords include `PS：`; an explicitly saved keyword list remains authoritative, and an empty array deliberately disables keyword matching. `literal:true` matches exact watermark text; otherwise `pattern` is a regular expression, with a 250 ms per-chapter scan timeout. Invalid/slow rules return 422 before committing. |
| `POST /documents/{id}/chapters/clean` | `{base_version_id,selections:[{chapter_id,proposal_ids:[string]}]}` → new version containing only the selected removals. The server regenerates and validates candidate IDs against that version. Unknown, changed or non-removable IDs return 409. Unselected ranges remain exact. |

All ranges are half-open Unicode **codepoint** ranges `[start,end)`. The chapter index's `start,end,body_start` are absolute within the version text. A chapter-page response's `start,end,total` and all proposal/diagnostic offsets are local to that chapter; `body_offset` is the local start of the body. Pagination never makes a proposal's offsets page-relative. JavaScript must use codepoints (`Array.from(text)`) rather than UTF-16 offsets. The body begins immediately after the heading and can include its line break.

`GET /documents/{id}` returns retained version metadata with `protected` flags, `history_limit:10` and `read_view_limit:3`. A commit retains the ten most recent ordinary text versions plus the initial/current versions and snapshots pinned by real task, approval or completion references; protected exceptions may exceed ten. Retained analysis-history ledger snapshots also pin their original source versions. Necessary pre-migration parent chains or unknown source modes remain protected until source provenance is materialized. Old ordinary versions are removed, while original source bytes/text/mode remain independent. Compact `doc_version_meta` fields include `has_source_provenance`, `active_source_ids_json` and source/text lengths; source listing does not hydrate full version payloads. Upload receipts and normal source rows do not pin full-book versions forever; source version links become null when an ordinary referenced version is removed. Retry receipts remain idempotent and do not append again. Chapter read models are retained for the latest three versions and rebuilt lazily for an older protected snapshot.

All chapter mutations above except preview accept `?summary=true`, returning lightweight new-version metadata instead of the full text/chunk arrays. The GUI uses that response and refreshes the small directory/current page. Default responses retain the full-version compatibility contract. Body-only paging excludes heading and boundary empty lines; collapsed display text must never be submitted as `expected_text`. Save with the returned original slice, source bounds and mapping, preserving all undisplayed content.

Missing numbered chapters are virtual directory items with `kind:"missing",placeholder:true`, empty text/body and zero-length spans at the next real chapter. A gap from 11 to 17 yields five independently searchable/editable items (12–16), each with a stable `missing-before-{next_real_id}-{number}` ID within that version. Only those five items carry the gap issue in the presentation directory; chapter 17 is not counted again solely because a gap precedes it. The authoritative version/index gap metadata remains available. Real chapter IDs/source indices do not change until an actual insertion is saved. An exceptionally large gap is bounded and includes an explicit `gap_range` remainder with `number_start,number_end,missing_count`; the remainder is not silently dropped or expanded into an unbounded allocation. The index's `placeholder_count` counts individually expanded items (at most 2000), `truncated_missing_count` counts the explicit remainder, and version-summary `placeholder_count` reports their total.

A proposal is `{id,start,end,text,text_truncated,kind,label,reason,enabled,removable,scope}`. `scope` is `heading` or `body`; all offsets remain local to the complete chapter text even when presented in the title editor. `text` is a preview capped at 2000 codepoints; the full range remains readable through chapter pages. `enabled` is the suggested review selection, **not** evidence of an applied deletion. Changing the selection in the GUI has no write effect until `/chapters/clean` succeeds. Explicit solicitation phrases such as 求订阅/求月票 and brackets consisting entirely of such phrases can be proposed within titles without selecting the chapter number. Author notes and scrape-noise candidates are removable; suspected replacement characters, private-use glyphs and mojibake remain diagnostic annotations with `removable:false`. Missing source character information is not repaired by deleting a placeholder. The single chapter reader shows original content with red removal and purple diagnostic highlights, without a second original/cleaned text column.

Title and body editing are available in the reader, with dirty-state marking and a shared save action. Paging/chapter changes protect unsaved changes. The directory has no checkboxes and uses row highlights for flagged chapters; missing placeholders have their own color and text state. The chapter-delete action is a persistent trash-icon button with an accessible name; redundant previous/next chapter arrows are absent from the title bar. The batch dialog groups exact matched-text previews from titles and bodies by chapter and supports individual removable-span selection, chapter selection and select-all. It submits only the selected proposal IDs; a partly selected chapter is still counted once. Red removals, purple repair flags and missing-chapter guidance share one legend. Red and purple snippets both support exact navigation to their heading span or actual body page; invisible characters receive visible marks, while non-body structural issues display concrete reasons. A batch creates a new version before candidates are recomputed. Internal chunk planning remains part of analysis, with no standalone chunk-list button in the full-text GUI. Recognition/rule settings share “分卷与章节” with volume plans and start collapsed. A changed recognition directory requires the preview/keep-old/use-new flow above. Final export is offered after “完成清洗” links the selected existing/new work and records the fixed completed version, using `/download?format=clean` to produce canonical headings, single-newline separation throughout (including between chapters) and blank headings for unfilled chapters; the exact saved snapshot remains downloadable with `format=raw`.

Derived chapter read models use layout version 5. New immutable snapshots mark `preprocessing_version:2`; earlier snapshots whose saved keywords exactly equal the complete prior defaults use the expanded `PS：` default set when rebuilding their derived view. Distinct custom lists and empty lists remain unchanged. The marker preserves an explicit later choice to remove `PS：`, even when the resulting list equals the old defaults. This upgrades derived rules without rewriting the original or historical snapshot payload.

Issue kinds are `author_note,missing_glyph,mojibake,noise,length,gap,duplicate`; entries include `label,count,severity`. `issue_count` is 0 or 1 per chapter, independently of its number of marked spans. Gap/duplicate detection shares a numeric sequence across recognized heading formats; standalone volume markers and decreasing numbers start another sequence. Outliers use relative chapter-body length. Neither missing numbers nor repeated headings discard text. Exact snapshots retain source coverage; body normalization belongs to a derived version/presentation/export, never the immutable original bytes.

## Version-specific volume plans

| Endpoint | Request and response |
|---|---|
| `GET /documents/{id}/versions/{vid}/volumes` | `{document_id,version_id,revision,chapter_count,volumes,method}`. An unsaved plan has revision 0 and `method:"chapter_markers"`; saved plans use `method:"saved"`. |
| `PUT /documents/{id}/versions/{vid}/volumes` | `{expected_revision,volumes:[{title,start_chapter,end_chapter}]}` → updated plan. Every chapter must belong to exactly one continuous range, from 1 to the final chapter; 409 on stale revision. |
| `POST /documents/{id}/versions/{vid}/volumes/detect` | `{volumes,method:"chapter_markers"}` derived from source volume markers; does not save the suggestion. |
| `GET /documents/{id}/versions/{vid}/volumes/prompt` | `{prompt,chapter_count}` for user-directed copying to another model. Includes the complete chapter listing. |
| `POST /documents/{id}/versions/{vid}/volumes/paste` | `{content}` → `{volumes}`. Accepts JSON (optionally fenced), validates complete ranges and does not save. |
| `POST /documents/{id}/versions/{vid}/volumes/model` | `{profile_id,remote_consent?,expected_model_fingerprint?}` → `{volumes,method:"model",usage,call_id}`. Explicit model operation with the same remote target checks and token ledger; save separately through PUT. |

Volume range numbers are **one-based ordinals in the immutable real chapter list**, including a preface/fallback segment if present. They exclude virtual missing-chapter directory items until those chapters are actually filled and saved. They are not a work's potentially duplicated or missing printed chapter numbers. Plans are keyed by `(document_id,version_id)` and have their own revision independent of the source version or analysis revision.

## Rich reference analysis and section review

`GET /documents/{id}/reference` returns `{document_id,revision,structure_type,sections,updated_at,commits}`. The initial analysis revision is 0. `structure_type` is `narrative` or `setting_collection`. Object sections are `plot,style,rhythm,opening,era_background`; array sections are `characters,settings,setting_features,raw_entries`. New fixed tasks use content Schema 3 described below; legacy nested `epochs → periods → events`, chapter-tagged character facts and setting updates remain readable without destructive conversion. `raw_entries` have `{title,content}`. Ratio metrics and chapter information density are numbers in `[0,1]`.

| Endpoint | Request and behavior |
|---|---|
| `PUT /documents/{id}/reference` | `{structure_type,expected_revision}` → updated reference state. |
| `PUT /documents/{id}/reference/{section}` | `{data,expected_revision}` → validated, edited section and updated reference state. The revision belongs to this work-level reference state, not the job. |
| `GET /documents/{id}/reference/history` | `{items:[{revision,structure_type,updated_at}]}`; the ten most recent restore snapshots per text are retained. Current reference state and its valid contribution ledger remain intact. |
| `POST /documents/{id}/reference/restore` | `{target_revision,expected_revision}` → a new reference revision containing the historical sections. The contribution ledger remains retained for idempotency. |
| `POST /documents/{id}/reference/transform` | `{kind:"summarize"\|"translate",profile_id?,payload,remote_consent?,expected_model_fingerprint?}`. `payload.prompt_only:true` returns `{kind,prompt,source}` without a provider call. Summary supports `granularity:"chapter"\|"major_event"\|"volume"\|"book"` and optional `text`; translation supports `entry_index` or explicit `text`, and `target_language`. A model response returns `{kind,prompt,content,call_id,usage}` plus validated plot `data` for summary. These are review previews; section/entry saving is separate. |
| `POST /reference-collections` | `{title}` → empty collection document metadata with `format:"collection"`, empty immutable version, and a `setting_collection` reference state. Does not fabricate an uploaded TXT file. |
| `POST /documents/{id}/reference/prepare-source` | `{expected_revision}` → `{document,version,reference_revision}`. Creates/reuses a fixed literal text version assembled from raw entries, existing settings and characters for subsequent extraction; HTML entity strings, CRLF and Unicode characters are retained exactly. Repeating preparation with unchanged entries reuses that version. Upload/append originals remain unchanged. Empty collections cannot start extraction. |
| `GET /documents/{id}/reference/export` | Plain UTF-8 text attachment containing raw entries in the length-prefixed `InkReference entries v1` format, preserving multiline contents and marker-like text. |
| `POST /documents/{id}/reference/import` | `{text,expected_revision,mode?:"merge"\|"replace",title?:string}` → updated reference state. Round-trips the exported format; ordinary text becomes one raw entry. |
| `GET /jobs/{run_id}/chunks/{chunk_id}/prompt` | `{run_id,chunk_id,text,prompt}` for that task's immutable source chunk and pinned Skill. Does not invoke a model. |
| `POST /jobs/{run_id}/chunks/{chunk_id}/paste` | `{content,expected_revision}` → updated job. Accepts fenced/plain JSON, validates it and any supplied exact source evidence; stores a human-reviewed candidate without a model call. `expected_revision` is the **job** revision (minimum 1). Committed chunks cannot be replaced. |
| `POST /jobs/{run_id}/chunks/{chunk_id}/start` | Starts only that source chunk through LangGraph. Other chunks retain their progress. Uses the task's fixed model and already confirmed target. |
| `POST /jobs/{run_id}/chunks/{chunk_id}/commit` | `{sections:[string],expected_revision}` → reference state. Approves selected available sections independently; `expected_revision` is the **work-level reference** revision (minimum 0). Repeating the same `(run_id,chunk_id,section)` contribution is idempotent. |

The GUI uses summary-only global task lists and selected-work summaries; historical deep links fetch their own summary. Full candidates are loaded only for the selected task/explicit segment view or the selected approved legacy-source run, not for every historical task. V2 full-book analysis reads persisted sections/projection without candidate hydration.

The HTTP default workflow/Skill is `default@3.0.0` / `unified-reference@3.0.0`. A manual job uses `{document_id,version_id,workflow_id:"default",workflow_version:"3.0.0",manual:true,start:false}` without a profile. The runner completes the full selected source range in `plot → characters → settings → language` stage order; each source chunk is covered once within each stage. Manual prompts/pastes use the same stage contracts, without automatic provider requests. Provider jobs require fixed default/stage model snapshots and endpoint/model consent checks. `expected_stage_fingerprints` maps each remote overridden stage to its selected profile target fingerprint; a missing confirmation is 428 and a changed target is 409, before creating the job or sending text. A single `remote_consent:true` covers the full displayed target set. Start/resume checks each actual stage target against its snapshot before invocation; endpoint/model edits cannot redirect an existing job. Historical `1.0.0`, `1.1.0` and `2.0.0` declarations and snapshots remain immutable and explicitly addressable.

The 1.1 output retains `summary,characters,events,cliches` and adds optional `reference:{plot,characters,settings,style,rhythm,opening,setting_features}`. The schema's authoritative declarations remain in `/skills/unified-reference/versions/1.1.0`; old `1.0.0` definitions remain immutable. Model-derived rich character/setting/event/feature facts need contiguous, nonempty original `evidence`. A native upstream-shaped human paste may instead contain `events,characters,settings,style` directly and is kept as an explicitly reviewed reference candidate; it does not silently create v8 material facts lacking evidence. Supplied evidence must still be exact. Core v8 material approval (`/jobs/{id}/approve`) and work-level section approval (`/chunks/{id}/commit`) remain separate operations.

Section commit records retain `source_version_id`. Character/setting merges preserve existing scalar edits and union chapter fact lists; style metrics aggregate by source character weight, with separately saved manual style/rhythm/opening overrides retained. Source text edits never change an existing job's fixed text, prompts, chunk ranges or model snapshot; create another task to analyze another source version.


## Chapter-folder projection and book metadata

Routes are relative to `/api/studio`. Document metadata adds `book_title,author,earliest_update_at,book_type,metadata_revision,storage_folder,storage_state`; list/detail responses additionally project `work_category,catalog_author` from the explicitly linked work without hydrating text or analysis. Source format and work category are independent.

| Endpoint | Request and behavior |
|---|---|
| `GET /documents/{id}/book-metadata` | `{document_id,book_title,title,author,earliest_update_at,book_type,metadata_revision,version_id,storage_folder,storage_state,storage_error}`. |
| `PUT /documents/{id}/book-metadata` | `{values:{title? or book_title?,author?,earliest_update_at?,book_type?},expected_version_id,expected_revision?}`. Unknown fields/control characters are rejected; title must remain nonempty. A stale latest version or metadata revision returns 409. Metadata edits do not create a text version. |
| `GET /documents/{id}/chapter-files?verify=false` | Current projection state, folder, version, chapter count and manifest hash. `verify=true` checks all listed file hashes; ordinary reads avoid that full scan. |
| `POST /documents/{id}/chapter-files` | Rebuilds the current projection from authoritative SQLite and verifies it, without creating text/history versions. Disk-write errors retain the committed source and are exposed by state/error. |
| `GET /documents/{id}/export/chapters?version_id=...` | ZIP containing one title-named folder, ordered UTF-8 chapter TXT files and `metadata.json`, generated from the selected retained version. No user-added folder files are exported. |

`books/<safe title>/metadata.json` identifies `format:"InkReference-chapters",format_version:1`, document/version IDs, book metadata, source hash, imported time, encoding, ordered chapters and their file hashes. On-disk chapter names use ordinal/title/content hash; ZIP chapter names use ordinal/title. Duplicate titles receive a document-ID suffix on disk. Missing chapter placeholders remain blank titled TXT items. Joining chapter files in manifest order equals the canonical cleaned full-book TXT; the existing `/download?format=clean` endpoint remains that merged export.

Dates retain supplied year/month/day precision; unknown earliest update is null and is never filled with import time. Explicit source frontmatter can supply book title, author and first-update date; user-supplied metadata takes precedence. Files are recoverable current-version projections, not an external editing API. SQLite commits before atomic manifest publication; failed publication is rebuildable. Full project backups omit this redundant projection and reconstruct it in the independent restored directory. Originals, retained text versions and approved facts stay authoritative.

## Catalog Schema 6 and shared characters

Current core columns are exactly `catalog_schema_v11.COLUMNS`. Works replace `两句话概括` with `剧情梗概`, add `讨论热度` and `商业规模`, and retain `热度权重` for legacy compatibility. Characters expose `人物ID,角色名,别名,角色定位,性格基调,视觉元素设计,初始状态,阶段状态,关联作品,关联cliché`; aliases and states are JSON strings in portable tables. Character identity and snapshots use the same Schema-3 `character_contract.py` business contract as reference extraction. Cross-work snapshots are isolated by `(character_id,work_id)` rather than copied between works based on name. The first import/binding is explicit; bound reference edits and catalog context edits synchronize transactionally with independent revision checks.

Cliches expose `clichéID,cliché内容,cliché评分,关联作品,关联角色,人设塑造,剧情机制`. The score is computed as `100 * sum(heat of unique matched works) / sum(heat of all unique catalog works)`. `heat` is the mean of discussion and market components; a component can be a finite 1–10 number or `{min,max,source_ids?,rationale?}` JSON with 1 <= min <= max <= 10, using its midpoint. Missing components use 1; a valid old single heat remains a compatibility fallback. Record responses include `computed:{cliche_score,matched_work_count,total_work_count,missing_heat_count,formula}`. These implementation counts explain the formula; the GUI presents the score rather than the removed prevalence/hit metrics. This is catalog-weighted prevalence, not a measured global-web probability. Character–cliche maps maintain reverse links without duplicate related cards.

Raw v8 and portable schemas 3/4/5 remain importable. Original headers and schema-specific digests are validated before migration. `legacy_values` preserves the original v8 snapshot; `catalog_field_archive` preserves later removed schema-4/5 fields without overwriting that first archive. Schema-6 exports include optional archives/character contexts when present; omitted optional tables do not imply lost required main tables. User edits do not recreate removed fields as current columns.

### Character identity/context bridge

| Endpoint | Request / response |
|---|---|
| `GET /api/library/revision` | `{revision:string}` catalog snapshot hash for cross-table operations. |
| `GET /api/library/characters/{id}/contexts` | `{character_id,catalog_revision,contexts:[{character_id,work_id,document_id,extracted_character_id,revision,character,reference_revision,characters,events,source_available,missing_source}]}`; selectable relationship/event targets come from the bound reference. |
| `PUT /api/library/characters/{id}/contexts/{work_id}` | `{character,expected_revision,expected_catalog_revision,expected_reference_revision?}`; validates the shared character contract and updates bound reference/context atomically. |
| `GET /api/studio/documents/{id}/reference/characters/catalog` | `{catalog_revision,links:[{extracted_character_id,character_id,work_id}]}`. |
| `POST /api/studio/documents/{id}/reference/characters/{extracted_id}/catalog` | `{character_id?,expected_reference_revision,expected_catalog_revision}`; explicitly creates/imports or binds an existing catalog identity. A colliding existing ID requires an explicit reuse target. |
| `POST /api/studio/documents/{id}/reference/preview` | `{sections,source_version_id?}` → `{sections}` as a Schema-3 projected copy; reads referenced source versions but writes no fact/history revision. |

All update bodies reject extra fields, apply revision checks and preserve per-work contexts. Import first requires the document's explicit linked work ID. The full-text parser's absolute IDs and source proof remain available internally; UI simplification never removes the source audit. Portable catalogs retain original document/extracted IDs and exact character JSON even when the source document is absent. Such contexts report `source_available:false,missing_source:true`; reads do not create empty reference states, and offline context edits update catalog snapshots without modifying a nonexistent source. A present source with a mismatched work/character binding returns 409 and requires explicit rebinding. Full linked-source migration uses a complete project backup.

## Current fixed analysis contract, content Schema 3

Main-library schema 6, portable format 1 and analysis content schema 3 are independent contracts. `canonical_stage_schema(stage,version=3)` is the default; provider schemas require internal source proof. Skills carry `contract_version:3` and cannot alter the canonical output shape. Uploaded JSON/Markdown/ZIP may customize prompt content, and `/skills/upload?contract_version=3` rejects a mismatched contract before saving. Each stage override must match the main Skill's contract. Explicit v2 Skills/prompts remain byte-for-byte compatible with their earlier stage contracts and cannot be mixed into a v3 run.

| Stage | Required result sections |
|---|---|
| `plot` | `{plot:{schema_version:3,logline,events,plotlines}}` |
| `characters` | `{characters:[CharacterV3]}` |
| `settings` | `{settings:[SettingV3]}` |
| `language` | `{style:StyleV3,rhythm:RhythmV3,opening:OpeningV3,era_background:EraV3}` |

| Record | Business fields |
|---|---|
| Event | `id,name,theme,description,narrative_progress,story_time,participants,setting_ids,causes,results,plotline_ids,volume`; flat events, no epochs/periods containers |
| Character | `schema_version:3,id,name,aliases,role_tag,temperament,visual_design,initial_state,snapshots` |
| Initial state | `gender:null|男|女|特殊,age,identity,ability,personality,appearance,relationships,narrative_progress,story_time` |
| Character snapshot | `id,narrative_range:{start,end:null|number},story_time,event_ids,identity,ability,personality,appearance,visual_design,relationships` |
| Relationship | `{character_id,relationship}`; target ID must resolve |
| Setting | `schema_version:3,id,title,category,content,earlier_setting_id:null|ID,narrative_progress,story_time,snapshots` |
| Setting snapshot | `id,narrative_range,story_time,event_ids,content`; appended setting ancestry must resolve and be acyclic |
| Era item | `id,kind:meme|real_world_event,title,content,approximate_time,appearances` |
| Era appearance | `narrative_progress,story_time,event_ids,character_ids,context,usage:dialogue|thought|narration|reference` |

Temperament is one value from `强大,智谋,利他,共情,利己,执念,反差,宿命`, or empty when unsupported. All narrative progress values use 0–100. A state's end may be null while ongoing; an end before its start is invalid. Story time is a free-form explicit source string or null, without an exposed sort key. Provider facts additionally require internal `{revealed_at:{chapter,offset},source_version_id,evidence}`; local chunk offsets become absolute source offsets, source IDs are bound to the fixed version and exact continuous quotes are verified. The runner derives narrative progress from exact source position: parsed chapter ordinal / chapter count, otherwise non-whitespace body-character position / total. Provider guesses are not authoritative. Human business records may omit source proof; they retain manual origin and are never disguised as model-proven source facts.

`GET /documents/{id}/reference/projection?schema_version=3&progress=0..100` returns a read-only as-of view; the legacy `chapter=N` scope remains supported. Full projection includes `plot.views.{story,narrative}`, event-derived book/volume outlines and current snapshots. Narrative order follows progress plus source offset; explicit comparable calendar dates reorder in story view, while unknown/relative dates retain their narrative slots. No dates are invented. Projection-only fields must not be persisted as canonical records. Legacy source sections are adapted for compatible reading with preserved originals, not re-extracted by a hidden model call.

The v3 default is `chunk_chars:100000,max_input_tokens:128000,max_output_tokens:12000,budget_tokens:800000`. Actual chunks remain bounded by every selected stage model, its output reserve, complete schema/system prompt and bounded entity memory. Saved volume boundaries limit chapter groups; over-budget volumes split by whole chapters, then long chapters by paragraphs without truncation or source overlap. `snapshots.contract_version`, `document.volumes`, `document.progress_chapters`, `document.body_characters` and `snapshots.chunk_groups[{chunk_id,start,end,volume,start_chapter,end_chapter,body_characters_before}]` make the coarse display groups reproducible. Every stage covers all fixed source chunks; larger groups reduce repeated prompt overhead without promising a universal token-saving ratio. Cache hashes explicitly include contract version; v2 and v3 cannot share cache entries.

Language retains writer-facing deterministic sentence/paragraph/punctuation metrics separately from semantic style and plot judgments. Era frequency derives approved appearances, not search popularity; only meme dialogue/thought counts toward per-character play frequency, with zero-use characters included in the average. No reliable approximate time means an empty value. The UI omits the old end-date, precision and verification-status fields. Real-world origin/date correctness still requires review.

## Reviewed web research and semantic deduplication

Routes below are relative to `/api/studio/research`. Services initialize lazily. This is a separate source-research flow for catalog metadata, never a replacement for uploaded text cleaning/extraction.

| Endpoint | Request / behavior |
|---|---|
| `GET /settings` | Search provider config plus `key_available,target_fingerprint`; no API key returned. |
| `PUT /settings` | `{provider:tavily|brave|searxng,endpoint?,credential_mode:session|vault|environment,key_env?,api_key?}`; credentials reuse the OS/session/environment boundary. |
| `GET /targets?model_profile_id=...&embedding_profile_id=...&embedding_model=...` | Fixed model, embedding and search endpoints/models and fingerprints for the single start confirmation. |
| `POST /jobs` | `{title,author,model_profile_id,embedding_profile_id,embedding_model?,message?,target_work_id?,remote_consent,expected_fingerprints,start?}`; name/author required, `start` defaults true. Fingerprints must match model/embedding/search targets; missing consent returns 428, changed target 409. |
| `GET /jobs` | At most 100 lightweight history summaries, no source/draft/vector hydration. |
| `GET /jobs/{id}` | Revision, status, stage, messages, sources, draft, semantic matches, progress, work matches, catalog revision and usage; detail loads only on demand. |
| `POST /jobs/{id}/chat` | `{message,expected_revision}`; appends a request and reruns draft/matching from retained sources. |
| `PUT /jobs/{id}/draft` | `{draft,expected_revision}`; validates manual edits and reruns semantic matching before approval. |
| `POST /jobs/{id}/start` or `/resume` | Continues explicit work from retained successful stages, checking fixed targets. |
| `POST /jobs/{id}/cancel` | Requests cancellation; completed responses remain reusable. |
| `POST /jobs/{id}/approve` | `{expected_revision,expected_catalog_revision,decisions:{work_id?,allow_new_edition?,characters,cliches}}`; atomic catalog update, idempotent by job ID. |

The real LangGraph is `search → fetch → draft → dedup → review`. Tavily/Brave use their search API credentials; SearXNG requires JSON search support. Remote search endpoints use HTTPS, with explicit loopback HTTP allowed for local SearXNG. Up to ten pages are fetched independently with size/redirect/public-network restrictions, script stripping and bounded excerpts. A source's read status proves retrieval, not semantic truth. Identity confirmation must be backed by successfully read sources containing both the work title and author. Same title/author already in the library requires an explicit update target or independently confirmed edition, not automatic title merging.

The draft holds `identity,work,characters,cliches,warnings`. Job details also return `character_matches`: same-work normalized name/alias matches support explicit reuse decisions; no cross-work character is silently merged. It includes at most the protagonist, one source-supported popular supporting character and three cliche candidates. Each entry cites retained source IDs; unsupported global top rankings are warnings, not claims of verified popularity. Work discussion and commercial scores are 1–10 min/max ranges with source IDs and a rationale, or null when evidence is insufficient. Users edit and approve before any catalog write; full texts and narrative-extraction results are untouched.

Embeddings are real OpenAI-compatible `/embeddings` or Ollama `/api/embed` calls using a selected saved connection and optional embedding-model override. Invalid/missing vectors fail instead of falling back to lexical similarity. The bounded cache keys vectors by content hash plus endpoint/model fingerprint (at most 50000 rows); exact cosine search returns the top five existing candidates. Similarity >=0.90 recommends reuse, >=0.78 requires comparison, otherwise suggests new. These are configurable-code heuristics, not validated semantic-identity guarantees. Every candidate requires `{action:reuse|new,id?}` in the character/cliche decision maps; similar new cliches require `acknowledge_similar:true`, and two >=0.90 similar in-draft cliches cannot both be newly inserted. Reuse adds work/character maps instead of duplicating the existing cliche.

The catalog revision is rechecked inside its transaction; a changed library requires rematching. Chat and embedding requests share actual/estimated token accounting, including retries and interrupted unknown usage. Vector/cache reuse does not fabricate provider usage. Search-service billing is separate from LLM tokens. No real paid search, commercial LLM or embedding provider was exercised by the synthetic transport tests; no accuracy, full-web ranking or universal deduplication guarantee follows from them.

## Historical fixed analysis contract, content Schema 2

This frozen contract applies only to explicitly selected `2.0.0` workflows/Skills; new tasks use Schema 3 above. It was introduced with main-library schema 5 and exchange format 1. Canonical definitions are in `chronicle_schema.py`; `canonical_stage_schema(stage,version=2)` rejects missing/unknown fields, invalid enum/type and unresolved references. Stage Skills use `contract_version:2` and the canonical output schema unchanged. Prompt content can be customized; output fields cannot. Workflow versions preserve `fixed_pipeline:true` and all fixed nodes enabled. Stage options are `stage_prompts`, `stage_settings[stage]{max_output_tokens,budget_tokens}`, `stage_skills[stage]{id,version}` and optional `stage_models[stage]:profile_id`. An absent/cleared model override inherits the task model. Creation resolves immutable default/stage model snapshots and `stage_model_fingerprints`; manual tasks use the manual profile without invoking overrides. `reuse_candidates` is a boolean, default true. The historical v2 fixed definition uses `chunk_chars:4000,max_input_tokens:64000,max_output_tokens:6000,budget_tokens:800000`.

| Stage | Required result sections |
|---|---|
| `plot` | `{plot:{schema_version:2,logline,events,plotlines}}` |
| `characters` | `{characters:[CharacterV2]}` |
| `settings` | `{settings:[SettingV2]}` |
| `language` | `{style:StyleV2,rhythm:RhythmV2,opening:OpeningV2,era_background:EraV2}` |

Shared evidence is `{revealed_at:{chapter:integer|null,offset:integer|null},source_version_id,evidence}`. Chapter is a one-based narrative anchor; persisted offset is an absolute Unicode codepoint position within the pinned source version. Stage model/paste output uses the current source chunk's local codepoint offset, which the runner validates and converts to absolute position before saving. Evidence must be a nonempty contiguous exact source quote; a supplied offset must point to it. Missing source IDs are filled from the pinned version, conflicting IDs rejected. `story_time:{label,sort_key:string|null}` is independent of disclosure order; an unknown label cannot have an invented sort key. Chronological accuracy depends on the provided sortable key, not a date-inference service.

| Type | Main fields |
|---|---|
| Event | `id,name,description,participants:[character ID],setting_ids,causes:[event ID],results:[event ID],plotline_ids,story_time,volume` and evidence |
| Plotline | `id,title,goal,stages:[{id,goal,status,event_ids,...evidence}]`; status is `open\|progressing\|resolved\|abandoned` |
| State | `id,attribute,value,change_type,supersedes:[state ID],event_id,setting_ids,character_ids,story_time` and evidence; change is `addition\|change\|revelation\|correction` |
| CharacterV2 | `schema_version:2,id,name,intro,temperament,visual_design,states,experiences` and evidence; optional retained chapter appearance/personality/speech/role fields |
| Experience | `event_id,changes:[State]` and evidence; no copied independent event narrative |
| SettingV2 | `schema_version:2,id,title,category,content,states` and evidence; category is `power_system\|factions\|geography\|social_rules\|history\|worldview\|other` |

IDs identify entities/facts across blocks. Names are display values, not identity. Links are validated against current candidates and their supplied entity context; self-causality and causal cycles are rejected. Formal events/states are not deleted by the ten-snapshot recovery-history limit. Existing legacy objects are preserved under compatibility data or alongside new items; a read does not fabricate IDs, time, evidence or conversion success.

`GET /documents/{id}/reference/projection?chapter=N` returns a read-only projection with generated `plot.views.{narrative,story}`, `plot.outlines.{book,volumes}` and character/setting `current_state`. Event participants/settings can be derived from later-stage experiences/states by their event IDs, allowing the plot-first stage to leave unavailable forward entity references empty. Character `setting_links` similarly derives from character setting IDs and explicit setting-state character IDs. Full/volume outlines concatenate traceable event descriptions rather than invoking an extra model pass. The chapter scope filters strictly anchored V2 disclosures, removes forward event/entity links, and derives scope-safe descriptions; unknown V2 anchors are excluded. Legacy unknown anchors retain compatibility behavior. Story order uses `story_time.sort_key`, unresolved dates after known dates. Projection-only fields must not be submitted as persisted canonical sections.

### Language and era background

`StyleV2` contains `narrative_person,focalization,register,colloquialism,rhetoric,dialogue_style,catchphrases,naming,narrative_composition,observations`. Composition kinds are `action|description|psychology|exposition`; judgments and occurrences retain evidence. `RhythmV2.chapter_analysis` stores climax, payoffs, foreshadowing, pacing and emotion by chapter. `OpeningV2` retains pattern and anchored observations. These are model/manual judgments, not local quality scores.

On style approval, local `deterministic_statistics` is derived from the pinned full chapter bodies, excluding recognized headings: Unicode non-whitespace coverage; sentence/paragraph count, mean, median, p90, min/max and length buckets; punctuation counts and frequency per 1000 characters. A paragraph is a nonempty physical line. Sentence boundaries use Chinese stops, exclamation/question marks and ellipsis runs; punctuation remains in measured length. Median uses the central value/average and p90 uses nearest rank. No vocabulary-diversity requirement or word tokenizer is added. The saved method/version and coverage distinguish deterministic values from semantic judgments.

`EraV2.items` contain `id,kind:meme|real_world_event,expression,meaning,time_range:{start,end,precision,verification},occurrences`. Dates permit null; precision is `unknown|approximate|year|month|day`, verification `unverified|source_stated`. Occurrences carry evidence plus `character_id,context,usage:dialogue|thought|narration|reference`. The application does not browse or externally verify origins, date ranges or real-world popularity.

Derived era statistics deduplicate by item/source-version/chapter/offset/quote. They return per-item and total counts, per-10000-source-character rates when coverage exists (otherwise null), character usage, all tied highest users and average over every identified character including zero users. Only `kind:"meme"` dialogue/thought counts as character use; real-world-event references remain in work/item occurrence counts and do not enter the character meme ranking. These counts describe approved extracted occurrences, not guaranteed exhaustive mentions or externally confirmed popularity.

### Execution and token accounting

Job responses include `stages:[{id,name,order,completed,total,progress,status,attempts,tokens}]`, `current_stage` and aggregate `progress`; attempt histories identify their stage. Source ranges are contiguous with no overlap and prefer the pinned parsed chapter ends, including custom chapter formats; only absent chapter metadata uses regex fallback, then long chapters split on paragraph boundaries. Context planning uses each stage's actual model/context/output limits and reserves the canonical schema/system/output budget plus adaptive `context_limits[stage]` memory, capped at 6000 codepoints. Context carries indexed stable IDs, short facts and nearby events rather than the complete accumulated analysis. Future events, plotlines and state summaries are excluded; a future character/setting entry may supply ID/name for identity reuse, with its future summary empty. Four stages still each cover selected source text; actual savings depend on task/context and no fixed token-reduction ratio is claimed. Durable stage attempts and successful responses are reused on explicit resume; restart never automatically sends requests. Existing per-attempt model accounting includes retries and unknown interrupted usage.

Work-level analysis JSON remains bounded: 8 MB serialized payload, depth 14 and at most 10000 entries per list. Exceeding those limits returns a validation error; it does not silently drop formal facts. Chunked input does not make the work-level fact store unlimited.


The disposable `run_stage_cache` matches a SHA-256 of contract version, full messages (including current source/context/schema), immutable selected model snapshot and output limit. It stores only unedited model candidates in chunk-local coordinates, normalizing the old source-version label. A hit is schema/link/source validated again, then grounded to the current version, chapter and absolute offset. Bad entries are evicted; cache mismatch/disabled `reuse_candidates:false` sends a new request. Human candidate edits are not cached. The cache retains at most 1000 entries and 32 MiB payload, pruning oldest entries independently of durable task audits. Attempt states `cache_received` and `cache_succeeded` identify reuse with `usage.source:"local_candidate_cache"`, zero input/output tokens and null `call_id`; a hit is not a provider-ledger call.

Manual fixed-stage POST `/jobs/{run_id}/chunks/{chunk_id}/paste` with direct stage JSON advances only the current ordered stage/chunk. A full `{reference:{...}}` candidate envelope revises existing candidates without advancing stage completion; source proofs, job revision and committed-section guards still apply. Saved V2 sections and committed candidates recheck all nested evidence against the addressed document's real source version, exact offset and chapter range. Unknown/other-document versions or invalid quotes reject atomically without revision/history changes. Summaries and interpretations need not themselves be verbatim; their evidence must be. Legacy manual data without provenance retains its compatibility path.
