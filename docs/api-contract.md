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
- `GET /api/records/{table}/{id}/relations` → `{works: RelatedRecord[],characters:RelatedRecord[],cliches:RelatedRecord[],edges:Edge[]}`. Each `RelatedRecord` adds `relationship` (first edge) and `relationships` (all meanings for that same record): `{id,type,label,description,direction,metadata,source}`. Direction is `outgoing|incoming`; `source` identifies `character_work`, `work_cliches`, or `explicit`. Cards are deduplicated by target; the individual relationship meanings remain separate.
- `POST /api/records/{table}` body `{values: Record<string,string>}` → Record, 201.
- `PATCH /api/records/{table}/{id}` body `{values: Record<string,string>,expected_revision:number}` → Record. Immutable IDs. 409 on revision conflict.
- `DELETE /api/records/{table}/{id}?expected_revision=N&cascade=false` → `{deleted:number}`; 409 if related records block deletion. `cascade=true` is only sent after a GUI confirmation describing related records.

Only direct edges are shown. Sharing a work does not infer a character-to-character or character-to-cliche relationship; cliche-to-cliche edges are forbidden. Character/work edges show the original `角色定位`; work/cliche edges show the original authoritative `work_cliches.detail`. Work editions remain distinct by ID.

### Typed material associations

An explicit relation has `{id,source_table,source_id,target_table,target_id,relation_type,description,metadata,revision}`. Both table keys are among `works,characters,cliches`. `relation_type` contains the user-facing relationship meaning; arbitrary structured extensions are retained in `metadata`. Unknown input relation fields are folded into this object. The source and target must exist, cannot be the same record, and cannot both be cliches. Two different relation types may connect the same endpoints; duplicate endpoint/type tuples under different IDs are rejected with both IDs identified. Existing legacy work/cliche links remain in their single authoritative table.

- `GET /api/relations` → `{items: Relation[]}`.
- `POST /api/relations` body `{values:{source_table,source_id,target_table,target_id,relation_type,description?,id?,metadata?}}` → Relation, 201; an omitted ID is generated.
- `PATCH /api/relations/{id}` body `{values:{...patch},expected_revision:number}` → Relation. IDs are immutable.
- `DELETE /api/relations/{id}?expected_revision=N` → `{deleted:1}`.

Revision checks and tombstones prevent stale edits and ID-reuse conflicts. Associated material deletion requires `cascade=true`; confirmed deletion removes its typed links, including links belonging to child characters removed with a work. SQLite endpoint triggers protect direct writes and cascades as well as service methods.

## Complete material-library exchange

The GUI uses the same complete dataset for DB, CSV and XLSX. The five business tables are `works,characters,cliches,work_cliches,material_relations`; their portable values are strings. The three material tables keep v8 columns; `work_cliches` columns are `work_id,cliche_id,detail,work_position,cliche_position`; typed association columns are `id,source_table,source_id,target_table,target_id,relation_type,description,metadata_json`.

| Endpoint | Contract |
|---|---|
| `GET /api/library/formats` | `{default:"db",schema_version:3,tables,formats,legacy_csv,guidance}`. Provides exact filenames, required columns, format choices and concise import guidance. |
| `POST /api/library/import/preview` | Multipart `files`. Accepts one portable `.db` / `.sqlite` / `.sqlite3`, one complete CSV ZIP, all five CSV files plus `manifest.json`, one `.xlsx`, or all three legacy v8 CSVs together. Returns `{batch_id,tables,errors,warnings,stats,format,schema_version,bundle_id,legacy,required_tables,guidance}`. Each required-table entry includes `table,label,filename,present`; validation remains reviewable with commit disabled on errors. |
| `POST /api/library/import/commit` | `{batch_id,policy:"skip"|"update"|"error"}` → `{imported,updated,skipped,relations_imported,relations_updated,relations_skipped,stats}`. A single transaction covers every material and association. Both material revisions and relation snapshots must match the preview, otherwise 409. |
| `GET /api/library/export?format=db|csv|xlsx` | Defaults to DB. Downloads `InkReference-library.db`, `InkReference-library-csv.zip`, or `InkReference-library.xlsx`. Each carries the same five tables and format manifest. |

Portable format version 1 uses material schema version 3. Manifest entries contain column declarations, row counts and SHA-256 hashes, with a shared `bundle_id`. DB stores the manifest in a `_manifest` table; CSV ZIP has five CSVs and `manifest.json`; XLSX uses five data sheets and hidden `_manifest`. CSV and XLSX data tables append per-row `_bundle_id` and `_schema_version` fields. XLSX adds hidden `_long_text` when necessary to preserve text exceeding Excel's UTF-16 cell limit, CR characters, XML-incompatible characters, XML escape-like text or reserved literal markers. All XLSX data cells are text, including formula-like strings; actual imported formulas/errors/date objects are rejected with sheet and cell locations. Decimal/numeric cells receive a warning when converted to text because lost leading zeros cannot be recovered.

CSV uses UTF-8 BOM, with strict UTF-8/GB18030 decoding on import. CSV export rejects NUL control characters with guidance to use DB/XLSX for exact preservation, rather than emitting an unreadable CSV set. Empty tables must retain headers. Missing required files/sheets, duplicate table selection, missing columns, mismatched batch markers and cross-table references are reported by name; diagnostics identify participating files and affected IDs rather than relying on records already in the target database. Edited hashes/row counts produce review warnings. Old v8 input requires the complete works/characters/cliches set and has no typed-association table. Portable imports keep business values and external IDs; target revisions remain locally managed. Use full project backup for text, analysis and history.

Limits are 32 selected files and 128 MiB total compressed/raw input, 256 ZIP entries and 256 MiB expanded data, 200000 rows per table and 32 MiB per field. A DB is opened read-only with schema restrictions; only ordinary data tables from the portable format are accepted, without triggers, views or virtual tables. Raw workspace SQLite files use the project backup/restore flow. Preview tokens expire after two hours or a restart (404); originals are private application data outside git.

### Legacy CSV APIs

These endpoints remain for existing integrations; the GUI uses the complete exchange endpoints above. Legacy single-table import does not provide the new all-table guidance or typed association format.

- `POST /api/import/preview`, multipart field `files` (multiple CSVs) → `{batch_id:string, tables:[{table:string, filename:string, rows:number,new:number,existing:number}],errors:string[],warnings:string[],stats:{works:number,characters:number,cliches:number,work_cliches:number}}`. Validation errors remain reviewable in this response, and commit is disabled if errors are nonempty.
- `POST /api/import/commit` body `{batch_id:string,policy:"skip"|"update"|"error"}` → `{imported:number,updated:number,skipped:number,stats:{...}}`. One atomic transaction, rejects a changed database snapshot with 409. Preview tokens expire after two hours or a server restart (404); originals are private app data outside git. File limits: 32 files, 32 MB per file, 128 MB total.
- `GET /api/export?mode=compatible|full` → ZIP attachment. Compatible contains v8's three named CSVs; full contains `works`, `characters`, `cliches` with `_record_id`/`_revision`, `work_cliches`, `material_relations` (including revision) and a legacy `manifest.json`. This legacy full export is for inspection; its manifest differs from the new complete exchange format. For workbench-wide business tables, use `/api/studio/projects/tables/export` instead. Neither CSV ZIP is a restore-capable project backup.
- `GET /api/export/{table}` → UTF-8 BOM CSV attachment.

Frontend use relative `/api`, native file input and downloadable response blobs. No real user data is bundled into the app; show an actionable empty state with import button. Backend module `inkreference.api:app`; material modules are `inkreference.database`, `inkreference.library_exchange` and legacy `inkreference.csvio`.

## Reference workbench

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
| Analysis | `GET/POST /jobs`; `GET /jobs?summary=true&document_id=...&version_id=...&limit=100` and `GET /jobs/{id}?summary=true` omit chunk/results payloads for progress polling. POST `{document_id,version_id,profile_id,workflow_id,workflow_version,start:true,remote_consent:false,expected_model_fingerprint?:string}`. Remote creation requires `remote_consent:true` plus the selected public profile's `target_fingerprint` as `expected_model_fingerprint`. Missing confirmation is 428; a changed endpoint/model is 409 with no job or model request. `GET /jobs/{id}` returns real progress, chunks, coverage, evidence, errors and configuration snapshots. |
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

The following endpoints are relative to `/api/studio`. The GUI accepts TXT, PDF and EPUB. TXT is strictly decoded; EPUB/PDF first pass through the existing conversion service to plain text, then use the same chapter parser and issue scan. Uploading does not apply diagnostic deletion proposals. The global upload creates a new work (`mode:new`); each existing work's upload button pins its target and offers `replace` (“更新正文”, replace all current text) or `append` (“补充章节”, add after the current ending), without a second destination selector. Existing immutable sources and version history are retained in both cases.

| Endpoint | Request and response |
|---|---|
| `GET /ingestions` | `{items:Ingestion[]}`. Metadata only, without original BLOBs. |
| `POST /ingestions` | Multipart `file,title?,encoding?,work_id?,mode?,target_document_id?,base_version_id?,options?`. `mode` is `new|replace|append`; replace/append require the current target document and base version. `options` is a JSON string, `{tier:"flash"|"standard",ocr_mode:"txt"|"ocr"}` for PDF only. Starts a durable worker and returns its task metadata, 201. |
| `GET /ingestions/{id}` | Task includes `status,stage,progress,completed,total,error,attempt,document_id,version_id,conversion_id,options`. `progress:null` indicates an indeterminate stage; PDF/EPUB conversion stages forward the actual conversion progress. |
| `POST /ingestions/{id}/cancel` | Cancels pending work and the associated conversion process. A document commit that already succeeded remains committed and completed. |
| `POST /ingestions/{id}/retry` | Explicitly retries a failed/cancelled/interrupted task using its immutable source and target. Idempotency checks reconcile a prior successful document commit before repeating work. |

An application restart marks running imports interrupted and requires an explicit retry; it does not submit a model request. Stages cover decoding/conversion, chapter parsing and issue scanning. A stale replace/append target returns 409 and does not overwrite a newer version. Originals and target configuration are immutable; source filename/hash/bytes remain provenance even though original download is absent from the GUI.

### Chapter operations

The routes in this section are relative to `/api/studio`. Document metadata includes `source_characters` (initial decoded upload length) and `current_characters` (latest text version length); append/edit/collection preparation updates the latter. List/detail metadata queries do not fetch original BLOBs. A document version is immutable. Every chapter mutation supplies its current `base_version_id`; a stale base returns 409 and writes neither a new version nor an append source. Chapter IDs identify positions **within the addressed version**, independently of parsed chapter numbers. Duplicate numbered chapters remain separate records.

| Endpoint | Request and response |
|---|---|
| `GET /documents/{id}/versions/{vid}/chapters` | Returns `{document_id,version_id,pattern,heading_formats,candidates,gaps,options,author_keyword_defaults,chapters}` and placeholder/count metadata. Chapters include `id,index,display_index,number,title,display_label,raw_marker,kind,pattern,start,end,body_start,char_count,issues,issue_count,proposal_count,proposal_ids,enabled_proposal_ids`. `display_label` is `第X章 标题`; no chapter body is included. |
| `GET /documents/{id}/versions/{vid}/chapters/{cid}?start=0&limit=20000&body_only=true` | GUI reading mode returns only the body, with repeated empty lines collapsed: `text,editable_text,expected_text,source_offset_map,start,end,total,source_total,body_offset,body_total,editable_start,editable_end,next_start,body_only`, plus chapter metadata, proposals and diagnostics. `expected_text` is the exact original slice for saving; mapping entries are chapter-local source codepoint offsets for each displayed codepoint boundary. `body_text` is null. `next_start` is the next source offset, not a display character count. |
| `GET /documents/{id}/versions/{vid}/chapters/{cid}?start=0&limit=20000` | Compatibility mode returns the full chapter span including its heading, with the previous `body_text,editable_text` fields. `limit` is 1–100000. Bodies above 200000 codepoints remain readable/editable through pages. |
| `GET /documents/{id}/versions/{vid}/findings?chapter_id=...&offset=0&limit=1000` | `{document_id,version_id,offset_unit,items,total,start,limit,next_start,issue_chapter_count}`. Optional chapter filter and proposal pagination. Items retain `chapter_id,chapter_index,chapter_title`; `total` counts individual proposals, while `issue_chapter_count` counts affected chapters once each. Used on demand by the batch-cleaning dialog, which displays actual matched text beside each selection. |
| `POST /documents/{id}/append` | Multipart `base_version_id,file,encoding` (`auto` by default). Strictly parses TXT/MD/SRT/DOCX/EPUB and appends after the current edited text. Returns a new version. Each upload retains its own immutable bytes, hash and decoded source; it does not replace the initial original. |
| `GET /documents/{id}/sources` | `{items:[{id,filename,format,encoding,original_sha256,original_bytes,version_id,created_at}]}`. Initial original uses ID `original`; later IDs identify append uploads. |
| `GET /documents/{id}/sources/{source_id}/download` | Exact original bytes for that source, after SHA-256 verification. |
| `POST /documents/{id}/chapters/edit` | `{base_version_id,chapter_id,body_text,title?}` → new version. Edits the body while preserving heading and exterior source spans. A virtual missing `chapter_id` materializes a new `第N章 title` at its pinned position; `title` is optional, single-line and at most 80 characters. The new heading must remain independently recognized; otherwise 422 is returned before committing. Previous versions retain the blank virtual item. |
| `POST /documents/{id}/chapters/edit-slice` | `{base_version_id,chapter_id,start,end,text,expected_text}` → new version. Replaces only the addressed chapter-local codepoint range within the body. The original slice must equal `expected_text`; 409 on mismatch. Unseen parts of long chapters are preserved. |
| `POST /documents/{id}/chapters/rename` | `{base_version_id,chapter_id,title}` → new version; retains a numbered heading's prefix and separator. The title is 1–80 characters on one line. A title that stops the chapter being independently recognized returns 422, retaining the previous version. |
| `POST /documents/{id}/chapters/insert` | `{base_version_id,after_chapter_id:null|string,heading,body_text}` → new version. `null` inserts at the beginning; use a heading matching the selected formats. |
| `POST /documents/{id}/chapters/delete` | `{base_version_id,chapter_ids:[string]}` → new version without those exact chapter spans. |
| `POST /documents/{id}/chapters/restore` | `{base_version_id,target_version_id}` → a new version containing the selected historical text/settings. Existing history remains immutable. |
| `POST /documents/{id}/chapters/resegment` | `{base_version_id,heading_formats:[string],custom_formats:[{name,format?,regex?,enabled?}]}` → new derived version. Empty `heading_formats` auto-detects; an explicit list uses only those named formats. Literal templates use one `N` placeholder, e.g. `第N章` or `卷N`; advanced regex uses captures for chapter number and title. Custom heading regex has a two-second scan timeout. The selected parse also collapses repeated body blank lines; heading bytes, immutable originals and earlier versions remain unchanged. |
| `POST /documents/{id}/chapters/settings` | `{base_version_id,author_keywords?:[string],garble_rules?:[{name,pattern,literal?:boolean,enabled?:boolean}]}` → new version, retaining the text. An empty keyword array deliberately disables keyword matching. `literal:true` matches exact watermark text; otherwise `pattern` is a regular expression, with a 250 ms per-chapter scan timeout. Invalid/slow rules return 422 before committing. |
| `POST /documents/{id}/chapters/clean` | `{base_version_id,selections:[{chapter_id,proposal_ids:[string]}]}` → new version containing only the selected removals. The server regenerates and validates candidate IDs against that version. Unknown, changed or non-removable IDs return 409. Unselected ranges remain exact. |

All ranges are half-open Unicode **codepoint** ranges `[start,end)`. The chapter index's `start,end,body_start` are absolute within the version text. A chapter-page response's `start,end,total` and all proposal/diagnostic offsets are local to that chapter; `body_offset` is the local start of the body. Pagination never makes a proposal's offsets page-relative. JavaScript must use codepoints (`Array.from(text)`) rather than UTF-16 offsets. The body begins immediately after the heading and can include its line break.

All chapter mutations above accept `?summary=true`, returning lightweight new-version metadata instead of the full text/chunk arrays. The GUI uses that response and refreshes the small directory/current page. Default responses retain the full-version compatibility contract. Body-only paging excludes heading and boundary empty lines; collapsed display text must never be submitted as `expected_text`. Save with the returned original slice, source bounds and mapping, preserving all undisplayed content.

Missing numbered chapters are virtual directory items with `kind:"missing",placeholder:true`, empty text/body and zero-length spans at the next real chapter. A gap from 11 to 17 yields five independently searchable/editable items (12–16), each with a stable `missing-before-{next_real_id}-{number}` ID within that version. Only those five items carry the gap issue in the presentation directory; chapter 17 is not counted again solely because a gap precedes it. The authoritative version/index gap metadata remains available. Real chapter IDs/source indices do not change until an actual insertion is saved. An exceptionally large gap is bounded and includes an explicit `gap_range` remainder with `number_start,number_end,missing_count`; the remainder is not silently dropped or expanded into an unbounded allocation. The index's `placeholder_count` counts individually expanded items (at most 2000), `truncated_missing_count` counts the explicit remainder, and version-summary `placeholder_count` reports their total.

A proposal is `{id,start,end,text,text_truncated,kind,label,reason,enabled,removable}`. `text` is a preview capped at 2000 codepoints; the full range remains readable through chapter pages. `enabled` is the suggested review selection, **not** evidence of an applied deletion. Changing the selection in the GUI has no write effect until `/chapters/clean` succeeds. Author notes and scrape-noise candidates are removable; suspected replacement characters, private-use glyphs and mojibake remain diagnostic annotations with `removable:false`. Missing source character information is not repaired by deleting a placeholder. The single chapter reader can therefore show proposed removals in red and let a user retain each span, without a second original/cleaned text column.

Body editing is directly available in the reader, with dirty-state marking and explicit saving. Paging/chapter changes protect unsaved changes. The directory has no checkboxes; the batch dialog groups exact matched-text previews by chapter and supports individual removable-span selection, chapter selection and select-all. It submits only the selected proposal IDs; a partly selected chapter is still counted once. Red removals, purple repair flags and missing-chapter guidance share one legend. A batch creates a new version before candidates are recomputed. Internal chunk planning remains part of analysis, with no standalone chunk-list button in the full-text GUI. Recognition/rule settings share “分卷与章节识别” with volume plans. Final export is offered after “完成清洗”, using `/download?format=clean` to produce canonical headings, single-newline separation throughout (including between chapters) and blank headings for unfilled chapters; the exact saved snapshot remains downloadable with `format=raw`.

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
