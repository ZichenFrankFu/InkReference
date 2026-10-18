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
  ChevronDown,
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
  Menu,
  PanelRightClose,
  Plus,
  Search,
  Settings2,
  Sparkles,
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
  type Page,
  type Preview,
  type Relations,
  type Stats,
  type TableKey,
  type TableMeta,
} from "./api";

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
  TABLES.find((t) => t.key === key)?.label || key;
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

function ImportDialog({
  onClose,
  onComplete,
}: {
  onClose: () => void;
  onComplete: (message: string) => void;
}) {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [policy, setPolicy] = useState<"skip" | "update" | "error">("skip");
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const inspect = async (files: FileList | File[]) => {
    if (!files.length || busy) return;
    if ([...files].some((f) => !f.name.toLowerCase().endsWith(".csv"))) {
      setError("请选择 CSV 文件。可同时选择作品、人物和桥段文件。");
      return;
    }
    setBusy(true);
    setError("");
    setPreview(null);
    try {
      const form = new FormData();
      [...files].forEach((f) => form.append("files", f));
      setPreview(
        await request<Preview>("/import/preview", {
          method: "POST",
          body: form,
        }),
      );
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  };
  const commit = async () => {
    if (!preview || preview.errors.length) return;
    setBusy(true);
    setError("");
    try {
      const result = await request<{
        imported: number;
        updated: number;
        skipped: number;
      }>("/import/commit", {
        method: "POST",
        ...jsonBody({ batch_id: preview.batch_id, policy }),
      });
      onComplete(
        `导入完成：新增 ${nf.format(result.imported)} 条，更新 ${nf.format(result.updated)} 条，跳过 ${nf.format(result.skipped)} 条。`,
      );
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  };
  return (
    <Modal title="导入 CSV" onClose={onClose} wide busy={busy}>
      <div className="modal-body">
        <p className="muted">
          同时选择三个素材文件，自动识别表头并检查关联。确认后一次性写入。
        </p>
        <input
          type="file"
          accept=".csv,text/csv"
          multiple
          ref={input}
          className="visually-hidden"
          aria-label="选择 CSV 文件"
          onChange={(e) => e.target.files && void inspect(e.target.files)}
        />
        <button
          className={`dropzone ${dragging ? "dragging" : ""}`}
          disabled={busy}
          onClick={() => input.current?.click()}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            void inspect(e.dataTransfer.files);
          }}
        >
          <span className="drop-icon">
            <FolderInput size={25} />
          </span>
          <strong>
            {busy && !preview
              ? "正在检查文件…"
              : preview
                ? "重新选择文件"
                : "点击选择或拖入 CSV 文件"}
          </strong>
          <span>支持 v8 作品、人物、桥段 CSV · 可多选</span>
        </button>
        {error && (
          <div className="notice error" role="alert">
            <CircleAlert size={17} />
            <span>{error}</span>
          </div>
        )}
        {preview && (
          <>
            <div className="section-caption">
              <span>导入预检</span>
              <span>{preview.tables.length} 个文件</span>
            </div>
            <div className="preview-table-wrap">
              <table className="preview-table">
                <thead>
                  <tr>
                    <th>文件 / 数据表</th>
                    <th>记录</th>
                    <th>新增</th>
                    <th>已存在</th>
                  </tr>
                </thead>
                <tbody>
                  {preview.tables.map((t, i) => (
                    <tr key={`${t.filename}-${i}`}>
                      <td>
                        <strong>{labelFor(t.table)}</strong>
                        <small>{t.filename}</small>
                      </td>
                      <td>{nf.format(t.rows)}</td>
                      <td className="positive">{nf.format(t.new)}</td>
                      <td>{nf.format(t.existing)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {preview.errors.length > 0 && (
              <div className="notice error" role="alert">
                <CircleAlert size={18} />
                <div>
                  <strong>请修复以下问题后重新选择文件</strong>
                  <ul>
                    {preview.errors.map((s, i) => (
                      <li key={i}>{s}</li>
                    ))}
                  </ul>
                </div>
              </div>
            )}
            {preview.warnings.length > 0 && (
              <div className="notice warning">
                <CircleAlert size={18} />
                <div>
                  <strong>需要留意</strong>
                  <ul>
                    {preview.warnings.map((s, i) => (
                      <li key={i}>{s}</li>
                    ))}
                  </ul>
                </div>
              </div>
            )}
            {!preview.errors.length && (
              <div className="notice success">
                <FileCheck2 size={18} />
                <span>格式与关联检查通过。确认后才会保存数据。</span>
              </div>
            )}
            <fieldset className="policy-options" disabled={busy}>
              <legend>遇到相同 ID 时</legend>
              {(
                [
                  ["skip", "保留已有记录", "跳过相同 ID，适合首次或重复导入"],
                  ["update", "更新已有记录", "用 CSV 内容覆盖相同 ID 的字段"],
                  ["error", "中止本次导入", "发现重复即停止，整批不写入"],
                ] as const
              ).map(([value, label, description]) => (
                <label
                  className={`radio-card ${policy === value ? "checked" : ""}`}
                  key={value}
                >
                  <input
                    type="radio"
                    name="policy"
                    value={value}
                    checked={policy === value}
                    onChange={() => setPolicy(value)}
                  />
                  <span>
                    <strong>{label}</strong>
                    <small>{description}</small>
                  </span>
                </label>
              ))}
            </fieldset>
          </>
        )}
      </div>
      <div className="modal-footer">
        <button className="button secondary" disabled={busy} onClick={onClose}>
          取消
        </button>
        <button
          className="button primary"
          disabled={busy || !preview || !!preview.errors.length}
          onClick={() => void commit()}
        >
          {busy ? (
            <Spinner text={preview ? "正在导入…" : "正在检查…"} />
          ) : (
            <>
              <ArrowRight size={16} />
              确认导入
            </>
          )}
        </button>
      </div>
    </Modal>
  );
}

function ExportDialog({
  table,
  onClose,
  notify,
}: {
  table: TableKey;
  onClose: () => void;
  notify: (message: string) => void;
}) {
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const exportFile = async (mode: string, path: string, filename: string) => {
    setBusy(mode);
    setError("");
    try {
      await download(path, filename);
      notify("导出文件已准备好。");
      onClose();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy("");
    }
  };
  return (
    <Modal title="导出素材" onClose={onClose} busy={!!busy}>
      <div className="modal-body">
        <p className="muted">
          CSV 使用 UTF-8 BOM 编码，保留多行内容与原始字段。
        </p>
        <div className="export-options">
          {[
            {
              id: "compatible",
              title: "兼容 v8 格式",
              text: "作品、人物、桥段三个 CSV，打包为 ZIP。",
              icon: FileSpreadsheet,
              path: "/export?mode=compatible",
              filename: "inkreference_v8.zip",
            },
            {
              id: "full",
              title: "全部业务数据",
              text: "各业务表 CSV 和字段说明，打包为 ZIP。",
              icon: Database,
              path: "/export?mode=full",
              filename: "inkreference_full.zip",
            },
            {
              id: "table",
              title: `仅导出${labelFor(table)}`,
              text: "导出当前数据表的全部记录，生成一个 CSV。",
              icon: ArrowDownToLine,
              path: `/export/${table}`,
              filename: `${table}.csv`,
            },
          ].map((option) => (
            <button
              className="export-option"
              disabled={!!busy}
              key={option.id}
              onClick={() =>
                void exportFile(option.id, option.path, option.filename)
              }
            >
              <span className="option-icon">
                <option.icon size={22} />
              </span>
              <span>
                <strong>{option.title}</strong>
                <small>{option.text}</small>
              </span>
              {busy === option.id ? (
                <LoaderCircle className="spin" size={18} />
              ) : (
                <ChevronRight size={18} />
              )}
            </button>
          ))}
        </div>
        <p className="helper">导出包含完整数据表，不受当前搜索条件影响。</p>
        {error && (
          <div className="notice error" role="alert">
            <CircleAlert size={17} />
            {error}
          </div>
        )}
      </div>
      <div className="modal-footer">
        <button
          className="button secondary"
          disabled={!!busy}
          onClick={onClose}
        >
          关闭
        </button>
      </div>
    </Modal>
  );
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
  const relatedTotal = TABLES.reduce(
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
              {TABLES.map((group) => (
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

export default function App() {
  const [table, setTable] = useState<TableKey>("works");
  const [metas, setMetas] = useState<TableMeta[]>([]);
  const [stats, setStats] = useState<Stats>(emptyStats);
  const [version, setVersion] = useState("0.1.0");
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
  const [mobileNav, setMobileNav] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);
  const config = TABLES.find((t) => t.key === table)!;
  const reload = useCallback(() => setRefresh((r) => r + 1), []);
  const notify = useCallback((message: string) => setToast(message), []);
  const metadata = useCallback(async () => {
    const [meta, result] = await Promise.all([
      request<{ tables: TableMeta[]; version: string }>("/meta"),
      request<Stats>("/stats"),
    ]);
    setMetas(meta.tables);
    setVersion(meta.version);
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
      if ((e.ctrlKey || e.metaKey) && e.key === "k") {
        e.preventDefault();
        searchRef.current?.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);
  const canLeave = () =>
    !dirty || window.confirm("有尚未保存的修改。确定放弃这些修改吗？");
  const chooseTable = (key: TableKey) => {
    if (!canLeave()) return;
    setDirty(false);
    setTable(key);
    setQuery("");
    setDebouncedQuery("");
    setOffset(0);
    setSelection(null);
    setMobileNav(false);
  };
  const chooseRecord = (targetTable: TableKey, id?: string) => {
    if (selection?.id === id && selection?.table === targetTable) return;
    if (!canLeave()) return;
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
    if (!canLeave()) return;
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
    <div className={`app ${selection ? "has-detail" : ""}`}>
      <a href="#main-content" className="skip-link">
        跳到素材列表
      </a>
      {mobileNav && (
        <div className="nav-shade" onClick={() => setMobileNav(false)} />
      )}
      <aside
        className={`sidebar ${mobileNav ? "mobile-open" : ""}`}
        aria-label="主导航"
      >
        <a
          className="brand"
          href="#"
          onClick={(e) => {
            e.preventDefault();
            chooseTable("works");
          }}
        >
          <span className="brand-mark">
            <span /> <span />
          </span>
          <span>
            InkReference<small>写作参考素材库</small>
          </span>
        </a>
        <div className="workspace-label">
          <span className="local-dot" />
          本地工作区
          <ChevronDown size={13} />
        </div>
        <div className="nav-caption">素材管理</div>
        <nav>
          {TABLES.map((t) => (
            <button
              key={t.key}
              className={`nav-item ${table === t.key ? "active" : ""}`}
              onClick={() => chooseTable(t.key)}
              aria-current={table === t.key ? "page" : undefined}
            >
              <t.icon size={19} />
              <span>{t.label}</span>
              <span className="nav-count">{nf.format(stats[t.key])}</span>
            </button>
          ))}
        </nav>
        <div className="sidebar-line" />
        <button className="nav-item utility" onClick={() => setModal("import")}>
          <Upload size={18} />
          <span>导入素材</span>
        </button>
        <button className="nav-item utility" onClick={() => setModal("export")}>
          <ArrowDownToLine size={18} />
          <span>导出数据</span>
        </button>
        <div className="sidebar-bottom">
          <div className="phase-note">
            <Sparkles size={17} />
            <strong>让灵感有迹可循</strong>
            <p>
              先收藏，再关联。
              <br />
              为下一次写作留一份索引。
            </p>
            <span>第一批 · 素材管理</span>
          </div>
          <button
            className="sidebar-settings"
            onClick={() => setModal("about")}
          >
            <Settings2 size={16} />
            <span>关于此工作区</span>
            <span>v{version}</span>
          </button>
        </div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <div className="breadcrumb">
            <button
              className="icon-button mobile-menu"
              onClick={() => setMobileNav(true)}
              aria-label="打开导航"
            >
              <Menu size={20} />
            </button>
            <span>我的素材</span>
            <ChevronRight size={13} />
            <strong>{config.label}</strong>
          </div>
          <div className="topbar-status">
            <span className="local-dot" />
            本地存储
            <span className="topbar-divider" />
            <span>InkReference</span>
          </div>
        </header>
        <main id="main-content" className="main-content" tabIndex={-1}>
          <div className="page-heading">
            <div>
              <div className="eyebrow">REFERENCE LIBRARY</div>
              <h1>
                {config.label}
                <span>为创作积累，给灵感归档。</span>
              </h1>
              <p>{config.description}</p>
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
                  导入 CSV
                </button>
                <button
                  className="icon-button export-button"
                  aria-label="导出素材"
                  title="导出素材"
                  onClick={() => setModal("export")}
                >
                  <ArrowDownToLine size={18} />
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
                      ? `可以手动新增${config.singular}，也可以从 CSV 批量导入。`
                      : "把已有的作品、人物和桥段 CSV 放进来，逐步搭建自己的写作参考库。"}
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
                        导入 CSV 文件
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
                {!debouncedQuery && (
                  <div className="empty-hint">
                    <CheckCircle2 size={14} />
                    支持多文件导入 · 自动检查关联 · 数据保存在本机
                  </div>
                )}
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
          <div className="workspace-footnote">
            <span>
              <Database size={13} />
              素材在本机保存
            </span>
            <span>全文清洗、AI 提取与自定义工作流将在后续批次加入。</span>
          </div>
        </main>
      </div>
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
        <ExportDialog table={table} onClose={closeModal} notify={notify} />
      )}
      {modal === "about" && (
        <Modal title="关于此工作区" onClose={closeModal}>
          <div className="modal-body">
            <div className="about-brand">
              <BookOpen size={30} />
              <div>
                <strong>InkReference</strong>
                <span>v{version} · 第一批</span>
              </div>
            </div>
            <p>
              面向写作的本地参考素材库，整理作品、人物和桥段，并保留它们之间的联系。
            </p>
            <div className="about-row">
              <span>当前已可使用</span>
              <p>CSV 导入预检、素材编辑、关联浏览、兼容与完整数据导出。</p>
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
