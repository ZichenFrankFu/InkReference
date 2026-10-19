import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";
import {
  ArrowDownToLine,
  ArrowRight,
  ArrowUpRight,
  BookOpen,
  Check,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  Database,
  FileCheck2,
  FileSpreadsheet,
  FolderInput,
  Layers3,
  Link2,
  LoaderCircle,
  PanelRightClose,
  Pencil,
  Plus,
  Search,
  Trash2,
  Upload,
  UsersRound,
  SlidersHorizontal,
  ArrowDownUp,
  FileText,
  Sparkles,
  X,
} from "lucide-react";
import {
  ApiError,
  download,
  jsonBody,
  request,
  type Item,
  type LibraryFormats,
  type Page,
  type Preview,
  type Relations,
  type Stats,
  type TableKey,
  type TableMeta,
} from "./api";
import "./library.css";
import CharacterSnapshots, { characterTemperaments } from "./CharacterSnapshots";
import { record, records, stringList, stringValue, type ReferenceRecord } from "./reference-types";
import { Busy, Modal, PageTitle } from "./ui";
const WorkReference = lazy(() => import("./WorkReference"));

const TABLES: {
  key: TableKey;
  label: string;
  singular: string;
  icon: typeof BookOpen;
}[] = [
  {
    key: "works",
    label: "作品库",
    singular: "作品",
    icon: BookOpen,
  },
  {
    key: "characters",
    label: "角色库",
    singular: "角色",
    icon: UsersRound,
  },
  {
    key: "cliches",
    label: "桥段库",
    singular: "桥段",
    icon: Layers3,
  },
];
const emptyStats: Stats = {
  works: 0,
  characters: 0,
  cliches: 0,
  work_cliches: 0,
};
const nf = new Intl.NumberFormat("zh-CN");
const labelFor = (key: string) =>
  TABLES.find((t) => t.key === key)?.label || ({ work_cliches: "作品桥段表", material_relations: "素材关系" } as Record<string, string>)[key] || key;
type CatalogItem = Item & { catalog_values?: Record<string, string>; computed?: { cliche_score?: number | null; missing_heat_count?: number; formula?: string } };
const valuesOf = (item: Item) => (item as CatalogItem).catalog_values || item.values;
const titleOf = (item: Item, table: TableKey) => {
  const values = valuesOf(item);
  return values[table === "works" ? "作品" : table === "characters" ? "角色名" : "cliché内容"] || (table === "characters" ? values["角色"] : "") || item.id;
};
const canonicalColumns: Record<TableKey, string[]> = {
  works: ["作品ID", "作品", "类别", "作者", "发布日期", "用户是否完整体验过", "剧情梗概", "讨论热度", "商业规模", "热度权重", "关联作品", "关联角色", "关联cliché"],
  characters: ["人物ID", "角色名", "别名", "角色定位", "性格基调", "视觉元素设计", "初始状态", "阶段状态", "关联作品", "关联cliché"],
  cliches: ["clichéID", "cliché内容", "cliché评分", "人设塑造", "剧情机制", "关联作品", "关联角色"],
};
const normalizeItem = (item: Item): Item => ({ ...item, values: valuesOf(item) });
const scoreOf = (item: Item) => { const raw = (item as CatalogItem).computed?.cliche_score ?? valuesOf(item)["cliché评分"]; return raw !== null && raw !== undefined && raw !== "" && Number.isFinite(Number(raw)) ? Number(raw).toFixed(1) : "—"; };
function jsonValue(value: string | undefined, fallback: unknown): unknown { try { return value ? JSON.parse(value) : fallback; } catch { return fallback; } }
function characterFromValues(values: Record<string, string>): ReferenceRecord { return { schema_version: 3, id: values["人物ID"] || "", name: values["角色名"] || values["角色"] || "", aliases: jsonValue(values["别名"], []), role_tag: values["角色定位"] || "", temperament: values["性格基调"] || "", visual_design: values["视觉元素设计"] || "", initial_state: jsonValue(values["初始状态"], { gender: null, age: "", identity: "", ability: "", personality: "", appearance: "", relationships: [], narrative_progress: 0, story_time: null }), snapshots: jsonValue(values["阶段状态"], []) }; }
function valuesFromCharacter(values: Record<string, string>, character: ReferenceRecord): Record<string, string> { return { ...values, "角色名": stringValue(character.name), "别名": JSON.stringify(Array.isArray(character.aliases) ? character.aliases : stringList(character.aliases)), "角色定位": stringValue(character.role_tag), "性格基调": stringValue(character.temperament), "视觉元素设计": stringValue(character.visual_design), "初始状态": JSON.stringify(record(character.initial_state)), "阶段状态": JSON.stringify(records(character.snapshots)) }; }
const isLong = (column: string) =>
  /概括|梗概|说明|内容|出现作品|桥段|能力|性格|身份|关系/.test(column);
const errorMessage = (e: unknown) =>
  e instanceof Error ? e.message : "操作未完成，请稍后重试。";
const encoded = (v: string) => encodeURIComponent(v);
const fieldLabel = (field: string) => ({ "用户是否完整体验过": "看过", "关联cliché": "关联桥段", "cliché内容": "桥段内容", "clichéID": "桥段 ID", "cliché评分": "Cliché 评分", "人物ID": "人物 ID", "两句话概括": "剧情梗概", "热度权重": "综合热度" } as Record<string, string>)[field] || field;
const hiddenColumns = new Set(["版本与原作关系", "剧情补充说明", "典型桥段（剧透）", "经典桥段（剧透）", "两句话概括", "cliché程度（%）", "cliché程度", "命中作品数", "命中热度合计"]);
const seenKey = "用户是否完整体验过";
const clicheTypes = [
  { key: "人设塑造", options: ["强大", "智谋", "利他", "共情", "利己", "执念", "反差", "宿命"] },
  { key: "剧情机制", options: ["信息反转", "机械降神", "顿悟突破", "代价交换"] },
] as const;
const isRelationColumn = (column: string) => /^关联/.test(column);
function HeatRangeField({ id, label, value, onChange }: { id: string; label: string; value: string; onChange: (value: string) => void }) {
  const parsed = jsonValue(value, null);
  const metadata = record(parsed);
  const lower = Object.keys(metadata).length ? metadata.min : value;
  const upper = Object.keys(metadata).length ? metadata.max : value;
  const update = (key: "min" | "max", next: string) => {
    const min = key === "min" ? next : lower, max = key === "max" ? next : upper;
    if (String(min ?? "") === "" && String(max ?? "") === "") onChange("");
    else onChange(JSON.stringify({ ...metadata, min: String(min ?? "") === "" ? null : Number(min), max: String(max ?? "") === "" ? null : Number(max) }));
  };
  return <div className="catalog-heat-input"><input id={id} type="number" min="1" max="10" step="0.1" aria-label={`${label}下限`} value={stringValue(lower)} onChange={event => update("min", event.target.value)} /><span>—</span><input type="number" min="1" max="10" step="0.1" aria-label={`${label}上限`} value={stringValue(upper)} onChange={event => update("max", event.target.value)} /><span>/ 10</span></div>;
}
function ClicheTypeTags({ values }: { values: Record<string, string> }) {
  return <span className="cliche-type-tags">{clicheTypes.map(group => values[group.key] ? <span key={group.key} className={`cliche-type-tag ${group.key === "剧情机制" ? "mechanism" : ""}`}>{group.key}·{values[group.key]}</span> : null)}</span>;
}
function ClicheTypeFields({ values, onChange }: { values: Record<string, string>; onChange: (key: string, value: string) => void }) {
  return <fieldset className="cliche-type-fields"><legend>类型</legend>{clicheTypes.map(group => <div className="cliche-type-group" key={group.key}>
    <div><span>{group.key}</span>{values[group.key] && <button className="icon-button" type="button" aria-label={`清除${group.key}`} onClick={() => onChange(group.key, "")}><X size={13} /></button>}</div>
    <div className="cliche-type-options" role="group" aria-label={group.key}>{group.options.map(option => <button type="button" key={option} aria-pressed={values[group.key] === option} className={values[group.key] === option ? "selected" : ""} onClick={() => onChange(group.key, values[group.key] === option ? "" : option)}>{option}</button>)}</div>
  </div>)}</fieldset>;
}
function arrayValue(value: string): unknown[] | null {
  if (!value.trim()) return [];
  try { const parsed: unknown = JSON.parse(value); return Array.isArray(parsed) ? parsed : null; } catch { return null; }
}
function linkedCount(value: string): number { return arrayValue(value)?.length || 0; }
function relationMetadata(value: unknown, prefix = ""): [string, string][] {
  if (!value || typeof value !== "object" || Array.isArray(value)) return [];
  return Object.entries(value as Record<string, unknown>).flatMap(([key, current]): [string, string][] => {
    if (["作品ID", "人物ID", "clichéID", "作品名", "关联方式", "关联内容"].includes(key)) return [];
    const label = prefix ? `${prefix} · ${key}` : key;
    if (current && typeof current === "object" && !Array.isArray(current)) return relationMetadata(current, label);
    return current !== null && current !== undefined && String(current) ? [[label, Array.isArray(current) ? current.filter(v => typeof v !== "object").join(" · ") : String(current)]] : [];
  });
}
function AttributeFields({ value, onChange, prefix = "关联属性" }: { value: Record<string, unknown>; onChange: (value: Record<string, unknown>) => void; prefix?: string }) {
  return <div className="association-attributes">{Object.entries(value).map(([key, current]) => {
    const label = `${prefix} ${key}`;
    if (current && typeof current === "object" && !Array.isArray(current)) return <details className="association-extra" key={key}><summary>{key}</summary><AttributeFields value={current as Record<string, unknown>} prefix={label} onChange={next => onChange({ ...value, [key]: next })} /></details>;
    if (Array.isArray(current)) return <details className="association-extra" key={key}><summary>{key}</summary><div className="association-attributes">{current.map((entry, index) => <input key={index} aria-label={`${label} ${index + 1}`} value={String(entry ?? "")} onChange={e => onChange({ ...value, [key]: current.map((v, i) => i === index ? e.target.value : v) })} />)}</div></details>;
    return <label key={key}><span>{key}</span><input aria-label={label} value={String(current ?? "")} onChange={e => onChange({ ...value, [key]: typeof current === "number" && e.target.value !== "" && Number.isFinite(Number(e.target.value)) ? Number(e.target.value) : e.target.value })} /></label>;
  })}</div>;
}
function AssociationField({ column, value, table, onChange }: { column: string; value: string; table: TableKey; onChange: (value: string) => void }) {
  const items = arrayValue(value);
  const isWorks = column === "关联作品";
  const objectEntries = isWorks;
  const idKey = isWorks ? "作品ID" : column === "关联角色" ? "人物ID" : "clichéID";
  const semanticKey = table === "cliches" ? "关联内容" : "关联方式";
  const target: TableKey = isWorks ? "works" : column === "关联角色" ? "characters" : "cliches";
  const choiceId = useId();
  const [open, setOpen] = useState(false), [query, setQuery] = useState("");
  const [choices, setChoices] = useState<Item[]>([]), [loadingChoices, setLoadingChoices] = useState(false);
  const [choiceError, setChoiceError] = useState("");
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => { setLoadingChoices(true); setChoiceError(""); void request<Page>(`/records/${target}?${new URLSearchParams({ q: query, limit: "100" })}`, { signal: controller.signal }).then(page => { if (!controller.signal.aborted) setChoices(page.items); }).catch(cause => { if (!controller.signal.aborted) setChoiceError(errorMessage(cause)); }).finally(() => { if (!controller.signal.aborted) setLoadingChoices(false); }); }, query ? 250 : 0);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [open, target, query]);
  const update = (index: number, next: unknown) => onChange(JSON.stringify(items?.map((item, i) => i === index ? next : item) || []));
  return <details className="association-field" onToggle={event => setOpen(event.currentTarget.open)}><summary><span>{fieldLabel(column)}</span><span className="association-count">{items?.length ?? "JSON"}</span><ChevronRight size={15} /></summary><div className="association-editor"><datalist id={choiceId}>{choices.map(choice => <option key={choice.id} value={choice.id}>{titleOf(choice, target)}</option>)}</datalist>{loadingChoices && <Busy label="正在查找关联素材" compact />}{choiceError && <div className="notice error" role="alert">{choiceError}</div>}
    {items === null ? <textarea aria-label={fieldLabel(column)} rows={5} value={value} onChange={e => onChange(e.target.value)} /> : <>
      {items.map((entry, index) => { const obj = entry && typeof entry === "object" && !Array.isArray(entry) ? entry as Record<string, unknown> : null; return <div className="association-row" key={index}>
        <div className="association-row-heading"><input aria-label={`${fieldLabel(column)} ${index + 1} ID`} list={choiceId} placeholder={idKey} value={String(obj?.[idKey] ?? entry ?? "")} onChange={e => { setQuery(e.target.value); update(index, obj ? { ...obj, [idKey]: e.target.value } : objectEntries ? { [idKey]: e.target.value } : e.target.value); }} /><button type="button" className="icon-button danger" aria-label={`删除第 ${index + 1} 项关联`} onClick={() => onChange(JSON.stringify(items.filter((_, i) => i !== index)))}><X size={15} /></button></div>
        {isWorks && <input aria-label={`${fieldLabel(column)} ${index + 1} ${semanticKey}`} placeholder={semanticKey} value={String(obj?.[semanticKey] ?? "")} onChange={e => update(index, { ...(obj || { [idKey]: String(entry || "") }), [semanticKey]: e.target.value })} />}
        {obj && Object.keys(obj).some(key => ![idKey, semanticKey].includes(key)) && <AttributeFields value={Object.fromEntries(Object.entries(obj).filter(([key]) => ![idKey, semanticKey].includes(key)))} onChange={extra => update(index, { [idKey]: obj[idKey], ...(obj[semanticKey] !== undefined ? { [semanticKey]: obj[semanticKey] } : {}), ...extra })} />}

      </div>; })}
      <button type="button" className="button secondary association-add" onClick={() => onChange(JSON.stringify([...items, objectEntries ? { [idKey]: "" } : ""]))}><Plus size={14} />添加关联</button>
    </>}
  </div></details>;
}

function Spinner({ text = "正在加载…" }: { text?: string }) {
  return <><Busy label={text} /><span className="loading-inline" role="status">{text}</span></>;
}

const exchangeFormats = [
  { id: "db", title: "SQLite 数据库", suffix: ".db", description: "全部数据表与关联", icon: Database },
  { id: "csv", title: "CSV 数据包", suffix: ".zip", description: "作品.csv · 角色.csv · cliche.csv", icon: FolderInput },
  { id: "xlsx", title: "Excel 工作簿", suffix: ".xlsx", description: "作品 · 角色 · cliche 工作表", icon: FileSpreadsheet },
] as const;

function ImportDialog({
  onClose,
  onComplete,
}: {
  onClose: () => void;
  onComplete: (message: string) => void;
}) {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [formats, setFormats] = useState<LibraryFormats | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    void request<LibraryFormats>("/library/formats", { signal: controller.signal }).then(setFormats).catch(() => undefined);
    return () => controller.abort();
  }, []);
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState<"" | "preview" | "commit">("");
  const [error, setError] = useState("");
  const [policy, setPolicy] = useState<"skip" | "update" | "error">("skip");
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const inspect = async (selected: File[]) => {
    setFiles(selected);
    setPreview(null);
    setError("");
    if (!selected.length) return;
    const unsupported = selected.filter((file) => !/\.(db|sqlite3|zip|csv|xlsx|json)$/i.test(file.name));
    if (unsupported.length) {
      setError(`不支持的文件：${unsupported.map(file => file.name).join("、")}。请选择 .db 数据库、完整 CSV 数据包或 .xlsx 工作簿。`);
      return;
    }
    setBusy("preview");
    try {
      const form = new FormData();
      selected.forEach(file => form.append("files", file));
      setPreview(await request<Preview>("/library/import/preview", { method: "POST", body: form }));
    } catch (e) { setError(errorMessage(e)); }
    finally { setBusy(""); if (input.current) input.current.value = ""; }
  };
  const addFiles = (incoming: FileList | File[]) => {
    if (!incoming.length || busy) return;
    const merged = new Map(files.map(file => [file.name, file]));
    [...incoming].forEach(file => merged.set(file.name, file));
    void inspect([...merged.values()]);
  };
  const commit = async () => {
    if (!preview || preview.errors.length) return;
    setBusy("commit"); setError("");
    try {
      const result = await request<{ imported: number; updated: number; skipped: number }>("/library/import/commit", {
        method: "POST", ...jsonBody({ batch_id: preview.batch_id, policy }),
      });
      onComplete(`导入完成：新增 ${nf.format(result.imported)} 条，更新 ${nf.format(result.updated)} 条，跳过 ${nf.format(result.skipped)} 条。`);
    } catch (e) { setError(errorMessage(e)); setBusy(""); }
  };
  return <Modal title="导入素材库" onClose={onClose} wide busy={!!busy}>
    <div className="modal-body library-exchange">
      <div className="exchange-format-guide" aria-label="支持的导入格式">
        {exchangeFormats.map(format => <div key={format.id} className={format.id === "db" ? "recommended" : ""}>
          <format.icon size={18} /><strong>{format.suffix === ".zip" ? "CSV / ZIP" : format.suffix.toUpperCase()}{format.id === "db" && <span>推荐</span>}</strong>
          <small>{format.id === "db" ? "选择一个数据库文件" : format.id === "csv" ? "作品 · 角色 · cliche" : "选择包含全部数据表的工作簿"}</small>
        </div>)}
      </div>
      <section className="exchange-schema-tables" aria-label="必要数据表">
        {(formats?.tables?.filter(t => ["works", "characters", "cliches"].includes(t.table)) || [
          { table: "works", label: "作品", filename: "作品.csv", columns: ["作品ID", "作品", "关联作品"] },
          { table: "characters", label: "角色", filename: "角色.csv", columns: ["人物ID", "角色名", "关联作品"] },
          { table: "cliches", label: "桥段", filename: "cliche.csv", columns: ["clichéID", "cliché内容", "关联作品"] },
        ]).map(t => <div key={t.table}><CheckCircle2 size={16} /><span><strong>{t.label}</strong><small>{t.columns.slice(0, 3).join(" · ")}</small></span><code>{t.filename}</code></div>)}
      </section>
      <input type="file" accept=".db,.sqlite3,.zip,.csv,.xlsx,.json" multiple ref={input} className="visually-hidden" aria-label="选择素材库文件" onChange={event => { const incoming = [...(event.target.files || [])]; event.target.value = ""; addFiles(incoming); }} />
      <button className={`dropzone exchange-dropzone ${dragging ? "dragging" : ""}`} disabled={!!busy} onClick={() => input.current?.click()} onDragOver={event => { event.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={event => { event.preventDefault(); setDragging(false); addFiles(event.dataTransfer.files); }}>
        <span className="drop-icon"><Upload size={23} /></span>
        <strong>{busy === "preview" ? "正在检查数据表与关联…" : files.length ? "补充文件" : "点击选择或拖入文件"}</strong>
        <span>{files.length ? "添加文件" : ".db · .xlsx · 三个 CSV"}</span>
      </button>
      {!!files.length && <div className="exchange-files">
        <div className="section-caption"><span>已选择 {files.length} 个文件</span><button className="text-button" disabled={!!busy} onClick={() => { setFiles([]); setPreview(null); setError(""); }}>清空重选</button></div>
        <ul>{files.map(file => <li key={file.name}><FileSpreadsheet size={14} /><span title={file.name}>{file.name}</span><small>{nf.format(Math.ceil(file.size / 1024))} KB</small><button className="icon-button" disabled={!!busy} aria-label={`移除 ${file.name}`} onClick={() => void inspect(files.filter(selected => selected !== file))}><X size={14} /></button></li>)}</ul>
      </div>}
      {busy === "preview" && <div className="exchange-progress"><Spinner text="正在检查全部数据表…" /></div>}
      {error && <div className="notice error" role="alert"><CircleAlert size={17} /><span>{error}</span></div>}
      {preview && <>
        {!!preview.required_tables?.length && <section className="exchange-checklist" aria-label="必要数据表检查">
          <div className="section-caption"><span>数据表完整性</span><span>{preview.required_tables.filter(table => table.present).length} / {preview.required_tables.length}</span></div>
          <ul>{preview.required_tables.map(table => <li key={table.table} className={table.present ? "present" : "missing"}>
            {table.present ? <CheckCircle2 size={16} /> : <CircleAlert size={16} />}<span><strong>{table.label || labelFor(table.table)}</strong><small>{table.filename}</small></span><em>{table.present ? "已就绪" : "缺少文件 / 工作表"}</em>
          </li>)}</ul>
        </section>}
        {preview.errors.length > 0 && <div className="notice error" role="alert"><CircleAlert size={18} /><div><strong>暂时无法导入</strong><ul>{preview.errors.map((message, index) => <li key={index}>{message}</li>)}</ul></div></div>}
        {preview.warnings.length > 0 && <div className="notice warning"><CircleAlert size={18} /><div><strong>请核对以下内容</strong><ul>{preview.warnings.map((message, index) => <li key={index}>{message}</li>)}</ul></div></div>}
        {!!preview.guidance?.length && <div className="exchange-guidance">{preview.guidance.map((message, index) => <p key={index}>{message}</p>)}</div>}
        {!!preview.tables.length && <><div className="section-caption"><span>导入预览</span><span>{preview.format?.toUpperCase()}</span></div><div className="preview-table-wrap"><table className="preview-table"><thead><tr><th>数据表 / 来源</th><th>记录</th><th>新增</th><th>已存在</th></tr></thead><tbody>{preview.tables.map((table, index) => <tr key={`${table.filename}-${index}`}><td><strong>{labelFor(table.table)}</strong><small>{table.filename}</small></td><td>{nf.format(table.rows)}</td><td>{nf.format(table.new)}</td><td>{nf.format(table.existing)}</td></tr>)}</tbody></table></div></>}
        {!preview.errors.length && <div className="notice success"><FileCheck2 size={18} /><span>全部数据表与关联检查通过。</span></div>}
        <fieldset className="policy-options" disabled={!!busy || !!preview.errors.length}>
          <legend>遇到相同 ID 时</legend>
          {([ ["skip", "保留已有记录", "跳过相同 ID"], ["update", "更新已有记录", "使用导入内容更新相同 ID"], ["error", "中止本次导入", "发现重复时整批不写入"] ] as const).map(([value, label]) => <label className={`radio-card ${policy === value ? "checked" : ""}`} key={value}><input type="radio" name="policy" checked={policy === value} onChange={() => setPolicy(value)} /><span><strong>{label}</strong></span></label>)}
        </fieldset>
      </>}

    </div>
    <div className="modal-footer"><button className="button secondary" disabled={!!busy} onClick={onClose}>取消</button><button className="button primary" disabled={!!busy || !preview || !!preview.errors.length} onClick={() => void commit()}>{busy ? <Spinner text={busy === "commit" ? "正在导入…" : "正在检查…"} /> : <><ArrowRight size={16} />确认导入</>}</button></div>
  </Modal>;
}

function ExportDialog({ onClose, notify }: { onClose: () => void; notify: (message: string) => void }) {
  const [format, setFormat] = useState<"db" | "csv" | "xlsx">("db");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const exportFile = async () => {
    setBusy(true); setError("");
    try {
      await download(`/library/export?format=${format}`, `InkReference${format === "csv" ? ".zip" : `.${format}`}`);
      notify("完整素材库已导出。"); onClose();
    } catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  };
  return <Modal title="导出素材库" onClose={onClose} busy={busy}>
    <div className="modal-body library-exchange"><fieldset className="exchange-export-formats" disabled={busy}><legend className="visually-hidden">文件格式</legend>{exchangeFormats.map(option => <label className={`exchange-export-option ${format === option.id ? "selected" : ""}`} key={option.id}><input type="radio" name="export-format" value={option.id} checked={format === option.id} onChange={() => setFormat(option.id)} /><span className="option-icon"><option.icon size={22} /></span><span className="exchange-format-text"><strong>{option.title}<em>{option.suffix}</em>{option.id === "db" && <small className="exchange-recommended">推荐</small>}</strong><span>{option.description}</span></span>{format === option.id && <CheckCircle2 size={19} />}</label>)}</fieldset>{error && <div className="notice error" role="alert"><CircleAlert size={17} />{error}</div>}</div>
    <div className="modal-footer"><button className="button secondary" disabled={busy} onClick={onClose}>取消</button><button className="button primary" disabled={busy} onClick={() => void exportFile()}>{busy ? <Spinner text="正在生成导出文件…" /> : <><ArrowDownToLine size={16} />导出{format === "csv" ? " CSV 数据包" : ` .${format}`}</>}</button></div>
  </Modal>;
}

type Selection = { table: TableKey; id?: string };
type CharacterContext = { character_id: string; work_id: string; document_id: string; extracted_character_id: string; revision: number; reference_revision: number | null; character: ReferenceRecord; characters: ReferenceRecord[]; events: ReferenceRecord[] };
type CharacterContexts = { character_id: string; catalog_revision: string; contexts: CharacterContext[] };
function DetailPanel({
  selection,
  meta,
  onClose,
  onSaved,
  onDeleted,
  onNavigate,
  onDirty,
  refreshRecord,
}: {
  selection: Selection;
  meta: TableMeta | undefined;
  onClose: () => void;
  onSaved: (item: Item) => void;
  onDeleted: () => void;
  onNavigate: (table: TableKey, id: string) => void;
  onDirty: (dirty: boolean) => void;
  refreshRecord: number;
}) {
  const { table, id } = selection;
  const [item, setItem] = useState<Item | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [baseline, setBaseline] = useState<Record<string, string>>({});
  const [characterContexts, setCharacterContexts] = useState<CharacterContexts | null>(null);
  const [contextWorkId, setContextWorkId] = useState("");
  const [characterChoices, setCharacterChoices] = useState<ReferenceRecord[]>([]);
  const characterContext = characterContexts?.contexts.find(context => context.work_id === contextWorkId);
  const [relations, setRelations] = useState<Relations>({
    works: [],
    characters: [],
    cliches: [],
  });
  const [loading, setLoading] = useState(!!id);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [conflict, setConflict] = useState(false);
  const [tab, setTab] = useState<"details" | "relations" | "reference">("details");
  const [editingRelations, setEditingRelations] = useState(!id);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteError, setDeleteError] = useState("");
  const [cascade, setCascade] = useState(false);
  const title = item
    ? titleOf(item, table)
    : `新增${TABLES.find((t) => t.key === table)?.singular}`;
  const dirty = id
    ? JSON.stringify(draft) !== JSON.stringify(baseline)
    : Object.values(draft).some(Boolean);
  const load = useCallback(async () => {
    setLoading(!!id);
    setError("");
    setConflict(false);
    if (!id) {
      setItem(null);
      setDraft({});
      setBaseline({});
      setCharacterContexts(null);
      setContextWorkId("");
      setRelations({ works: [], characters: [], cliches: [] });
      return;
    }
    try {
      const [record, links, contexts] = await Promise.all([
        request<Item>(`/records/${table}/${encoded(id)}`),
        request<Relations>(`/records/${table}/${encoded(id)}/relations`),
        table === "characters" ? request<CharacterContexts>(`/library/characters/${encoded(id)}/contexts`) : Promise.resolve(null),
      ]);
      setItem(normalizeItem(record));
      const context = contexts?.contexts[0];
      const next = context ? valuesFromCharacter(valuesOf(record), context.character) : valuesOf(record);
      setDraft(next);
      setBaseline(next);
      setContextWorkId(context?.work_id || "");
      setCharacterContexts(contexts);
      setRelations(links);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }, [table, id]);
  useEffect(() => {
    void load();
    setTab("details");
    setEditingRelations(!id);
    setConfirmDelete(false);
  }, [load, refreshRecord]);
  useEffect(() => {
    onDirty(dirty);
  }, [dirty, onDirty]);
  useEffect(() => {
    if (table !== "characters") return;
    const controller = new AbortController();
    void request<Page>("/records/characters?limit=200", { signal: controller.signal }).then(page => { if (!controller.signal.aborted) setCharacterChoices(page.items.map(item => ({ id: item.id, name: titleOf(item, "characters") }))); }).catch(() => undefined);
    return () => controller.abort();
  }, [table, id, refreshRecord]);
  const searchCharacters = useCallback(async (query: string) => {
    const page = await request<Page>(`/records/characters?${new URLSearchParams({ q: query, limit: "100" })}`);
    return page.items.map(character => ({ id: character.id, name: titleOf(character, "characters") }));
  }, []);
  const chooseCharacterContext = (workId: string) => {
    if (dirty && !window.confirm("有未保存的修改，是否放弃？")) return;
    const context = characterContexts?.contexts.find(candidate => candidate.work_id === workId);
    const next = context ? valuesFromCharacter(item?.values || {}, context.character) : item?.values || {};
    setContextWorkId(workId); setDraft(next); setBaseline(next); setEditingRelations(false); setError(""); setConflict(false);
  };
  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    setSaving(true);
    setError("");
    setConflict(false);
    try {
      if (table === "characters" && id && characterContext) {
        await request(`/library/characters/${encoded(id)}/contexts/${encoded(characterContext.work_id)}`, { method: "PUT", ...jsonBody({ character: { ...characterContext.character, ...characterFromValues(draft), id: characterContext.extracted_character_id }, expected_revision: characterContext.revision, expected_catalog_revision: characterContexts?.catalog_revision, expected_reference_revision: characterContext.reference_revision }) });
        const [updated, contexts] = await Promise.all([request<Item>(`/records/characters/${encoded(id)}`), request<CharacterContexts>(`/library/characters/${encoded(id)}/contexts`)]);
        const current = contexts.contexts.find(context => context.work_id === contextWorkId);
        const next = current ? valuesFromCharacter(valuesOf(updated), current.character) : valuesOf(updated);
        setItem(normalizeItem(updated)); setCharacterContexts(contexts); setDraft(next); setBaseline(next); onDirty(false); onSaved(updated);
        window.dispatchEvent(new Event("inkreference:reference-refresh"));
        return;
      }
      const record = await request<Item>(
        id ? `/records/${table}/${encoded(id)}` : `/records/${table}`,
        {
          method: id ? "PATCH" : "POST",
          ...jsonBody(
            id
              ? { values: Object.fromEntries(Object.entries(draft).filter(([key]) => !["热度权重", "cliché评分", ...(table === "characters" && characterContexts?.contexts.length ? ["初始状态", "阶段状态"] : [])].includes(key))), expected_revision: item?.revision }
              : { values: Object.fromEntries(Object.entries(draft).filter(([key]) => !["热度权重", "cliché评分", ...(table === "characters" && characterContexts?.contexts.length ? ["初始状态", "阶段状态"] : [])].includes(key))) },
          ),
        },
      );
      setItem(normalizeItem(record));
      setDraft(valuesOf(record));
      setBaseline(valuesOf(record));
      if (table === "characters" && characterContexts?.contexts.length) setCharacterContexts(await request<CharacterContexts>(`/library/characters/${encoded(record.id)}/contexts`));
      onDirty(false);
      onSaved(record);
      const links = await request<Relations>(
        `/records/${table}/${encoded(record.id)}/relations`,
      );
      setRelations(links);
    } catch (e) {
      setError(errorMessage(e));
      setConflict(e instanceof ApiError && e.status === 409);
    } finally {
      setSaving(false);
    }
  };
  const relationGroups = TABLES.filter(group => table !== "cliches" || group.key !== "cliches");
  const relatedTotal = relationGroups.reduce(
    (total, t) => total + (relations[t.key]?.length || 0),
    0,
  );
  const needsCascade = relatedTotal > 0;
  const remove = async () => {
    if (!item) return;
    setDeleteBusy(true);
    setDeleteError("");
    try {
      await request(
        `/records/${table}/${encoded(item.id)}?expected_revision=${item.revision}&cascade=${needsCascade ? cascade : false}`,
        { method: "DELETE" },
      );
      onDirty(false);
      onDeleted();
    } catch (e) {
      setDeleteError(errorMessage(e));
    } finally {
      setDeleteBusy(false);
    }
  };
  const columns = canonicalColumns[table].filter(column => !hiddenColumns.has(column));
  return (
    <aside className="detail-panel" aria-label={`${labelFor(table)}详情`}>
      {(loading || saving) && <Busy label={saving ? "正在保存素材" : "正在读取素材"} />}
      <div className="detail-heading">
        <div>
          <span className="eyebrow">{id ? "素材详情" : "创建素材"}</span>
          <h2 title={title}>{title}</h2>
          <span className="record-id">{item?.id || "新记录"}</span>
        </div>
        <button className="icon-button" onClick={onClose} aria-label="收起详情">
          <PanelRightClose size={20} />
        </button>
      </div>
      {
        <div className="detail-tabs" role="tablist" aria-label="详情视图">
          <button
            role="tab"
            aria-selected={tab === "details"}
            className={tab === "details" ? "active" : ""}
            onClick={() => setTab("details")}
          >
            信息
          </button>
          <button
            role="tab"
            aria-selected={tab === "relations"}
            className={tab === "relations" ? "active" : ""}
            onClick={() => setTab("relations")}
          >
            关联 <span>{relatedTotal}</span>
          </button>
          {table === "works" && id && <button role="tab" aria-selected={tab === "reference"} className={tab === "reference" ? "active" : ""} onClick={() => setTab("reference")}>文本与分析</button>}
        </div>
      }
      {loading ? (
        <div className="panel-loading">
          <Spinner />
        </div>
      ) : (
        <div className="detail-form">
                {error && (
                  <div className="notice error detail-error" role="alert">
                    <CircleAlert size={17} />
                    <div>
                      {error}
                      {conflict && (
                        <>
                          <p>
                            此记录可能已在其他窗口更新。请复制需要保留的内容，再重新载入。
                          </p>
                          <button
                            type="button"
                            className="text-button"
                            onClick={() => void load()}
                          >
                            重新载入最新记录
                          </button>
                        </>
                      )}
                    </div>
                  </div>
                )}
          <form id="library-record-form" className="detail-scroll" hidden={tab !== "details"} onSubmit={(e) => void save(e)}>
                {table === "cliches" && <ClicheTypeFields values={draft} onChange={(key, value) => setDraft(d => ({ ...d, [key]: value }))} />}
                {table === "characters" ? <>{!!characterContexts?.contexts.length && <label className="catalog-character-context"><span>作品状态</span><select aria-label="人物作品状态" value={contextWorkId} onChange={event => chooseCharacterContext(event.target.value)}><option value="">人物资料</option>{characterContexts.contexts.map(context => <option key={context.work_id} value={context.work_id}>{relations.works.find(work => work.id === context.work_id)?.values["作品"] || context.work_id}</option>)}</select></label>}<div className="field"><label htmlFor="catalog-character-id">人物 ID</label><input id="catalog-character-id" value={draft["人物ID"] || ""} readOnly={!!id} onChange={event => setDraft(current => ({ ...current, "人物ID": event.target.value }))} /></div><CharacterSnapshots value={characterFromValues(draft)} characters={characterContext?.characters || characterChoices} events={characterContext?.events || []} identityOnly={!!characterContexts?.contexts.length && !contextWorkId} onCharacterSearch={characterContext ? undefined : searchCharacters} onChange={character => setDraft(current => valuesFromCharacter(current, character))} /></> : columns.filter(column => !isRelationColumn(column) && !(table === "cliches" && clicheTypes.some(group => group.key === column))).map((column) => {
                  const immutable = (!!id && column === meta?.id_field) || ["cliché评分", "热度权重"].includes(column);
                  const fieldId = `field-${column}`;
                  return <div className={`field ${isLong(column) ? "field-long" : ""}`} key={column}>
                    <label htmlFor={fieldId}>{fieldLabel(column)}{immutable && <span aria-hidden="true">ID</span>}</label>
                    {column === "cliché评分" ? <div className="catalog-score" title={item ? (item as CatalogItem).computed?.formula : undefined}><strong>{item ? scoreOf(item) : "—"}</strong><span>/ 100</span>{!!(item as CatalogItem | null)?.computed?.missing_heat_count && <CircleAlert size={15} className="catalog-score-warning" aria-label="关联作品热度待补充" />}</div> : ["讨论热度", "商业规模"].includes(column) ? <HeatRangeField id={fieldId} label={fieldLabel(column)} value={draft[column] || ""} onChange={value => setDraft(current => ({ ...current, [column]: value }))} /> : column === seenKey ? <select id={fieldId} value={draft[column] || ""} onChange={e => setDraft(d => ({ ...d, [column]: e.target.value }))}>
                      <option value={!["是", "否"].includes(draft[column] || "") ? draft[column] || "" : ""}>未记录</option><option value="是">是</option><option value="否">否</option>
                    </select> : isLong(column) ? <textarea id={fieldId} rows={3} value={draft[column] || ""} onChange={e => setDraft(d => ({ ...d, [column]: e.target.value }))} readOnly={immutable} /> : <input id={fieldId} value={draft[column] || ""} onChange={e => setDraft(d => ({ ...d, [column]: e.target.value }))} readOnly={immutable} />}
                  </div>;
                })}
                {!columns.length && <p className="muted">正在读取数据字段…</p>}
          </form>
          {tab === "reference" && id && <Suspense fallback={<div className="panel-loading"><Busy label="正在读取作品分析" /></div>}><WorkReference workId={id} /></Suspense>}
          <div className="detail-scroll relations-list" hidden={tab !== "relations"}>
            <div className="relations-toolbar"><h3>关联</h3>{id && !contextWorkId && <button type="button" className="button secondary" aria-pressed={editingRelations} onClick={() => setEditingRelations(value => !value)}>{editingRelations ? <BookOpen size={14} /> : <Pencil size={14} />}{editingRelations ? "查看关联" : "编辑关联"}</button>}</div>
            {editingRelations ? <div className="relations-editor">{columns.filter(isRelationColumn).map(column => <AssociationField key={column} column={column} value={draft[column] || ""} table={table} onChange={value => setDraft(d => ({ ...d, [column]: value }))} />)}</div> : relationGroups.map((group) => (
                <section key={group.key}>
                  <h3>
                    <group.icon size={16} />
                    {group.label}
                    <span>{relations[group.key]?.length || 0}</span>
                  </h3>
                  {relations[group.key]?.length ? (
                    relations[group.key].map((link) => (
                      <button
                        key={link.id}
                        className="relation-card"
                        type="button"
                        onClick={() => onNavigate(group.key, link.id)}
                      >
                        <span>
                          <strong>{titleOf(link, group.key)}</strong>
                          <small>
                            {link.id}

                          </small>
                          <span className="relation-semantics">
                            {(link.relationships?.length ? link.relationships : link.relationship ? [link.relationship] : []).map((relationship, index) => <span className="relation-meaning" key={`${relationship.id}-${index}`}>
                              <span className="relation-type">{relationship.label === "使用桥段" ? "剧情关联" : relationship.label || relationship.type || "关联"}</span>
                              {relationship.description && relationship.description !== relationship.label && <span className="relation-description">{relationship.description}</span>}
                              {relationMetadata((relationship as typeof relationship & { metadata?: unknown }).metadata).map(([label, value]) => <span className="relation-attribute" key={label}><small>{label}</small>{value}</span>)}
                            </span>)}
                            {!link.relationship && !link.relationships?.length && <span className="relation-description">{link.values["关联说明"] || link.values["关系说明"] || "关联"}</span>}
                          </span>
                        </span>
                        <ArrowUpRight size={16} />
                      </button>
                    ))
                  ) : (
                    <p className="relation-empty">暂无关联{group.singular}</p>
                  )}
                </section>
              ))}
            </div>
              <div className="detail-footer" hidden={tab === "reference" && !dirty}>
                <div>
                  {id && (
                    <button
                      type="button"
                      className="icon-button danger"
                      onClick={() => {
                        setConfirmDelete(true);
                        setCascade(false);
                        setDeleteError("");
                      }}
                      aria-label="删除当前素材"
                      title="删除素材"
                    >
                      <Trash2 size={17} />
                    </button>
                  )}
                  {dirty ? (
                    <span className="unsaved">
                      <span />
                      未保存
                    </span>
                  ) : (
                    item && (
                      <span className="saved-status">
                        <Check size={14} />
                        已保存 · v{item.revision}
                      </span>
                    )
                  )}
                </div>
                <button
                  className="button primary"
                  type="submit"
                  form="library-record-form"
                  disabled={saving || (!dirty && !!id) || !columns.length}
                >
                  {saving ? (
                    <Spinner text="保存中…" />
                  ) : (
                    <>
                      <Check size={16} />
                      {id ? "保存修改" : "创建素材"}
                    </>
                  )}
                </button>
              </div>
        </div>
      )}
      {confirmDelete && (
        <Modal
          title={`删除${TABLES.find((t) => t.key === table)?.singular}`}
          onClose={() => setConfirmDelete(false)}
          busy={deleteBusy}
        >
          <div className="modal-body">
            <div className="delete-object">
              <Trash2 size={21} />
              <strong>{title}</strong>
              <span>{id}</span>
            </div>
            <p>删除后无法直接恢复。建议先导出需要保留的数据。</p>
            {relatedTotal > 0 && <div className="notice warning"><CircleAlert size={18} /><span>将解除与 <strong>{relatedTotal} 条素材</strong> 的关联。关联素材保留。</span></div>}
            {needsCascade && (
              <label className="check-line">
                <input
                  type="checkbox"
                  checked={cascade}
                  onChange={(e) => setCascade(e.target.checked)}
                />
                同时解除关联
              </label>
            )}
            {deleteError && (
              <div className="notice error" role="alert">
                {deleteError}
              </div>
            )}
          </div>
          <div className="modal-footer">
            <button
              className="button secondary"
              disabled={deleteBusy}
              onClick={() => setConfirmDelete(false)}
            >
              取消
            </button>
            <button
              className="button destructive"
              disabled={deleteBusy || (needsCascade && !cascade)}
              onClick={() => void remove()}
            >
              {deleteBusy ? <Spinner text="删除中…" /> : "确认删除"}
            </button>
          </div>
        </Modal>
      )}
    </aside>
  );
}

type LibraryFacets = { categories?: string[]; roles?: string[]; character_traits?: string[]; plot_mechanisms?: string[]; works?: { id: string; title: string }[] };
type LibraryPage = Page & { facets?: LibraryFacets };
function RecordTable({ table, items, selectedId, onSelect, onToggleSeen, seenBusy }: {
  table: TableKey; items: Item[]; selectedId?: string; onSelect: (item: Item) => void;
  onToggleSeen: (item: Item) => void; seenBusy: string;
}) {
  const headings = table === "works" ? ["作品", "类别", "作者", "年份", "看过"] : table === "characters" ? ["角色", "角色定位", "性格基调", "关联作品"] : ["桥段内容", "Cliché 评分"];
  return <div className="record-table-wrap"><table className={`record-table table-${table}`}><thead><tr>{headings.map((heading, i) => <th key={heading} className={table === "works" && (i === 2 || i === 3) ? "optional-column" : ""}>{heading}</th>)}<th className="row-action"><span className="visually-hidden">操作</span></th></tr></thead><tbody>{items.map(item => <tr key={item.id} className={item.id === selectedId ? "selected" : ""}>
    <td><button className={`record-title ${table === "cliches" ? "cliche-title" : ""}`} onClick={() => onSelect(item)}><span><strong>{titleOf(item, table)}</strong><small>{item.id}</small>{table === "cliches" && <ClicheTypeTags values={item.values} />}</span></button></td>
    {table === "works" ? <><td><span className="tag">{item.values["类别"] || "—"}</span></td><td className="optional-column">{item.values["作者"] || "—"}</td><td className="optional-column date-cell">{item.values["发布日期"] || "—"}</td><td><button className={`seen-toggle ${item.values[seenKey] === "是" ? "seen" : ""} ${!["是", "否"].includes(item.values[seenKey]) ? "unknown" : ""}`} onClick={() => onToggleSeen(item)} disabled={seenBusy === item.id} aria-busy={seenBusy === item.id} aria-label={`${titleOf(item, table)} 看过：${["是", "否"].includes(item.values[seenKey]) ? item.values[seenKey] : "未记录"}`} aria-pressed={item.values[seenKey] === "是"}>{seenBusy === item.id ? <LoaderCircle size={13} className="spin" /> : item.values[seenKey] === "是" ? <Check size={13} /> : null}{["是", "否"].includes(item.values[seenKey]) ? item.values[seenKey] : "—"}</button></td></> : table === "characters" ? <><td><span className="tag neutral">{item.values["角色定位"] || "—"}</span></td><td><span className="cell-truncate" title={item.values["性格基调"]}>{item.values["性格基调"] || "—"}</span></td><td><span className="link-count"><Link2 size={13} />{linkedCount(item.values["关联作品"] || "[]")}</span></td></> : <><td className="date-cell"><span className="cliche-score-cell">{scoreOf(item)}</span></td></>}
    <td className="row-action"><button className="icon-button" aria-label={`查看${titleOf(item, table)}`} onClick={() => onSelect(item)}><ChevronRight size={17} /></button></td>
  </tr>)}</tbody></table></div>;
}

export default function Library() {
  const [table, setTable] = useState<TableKey>("works");
  const [metas, setMetas] = useState<TableMeta[]>([]);
  const [stats, setStats] = useState<Stats>(emptyStats);

  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [page, setPage] = useState<LibraryPage>({
    items: [],
    total: 0,
    offset: 0,
    limit: 30,
  });
  const [offset, setOffset] = useState(0);
  const [limit, setLimit] = useState(30);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [dirty, setDirty] = useState(false);
  const [modal, setModal] = useState<"import" | "export" | null>(
    null,
  );
  const [toast, setToast] = useState("");
  const [category, setCategory] = useState("");
  const [seen, setSeen] = useState("");
  const [hasText, setHasText] = useState("");
  const [hasAnalysis, setHasAnalysis] = useState("");
  const [sortBy, setSortBy] = useState("");
  const [seenBusy, setSeenBusy] = useState("");
  const [detailRefresh, setDetailRefresh] = useState(0);
  const [facets, setFacets] = useState<LibraryFacets>({});
  const [role, setRole] = useState("");
  const [workId, setWorkId] = useState("");
  const [characterTrait, setCharacterTrait] = useState("");
  const [plotMechanism, setPlotMechanism] = useState("");
  const filterActive = !!(category || seen || hasText || hasAnalysis || role || workId || characterTrait || plotMechanism);
  const resetFilters = () => { setCategory(""); setSeen(""); setHasText(""); setHasAnalysis(""); setRole(""); setWorkId(""); setCharacterTrait(""); setPlotMechanism(""); setOffset(0); };

  const workspaceRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const config = TABLES.find((t) => t.key === table)!;
  const reload = useCallback(() => setRefresh((r) => r + 1), []);
  useEffect(() => {
    window.addEventListener("inkreference:library-refresh", reload);
    return () =>
      window.removeEventListener("inkreference:library-refresh", reload);
  }, [reload]);
  const notify = useCallback((message: string) => setToast(message), []);
  useEffect(() => {
    const host = workspaceRef.current?.closest<HTMLElement>(".workspace-page");
    const observer = new MutationObserver(() => { if (host && !host.hidden) reload(); });
    if (host) observer.observe(host, { attributes: true, attributeFilter: ["hidden"] });
    return () => observer.disconnect();
  }, [reload]);
  const metadataLoaded = useRef(false);
  useEffect(() => {
    if (metadataLoaded.current) return;
    const controller = new AbortController();
    void request<{ tables: TableMeta[]; version: string }>("/meta", { signal: controller.signal })
      .then(result => { if (!controller.signal.aborted) { metadataLoaded.current = true; setMetas(result.tables); } })
      .catch(e => { if (!controller.signal.aborted) setLoadError(errorMessage(e)); });
    return () => controller.abort();
  }, [refresh]);
  useEffect(() => {
    const controller = new AbortController();
    void request<Stats>("/stats", { signal: controller.signal })
      .then(setStats)
      .catch(e => { if (!controller.signal.aborted) setLoadError(errorMessage(e)); });
    return () => controller.abort();
  }, [refresh]);
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDebouncedQuery(query);
      setOffset(0);
    }, 250);
    return () => window.clearTimeout(timer);
  }, [query]);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setLoadError("");
    const params = new URLSearchParams({
      q: debouncedQuery,
      offset: String(offset),
      limit: String(limit),
    });
    if (table === "works") {
      if (category) params.set("category", category);
      if (seen) params.set("seen", seen);
      if (hasText) params.set("has_text", hasText);
      if (hasAnalysis) params.set("has_analysis", hasAnalysis);
    }
    if (table !== "works" && workId) params.set("work_id", workId);
    if (table === "characters") { if (role) params.set("role", role); if (characterTrait) params.set("character_trait", characterTrait); }
    if (table === "cliches") {
      if (characterTrait) params.set("character_trait", characterTrait);
      if (plotMechanism) params.set("plot_mechanism", plotMechanism);
    }
    if (sortBy) { params.set("sort_by", sortBy); params.set("sort_direction", sortBy === "发布日期" ? "desc" : "asc"); }
    request<LibraryPage>(`/records/${table}?${params}`, { signal: controller.signal })
      .then((data) => {
        if (!controller.signal.aborted) {
          setPage({ ...data, items: data.items.map(normalizeItem) });
          if (data.facets) setFacets(data.facets);
          if (offset > 0 && !data.items.length && data.total > 0)
            setOffset(Math.floor((data.total - 1) / limit) * limit);
        }
      })
      .catch((e) => {
        if (!controller.signal.aborted) setLoadError(errorMessage(e));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [table, debouncedQuery, offset, limit, refresh, category, seen, hasText, hasAnalysis, role, workId, characterTrait, plotMechanism, sortBy]);
  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(""), 6500);
    return () => window.clearTimeout(timer);
  }, [toast]);
  useEffect(() => {
    const guard = (e: BeforeUnloadEvent) => {
      if (dirty) {
        e.preventDefault();
      }
    };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [dirty]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (searchRef.current?.closest("[hidden]")) return;
      if ((e.ctrlKey || e.metaKey) && e.key === "k") {
        e.preventDefault();
        searchRef.current?.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);
  const pendingAction = useRef<(() => void) | null>(null);
  const allowDiscard = useRef(false);
  const [discardOpen, setDiscardOpen] = useState(false);
  const canLeave = (action: () => void) => {
    if (!dirty || allowDiscard.current) {
      allowDiscard.current = false;
      return true;
    }
    pendingAction.current = action;
    setDiscardOpen(true);
    return false;
  };
  const chooseTable = (key: TableKey) => {
    if (!canLeave(() => chooseTable(key))) return;
    setDirty(false);
    setTable(key);
    resetFilters(); setSortBy("");
    setQuery("");
    setDebouncedQuery("");
    setOffset(0);
    setSelection(null);
  };
  const chooseRecord = (targetTable: TableKey, id?: string) => {
    if (selection?.id === id && selection?.table === targetTable) return;
    if (!canLeave(() => chooseRecord(targetTable, id))) return;
    setDirty(false);
    if (table !== targetTable) {
      setTable(targetTable);
      resetFilters(); setSortBy("");
      setQuery("");
      setDebouncedQuery("");
      setOffset(0);
    }
    setSelection({ table: targetTable, id });
  };
  const closeDetail = () => {
    if (!canLeave(closeDetail)) return;
    setDirty(false);
    setSelection(null);
  };
  const importComplete = (message: string) => {
    setModal(null);
    notify(message);
    reload();
  };
  const closeModal = useCallback(() => setModal(null), []);
  const focusRecord = useRef(chooseRecord); focusRecord.current = chooseRecord;
  useEffect(() => {
    const consume = (target: { section?: string; workId?: string } | undefined) => {
      if (target?.section !== "library" || !target.workId) return;
      focusRecord.current("works", target.workId);
      try { sessionStorage.removeItem("inkreference.catalog-focus"); } catch { /* Navigation still works without session storage. */ }
    };
    const focus = (event: Event) => consume((event as CustomEvent<{ section?: string; workId?: string }>).detail);
    window.addEventListener("inkreference:catalog-focus", focus);
    try { const pending = sessionStorage.getItem("inkreference.catalog-focus"); if (pending) consume(JSON.parse(pending)); } catch { /* Ignore an obsolete navigation target. */ }
    return () => window.removeEventListener("inkreference:catalog-focus", focus);
  }, []);
  const toggleSeen = async (item: Item) => {
    if (seenBusy || (selection?.table === "works" && selection.id === item.id && dirty)) return;
    setSeenBusy(item.id); setLoadError("");
    try {
      const updated = await request<Item>(`/records/works/${encoded(item.id)}`, { method: "PATCH", ...jsonBody({ values: { [seenKey]: item.values[seenKey] === "是" ? "否" : "是" }, expected_revision: item.revision }) });
      setPage(current => ({ ...current, items: current.items.map(row => row.id === updated.id ? normalizeItem(updated) : row) }));
      if (selection?.table === "works" && selection.id === item.id) setDetailRefresh(value => value + 1);
      if (seen) reload();
    } catch (e) { notify(errorMessage(e)); } finally { setSeenBusy(""); }
  };
  return (
    <div ref={workspaceRef} className={`library-layout library-workspace ${selection ? "has-detail" : ""}`}>
      {(loading || seenBusy) && <Busy label={seenBusy ? "正在保存看过状态" : "正在读取素材库"} />}
      <div className="library-main">
        <main id="main-content" className="main-content" tabIndex={-1}>
          <PageTitle title={config.label} action={<div className="library-heading-actions"><button className="button secondary" onClick={() => window.dispatchEvent(new CustomEvent("inkreference:navigate", { detail: { section: "research", ...(selection?.table === "works" && selection.id ? { workId: selection.id } : {}), table } }))}><Sparkles size={15} />AI 更新</button>
            <button
              className="button primary heading-create"
              onClick={() => chooseRecord(table)}
            >
              <Plus size={17} />
              新增{config.singular}
            </button></div>
          } />
          <div className="stats-grid" aria-label="素材数量统计">
            {TABLES.map((t) => (
              <button
                className={`stat-card ${table === t.key ? "current" : ""}`}
                key={t.key}
                onClick={() => chooseTable(t.key)}
              >
                <div>
                  <span>{t.label}</span>
                  <strong>
                    {nf.format(stats[t.key])}
                    <small>{t.key === "works" ? "部" : "条"}</small>
                  </strong>
                </div>
                <span className={`stat-icon ${t.key}`}>
                  <t.icon size={21} />
                </span>
              </button>
            ))}
          </div>
          <section className="library-card" aria-label={`${config.label}列表`}>
            <div className="list-toolbar">
              <div className="list-title">
                <config.icon size={18} />
                <h2>全部{config.singular}</h2>
                <span>{nf.format(stats[table])}</span>
              </div>
              <div className="toolbar-actions">
                <div className="search-box">
                  <Search size={16} />
                  <input
                    ref={searchRef}
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder={`搜索${config.singular}、内容或 ID…`}
                    aria-label={`搜索${config.label}`}
                  />
                  {query ? (
                    <button
                      className="icon-button"
                      aria-label="清空搜索"
                      onClick={() => setQuery("")}
                    >
                      <X size={14} />
                    </button>
                  ) : (
                    <kbd>⌘ K</kbd>
                  )}
                </div>
                <button
                  className="button secondary toolbar-import"
                  onClick={() => setModal("import")}
                >
                  <Upload size={15} />
                  导入素材库
                </button>
                <button
                  className="button secondary toolbar-export"
                  aria-label="导出素材"
                  title="导出素材"
                  onClick={() => setModal("export")}
                >
                  <ArrowDownToLine size={16} />
                  导出
                </button>
              </div>
            </div>
            <div className="database-filterbar" aria-label="数据库筛选">
              <SlidersHorizontal size={15} />
              {table === "works" && <>
                <label><span>类别</span><select aria-label="筛选类别" value={category} onChange={e => { setCategory(e.target.value); setOffset(0); }}><option value="">全部类别</option>{facets.categories?.map(value => <option key={value}>{value}</option>)}</select></label>
                <label><span>看过</span><select aria-label="筛选看过" value={seen} onChange={e => { setSeen(e.target.value); setOffset(0); }}><option value="">全部</option><option value="是">是</option><option value="否">否</option><option value="unknown">未记录</option></select></label>
                <label><FileText size={13} /><select aria-label="筛选文本" value={hasText} onChange={e => { setHasText(e.target.value); setOffset(0); }}><option value="">文本</option><option value="true">有文本</option><option value="false">无文本</option></select></label>
                <label><Sparkles size={13} /><select aria-label="筛选分析" value={hasAnalysis} onChange={e => { setHasAnalysis(e.target.value); setOffset(0); }}><option value="">分析</option><option value="true">有分析</option><option value="false">无分析</option></select></label>
              </>}
              {table !== "works" && <label className="database-work-filter"><span>作品</span><select aria-label="筛选关联作品" value={workId} onChange={e => { setWorkId(e.target.value); setOffset(0); }}><option value="">全部作品</option>{facets.works?.map(work => <option key={work.id} value={work.id}>{work.title} · {work.id}</option>)}</select></label>}
              {table === "characters" && <><label><span>基调</span><select aria-label="筛选性格基调" value={characterTrait} onChange={event => { setCharacterTrait(event.target.value); setOffset(0); }}><option value="">全部基调</option>{characterTemperaments.map(value => <option key={value}>{value}</option>)}</select></label><label><span>定位</span><select aria-label="筛选角色定位" value={role} onChange={e => { setRole(e.target.value); setOffset(0); }}><option value="">全部定位</option>{facets.roles?.map(value => <option key={value}>{value}</option>)}</select></label></>}
              {table === "cliches" && <>
                <label><span>人设塑造</span><select aria-label="筛选人设塑造" value={characterTrait} onChange={e => { setCharacterTrait(e.target.value); setOffset(0); }}><option value="">全部</option>{clicheTypes[0].options.map(value => <option key={value}>{value}</option>)}</select></label>
                <label><span>剧情机制</span><select aria-label="筛选剧情机制" value={plotMechanism} onChange={e => { setPlotMechanism(e.target.value); setOffset(0); }}><option value="">全部</option>{clicheTypes[1].options.map(value => <option key={value}>{value}</option>)}</select></label>
              </>}
              <label className="database-sort"><ArrowDownUp size={13} /><select aria-label="排序" value={sortBy} onChange={e => { setSortBy(e.target.value); setOffset(0); }}><option value="">默认排序</option><option value={table === "works" ? "作品" : table === "characters" ? "角色名" : "cliché内容"}>名称</option>{table === "works" && <option value="发布日期">年份</option>}</select></label>
              {filterActive && <button className="text-button" onClick={resetFilters}><X size={13} />清除</button>}
            </div>
            {debouncedQuery && (
              <div className="search-summary">
                搜索“{debouncedQuery}”
                <span>
                  {loading ? "查询中…" : `${nf.format(page.total)} 条结果`}
                </span>
                <button className="text-button" onClick={() => setQuery("")}>
                  清空条件
                </button>
              </div>
            )}
            {loadError ? (
              <div className="list-state">
                <span className="empty-illustration error-illustration">
                  <CircleAlert size={30} />
                </span>
                <h3>暂时无法读取素材</h3>
                <p>{loadError}</p>
                <button className="button secondary" onClick={reload}>
                  重新连接
                </button>
              </div>
            ) : loading && !page.items.length ? (
              <div className="list-state">
                <Spinner text="正在读取本地素材…" />
              </div>
            ) : !page.items.length ? (
              <div className="list-state empty-state">
                <div className="empty-illustration">
                  <config.icon size={32} />
                  <span>
                    <Plus size={15} />
                  </span>
                </div>
                <h3>
                  {debouncedQuery || filterActive
                    ? "没有匹配记录"
                    : `暂无${config.singular}`}
                </h3>
                <div className="empty-actions">
                  {debouncedQuery || filterActive ? (
                    <button
                      className="button secondary"
                      onClick={() => { setQuery(""); resetFilters(); }}
                    >
                      清空条件
                    </button>
                  ) : (
                    <>
                      <button
                        className="button primary"
                        onClick={() => setModal("import")}
                      >
                        <Upload size={16} />
                        导入素材库
                      </button>
                      <button
                        className="button secondary"
                        onClick={() => chooseRecord(table)}
                      >
                        <Plus size={16} />
                        手动新增
                      </button>
                    </>
                  )}
                </div>
              </div>
            ) : (
              <div
                className={`table-region ${loading ? "refreshing" : ""}`}
                aria-busy={loading}
              >
                <RecordTable
                  table={table}
                  items={page.items}
                  selectedId={
                    selection?.table === table ? selection.id : undefined
                  }
                  onSelect={(item) => chooseRecord(table, item.id)}
                  onToggleSeen={item => void toggleSeen(item)} seenBusy={seenBusy}
                />
              </div>
            )}
            <footer className="pagination">
              <span>
                {page.total
                  ? `显示 ${nf.format(offset + 1)}–${nf.format(Math.min(offset + limit, page.total))} 条，共 ${nf.format(page.total)} 条`
                  : "暂无记录"}
              </span>
              <div>
                <label>
                  每页
                  <select
                    value={limit}
                    aria-label="每页记录数"
                    onChange={(e) => {
                      setLimit(Number(e.target.value));
                      setOffset(0);
                    }}
                  >
                    <option value={15}>15</option>
                    <option value={30}>30</option>
                    <option value={50}>50</option>
                    <option value={100}>100</option>
                  </select>
                  条
                </label>
                <span className="page-number">
                  {page.total ? Math.floor(offset / limit) + 1 : 1} /{" "}
                  {Math.max(1, Math.ceil(page.total / limit))}
                </span>
                <button
                  className="icon-button"
                  disabled={!offset || loading}
                  onClick={() => setOffset(Math.max(0, offset - limit))}
                  aria-label="上一页"
                >
                  <ChevronLeft size={17} />
                </button>
                <button
                  className="icon-button"
                  disabled={offset + limit >= page.total || loading}
                  onClick={() => setOffset(offset + limit)}
                  aria-label="下一页"
                >
                  <ChevronRight size={17} />
                </button>
              </div>
            </footer>
          </section>
        </main>
      </div>
      {discardOpen && (
        <Modal
          title="放弃未保存的修改？"
          onClose={() => {
            pendingAction.current = null;
            setDiscardOpen(false);
          }}
        >
          <div className="modal-body">
            <p>当前素材有尚未保存的内容。</p>
          </div>
          <div className="modal-footer">
            <button
              className="button secondary"
              onClick={() => {
                pendingAction.current = null;
                setDiscardOpen(false);
              }}
            >
              继续编辑
            </button>
            <button
              className="button destructive"
              onClick={() => {
                setDirty(false);
                setDiscardOpen(false);
                allowDiscard.current = true;
                const action = pendingAction.current;
                pendingAction.current = null;
                action?.();
              }}
            >
              放弃修改
            </button>
          </div>
        </Modal>
      )}
      {selection && <button className="library-detail-shade" aria-label="收起素材详情" onClick={closeDetail} />}
      {selection && (
        <DetailPanel
          key={`${selection.table}-${selection.id || "new"}`}
          selection={selection}
          refreshRecord={detailRefresh}
          meta={metas.find((m) => m.key === selection.table)}
          onClose={closeDetail}
          onDirty={setDirty}
          onNavigate={chooseRecord}
          onSaved={(record) => {
            setDirty(false);
            setSelection({ table: selection.table, id: record.id });
            notify("素材已保存。");
            reload();
          }}
          onDeleted={() => {
            setDirty(false);
            setSelection(null);
            notify("素材已删除。");
            reload();
          }}
        />
      )}
      {modal === "import" && (
        <ImportDialog onClose={closeModal} onComplete={importComplete} />
      )}
      {modal === "export" && (
        <ExportDialog onClose={closeModal} notify={notify} />
      )}
      {toast && (
        <div className="toast" role="status">
          <CheckCircle2 size={18} />
          <span>{toast}</span>
          <button
            className="icon-button"
            aria-label="关闭提示"
            onClick={() => setToast("")}
          >
            <X size={15} />
          </button>
        </div>
      )}
    </div>
  );
}
