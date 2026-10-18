import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowDownToLine,
  ArrowRight,
  Check,
  ChevronRight,
  FileText,
  History,
  Plus,
  RefreshCw,
  Upload,
} from "lucide-react";
import { download } from "./api";
import Conversions from "./Conversions";
import {
  studio,
  send,
  normalizeList,
  describe,
  type DocumentMeta,
  type TextVersion,
  type ModelProfile,
  type Workflow,
  type List,
  type Run,
} from "./studio";
import {
  Busy,
  Empty,
  Modal,
  Notice,
  PageTitle,
  SelectField,
  dateLabel,
  messageOf,
} from "./ui";
type Props = {
  navigate: (
    id: "library" | "documents" | "jobs" | "workflows" | "models" | "backup",
  ) => void;
  onJob: (id: string) => void;
};
export default function Documents({ navigate, onJob }: Props) {
  const [view, setView] = useState("documents");
  const [fullLoaded, setFullLoaded] = useState(true);
  const [pageStart, setPageStart] = useState(0);
  const [pageTotal, setPageTotal] = useState(0);
  const [pageText, setPageText] = useState("");
  const [items, setItems] = useState<DocumentMeta[]>([]);
  const [selected, setSelected] = useState<DocumentMeta | null>(null);
  const [version, setVersion] = useState<TextVersion | null>(null);
  const [tab, setTab] = useState("clean");
  const [chapter, setChapter] = useState(-1);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [uploadOpen, setUploadOpen] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [encoding, setEncoding] = useState("auto");
  const [workId, setWorkId] = useState("");
  const [cleanOpen, setCleanOpen] = useState(false);
  const [manual, setManual] = useState("");
  const [manualEnabled, setManualEnabled] = useState(false);
  const [options, setOptions] = useState<Record<string, unknown>>({});
  const [startOpen, setStartOpen] = useState(false);
  const [profiles, setProfiles] = useState<ModelProfile[]>([]);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [profile, setProfile] = useState("");
  const [workflow, setWorkflow] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);
  const load = useCallback(async () => {
    setLoading(true);
    try {
      setItems(normalizeList(await studio<List<DocumentMeta>>("/documents")));
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);
  const choose = async (id: string, vid?: string) => {
    setBusy("version");
    setError("");
    setChapter(-1);
    setTab("clean");
    try {
      const doc = await studio<DocumentMeta>(
        `/documents/${encodeURIComponent(id)}`,
      );
      setSelected(doc);
      const versionPath = `/documents/${encodeURIComponent(id)}/versions/${encodeURIComponent(vid || doc.latest_version_id)}`;
      const meta = await studio<TextVersion>(
        `${versionPath}?include_text=false`,
      );
      const complete = Number(meta.characters || 0) <= 200000;
      setFullLoaded(complete);
      setPageStart(0);
      setPageTotal(Number(meta.characters || 0));
      let result = meta;
      if (complete) {
        result = await studio<TextVersion>(versionPath);
      } else {
        const page = await studio<{
          text: string;
          start: number;
          total: number;
        }>(`${versionPath}/text?start=0&limit=20000`);
        setPageText(page.text);
        setPageStart(page.start);
        setPageTotal(page.total);
        result = { ...meta, text: "", source_text: "", diff: "" };
      }
      setVersion(result);
      setOptions(result.options || {});
      setManual(complete ? result.text || "" : "");
      setManualEnabled(false);
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setBusy("");
    }
  };
  const upload = async () => {
    if (!file) return;
    setBusy("upload");
    setError("");
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("title", title || file.name.replace(/\.[^.]+$/, ""));
      form.append("encoding", encoding);
      if (workId) form.append("work_id", workId);
      const doc = await studio<DocumentMeta>("/documents", {
        method: "POST",
        body: form,
      });
      setUploadOpen(false);
      setFile(null);
      await load();
      await choose(doc.id);
      setMessage("全文已导入，原件已保留。");
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setBusy("");
    }
  };
  const clean = async () => {
    if (!selected || !version) return;
    setBusy("clean");
    setError("");
    try {
      const result = await send<TextVersion>(
        `/documents/${selected.id}/versions`,
        {
          base_version_id: version.id,
          options,
          ...(manualEnabled ? { manual_text: manual } : {}),
        },
      );
      setCleanOpen(false);
      await choose(selected.id, result.id);
      setMessage("新的清洗版本已保存。");
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setBusy("");
    }
  };
  const begin = async () => {
    if (!selected || !version) return;
    setBusy("start");
    setError("");
    try {
      const w = workflows.find((w) => `${w.id}@${w.version}` === workflow);
      const run = await send<Run>("/jobs", {
        document_id: selected.id,
        version_id: version.id,
        workflow_id: w?.id || "default",
        workflow_version: w?.version || "1.0.0",
        profile_id: profile,
        start: true,
      });
      setStartOpen(false);
      onJob(run.id);
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setBusy("");
    }
  };
  const startDialog = async () => {
    setBusy("config");
    setError("");
    try {
      const [p, w] = await Promise.all([
        studio<List<ModelProfile>>("/models"),
        studio<List<Workflow>>("/workflows"),
      ]);
      const ps = normalizeList(p),
        ws = normalizeList(w);
      setProfiles(ps);
      setWorkflows(ws);
      setProfile(ps[0]?.id || "");
      setWorkflow(ws[0] ? `${ws[0].id}@${ws[0].version}` : "");
      setStartOpen(true);
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setBusy("");
    }
  };
  const doDownload = async (path: string, name: string) => {
    setBusy("download");
    try {
      await download(`/studio${path}`, name);
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setBusy("");
    }
  };
  const readPage = async (start: number, kind: string = tab, part?: number) => {
    if (!selected || !version) return;
    if (fullLoaded) {
      setPageStart(start);
      setPageTotal(
        Array.from(kind === "source" ? version.source_text : version.text)
          .length,
      );
      return;
    }
    setBusy("page");
    setError("");
    try {
      const p = part !== undefined && part >= 0 ? version.chapters[part] : null;
      const limit = Math.max(
        1,
        Math.min(20000, p?.end !== undefined ? p.end - start : 20000),
      );
      const result = await studio<{
        text: string;
        start: number;
        total: number;
      }>(
        `/documents/${selected.id}/versions/${version.id}/text?start=${start}&limit=${limit}&kind=${kind === "source" ? "source" : "cleaned"}`,
      );
      setPageText(result.text);
      setPageStart(result.start);
      setPageTotal(result.total);
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setBusy("");
    }
  };
  const selectTab = async (id: string) => {
    setTab(id);
    setChapter(-1);
    if (["source", "clean"].includes(id)) await readPage(0, id);
    if (id === "diff" && version && !version.diff) {
      setBusy("diff");
      try {
        const result = await studio<{ diff: string; diff_truncated: boolean }>(
          `/documents/${selected?.id}/versions/${version.id}/diff`,
        );
        setVersion((v) => (v ? { ...v, ...result } : v));
      } catch (e) {
        setError(messageOf(e));
      } finally {
        setBusy("");
      }
    }
  };
  const cpSlice = (s: string, a: number, b?: number) =>
    Array.from(s).slice(a, b).join("");
  const source = version
    ? (tab === "source" ? version.source_text : version.text) || ""
    : "";
  const chosenChapter =
    version && chapter >= 0 ? version.chapters[chapter] : null;
  const content = !fullLoaded
    ? pageText
    : cpSlice(
        source,
        pageStart,
        Math.min(
          pageStart + 20000,
          chosenChapter?.end ??
            chosenChapter?.end_offset ??
            Array.from(source).length,
        ),
      );
  return (
    <main className="studio-main">
      <PageTitle
        kicker="DOCUMENT ARCHIVE"
        title="全文与清洗"
        description="保留原件，逐版整理故事。"
        action={
          <button
            className="button primary"
            onClick={() => {
              setUploadOpen(true);
              setError("");
            }}
          >
            <Upload size={17} />
            导入全文
          </button>
        }
      />
      <div className="page-tabs">
        <button
          className={view === "documents" ? "active" : ""}
          onClick={() => setView("documents")}
        >
          <FileText size={16} />
          全文库<span>{items.length}</span>
        </button>
        <button
          className={view === "conversions" ? "active" : ""}
          onClick={() => setView("conversions")}
        >
          <RefreshCw size={16} />
          格式转换
        </button>
      </div>
      {view === "conversions" ? (
        <Conversions
          onImported={(id) => {
            setView("documents");
            void load();
            void choose(id);
          }}
        />
      ) : (
        <>
          <Notice error={!!error} success={!error && !!message}>
            {error || message}
          </Notice>
          <div className="document-layout">
            <aside className="surface document-index">
              <div className="surface-heading">
                <h2>全文文档</h2>
                <span>{items.length}</span>
                <button
                  className="icon-button"
                  onClick={() => void load()}
                  aria-label="刷新文档"
                >
                  <RefreshCw size={15} />
                </button>
              </div>
              {loading ? (
                <Busy label="正在读取文档" />
              ) : !items.length ? (
                <div className="index-empty">尚未导入全文</div>
              ) : (
                <div className="document-list">
                  {items.map((doc) => (
                    <button
                      key={doc.id}
                      className={`document-item ${selected?.id === doc.id ? "active" : ""}`}
                      onClick={() => void choose(doc.id)}
                    >
                      <span className="document-file-icon">
                        <FileText size={20} />
                      </span>
                      <span>
                        <strong>{doc.title || doc.filename}</strong>
                        <small>
                          {doc.format?.toUpperCase()} ·{" "}
                          {Number(doc.source_characters || 0).toLocaleString()}{" "}
                          字
                        </small>
                      </span>
                      <ChevronRight size={14} />
                    </button>
                  ))}
                </div>
              )}
            </aside>
            <section className="surface document-content">
              {!selected ? (
                <Empty
                  icon={FileText}
                  title="给故事一个完整的来源"
                  description="导入全文后查看清洗差异、章节与版本。"
                  action={
                    <button
                      className="button secondary"
                      onClick={() => setUploadOpen(true)}
                    >
                      <Plus size={16} />
                      添加文档
                    </button>
                  }
                />
              ) : (
                <>
                  <div className="document-detail-header">
                    <div>
                      <span className="eyebrow">
                        {selected.format?.toUpperCase()} / {selected.encoding}
                      </span>
                      <h2>{selected.title}</h2>
                      <span className="helper">{selected.filename}</span>
                    </div>
                    <div className="inline-actions">
                      <button
                        className="button secondary"
                        disabled={!!busy || !version}
                        onClick={() => setCleanOpen(true)}
                      >
                        <RefreshCw size={15} />
                        清洗与修订
                      </button>
                      <button
                        className="button primary"
                        disabled={!!busy || !version}
                        onClick={() => void startDialog()}
                      >
                        <ArrowRight size={15} />
                        开始分析
                      </button>
                    </div>
                  </div>
                  {busy && (
                    <Busy
                      label={
                        {
                          version: "正在读取版本",
                          upload: "正在导入与解析全文",
                          clean: "正在生成清洗版本",
                          config: "正在读取模型与工作流",
                          start: "正在创建分析任务",
                          download: "正在准备下载",
                          page: "正在读取正文片段",
                          diff: "正在读取清洗差异",
                        }[busy]
                      }
                      compact
                    />
                  )}
                  {version && (
                    <>
                      <div className="document-subbar">
                        <div className="segmented" aria-label="全文视图">
                          {[
                            ["clean", "清洗正文"],
                            ["source", "原始文本"],
                            ["diff", "清洗差异"],
                            ["diagnostics", "诊断"],
                          ].map(([id, label]) => (
                            <button
                              key={id}
                              className={tab === id ? "active" : ""}
                              onClick={() => void selectTab(id)}
                            >
                              {label}
                            </button>
                          ))}
                        </div>
                        <div className="version-select">
                          <History size={14} />
                          <select
                            aria-label="全文版本"
                            value={version.id}
                            onChange={(e) =>
                              void choose(selected.id, e.target.value)
                            }
                          >
                            {(selected.versions || []).map((v) => (
                              <option key={v.id} value={v.id}>
                                版本 {v.version_number} ·{" "}
                                {dateLabel(v.created_at)}
                              </option>
                            ))}
                          </select>
                        </div>
                      </div>
                      {tab === "diff" ? (
                        <pre className="diff-view">
                          {typeof version.diff === "string"
                            ? version.diff
                            : describe(version.diff || version.changes)}
                          {version.diff_truncated
                            ? "\n…差异过长，当前展示已截断。"
                            : ""}
                        </pre>
                      ) : tab === "diagnostics" ? (
                        <div className="diagnostics-view">
                          <h3>文本诊断</h3>
                          {Array.isArray(version.diagnostics) &&
                          version.diagnostics.length ? (
                            <div className="diagnostic-cards">
                              {version.diagnostics.map(
                                (
                                  item: {
                                    message?: string;
                                    sample?: string;
                                    start?: number;
                                    end?: number;
                                  },
                                  i: number,
                                ) => (
                                  <div key={i}>
                                    <strong>
                                      {item.message || "发现需要检查的文本"}
                                    </strong>
                                    <span>
                                      字符 {item.start}–{item.end}
                                    </span>
                                    {item.sample && (
                                      <blockquote>{item.sample}</blockquote>
                                    )}
                                  </div>
                                ),
                              )}
                            </div>
                          ) : (
                            <div className="diagnostic-ok">
                              <Check size={17} />
                              未发现需要手动检查的文本问题
                            </div>
                          )}
                          <h3>清洗变更</h3>
                          {Array.isArray(version.changes) &&
                          version.changes.length ? (
                            <div className="clean-change-list">
                              {version.changes.map(
                                (
                                  item: {
                                    stage?: string;
                                    count?: number;
                                    input_characters?: number;
                                    output_characters?: number;
                                  },
                                  i: number,
                                ) => (
                                  <div key={i}>
                                    <span>
                                      {(
                                        {
                                          line_endings: "换行规范化",
                                          html_entities: "还原 HTML 转义",
                                          confirmed_random_ids:
                                            "移除确认的采集 ID",
                                          manual_edit: "手动修订",
                                        } as Record<string, string>
                                      )[item.stage || ""] || item.stage}
                                    </span>
                                    <strong>{item.count} 处</strong>
                                    <small>
                                      {item.input_characters} →{" "}
                                      {item.output_characters} 字
                                    </small>
                                  </div>
                                ),
                              )}
                            </div>
                          ) : (
                            <div className="index-empty">
                              本版本没有清洗变更
                            </div>
                          )}
                          <h3>覆盖检查</h3>
                          <div className="diagnostic-ok">
                            <Check size={17} />
                            {version.coverage?.complete &&
                            version.coverage?.chunks_complete
                              ? "章节与分析块覆盖完整"
                              : "覆盖信息尚不完整"}
                          </div>
                          <details className="diagnostic-details">
                            <summary>查看校验详情</summary>
                            <pre>{describe(version.coverage || {})}</pre>
                          </details>
                        </div>
                      ) : (
                        <div className="reader-layout">
                          {tab === "clean" && (
                            <aside className="chapter-index">
                              <div>
                                章节目录{" "}
                                <span>{version.chapters?.length || 0}</span>
                              </div>
                              <button
                                className={chapter === -1 ? "active" : ""}
                                onClick={() => {
                                  setChapter(-1);
                                  void readPage(0);
                                }}
                              >
                                全文预览
                              </button>
                              {version.chapters?.map((part, i) => (
                                <button
                                  key={i}
                                  className={chapter === i ? "active" : ""}
                                  onClick={() => {
                                    setChapter(i);
                                    void readPage(
                                      part.start ?? part.start_offset ?? 0,
                                      "clean",
                                      i,
                                    );
                                  }}
                                >
                                  <span>{String(i + 1).padStart(2, "0")}</span>
                                  {part.title || `第 ${i + 1} 章`}
                                </button>
                              ))}
                            </aside>
                          )}
                          <article className="text-reader">
                            <div className="reader-meta">
                              <span>
                                {chosenChapter?.title ||
                                  (tab === "source" ? "原始文本" : "清洗正文")}
                              </span>
                              <span>
                                {Array.from(content).length.toLocaleString()} 字
                              </span>
                            </div>
                            <pre>{content}</pre>
                            {Math.min(
                              pageTotal,
                              chosenChapter?.end ?? pageTotal,
                            ) -
                              (chosenChapter?.start ?? 0) >
                              20000 && (
                              <div className="reader-pagination">
                                <button
                                  className="button secondary"
                                  disabled={
                                    !!busy ||
                                    pageStart <= (chosenChapter?.start || 0)
                                  }
                                  onClick={() =>
                                    void readPage(
                                      Math.max(
                                        chosenChapter?.start || 0,
                                        pageStart - 20000,
                                      ),
                                      tab,
                                      chapter,
                                    )
                                  }
                                >
                                  上一段
                                </button>
                                <span>
                                  字符 {pageStart}–
                                  {pageStart + Array.from(content).length} /{" "}
                                  {pageTotal.toLocaleString()}
                                </span>
                                <button
                                  className="button secondary"
                                  disabled={
                                    !!busy ||
                                    pageStart + Array.from(content).length >=
                                      Math.min(
                                        pageTotal,
                                        chosenChapter?.end || pageTotal,
                                      )
                                  }
                                  onClick={() =>
                                    void readPage(
                                      pageStart + Array.from(content).length,
                                      tab,
                                      chapter,
                                    )
                                  }
                                >
                                  下一段
                                </button>
                              </div>
                            )}
                          </article>
                        </div>
                      )}
                      <div className="document-footer">
                        <span>
                          版本 {version.version_number} ·{" "}
                          {version.chapters?.length || 0} 章 ·{" "}
                          {version.chunks?.length || 0} 个分析块
                        </span>
                        <div>
                          <button
                            className="text-button"
                            disabled={!!busy}
                            onClick={() =>
                              void doDownload(
                                `/documents/${selected.id}/original/download`,
                                selected.filename,
                              )
                            }
                          >
                            <ArrowDownToLine size={13} />
                            原件
                          </button>
                          <button
                            className="text-button"
                            disabled={!!busy}
                            onClick={() =>
                              void doDownload(
                                `/documents/${selected.id}/versions/${version.id}/download`,
                                `${selected.title}.txt`,
                              )
                            }
                          >
                            <ArrowDownToLine size={13} />
                            清洗全文
                          </button>
                        </div>
                      </div>
                    </>
                  )}
                </>
              )}
            </section>
          </div>
        </>
      )}
      {uploadOpen && (
        <Modal
          title="导入全文"
          onClose={() => setUploadOpen(false)}
          busy={busy === "upload"}
        >
          <div className="modal-body">
            <input
              ref={fileInput}
              type="file"
              accept=".txt,.md,.epub,.docx,.srt,.vtt"
              hidden
              onChange={(e) => {
                setFile(e.target.files?.[0] || null);
                setTitle(
                  e.target.files?.[0]?.name.replace(/\.[^.]+$/, "") || "",
                );
              }}
            />
            <button
              className="upload-panel"
              onClick={() => fileInput.current?.click()}
              disabled={!!busy}
            >
              <Upload size={26} />
              <strong>{file?.name || "选择小说、剧本或字幕文件"}</strong>
              <span>TXT / Markdown / EPUB / DOCX / SRT</span>
            </button>
            <label className="studio-field">
              <span>文档标题</span>
              <input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="作品名称"
              />
            </label>
            <div className="form-grid">
              <SelectField
                label="文本编码"
                value={encoding}
                onChange={setEncoding}
              >
                <option value="auto">自动识别</option>
                <option value="utf-8">UTF-8</option>
                <option value="gb18030">GB18030 / GBK</option>
                <option value="utf-16">UTF-16</option>
                <option value="big5">Big5</option>
              </SelectField>
              <label className="studio-field">
                <span>关联作品 ID（可选）</span>
                <input
                  value={workId}
                  onChange={(e) => setWorkId(e.target.value)}
                  placeholder="例如 W001"
                />
              </label>
            </div>
            <Notice error>{error}</Notice>
            {busy === "upload" && <Busy label="正在解析并清洗全文" />}
          </div>
          <div className="modal-footer">
            <button
              className="button secondary"
              disabled={!!busy}
              onClick={() => setUploadOpen(false)}
            >
              取消
            </button>
            <button
              className="button primary"
              disabled={!file || !!busy}
              onClick={() => void upload()}
            >
              <Upload size={16} />
              导入并清洗
            </button>
          </div>
        </Modal>
      )}
      {cleanOpen && (
        <Modal
          title="生成新的清洗版本"
          onClose={() => setCleanOpen(false)}
          wide
          busy={busy === "clean"}
        >
          <div className="modal-body">
            <p className="muted">
              基于版本 {version?.version_number}。已有版本保留。
            </p>
            <div className="clean-options">
              {Object.entries(options)
                .filter(([, value]) => typeof value === "boolean")
                .map(([key, value]) => (
                  <label className="check-line" key={key}>
                    <input
                      type="checkbox"
                      checked={Boolean(value)}
                      onChange={(e) =>
                        setOptions((o) => ({ ...o, [key]: e.target.checked }))
                      }
                    />
                    {(
                      {
                        remove_random_ids: "移除已确认的采集随机 ID",
                        normalize_newlines: "统一换行",
                        remove_controls: "去除控制字符",
                        decode_html: "还原 HTML 转义",
                        remove_html: "移除 HTML 标签",
                        strip_html: "移除 HTML 标签",
                        remove_author_notes: "去除作者题外话",
                        deduplicate: "去除重复内容",
                        remove_noise: "去除采集噪声",
                      } as Record<string, string>
                    )[key] || key}
                  </label>
                ))}
            </div>
            <div className="form-grid">
              {["chunk_chars", "fallback_chars"].map((key) => (
                <label className="studio-field" key={key}>
                  <span>
                    {key === "chunk_chars"
                      ? "分析块字数"
                      : "无标题文本切分字数"}
                  </span>
                  <input
                    type="number"
                    min={1}
                    max={100000}
                    value={Number(options[key] || 3000)}
                    onChange={(e) =>
                      setOptions((o) => ({
                        ...o,
                        [key]: Number(e.target.value),
                      }))
                    }
                  />
                </label>
              ))}
            </div>
            <label className="check-line manual-check">
              <input
                type="checkbox"
                disabled={!fullLoaded}
                checked={manualEnabled}
                onChange={(e) => setManualEnabled(e.target.checked)}
              />
              同时手动修订正文
            </label>
            {!fullLoaded && (
              <p className="helper">
                超长全文使用只读分页。需要修改时请下载全文，修订后重新导入。
              </p>
            )}
            {manualEnabled && (
              <textarea
                className="manual-editor"
                value={manual}
                onChange={(e) => setManual(e.target.value)}
                aria-label="手动修订正文"
              />
            )}
            <Notice error>{error}</Notice>
            {busy === "clean" && <Busy label="正在生成并校验新版本" />}
          </div>
          <div className="modal-footer">
            <button
              className="button secondary"
              disabled={!!busy}
              onClick={() => setCleanOpen(false)}
            >
              取消
            </button>
            <button
              className="button primary"
              disabled={!!busy}
              onClick={() => void clean()}
            >
              <Check size={16} />
              保存新版本
            </button>
          </div>
        </Modal>
      )}
      {startOpen && (
        <Modal
          title="开始全文分析"
          onClose={() => setStartOpen(false)}
          busy={busy === "start"}
        >
          <div className="modal-body">
            <div className="run-source">
              <FileText size={20} />
              <div>
                <strong>{selected?.title}</strong>
                <span>
                  版本 {version?.version_number} ·{" "}
                  {(
                    version?.characters ??
                    Array.from(version?.text || "").length
                  ).toLocaleString()}{" "}
                  字
                </span>
              </div>
            </div>
            <SelectField
              label="工作流版本"
              value={workflow}
              onChange={setWorkflow}
            >
              {workflows.map((w) => (
                <option
                  key={`${w.id}@${w.version}`}
                  value={`${w.id}@${w.version}`}
                >
                  {w.name} · v{w.version}
                </option>
              ))}
            </SelectField>
            <SelectField label="模型配置" value={profile} onChange={setProfile}>
              {!profiles.length && <option value="">尚未配置模型</option>}
              {profiles.map((p) => (
                <option value={p.id} key={p.id}>
                  {p.name} · {p.model}
                </option>
              ))}
            </SelectField>
            {!profiles.length && (
              <button
                className="text-button"
                onClick={() => {
                  setStartOpen(false);
                  navigate("models");
                }}
              >
                前往配置模型
                <ArrowRight size={13} />
              </button>
            )}
            <Notice error>{error}</Notice>
            {busy === "start" && <Busy label="正在固定配置并创建任务" />}
          </div>
          <div className="modal-footer">
            <button
              className="button secondary"
              disabled={!!busy}
              onClick={() => setStartOpen(false)}
            >
              取消
            </button>
            <button
              className="button primary"
              disabled={!profile || !workflow || !!busy}
              onClick={() => void begin()}
            >
              创建并运行
              <ArrowRight size={15} />
            </button>
          </div>
        </Modal>
      )}
    </main>
  );
}
