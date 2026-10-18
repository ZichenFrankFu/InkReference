import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type ReactNode,
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
  Plus,
  Search,
  Trash2,
  Upload,
  UsersRound,
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

const TABLES: {
  key: TableKey;
  label: string;
  singular: string;
  icon: typeof BookOpen;
  description: string;
}[] = [
  {
    key: "works",
    label: "作品库",
    singular: "作品",
    icon: BookOpen,
    description: "记录故事，梳理每个版本的独特之处。",
  },
  {
    key: "characters",
    label: "人物库",
    singular: "人物",
    icon: UsersRound,
    description: "从开场身份到人物弧光，留下可回看的线索。",
  },
  {
    key: "cliches",
    label: "桥段库",
    singular: "桥段",
    icon: Layers3,
    description: "收集叙事手法，在不同作品之间发现联系。",
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
  TABLES.find((t) => t.key === key)?.label || ({ work_cliches: "作品与桥段关联", material_relations: "素材关系" } as Record<string, string>)[key] || key;
const titleOf = (item: Item, table: TableKey) =>
  item.values[
    table === "works" ? "作品" : table === "characters" ? "角色" : "cliché内容"
  ] || item.id;
const isLong = (column: string) =>
  /概括|说明|内容|出现作品|桥段|能力|性格|身份|关系/.test(column);
const errorMessage = (e: unknown) =>
  e instanceof Error ? e.message : "操作未完成，请稍后重试。";
const encoded = (v: string) => encodeURIComponent(v);
function workCount(value: string): number {
  let depth = 0,
    count = 0,
    part = "";
  for (const char of value) {
    if ("〔[【（(".includes(char)) depth++;
    if ("〕]】）)".includes(char)) depth = Math.max(0, depth - 1);
    if ((char === "；" || char === ";") && !depth) {
      if (part.trim()) count++;
      part = "";
    } else part += char;
  }
  return count + (part.trim() ? 1 : 0);
}

function Spinner({ text = "正在加载…" }: { text?: string }) {
  return (
    <span className="loading-inline" role="status">
      <LoaderCircle size={17} className="spin" />
      {text}
      <i className="inline-progress" role="progressbar" aria-label={text}>
        <b />
      </i>
    </span>
  );
}

function Modal({
  title,
  children,
  onClose,
  wide = false,
  busy = false,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  wide?: boolean;
  busy?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  const busyRef = useRef(busy);
  busyRef.current = busy;
  const titleId = useId();
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const modal = ref.current;
    modal?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busyRef.current) closeRef.current();
      if (event.key !== "Tab" || !modal) return;
      const elements = [
        ...modal.querySelectorAll<HTMLElement>(
          'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex="0"]',
        ),
      ];
      const first = elements[0],
        last = elements[elements.length - 1];
      if (!first) {
        event.preventDefault();
        return;
      }
      if (
        event.shiftKey &&
        (document.activeElement === first || document.activeElement === modal)
      ) {
        event.preventDefault();
        last.focus();
      } else if (
        !event.shiftKey &&
        (document.activeElement === last || document.activeElement === modal)
      ) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previous?.focus();
    };
  }, []);
  return (
    <div
      className="modal-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !busy) onClose();
      }}
    >
      <div
        ref={ref}
        tabIndex={-1}
        className={`modal ${wide ? "modal-wide" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        <div className="modal-header">
          <h2 id={titleId}>{title}</h2>
          <button
            className="icon-button"
            disabled={busy}
            onClick={onClose}
            aria-label="关闭对话框"
          >
            <X size={20} />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

const exchangeFormats = [
  { id: "db", title: "SQLite 数据库", suffix: ".db", description: "一个文件，完整保存所有素材表与关联。", icon: Database },
  { id: "csv", title: "CSV 数据包", suffix: ".zip", description: "所有数据表与版本清单，统一打包为 ZIP。", icon: FolderInput },
  { id: "xlsx", title: "Excel 工作簿", suffix: ".xlsx", description: "一个工作簿，包含全部数据表与版本清单。", icon: FileSpreadsheet },
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
          <small>{format.id === "db" ? "选择一个数据库文件" : format.id === "csv" ? "完整数据包，或同时选择全部 CSV 与 manifest.json" : "选择包含全部数据表的工作簿"}</small>
        </div>)}
      </div>
      <details className="exchange-requirements"><summary>CSV / Excel 需要哪些数据表？</summary><ul>{(formats?.tables || [
        { table: "works", label: "作品表", filename: "works.csv" },
        { table: "characters", label: "人物表", filename: "characters.csv" },
        { table: "cliches", label: "桥段表", filename: "cliches.csv" },
        { table: "work_cliches", label: "作品与桥段关联", filename: "work_cliches.csv" },
        { table: "material_relations", label: "素材关系", filename: "material_relations.csv" },
      ]).map(table => <li key={table.table}><strong>{table.label}</strong><span>{table.filename} / {table.table} 工作表</span></li>)}</ul><p>CSV 还需 manifest.json 版本清单；Excel 工作簿需 _manifest 工作表。直接导入本应用导出的完整文件即可。</p></details>
      <input type="file" accept=".db,.sqlite3,.zip,.csv,.xlsx,.json" multiple ref={input} className="visually-hidden" aria-label="选择素材库文件" onChange={event => { const incoming = [...(event.target.files || [])]; event.target.value = ""; addFiles(incoming); }} />
      <button className={`dropzone exchange-dropzone ${dragging ? "dragging" : ""}`} disabled={!!busy} onClick={() => input.current?.click()} onDragOver={event => { event.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={event => { event.preventDefault(); setDragging(false); addFiles(event.dataTransfer.files); }}>
        <span className="drop-icon"><Upload size={23} /></span>
        <strong>{busy === "preview" ? "正在检查数据表与关联…" : files.length ? "补充文件" : "点击选择或拖入文件"}</strong>
        <span>{files.length ? "可补齐遗漏文件；同名文件会替换本次选择" : ".db · CSV 数据包 · .xlsx"}</span>
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
        {!!preview.tables.length && <><div className="section-caption"><span>导入预览</span><span>{preview.legacy ? "v8 兼容数据" : preview.format?.toUpperCase()}</span></div><div className="preview-table-wrap"><table className="preview-table"><thead><tr><th>数据表 / 来源</th><th>记录</th><th>新增</th><th>已存在</th></tr></thead><tbody>{preview.tables.map((table, index) => <tr key={`${table.filename}-${index}`}><td><strong>{labelFor(table.table)}</strong><small>{table.filename}</small></td><td>{nf.format(table.rows)}</td><td>{nf.format(table.new)}</td><td>{nf.format(table.existing)}</td></tr>)}</tbody></table></div></>}
        {!preview.errors.length && <div className="notice success"><FileCheck2 size={18} /><span>全部数据表与关联检查通过。</span></div>}
        <fieldset className="policy-options" disabled={!!busy || !!preview.errors.length}>
          <legend>遇到相同 ID 时</legend>
          {([ ["skip", "保留已有记录", "跳过相同 ID"], ["update", "更新已有记录", "使用导入内容更新相同 ID"], ["error", "中止本次导入", "发现重复时整批不写入"] ] as const).map(([value, label, description]) => <label className={`radio-card ${policy === value ? "checked" : ""}`} key={value}><input type="radio" name="policy" checked={policy === value} onChange={() => setPolicy(value)} /><span><strong>{label}</strong><small>{description}</small></span></label>)}
        </fieldset>
      </>}
      {!files.length && <p className="exchange-compatibility">已有 v8 CSV：请同时选择 works_library、character_database、cliche_database 三个同版本文件。</p>}
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
    <div className="modal-body library-exchange"><p className="exchange-intro">所有格式均包含完整数据表与关联。</p><fieldset className="exchange-export-formats" disabled={busy}><legend className="visually-hidden">文件格式</legend>{exchangeFormats.map(option => <label className={`exchange-export-option ${format === option.id ? "selected" : ""}`} key={option.id}><input type="radio" name="export-format" value={option.id} checked={format === option.id} onChange={() => setFormat(option.id)} /><span className="option-icon"><option.icon size={22} /></span><span className="exchange-format-text"><strong>{option.title}<em>{option.suffix}</em>{option.id === "db" && <small className="exchange-recommended">推荐</small>}</strong><span>{option.description}</span></span>{format === option.id && <CheckCircle2 size={19} />}</label>)}</fieldset>{error && <div className="notice error" role="alert"><CircleAlert size={17} />{error}</div>}</div>
    <div className="modal-footer"><button className="button secondary" disabled={busy} onClick={onClose}>取消</button><button className="button primary" disabled={busy} onClick={() => void exportFile()}>{busy ? <Spinner text="正在生成导出文件…" /> : <><ArrowDownToLine size={16} />导出{format === "csv" ? " CSV 数据包" : ` .${format}`}</>}</button></div>
  </Modal>;
}

type Selection = { table: TableKey; id?: string };
function DetailPanel({
  selection,
  meta,
  onClose,
  onSaved,
  onDeleted,
  onNavigate,
  onDirty,
}: {
  selection: Selection;
  meta: TableMeta | undefined;
  onClose: () => void;
  onSaved: (item: Item) => void;
  onDeleted: () => void;
  onNavigate: (table: TableKey, id: string) => void;
  onDirty: (dirty: boolean) => void;
}) {
  const { table, id } = selection;
  const [item, setItem] = useState<Item | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [relations, setRelations] = useState<Relations>({
    works: [],
    characters: [],
    cliches: [],
  });
  const [loading, setLoading] = useState(!!id);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [conflict, setConflict] = useState(false);
  const [tab, setTab] = useState<"details" | "relations">("details");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteError, setDeleteError] = useState("");
  const [cascade, setCascade] = useState(false);
  const title = item
    ? titleOf(item, table)
    : `新增${TABLES.find((t) => t.key === table)?.singular}`;
  const dirty = id
    ? JSON.stringify(draft) !== JSON.stringify(item?.values || {})
    : Object.values(draft).some(Boolean);
  const load = useCallback(async () => {
    setLoading(!!id);
    setError("");
    setConflict(false);
    if (!id) {
      setItem(null);
      setDraft({});
      setRelations({ works: [], characters: [], cliches: [] });
      return;
    }
    try {
      const [record, links] = await Promise.all([
        request<Item>(`/records/${table}/${encoded(id)}`),
        request<Relations>(`/records/${table}/${encoded(id)}/relations`),
      ]);
      setItem(record);
      setDraft(record.values);
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
    setConfirmDelete(false);
  }, [load]);
  useEffect(() => {
    onDirty(dirty);
  }, [dirty, onDirty]);
  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    setSaving(true);
    setError("");
    setConflict(false);
    try {
      const record = await request<Item>(
        id ? `/records/${table}/${encoded(id)}` : `/records/${table}`,
        {
          method: id ? "PATCH" : "POST",
          ...jsonBody(
            id
              ? { values: draft, expected_revision: item?.revision }
              : { values: draft },
          ),
        },
      );
      setItem(record);
      setDraft(record.values);
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
  const needsCascade =
    table === "works"
      ? !!(relations.characters.length || relations.cliches.length)
      : table === "cliches" && !!relations.works.length;
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
  const columns = [
    ...new Set([...(meta?.columns || []), ...Object.keys(draft)]),
  ];
  return (
    <aside className="detail-panel" aria-label={`${labelFor(table)}详情`}>
      <div className="detail-heading">
        <div>
          <span className="eyebrow">{id ? "素材详情" : "创建素材"}</span>
          <h2 title={title}>{title}</h2>
          <span className="record-id">
            {item?.id || "填写以下字段，保存到本地素材库"}
          </span>
        </div>
        <button className="icon-button" onClick={onClose} aria-label="收起详情">
          <PanelRightClose size={20} />
        </button>
      </div>
      {id && (
        <div className="detail-tabs" role="tablist" aria-label="详情视图">
          <button
            role="tab"
            aria-selected={tab === "details"}
            className={tab === "details" ? "active" : ""}
            onClick={() => setTab("details")}
          >
            基本信息
          </button>
          <button
            role="tab"
            aria-selected={tab === "relations"}
            className={tab === "relations" ? "active" : ""}
            onClick={() => setTab("relations")}
          >
            关联素材 <span>{relatedTotal}</span>
          </button>
        </div>
      )}
      {loading ? (
        <div className="panel-loading">
          <Spinner />
        </div>
      ) : (
        <>
          {tab === "details" ? (
            <form className="detail-form" onSubmit={(e) => void save(e)}>
              <div className="detail-scroll">
                {error && (
                  <div className="notice error" role="alert">
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
                {columns.map((column) => {
                  const immutable =
                    !!id && column === meta?.id_field && table !== "cliches";
                  const fieldId = `field-${column}`;
                  return (
                    <div
                      className={`field ${isLong(column) ? "field-long" : ""}`}
                      key={column}
                    >
                      <label htmlFor={fieldId}>
                        {column}
                        {immutable && <span aria-hidden="true">固定 ID</span>}
                      </label>
                      {isLong(column) ? (
                        <textarea
                          id={fieldId}
                          rows={column === "出现作品" ? 5 : 3}
                          value={draft[column] || ""}
                          onChange={(e) =>
                            setDraft((d) => ({
                              ...d,
                              [column]: e.target.value,
                            }))
                          }
                          readOnly={immutable}
                        />
                      ) : (
                        <input
                          id={fieldId}
                          value={draft[column] || ""}
                          onChange={(e) =>
                            setDraft((d) => ({
                              ...d,
                              [column]: e.target.value,
                            }))
                          }
                          readOnly={immutable}
                        />
                      )}
                      {!id && column === meta?.id_field && (
                        <small className="helper">
                          {table === "cliches"
                            ? "以唯一桥段编号开头，例如 C001｜桥段描述。"
                            : "使用独立且唯一的 ID，保留同名作品的不同版本。"}
                        </small>
                      )}
                      {!!id &&
                        table === "cliches" &&
                        column === meta?.id_field && (
                          <small className="helper">
                            可以修改描述，请保留开头的桥段编号 {id}。
                          </small>
                        )}
                    </div>
                  );
                })}
                {!columns.length && <p className="muted">正在读取数据字段…</p>}
              </div>
              <div className="detail-footer">
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
            </form>
          ) : (
            <div className="detail-scroll relations-list">
              {relationGroups.map((group) => (
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
                        onClick={() => onNavigate(group.key, link.id)}
                      >
                        <span>
                          <strong>{titleOf(link, group.key)}</strong>
                          <small>
                            {link.id}
                            {link.values["版本与原作关系"]
                              ? ` · ${link.values["版本与原作关系"]}`
                              : ""}
                          </small>
                          <span className="relation-semantics">
                            {(link.relationships?.length ? link.relationships : link.relationship ? [link.relationship] : []).map((relationship, index) => <span className="relation-meaning" key={`${relationship.id}-${index}`}>
                              <span className="relation-type">{relationship.label || relationship.type || "关联"}</span>
                              {relationship.description && relationship.description !== relationship.label && <span className="relation-description">{relationship.description}</span>}
                            </span>)}
                            {!link.relationship && !link.relationships?.length && <span className="relation-description">{link.values["关联说明"] || link.values["关系说明"] || link.values["版本与原作关系"] || "关系说明待补充"}</span>}
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
          )}
        </>
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
            {table === "works" ? (
              <div className="notice warning">
                <CircleAlert size={18} />
                <span>
                  将删除此作品及其{" "}
                  <strong>{relations.characters.length} 条人物</strong>，并解除{" "}
                  <strong>{relations.cliches.length} 条桥段关联</strong>
                  。桥段本身保留。
                </span>
              </div>
            ) : table === "cliches" ? (
              <div className="notice warning">
                <CircleAlert size={18} />
                <span>
                  将删除此桥段并解除与{" "}
                  <strong>{relations.works.length} 部作品</strong>
                  的关联，作品本身保留。
                </span>
              </div>
            ) : (
              <div className="notice info">
                <CircleAlert size={18} />
                <span>仅删除当前人物记录，所属作品保留。</span>
              </div>
            )}
            {needsCascade && (
              <label className="check-line">
                <input
                  type="checkbox"
                  checked={cascade}
                  onChange={(e) => setCascade(e.target.checked)}
                />
                我已了解以上关联影响，确认一并处理
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

function RecordTable({
  table,
  items,
  selectedId,
  onSelect,
}: {
  table: TableKey;
  items: Item[];
  selectedId?: string;
  onSelect: (item: Item) => void;
}) {
  const headings =
    table === "works"
      ? ["作品 / 版本", "类别", "作者", "发布日期"]
      : table === "characters"
        ? ["人物 / 身份", "所属作品", "角色定位", "人物 ID"]
        : ["桥段内容", "程度", "涉及作品", "桥段 ID"];
  return (
    <div className="record-table-wrap">
      <table className={`record-table table-${table}`}>
        <thead>
          <tr>
            {headings.map((h, i) => (
              <th key={h} className={i === 3 ? "optional-column" : ""}>
                {h}
              </th>
            ))}
            <th className="row-action">
              <span className="visually-hidden">操作</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr
              key={item.id}
              className={item.id === selectedId ? "selected" : ""}
            >
              {table === "works" ? (
                <>
                  <td>
                    <button
                      className="record-title"
                      onClick={() => onSelect(item)}
                    >
                      <span className="record-symbol">
                        <BookOpen size={18} />
                      </span>
                      <span>
                        <strong>{titleOf(item, table)}</strong>
                        <small>
                          {item.values["版本与原作关系"] || item.id}
                        </small>
                      </span>
                    </button>
                  </td>
                  <td>
                    <span className="tag">
                      {item.values["类别"] || "未填写"}
                    </span>
                  </td>
                  <td>
                    {item.values["作者"] || (
                      <span className="empty-value">—</span>
                    )}
                  </td>
                  <td className="optional-column date-cell">
                    {item.values["发布日期"] || "—"}
                  </td>
                </>
              ) : table === "characters" ? (
                <>
                  <td>
                    <button
                      className="record-title"
                      onClick={() => onSelect(item)}
                    >
                      <span className="record-symbol character-symbol">
                        <UsersRound size={18} />
                      </span>
                      <span>
                        <strong>{titleOf(item, table)}</strong>
                        <small>
                          {item.values["开场身份"] || "尚未填写开场身份"}
                        </small>
                      </span>
                    </button>
                  </td>
                  <td>
                    <span className="cell-truncate" title={item.values["作品"]}>
                      {item.values["作品"] || "—"}
                    </span>
                  </td>
                  <td>
                    <span className="tag neutral">
                      {item.values["角色定位"] || "未填写"}
                    </span>
                  </td>
                  <td className="optional-column mono">{item.id}</td>
                </>
              ) : (
                <>
                  <td>
                    <button
                      className="record-title cliche-title"
                      onClick={() => onSelect(item)}
                    >
                      <span className="record-symbol cliche-symbol">
                        <Layers3 size={18} />
                      </span>
                      <span>
                        <strong>{titleOf(item, table)}</strong>
                      </span>
                    </button>
                  </td>
                  <td className="date-cell">
                    {item.values["cliché程度"] || "—"}
                  </td>
                  <td>
                    <span className="link-count">
                      <Link2 size={13} />
                      {workCount(item.values["出现作品"] || "")}
                    </span>
                  </td>
                  <td className="optional-column mono">{item.id}</td>
                </>
              )}
              <td className="row-action">
                <button
                  className="icon-button"
                  aria-label={`查看${titleOf(item, table)}`}
                  onClick={() => onSelect(item)}
                >
                  <ChevronRight size={17} />
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function Library() {
  const [table, setTable] = useState<TableKey>("works");
  const [metas, setMetas] = useState<TableMeta[]>([]);
  const [stats, setStats] = useState<Stats>(emptyStats);

  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [page, setPage] = useState<Page>({
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
  const [modal, setModal] = useState<"import" | "export" | "about" | null>(
    null,
  );
  const [toast, setToast] = useState("");

  const searchRef = useRef<HTMLInputElement>(null);
  const config = TABLES.find((t) => t.key === table)!;
  const reload = useCallback(() => setRefresh((r) => r + 1), []);
  useEffect(() => {
    window.addEventListener("inkreference:library-refresh", reload);
    return () =>
      window.removeEventListener("inkreference:library-refresh", reload);
  }, [reload]);
  const notify = useCallback((message: string) => setToast(message), []);
  const metadata = useCallback(async () => {
    const [meta, result] = await Promise.all([
      request<{ tables: TableMeta[]; version: string }>("/meta"),
      request<Stats>("/stats"),
    ]);
    setMetas(meta.tables);

    setStats(result);
  }, []);
  useEffect(() => {
    void metadata().catch((e) => setLoadError(errorMessage(e)));
  }, [metadata, refresh]);
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
    request<Page>(`/records/${table}?${params}`, { signal: controller.signal })
      .then((data) => {
        if (!controller.signal.aborted) {
          setPage(data);
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
  }, [table, debouncedQuery, offset, limit, refresh]);
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
  const totalAll = stats.works + stats.characters + stats.cliches;
  return (
    <div className={`library-layout library-workspace ${selection ? "has-detail" : ""}`}>
      <div className="library-main">
        <main id="main-content" className="main-content" tabIndex={-1}>
          <div className="page-heading">
            <div>
              <div className="eyebrow">素材管理</div>
              <h1>{config.label}</h1>
            </div>
            <button
              className="button primary heading-create"
              onClick={() => chooseRecord(table)}
            >
              <Plus size={17} />
              新增{config.singular}
            </button>
          </div>
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
            <div className="stat-card relation-stat">
              <div>
                <span>作品与桥段关联</span>
                <strong>
                  {nf.format(stats.work_cliches)}
                  <small>组</small>
                </strong>
              </div>
              <span className="stat-icon links">
                <Link2 size={21} />
              </span>
            </div>
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
                  {debouncedQuery
                    ? "没有找到匹配的素材"
                    : `从第一${table === "works" ? "部作品" : "条素材"}开始`}
                </h3>
                <p>
                  {debouncedQuery
                    ? "试试更短的关键词，或清空搜索查看全部素材。"
                    : totalAll
                      ? `可以手动新增${config.singular}，也可以从完整素材文件批量导入。`
                      : "导入已有素材库，或添加第一条作品、人物与桥段。"}
                </p>
                <div className="empty-actions">
                  {debouncedQuery ? (
                    <button
                      className="button secondary"
                      onClick={() => setQuery("")}
                    >
                      清空搜索
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
      {modal === "about" && (
        <Modal title="关于此工作区" onClose={closeModal}>
          <div className="modal-body">
            <div className="about-brand">
              <BookOpen size={30} />
              <div>
                <strong>InkReference</strong>
                <span>写作参考工作台</span>
              </div>
            </div>
            <p>
              面向写作的本地参考素材库，整理作品、人物和桥段，并保留它们之间的联系。
            </p>
            <div className="about-row">
              <span>当前已可使用</span>
              <p>数据库、CSV 与 Excel 完整导入导出，素材编辑与关联浏览。</p>
            </div>
            <div className="about-row">
              <span>后续开发</span>
              <p>
                全文清洗、LangGraph 提取工作流、Skills、本地模型与 API、token
                统计。
              </p>
            </div>
            <p className="helper">
              CSV 导出用于数据交换；请按项目说明备份本地数据库以保留完整工作区。
            </p>
          </div>
          <div className="modal-footer">
            <button className="button secondary" onClick={closeModal}>
              知道了
            </button>
          </div>
        </Modal>
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
