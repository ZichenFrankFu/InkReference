import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  Check,
  CheckCircle2,
  FileSearch,
  FileText,
  Play,
  RefreshCw,
  Save,
  Search,
  Server,
  SlidersHorizontal,
  Square,
} from "lucide-react";
import {
  studio,
  send,
  normalizeList,
  type Run,
  type RunSummary,
  summarizeRun,
  type List,
  type DocumentMeta,
  type EvidenceItem,
} from "./studio";
import { isRemoteModel, modelEndpoint } from "./model-security";
import ReferenceAnalysis from "./ReferenceAnalysis";
import { ReferenceExtraction } from "./ReferenceExtraction";
import type { ReferenceState } from "./reference-types";
import "./reference-analysis.css";
import {
  Busy,
  Empty,
  Modal,
  Notice,
  PageTitle,
  Progress,
  Status,
  dateLabel,
  messageOf,
} from "./ui";
type Props = {
  initialId?: string;
  documentId?: string;
  navigate: (
    id: "library" | "documents" | "reference" | "jobs" | "workflows" | "models" | "backup",
  ) => void;
};
const names: Record<string, string> = {
  summary: "剧情梗概",
  characters: "人物",
  events: "剧情事件",
  cliches: "桥段",
};
const stageNames: Record<string, string> = {
  queued: "等待开始",
  cancelling: "正在取消",
  cancelled: "已取消",
  failed: "运行失败",
  approving: "正在校验并入库",
  approved: "已入库",
  preparing: "准备全文分块",
  extracting: "提取人物与剧情",
  retrying: "重试失败文本块",
  validating: "检查结果与来源证据",
  review: "等待人工审核",
  complete: "已完成",
};
function LegacyJobReview({ initialId, documentId, navigate }: Props) {
  const [items, setItems] = useState<RunSummary[]>([]);
  const [docs, setDocs] = useState<DocumentMeta[]>([]);
  const [selected, setSelected] = useState<Run | null>(null);
  const [draft, setDraft] = useState<Run["results"] | null>(null);
  const [tab, setTab] = useState("summary");
  const [resultLimit, setResultLimit] = useState(30);
  const [activity, setActivity] = useState(false);
  const [busy, setBusy] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [approveOpen, setApproveOpen] = useState(false);
  const [evidence, setEvidence] = useState<{
    item: EvidenceItem;
    text: string;
  } | null>(null);
  const [evidenceBusy, setEvidenceBusy] = useState(false);
  const [query, setQuery] = useState("");
  const [pendingId, setPendingId] = useState<string | null>(null);
  const dirty =
    !!draft &&
    !!selected &&
    JSON.stringify(draft) !== JSON.stringify(selected.results);
  const selectedRef = useRef(selected);
  const selectionRequest = useRef(0);
  selectedRef.current = selected;
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;
  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [r, d] = await Promise.all([
        studio<List<RunSummary>>(`/jobs?summary=true&limit=1000${documentId ? `&document_id=${encodeURIComponent(documentId)}` : ""}`),
        studio<List<DocumentMeta>>("/documents"),
      ]);
      const listed = normalizeList(r), current = selectedRef.current;
      setItems(current && (!documentId || current.document_id === documentId) && !listed.some(item => item.id === current.id) ? [summarizeRun(current), ...listed] : listed);
      setDocs(normalizeList(d));
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setLoading(false);
    }
  }, [documentId]);
  const choose = useCallback(async (id: string, discard = false) => {
    if (dirtyRef.current && selectedRef.current?.id !== id && !discard) {
      setPendingId(id);
      return;
    }
    if (dirtyRef.current && selectedRef.current?.id === id && !discard) return;
    setBusy("load");
    const ticket = ++selectionRequest.current;
    setError("");
    try {
      const run = await studio<Run>(`/jobs/${id}`);
      if (ticket !== selectionRequest.current) return;
      setSelected(run);
      setItems(items => [summarizeRun(run), ...items.filter(item => item.id !== run.id)]);
      setDraft(run.results);
      setTab("summary");
    } catch (e) {
      if (ticket === selectionRequest.current) setError(messageOf(e));
    } finally {
      if (ticket === selectionRequest.current) setBusy("");
    }
  }, []);
  useEffect(() => {
    void load();
    const refresh = () => { setLoading(true); void load(); };
    window.addEventListener("inkreference:jobs-refresh", refresh);
    return () => window.removeEventListener("inkreference:jobs-refresh", refresh);
  }, [load]);
  useEffect(() => {
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (dirtyRef.current) event.preventDefault();
    };
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, []);
  useEffect(() => {
    if (initialId) {
      void choose(initialId);
      void load();
    }
  }, [initialId, choose, load]);
  useEffect(() => {
    const timer = window.setInterval(() => {
      const run = selectedRef.current;
      if (run && ["queued", "running", "cancelling"].includes(run.status)) {
        void studio<Run>(`/jobs/${run.id}`)
          .then((next) => {
            if (selectedRef.current?.id !== next.id) return;
            setSelected(next);
            if (!dirtyRef.current) setDraft(next.results);
            setItems((items) =>
              items.map((i) => (i.id === next.id ? summarizeRun(next) : i)),
            );
          })
          .catch((e) => setError(messageOf(e)));
      }
    }, 1800);
    return () => window.clearInterval(timer);
  }, []);
  const action = async (kind: "resume" | "cancel") => {
    if (!selected) return;
    setBusy(kind);
    setError("");
    try {
      const run = await send<Run>(`/jobs/${selected.id}/${kind}`, {});
      setSelected(run);
      setDraft(run.results);
      await load();
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setBusy("");
    }
  };
  const save = async (switchTo?: string) => {
    if (!selected || !draft) return;
    setBusy("save");
    setError("");
    try {
      const run = await send<Run>(
        `/jobs/${selected.id}/results`,
        { results: draft, expected_revision: selected.revision },
        "PUT",
      );
      setSelected(run);
      setDraft(run.results);
      setMessage("审核修改已保存。");
      if (switchTo) {
        dirtyRef.current = false;
        setPendingId(null);
        await choose(switchTo, true);
      }
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setBusy("");
    }
  };
  const approve = async () => {
    if (!selected) return;
    setBusy("approve");
    setError("");
    try {
      const run = await send<Run>(`/jobs/${selected.id}/approve`, {
        expected_revision: selected.revision,
      });
      setSelected(run);
      setDraft(run.results);
      setApproveOpen(false);
      setMessage("审核通过，参考素材已入库。");
      await load();
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setBusy("");
    }
  };
  const openEvidence = async (item: EvidenceItem) => {
    if (!selected) return;
    setEvidenceBusy(true);
    setError("");
    try {
      const start = Math.max(0, (item.start || 0) - 250),
        end = (item.end || Number(item.start || 0) + 600) + 250;
      const v = await studio<{ text: string }>(
        `/documents/${selected.document_id}/versions/${selected.version_id}/text?start=${start}&limit=${Math.min(100000, Math.max(1, end - start))}`,
      );
      setEvidence({ item, text: v.text });
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setEvidenceBusy(false);
    }
  };
  const updateItem = (
    key: "characters" | "events" | "cliches",
    index: number,
    field: string,
    value: string,
  ) =>
    setDraft((d) =>
      d
        ? {
            ...d,
            [key]: d[key].map((v, i) => {
              if (i !== index) return v;
              const updated = { ...v, [field]: value };
              if (field === "evidence") {
                delete updated.quote;
                delete updated.start;
                delete updated.end;
              }
              return updated;
            }),
          }
        : d,
    );
  const running =
    selected && ["queued", "running", "cancelling"].includes(selected.status);
  const review = selected?.status === "needs_review";
  const title = selected
    ? docs.find((d) => d.id === selected.document_id)?.title ||
      selected.document_id
    : "";
  const modelSnapshot = ((selected?.snapshots || {}) as { model?: { connection_mode?: "local" | "remote"; base_url?: string; model?: string } }).model;
  const destination = modelEndpoint(modelSnapshot?.base_url);
  const remote = isRemoteModel(modelSnapshot);
  return (
    <div className="legacy-job-review">
      <Notice error={!!error} success={!error && !!message}>
        {error || message}
      </Notice>
      {busy === "load" && !selected && <Busy label="正在读取任务详情" compact />}
      {(busy === "resume" || busy === "cancel") && <Busy label={busy === "resume" ? "正在继续分析" : "正在取消运行"} compact />}
      {evidenceBusy && <Busy label="正在读取原文证据" compact />}
      <div className="jobs-layout">
        <aside className="surface jobs-index">
          <div className="surface-heading">
            <h2>分析任务</h2>
            <span>{items.length}</span>
            <button
              className="icon-button"
              aria-label="刷新分析任务"
              onClick={() => void load()}
            >
              <RefreshCw size={15} />
            </button>
          </div>
          {!!items.length && <label className="index-search"><Search size={15} /><input aria-label="搜索分析任务" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="搜索作品或任务" /></label>}
          {loading ? (
            <Busy label="正在读取任务" />
          ) : !items.length ? (
            <div className="index-empty">还没有分析任务</div>
          ) : (
            items.filter((run) => `${docs.find((d) => d.id === run.document_id)?.title || ""} ${run.id} ${run.status}`.toLocaleLowerCase().includes(query.toLocaleLowerCase())).map((run) => (
              <button
                key={run.id}
                className={`job-list-item ${selected?.id === run.id ? "active" : ""}`}
                onClick={() => void choose(run.id)}
              >
                <div>
                  <strong>
                    {docs.find((d) => d.id === run.document_id)?.title ||
                      run.document_id}
                  </strong>
                  <Status value={run.status} />
                </div>
                <small>
                  {run.workflow_id} ·{" "}
                  {dateLabel(run.created_at)}
                </small>
                <Progress
                  completed={run.completed}
                  total={run.total}
                  stage={stageNames[run.stage] || run.stage}
                  busy={["queued", "running"].includes(run.status)}
                />
              </button>
            ))
          )}
        </aside>
        <section className="surface job-workspace">
          {!selected ? (
            <Empty
              icon={FileSearch}
              title="暂无分析任务"
              action={
                <div className="empty-actions"><button
                  className="button primary"
                  onClick={() => navigate("documents")}
                >
                  前往全文
                  <ArrowRight size={15} />
                </button>
                <button className="text-button" onClick={() => navigate("models")}><SlidersHorizontal size={15} />配置本地模型或 API</button></div>
              }
            />
          ) : (
            <>
              <div className="job-heading">
                <div>
                  <span className="eyebrow">{selected.id}</span>
                  <h2>{title}</h2>
                  <div className="inline-meta">
                    <Status value={selected.status} />
                    <span>
                      {selected.workflow_id} · v{selected.workflow_version}
                    </span>
                    <span>{modelSnapshot?.model || selected.profile_id}</span>
                  </div>
                </div>
                <div className="inline-actions">
                  {running ? (
                    <button
                      className="button secondary"
                      onClick={() => void action("cancel")}
                      disabled={!!busy || selected.status === "cancelling"}
                    >
                      <Square size={14} />
                      取消运行
                    </button>
                  ) : ["cancelled", "failed", "paused"].includes(
                      selected.status,
                    ) ? (
                    <button
                      className="button primary"
                      onClick={() => void action("resume")}
                      disabled={!!busy}
                    >
                      <Play size={15} />
                      继续运行
                    </button>
                  ) : null}
                  <button
                    className={`icon-button ${activity ? "active" : ""}`}
                    aria-label="查看运行记录"
                    onClick={() => setActivity((v) => !v)}
                  >
                    <FileText size={18} />
                  </button>
                </div>
              </div>
              {modelSnapshot && <div className="job-destination"><Server size={14} /><span>{remote ? "远程 API" : "本机模型"}</span><strong>{destination}</strong><button className="text-button" onClick={() => navigate("models")}>模型设置</button></div>}
              <div className="job-progress-section">
                <Progress
                  completed={selected.completed}
                  total={selected.total}
                  stage={stageNames[selected.stage] || selected.stage}
                  busy={!!running}
                />
                <div className="coverage-line">
                  <span>文本覆盖</span>
                  <strong>
                    {Number(
                      selected.coverage?.covered_characters || 0,
                    ).toLocaleString()}{" "}
                    /{" "}
                    {Number(
                      selected.coverage?.total_characters || 0,
                    ).toLocaleString()}{" "}
                    字
                  </strong>
                  {selected.coverage?.complete && <CheckCircle2 size={14} />}
                </div>
              </div>
              {busy && (
                <Busy
                  label={
                    {
                      load: "正在读取任务详情",
                      save: "正在保存审核结果",
                      approve: "正在校验并入库",
                      resume: "正在恢复任务",
                      cancel: "正在请求取消",
                    }[busy]
                  }
                  compact
                />
              )}
              {selected.error && <Notice error>{selected.error}</Notice>}
              {activity ? (
                <div className="run-log">
                  <div className="surface-heading">
                    <h3>运行记录</h3>
                    <button
                      className="text-button"
                      onClick={() => setActivity(false)}
                    >
                      返回结果
                    </button>
                  </div>
                  {selected.events?.map((event, i) => (
                    <div className="log-entry" key={i}>
                      <span className="log-dot" />
                      <time>{dateLabel(event.time)}</time>
                      <div>
                        <strong>
                          {stageNames[event.stage] || event.stage}
                        </strong>
                        <p>{event.message}</p>
                        {event.chunk_id && <code>{event.chunk_id}</code>}
                      </div>
                    </div>
                  ))}
                  {selected.candidates
                    ?.filter((c) => c.error)
                    .map((c) => (
                      <Notice key={c.chunk_id} error>
                        {c.chunk_id} · 尝试 {c.attempts} 次：{c.error}
                      </Notice>
                    ))}
                </div>
              ) : (
                <>
                  <div
                    className="result-tabs"
                    role="tablist"
                    aria-label="分析结果"
                  >
                    {Object.entries(names).map(([key, name]) => (
                      <button
                        key={key}
                        role="tab"
                        aria-selected={tab === key}
                        className={tab === key ? "active" : ""}
                        onClick={() => {
                          setTab(key);
                          setResultLimit(30);
                        }}
                      >
                        {name}
                        {key !== "summary" && (
                          <span>
                            {(draft?.[key as "characters"] || []).length}
                          </span>
                        )}
                      </button>
                    ))}
                  </div>
                  {!draft ||
                  (!draft.summary &&
                    !draft.characters?.length &&
                    !draft.events?.length &&
                    !draft.cliches?.length) ? (
                    <div className="result-wait">
                      <FileSearch size={30} />
                      <h3>{running ? "正在整理第一批结果" : "暂无提取结果"}</h3>
                      <p>
                        {running
                          ? "结果会随着任务进度更新。"
                          : "可以查看运行记录，修正配置后继续任务。"}
                      </p>
                    </div>
                  ) : tab === "summary" ? (
                    <div className="summary-result">
                      <label className="studio-field">
                        <span>剧情梗概</span>
                        <textarea
                          value={draft.summary || ""}
                          readOnly={!review}
                          onChange={(e) =>
                            setDraft({ ...draft, summary: e.target.value })
                          }
                          rows={16}
                        />
                      </label>
                      <div className="summary-sources">
                        {selected.summary_sources?.map((source, i) => (
                          <button
                            className="source-chip"
                            key={i}
                            onClick={() => void openEvidence(source)}
                          >
                            <FileText size={12} />
                            {source.chunk_id}
                            <ArrowRight size={12} />
                          </button>
                        ))}
                      </div>
                    </div>
                  ) : (
                    <div className="extraction-cards">
                      {(draft[tab as "characters"] || [])
                        .slice(0, resultLimit)
                        .map((item, i) => (
                          <article
                            className="extraction-card"
                            key={`${tab}-${i}`}
                          >
                            <div className="extraction-number">
                              {String(i + 1).padStart(2, "0")}
                            </div>
                            <div className="extraction-fields">
                              <label className="studio-field">
                                <span>
                                  {tab === "characters"
                                    ? "人物姓名"
                                    : tab === "events"
                                      ? "事件名称"
                                      : "桥段名称"}
                                </span>
                                <input
                                  value={item.name || ""}
                                  readOnly={!review}
                                  onChange={(e) =>
                                    updateItem(
                                      tab as "characters",
                                      i,
                                      "name",
                                      e.target.value,
                                    )
                                  }
                                />
                              </label>
                              <label className="studio-field">
                                <span>内容</span>
                                <textarea
                                  value={item.description || ""}
                                  readOnly={!review}
                                  rows={3}
                                  onChange={(e) =>
                                    updateItem(
                                      tab as "characters",
                                      i,
                                      "description",
                                      e.target.value,
                                    )
                                  }
                                />
                              </label>
                              {review ? (
                                <label className="studio-field">
                                  <span>原文证据</span>
                                  <textarea
                                    rows={3}
                                    value={item.evidence || item.quote || ""}
                                    onChange={(e) =>
                                      updateItem(
                                        tab as "characters",
                                        i,
                                        "evidence",
                                        e.target.value,
                                      )
                                    }
                                  />
                                </label>
                              ) : (
                                <blockquote>
                                  {item.quote ||
                                    item.evidence ||
                                    "尚未提供原文证据"}
                                </blockquote>
                              )}
                              <button
                                className="text-button"
                                disabled={
                                  evidenceBusy || item.start === undefined
                                }
                                onClick={() => void openEvidence(item)}
                              >
                                <FileSearch size={14} />
                                回到原文{" "}
                                {item.start !== undefined && (
                                  <span>
                                    字符 {item.start}–{item.end}
                                  </span>
                                )}
                              </button>
                              {review && (
                                <button
                                  className="text-button remove-candidate"
                                  onClick={() =>
                                    setDraft((d) =>
                                      d
                                        ? {
                                            ...d,
                                            [tab]: d[
                                              tab as "characters"
                                            ].filter((_, index) => index !== i),
                                          }
                                        : d,
                                    )
                                  }
                                >
                                  移除此候选
                                </button>
                              )}
                            </div>
                          </article>
                        ))}
                      {(draft[tab as "characters"] || []).length >
                        resultLimit && (
                        <button
                          className="button secondary load-more"
                          onClick={() => setResultLimit((n) => n + 30)}
                        >
                          再显示 30 条（剩余{" "}
                          {(draft[tab as "characters"] || []).length -
                            resultLimit}{" "}
                          条）
                        </button>
                      )}
                      {!(draft[tab as "characters"] || []).length && (
                        <div className="index-empty">此类暂无结果</div>
                      )}
                    </div>
                  )}
                </>
              )}
              {review && (
                <footer className="review-footer">
                  <span>
                    {dirty ? (
                      <>
                        <i />
                        有未保存的审核修改
                      </>
                    ) : (
                      <>
                        <Check size={14} />
                        审核结果已保存
                      </>
                    )}
                  </span>
                  <div>
                    <button
                      className="button secondary"
                      disabled={!dirty || !!busy}
                      onClick={() => void save()}
                    >
                      <Save size={15} />
                      保存修改
                    </button>
                    <button
                      className="button primary"
                      disabled={dirty || !!busy}
                      onClick={() => setApproveOpen(true)}
                    >
                      <CheckCircle2 size={16} />
                      审核通过并入库
                    </button>
                  </div>
                </footer>
              )}
              {selected.status === "approved" && (
                <div className="approved-banner">
                  <CheckCircle2 size={18} />
                  <span>审核通过，素材已保存到参考作品库。</span>
                  <button
                    className="text-button"
                    onClick={() => navigate("library")}
                  >
                    查看素材
                    <ArrowRight size={14} />
                  </button>
                </div>
              )}
            </>
          )}
        </section>
      </div>
      {approveOpen && (
        <Modal
          title="审核通过并入库"
          onClose={() => setApproveOpen(false)}
          busy={busy === "approve"}
        >
          <div className="modal-body">
            <p>将当前梗概、人物与桥段写入素材库，并保留来源证据。</p>
            <div className="review-counts">
              {Object.entries(names)
                .filter(([key]) => key !== "summary")
                .map(([key, name]) => (
                  <span key={key}>
                    {name}
                    <strong>
                      {(draft?.[key as "characters"] || []).length}
                    </strong>
                  </span>
                ))}
            </div>
            <Notice error>{error}</Notice>
            {busy === "approve" && <Busy label="正在校验证据并写入素材库" />}
          </div>
          <div className="modal-footer">
            <button
              className="button secondary"
              disabled={!!busy}
              onClick={() => setApproveOpen(false)}
            >
              继续检查
            </button>
            <button
              className="button primary"
              disabled={!!busy}
              onClick={() => void approve()}
            >
              确认入库
            </button>
          </div>
        </Modal>
      )}
      {pendingId && (
        <Modal title="有未保存的审核修改" onClose={() => setPendingId(null)} busy={busy === "save"}>
          <div className="modal-body"><p>切换任务前，请保存或放弃当前修改。</p><Notice error>{error}</Notice>{busy === "save" && <Busy label="正在保存审核修改" />}</div>
          <div className="modal-footer"><button className="button secondary" onClick={() => setPendingId(null)} disabled={!!busy}>继续审核</button><button className="button danger" disabled={!!busy} onClick={() => { const id = pendingId; setPendingId(null); void choose(id, true); }}>放弃并切换</button><button className="button primary" disabled={!!busy} onClick={() => void save(pendingId)}>保存并切换</button></div>
        </Modal>
      )}
      {evidence && (
        <Modal title="原文证据" onClose={() => setEvidence(null)} wide>
          <div className="modal-body">
            <div className="evidence-meta">
              <span>{evidence.item.chunk_id || "来源文本"}</span>
              <span>
                字符 {evidence.item.start ?? "—"}–{evidence.item.end ?? "—"}
              </span>
            </div>
            {(evidence.item.quote || evidence.item.evidence) && (
              <blockquote className="evidence-quote">
                {evidence.item.quote || evidence.item.evidence}
              </blockquote>
            )}
            <pre className="evidence-context">{evidence.text}</pre>
          </div>
          <div className="modal-footer">
            <button
              className="button secondary"
              onClick={() => setEvidence(null)}
            >
              关闭
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}


function mergeRunSummaries(previous: RunSummary[], incoming: RunSummary[]) {
  return [...new Map([...previous, ...incoming].map(run => [run.id, run])).values()].sort((a, b) => (b.created_at || "").localeCompare(a.created_at || "") || a.id.localeCompare(b.id));
}
function useAnalysisLibrary(documentId?: string) {
  const [docs, setDocs] = useState<DocumentMeta[]>([]), [runs, setRuns] = useState<RunSummary[]>([]), [loading, setLoading] = useState(true), [error, setError] = useState("");
  const selectedDocument = useRef(documentId), summaries = useRef(runs);
  selectedDocument.current = documentId; summaries.current = runs;
  const load = useCallback(async () => { setLoading(true); try {
    const selected = selectedDocument.current;
    const [d, globalRuns, selectedRuns] = await Promise.all([
      studio<List<DocumentMeta>>("/documents"),
      studio<List<RunSummary>>("/jobs?summary=true&limit=1000"),
      selected ? studio<List<RunSummary>>(`/jobs?summary=true&limit=1000&document_id=${encodeURIComponent(selected)}`) : Promise.resolve({ items: [] } as List<RunSummary>),
    ]);
    setDocs(normalizeList(d)); setRuns(previous => mergeRunSummaries(previous, [...normalizeList(globalRuns), ...normalizeList(selectedRuns)])); setError("");
  } catch (e) { setError(messageOf(e)); } finally { setLoading(false); } }, []);
  const lookupRun = useCallback(async (id: string) => {
    const known = summaries.current.find(run => run.id === id);
    if (known) return known;
    const run = await studio<RunSummary>(`/jobs/${encodeURIComponent(id)}?summary=true`);
    setRuns(previous => mergeRunSummaries(previous, [run]));
    return run;
  }, []);
  useEffect(() => { void load(); const refresh = () => { void load(); }; window.addEventListener("inkreference:jobs-refresh", refresh); window.addEventListener("inkreference:library-refresh", refresh); window.addEventListener("inkreference:reference-refresh", refresh); return () => { window.removeEventListener("inkreference:jobs-refresh", refresh); window.removeEventListener("inkreference:library-refresh", refresh); window.removeEventListener("inkreference:reference-refresh", refresh); }; }, [load]);
  useEffect(() => { if (!documentId) return; let active = true; void studio<List<RunSummary>>(`/jobs?summary=true&limit=1000&document_id=${encodeURIComponent(documentId)}`).then(response => { if (active) setRuns(previous => mergeRunSummaries(previous, normalizeList(response))); }).catch(e => { if (active) setError(messageOf(e)); }); return () => { active = false; }; }, [documentId]);
  return { docs, runs, loading, error, load, lookupRun };
}
export function ReferencePage({ navigate }: Props) {
  const { docs, runs, loading, error, load } = useAnalysisLibrary();
  const [, setDirty] = useState(false);
  return <main className="studio-main reference-page"><PageTitle title="参考作品分析" /><Notice error>{error}</Notice><ReferenceAnalysis docs={docs} runs={runs} loading={loading} navigate={navigate} onRun={run => { window.dispatchEvent(new CustomEvent("inkreference:navigate", { detail: { section: "jobs", runId: run.id } })); }} onRefresh={load} onDirtyChange={setDirty} /></main>;
}
export default function Jobs({ initialId, navigate }: Props) {
  const [documentId, setDocumentId] = useState(""), [state, setState] = useState<ReferenceState | null>(null), [dirty, setDirty] = useState(false), [localError, setLocalError] = useState(""), [legacy, setLegacy] = useState(false), [focusedRunId, setFocusedRunId] = useState("");
  const { docs, runs, loading, error, load, lookupRun } = useAnalysisLibrary(documentId);
  const doc = docs.find(d => d.id === documentId);
  const initialApplied = useRef("");
  useEffect(() => { const target = (event?: Event) => { const id = (event as CustomEvent<{ documentId?: string }> | undefined)?.detail?.documentId || sessionStorage.getItem("inkreference.task-document"); if (id && !dirty) { sessionStorage.removeItem("inkreference.task-document"); setDocumentId(id); } }; target(); window.addEventListener("inkreference:task-document", target); return () => window.removeEventListener("inkreference:task-document", target); }, [dirty]);
  useEffect(() => { const focus = (event: Event) => { const target = (event as CustomEvent<{ section?: string; runId?: string }>).detail; if (target?.section === "jobs" && target.runId && !dirty) { void lookupRun(target.runId).then(run => { setDocumentId(run.document_id); setFocusedRunId(run.id); window.dispatchEvent(new CustomEvent("inkreference:focus-run", { detail: { runId: run.id } })); }).catch(e => setLocalError(messageOf(e))); } }; window.addEventListener("inkreference:catalog-focus", focus); return () => window.removeEventListener("inkreference:catalog-focus", focus); }, [lookupRun, dirty]);
  useEffect(() => { if (initialId && initialApplied.current !== initialId) { initialApplied.current = initialId; void lookupRun(initialId).then(run => { setDocumentId(run.document_id); setFocusedRunId(run.id); }).catch(e => { initialApplied.current = ""; setLocalError(messageOf(e)); }); } else if (!initialId && !documentId && docs.length) setDocumentId(docs[0].id); }, [initialId, lookupRun, docs, documentId]);
  useEffect(() => { if (!documentId || legacy) return; let active = true; setState(null); void studio<ReferenceState>(`/documents/${documentId}/reference`).then(next => { if (active) setState(next); }).catch(e => { if (active) setLocalError(messageOf(e)); }); return () => { active = false; }; }, [documentId, legacy]);
  return <main className="studio-main tasks-page"><PageTitle title="任务管理" action={<button className="button secondary" onClick={() => navigate("reference")}><FileSearch size={15} />查看作品分析</button>} /><Notice error>{error || localError}</Notice><section className="surface task-manager-panel"><div className="task-manager-toolbar"><label className="studio-field"><span>参考作品库</span><select aria-label="选择分析作品" value={documentId} disabled={dirty} onChange={e => { setDocumentId(e.target.value); setFocusedRunId(""); setLocalError(""); }}>{!docs.length && <option value="">暂无作品</option>}{docs.map(d => <option key={d.id} value={d.id}>{d.title}</option>)}</select></label><button className="icon-button" aria-label="刷新任务" disabled={dirty} onClick={() => void load()}><RefreshCw size={16} /></button>{legacy && <button className="button secondary" disabled={dirty} onClick={() => setLegacy(false)}>返回分区审核</button>}</div>{loading && !docs.length && <Busy label="正在读取任务" />}{legacy ? <LegacyJobReview initialId={focusedRunId || initialId} documentId={documentId} navigate={navigate} /> : state && doc ? <ReferenceExtraction key={doc.id} document={doc} state={state} runs={runs.filter(r => r.document_id === doc.id)} initialRunId={focusedRunId || initialId} navigate={navigate} onRun={run => { setFocusedRunId(run.id); setLegacy(true); }} onState={setState} onRefresh={load} onDirty={setDirty} /> : !loading && !docs.length ? <Empty icon={FileSearch} title="暂无参考作品" action={<button className="button primary" onClick={() => navigate("documents")}>上传文件<ArrowRight size={14} /></button>} /> : <Busy label="正在读取作品任务" />}</section></main>;
}
