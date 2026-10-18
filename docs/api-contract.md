# InkReference API contract

All JSON endpoints are under `/api`; errors use `{detail: string | object}`. Tables are `works`, `characters`, `cliches`. All CSV values remain strings.

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
- `GET /api/export?mode=compatible|full` → ZIP attachment. Compatible contains v8's three named CSVs; full contains each business table CSV and manifest.json.
- `GET /api/export/{table}` → UTF-8 BOM CSV attachment.

Frontend use relative `/api`, native file input and downloadable response blobs. No real user data is bundled into the app; show an actionable empty state with import button. Backend module `inkreference.api:app`, core `inkreference.database` and `inkreference.csvio`.

## Reference workbench

All endpoints below use `/api/studio`. Lists return `{items:[...]}`. Timestamps are UTC; source spans use Unicode codepoints, never UTF-16, bytes or tokens. OpenAPI at `/docs` describes request schemas.

| Resource | Endpoints and behavior |
|---|---|
| Full texts | `GET/POST /documents`; POST multipart `file,title,encoding,work_id`. TXT/MD/SRT/DOCX/EPUB, 50 MiB. Originals remain immutable. |
| Versions | `GET /documents/{id}`, `GET /documents/{id}/versions/{vid}?include_text=false`; `POST /documents/{id}/versions` with `base_version_id,options,manual_text?`, 409 on a stale base. |
| Reading | Version suffix `/text?start=0&limit=20000&kind=cleaned|source` pages text; `/diff` returns the bounded diff; `/download` downloads TXT. `/documents/{id}/original/download` returns original bytes. |
| Models | `GET/POST /models`; `PUT/DELETE /models/{id}`. Session `api_key` is input only; `key_env` references an environment variable. `POST /models/{id}/test` sends a real request and records it. |
| Usage | `GET /usage?run_id=` returns `{items,summary}`. Counts have `actual`, `estimated` or `unknown` sources; costs require explicit prices/currency. |
| Skills | `GET/POST /skills`, `POST /skills/upload` multipart `file`, `GET /skills/{id}/versions/{version}`. JSON, SKILL.md or declarative ZIP; uploaded code is not executable. |
| Workflows | `GET/POST /workflows`, `GET /workflows/{id}/versions/{version}`. Definitions are immutable. `/layout` GET/PUT separately manages `{layout,expected_revision}`. |
| Analysis | `GET/POST /jobs`; POST `{document_id,version_id,profile_id,workflow_id,workflow_version,start:true}`. `GET /jobs/{id}` returns real progress, chunks, coverage, evidence, errors and configuration snapshots. |
| Control | `POST /jobs/{id}/cancel` or `/resume`. Resume skips completed chunks and uses the fixed versions. App restart never sends requests automatically. |
| Review | `PUT /jobs/{id}/results` with `{results,expected_revision}`; `POST /jobs/{id}/approve` with `{expected_revision}`. Approval commits reviewed candidates transactionally and idempotently. |
| Projects | `GET /projects/export` is the full restore-capable ZIP. `GET /projects/tables/export` contains a CSV for every business table. `POST /projects/restore` multipart `file` verifies the ZIP and creates an independent copy, returning `data_dir`. |

Default Skill result: `{summary:string,characters:[{name,description,evidence}],events:[{name,description,evidence}],cliches:[{name,description,evidence}]}`. Each evidence is a nonempty exact contiguous quote inside its chunk. The service adds absolute `start,end,chunk_id`. Invalid evidence fails the chunk instead of silently becoming a material record.

Run states are `queued,running,paused,failed,cancelled,needs_review,approved`; `progress` is completed chunks divided by total chunks (0–1). Cancelling takes effect at the current request boundary and does not imply a provider did not charge the in-flight request. Manual review is revision protected and cannot be overwritten by resuming that job.
