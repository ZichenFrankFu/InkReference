import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowDownToLine, ArrowRight, BookOpen, ChevronRight, FileText, History, Layers3, Plus, RefreshCw, RotateCcw, Search, Server, SlidersHorizontal, Upload } from "lucide-react";
import { download } from "./api";
import ChapterWorkbench from "./ChapterWorkbench";
import DocumentUpload, { DocumentSources, IngestionProgress, IngestionStatus, ingestionRunning, type IngestionTask } from "./DocumentUpload";
import { VolumePanel } from "./VolumePanel";
import { isRemoteModel, modelEndpoint } from "./model-security";
import { studio, send, normalizeList, type DocumentMeta, type TextVersion, type ModelProfile, type Workflow, type List, type Run } from "./studio";
import { Busy, Empty, Modal, Notice, PageTitle, Progress, SelectField, Status, dateLabel, messageOf } from "./ui";
import "./documents.css";

type Props = { navigate: (id: "library" | "documents" | "jobs" | "workflows" | "models" | "backup") => void; onJob: (id: string) => void };
const activeRun = (run: Run) => ["queued", "running", "cancelling"].includes(run.status);
const runStage: Record<string, string> = { queued: "等待分析", preparing: "准备分析", prepare: "准备分析", preprocess: "准备文本", extract: "提取作品特征", extracting: "提取作品特征", retrying: "重试未完成的分析", validating: "核对结果与原文", aggregate: "汇总分析结果", review: "等待审核", partial_review: "部分分析等待审核", complete: "分析完成", completed: "分析完成", failed: "分析中断", cancelled: "分析已取消", cancelling: "正在取消分析", interrupted: "分析中断", paused: "分析已暂停", approved: "已审核入库", approving: "正在审核入库" };

export default function Documents({ navigate, onJob }: Props) {
  const [items, setItems] = useState<DocumentMeta[]>([]);
  const [selected, setSelected] = useState<DocumentMeta | null>(null);
  const [version, setVersion] = useState<TextVersion | null>(null);
  const [tab, setTab] = useState("body");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [uploadOpen, setUploadOpen] = useState(false);
  const [uploadTarget, setUploadTarget] = useState<DocumentMeta | null>(null);
  const [ingestions, setIngestions] = useState<IngestionTask[]>([]);
  const [ingestion, setIngestion] = useState<IngestionTask | null>(null);
  const [chapterDirty, setChapterDirty] = useState(false);
  const [volumeDirty, setVolumeDirty] = useState(false);
  const workspaceDirty = chapterDirty || volumeDirty;
  const [chapterReset, setChapterReset] = useState(0);
  const [discardAction, setDiscardAction] = useState<(() => void) | null>(null);
  const [restoreTarget, setRestoreTarget] = useState("");
  const [startOpen, setStartOpen] = useState(false);
  const [profiles, setProfiles] = useState<ModelProfile[]>([]);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [profile, setProfile] = useState("");
  const [workflow, setWorkflow] = useState("");
  const [run, setRun] = useState<Run | null>(null);
  const [query, setQuery] = useState("");
  const continueAfterModels = useRef(false);
  const selectionRequest = useRef(0);
  const selectedRef = useRef(selected); selectedRef.current = selected;
  const ingestionRef = useRef(ingestion); ingestionRef.current = ingestion;
  const handledIngestions = useRef(new Set<string>());
  const dirtyRef = useRef(workspaceDirty); dirtyRef.current = workspaceDirty;
  const selectedProfile = profiles.find((item) => item.id === profile);
  const remote = isRemoteModel(selectedProfile);
  const target = modelEndpoint(selectedProfile?.base_url);
  const guard = (action: () => void) => dirtyRef.current ? setDiscardAction(() => action) : action();
  const openUpload = (doc: DocumentMeta | null = null) => guard(() => { setUploadTarget(doc); setUploadOpen(true); setError(""); });

  const load = useCallback(async () => {
    const [documents, tasks] = await Promise.allSettled([studio<List<DocumentMeta>>("/documents"), studio<List<IngestionTask>>("/ingestions")]);
    if (documents.status === "fulfilled") setItems(normalizeList(documents.value)); else setError(messageOf(documents.reason));
    if (tasks.status === "fulfilled") {
      const next = normalizeList(tasks.value); setIngestions(next);
      if (!ingestionRef.current) { const pending = next.find(ingestionRunning) || next.find((task) => ["failed", "interrupted"].includes(task.status)); if (pending) setIngestion(pending); }
    } else setError(messageOf(tasks.reason));
    setLoading(false);
  }, []);
  useEffect(() => { void load(); const refresh = () => void load(); window.addEventListener("inkreference:documents-refresh", refresh); return () => window.removeEventListener("inkreference:documents-refresh", refresh); }, [load]);
  useEffect(() => { const beforeUnload = (event: BeforeUnloadEvent) => { if (dirtyRef.current) event.preventDefault(); }; window.addEventListener("beforeunload", beforeUnload); return () => window.removeEventListener("beforeunload", beforeUnload); }, []);

  const choose = async (id: string, vid?: string, discard = false) => {
    if (dirtyRef.current && !discard) { setDiscardAction(() => () => void choose(id, vid, true)); return; }
    setBusy("version"); const ticket = ++selectionRequest.current; setError("");
    try {
      const doc = await studio<DocumentMeta>(`/documents/${encodeURIComponent(id)}`);
      const meta = await studio<TextVersion>(`/documents/${encodeURIComponent(id)}/versions/${encodeURIComponent(vid || doc.latest_version_id)}?include_text=false`);
      if (ticket !== selectionRequest.current) return;
      if (selectedRef.current?.id !== doc.id || version?.id !== meta.id) setRun(null);
      setSelected(doc); setVersion(meta); setChapterDirty(false); setVolumeDirty(false); dirtyRef.current = false;
    } catch (e) { if (ticket === selectionRequest.current) setError(messageOf(e)); }
    finally { if (ticket === selectionRequest.current) setBusy(""); }
  };
  const chooseRef = useRef(choose); chooseRef.current = choose;
  useEffect(() => {
    const open = (event: Event) => { const detail = (event as CustomEvent<{ document_id: string; tab?: string }>).detail; if (detail?.document_id) { const show = () => { setTab(detail.tab === "files" ? "files" : detail.tab === "volumes" ? "volumes" : "body"); void chooseRef.current(detail.document_id, undefined, true); }; if (dirtyRef.current) setDiscardAction(() => show); else show(); } };
    window.addEventListener("inkreference:document-open", open);
    try { const pending = sessionStorage.getItem("inkreference:pending-document"); if (pending) { sessionStorage.removeItem("inkreference:pending-document"); open(new CustomEvent("inkreference:document-open", { detail: JSON.parse(pending) })); } } catch { /* Navigation hints never change the source text. */ }
    return () => window.removeEventListener("inkreference:document-open", open);
  }, []);

  const receiveTask = useCallback((task: IngestionTask) => { setIngestion(task); setIngestions((all) => all.some((item) => item.id === task.id) ? all.map((item) => item.id === task.id ? task : item) : [task, ...all]); }, []);
  useEffect(() => {
    if (!ingestion || !ingestionRunning(ingestion)) return;
    let active = true; let timer = 0;
    const poll = async () => { try { const next = await studio<IngestionTask>(`/ingestions/${ingestion.id}`); if (active) receiveTask(next); } catch (e) { if (active) setError(messageOf(e)); } finally { if (active) timer = window.setTimeout(() => void poll(), 1200); } };
    timer = window.setTimeout(() => void poll(), 800);
    return () => { active = false; window.clearTimeout(timer); };
  }, [ingestion?.id, ingestion?.status, receiveTask]);
  useEffect(() => {
    if (!ingestion || ingestion.status !== "completed" || !ingestion.document_id || handledIngestions.current.has(ingestion.id)) return;
    handledIngestions.current.add(ingestion.id); setTab("body"); void chooseRef.current(ingestion.document_id, ingestion.version_id); void load(); setMessage("章节识别与问题检查已完成。");
  }, [ingestion, load]);
  const ingestionAction = async (kind: "cancel" | "retry") => { if (!ingestion) return; setBusy(`ingestion-${kind}`); setError(""); try { receiveTask(await send<IngestionTask>(`/ingestions/${ingestion.id}/${kind}`, {})); } catch (e) { setError(messageOf(e)); } finally { setBusy(""); } };

  useEffect(() => {
    if (!selected || !version) { setRun(null); return; }
    let active = true;
    void studio<List<Run>>("/jobs").then((response) => { if (active) setRun(normalizeList(response).filter((item) => item.document_id === selected.id && item.version_id === version.id).sort((a, b) => (b.created_at || "").localeCompare(a.created_at || ""))[0] || null); }).catch((e) => { if (active) setError(messageOf(e)); });
    return () => { active = false; };
  }, [selected?.id, version?.id]);
  useEffect(() => {
    if (!run || !activeRun(run)) return;
    let active = true;
    const timer = window.setInterval(() => { void studio<Run>(`/jobs/${run.id}`).then((next) => { if (active) setRun(next); }).catch((e) => { if (active) setError(messageOf(e)); }); }, 1500);
    return () => { active = false; window.clearInterval(timer); };
  }, [run?.id, run?.status]);
  const begin = async () => {
    if (!selected || !version || (remote && !selectedProfile?.target_fingerprint)) return;
    setBusy("start"); setError("");
    try { const chosen = workflows.find((item) => `${item.id}@${item.version}` === workflow); const next = await send<Run>("/jobs", { document_id: selected.id, version_id: version.id, workflow_id: chosen?.id || "default", workflow_version: chosen?.version || "1.0.0", profile_id: profile, start: true, remote_consent: remote, expected_model_fingerprint: selectedProfile?.target_fingerprint }); setRun(next); setStartOpen(false); window.dispatchEvent(new Event("inkreference:jobs-refresh")); }
    catch (e) { setError(messageOf(e)); } finally { setBusy(""); }
  };
  const startDialog = async () => {
    setBusy("config"); setError("");
    try { const [models, definitions] = await Promise.all([studio<List<ModelProfile>>("/models"), studio<List<Workflow>>("/workflows")]); const ps = normalizeList(models), ws = normalizeList(definitions); setProfiles(ps); setWorkflows(ws); setProfile((current) => ps.some((item) => item.id === current) ? current : ps[0]?.id || ""); setWorkflow(ws[0] ? `${ws[0].id}@${ws[0].version}` : ""); setStartOpen(true); }
    catch (e) { setError(messageOf(e)); } finally { setBusy(""); }
  };
  useEffect(() => { const resume = () => { if (continueAfterModels.current && selectedRef.current) { continueAfterModels.current = false; void startDialog(); } }; window.addEventListener("inkreference:documents-refresh", resume); return () => window.removeEventListener("inkreference:documents-refresh", resume); });
  useEffect(() => { const refresh = () => { void studio<List<ModelProfile>>("/models").then((result) => { const next = normalizeList(result); setProfiles(next); setProfile((current) => next.some((item) => item.id === current) ? current : next[0]?.id || ""); }).catch((e) => setError(messageOf(e))); }; window.addEventListener("inkreference:models-changed", refresh); return () => window.removeEventListener("inkreference:models-changed", refresh); }, []);
  const exportText = async () => { if (!selected || !version) return; setBusy("export"); setError(""); try { await download(`/studio/documents/${selected.id}/versions/${version.id}/download`, `${selected.title}.txt`); } catch (e) { setError(messageOf(e)); } finally { setBusy(""); } };
  const restore = async () => { if (!selected || !version) return; setBusy("restore"); setError(""); try { const next = await send<TextVersion>(`/documents/${selected.id}/chapters/restore`, { base_version_id: selected.latest_version_id, target_version_id: restoreTarget }); await choose(selected.id, next.id, true); setRestoreTarget(""); void load(); } catch (e) { setError(messageOf(e)); } finally { setBusy(""); } };
  const visibleItems = items.filter((doc) => `${doc.title} ${doc.filename}`.toLocaleLowerCase().includes(query.toLocaleLowerCase()));
  const pendingTasks = ingestions.filter((task) => ingestionRunning(task) || ["failed", "interrupted"].includes(task.status));

  return <main className="studio-main documents-page">
    <PageTitle kicker="" title="全文与清洗" description="" action={<button className="button primary" onClick={() => openUpload()}><Upload size={16} />上传文本</button>} />
    <Notice error={!!error} success={!error && !!message}>{error || message}</Notice>
    <div className="document-library-picker"><SelectField label="当前全文" value={selected?.id || ""} onChange={(id) => { if (id) void choose(id); }}><option value="">选择全文文档</option>{items.map((doc) => <option key={doc.id} value={doc.id}>{doc.title}</option>)}</SelectField></div>
    <div className="document-layout">
      <aside className="surface document-index">
        <div className="surface-heading"><h2>全文库</h2><span>{items.length}</span><button className="icon-button" onClick={() => void load()} aria-label="刷新文档"><RefreshCw size={15} /></button></div>
        {!!items.length && <label className="index-search"><Search size={15} /><input aria-label="搜索全文" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索作品或文件" /></label>}
        {loading ? <Busy label="正在读取全文库" /> : !items.length ? <div className="index-empty">上传文本，开始整理</div> : <div className="document-list">{visibleItems.map((doc) => <button key={doc.id} className={`document-item ${selected?.id === doc.id ? "active" : ""}`} onClick={() => void choose(doc.id)}><span className="document-file-icon"><FileText size={19} /></span><span><strong>{doc.title || doc.filename}</strong><small>{Number(doc.current_characters ?? doc.source_characters ?? 0).toLocaleString()} 字 · {doc.format?.toUpperCase()}</small></span><ChevronRight size={14} /></button>)}{query && !visibleItems.length && <div className="index-empty">没有匹配的全文</div>}</div>}
        {!!pendingTasks.length && <section className="document-pending-tasks"><h3>处理任务</h3>{pendingTasks.map((task) => <button key={task.id} className={ingestion?.id === task.id ? "active" : ""} onClick={() => setIngestion(task)}><span>{task.filename}</span><IngestionStatus status={task.status} /></button>)}</section>}
      </aside>
      <section className="surface document-content">
        <div className={`document-detail-progress ${busy ? "active" : ""}`}><div className="indeterminate" role={busy ? "progressbar" : undefined} aria-label={busy === "version" ? "正在读取全文版本" : "正在处理"}><i /></div></div>
        {!!pendingTasks.length && <div className="document-task-picker"><span>处理任务</span><div>{pendingTasks.map((task) => <button key={task.id} className={ingestion?.id === task.id ? "active" : ""} onClick={() => setIngestion(task)}>{task.filename}<IngestionStatus status={task.status} /></button>)}</div></div>}
        {ingestion && ingestion.status !== "completed" && <IngestionProgress task={ingestion} action={(kind) => void ingestionAction(kind)} busy={!!busy} />}
        {!selected ? <Empty icon={BookOpen} title={ingestion && ingestionRunning(ingestion) ? "正在准备正文" : "让全文成为可用的写作素材"} description={ingestion && ingestionRunning(ingestion) ? "处理完成后自动进入正文。" : "上传原始文件，识别章节，逐章清洗。"} action={<div className="empty-actions"><button className="button secondary" onClick={() => openUpload()}><Plus size={16} />上传文本</button><button className="text-button" onClick={() => navigate("models")}><SlidersHorizontal size={14} />模型配置</button></div>} /> : <>
          <div className="document-detail-header"><div className="document-heading-copy"><h2>{selected.title}</h2><span className="helper">{version?.chapters?.length || 0} 章 · {Number(version?.characters ?? selected.current_characters ?? selected.source_characters ?? 0).toLocaleString()} 字</span></div><div className="inline-actions"><button className="button secondary" disabled={!!busy || workspaceDirty} onClick={() => openUpload(selected)}><Upload size={15} />更新 / 补充</button><button className="button primary" disabled={!!busy || !version || workspaceDirty || (!!run && activeRun(run))} onClick={() => void startDialog()}><ArrowRight size={15} />开始分析</button></div></div>
          {run && <section className="document-analysis-progress" aria-label="全文分析进度"><div className="document-analysis-heading"><strong>作品分析</strong><Status value={run.status} /><button className="text-button" onClick={() => onJob(run.id)}>查看分析与审核<ArrowRight size={13} /></button></div><Progress completed={run.total ? run.completed : undefined} total={run.total || undefined} stage={runStage[run.stage] || (activeRun(run) ? "正在分析" : "分析任务")} busy={activeRun(run)} />{run.error && <Notice error>{run.error}</Notice>}</section>}
          {version && <>
            <div className="document-subbar"><div className="document-review-tabs" role="tablist" aria-label="全文工作区">{([["files", "原始文件", FileText], ["volumes", "分卷", Layers3], ["body", "正文", BookOpen]] as const).map(([id, label, TabIcon]) => <button key={id} role="tab" aria-selected={tab === id} className={tab === id ? "active" : ""} onClick={() => guard(() => setTab(id))}><TabIcon size={14} />{label}</button>)}</div><div className="document-version-history"><div className="version-select"><History size={14} /><select aria-label="全文版本" value={version.id} disabled={!!busy} onChange={(event) => void choose(selected.id, event.target.value)}>{(selected.versions || []).map((item) => <option value={item.id} key={item.id}>版本 {item.version_number} · {dateLabel(item.created_at)}</option>)}</select></div>{version.id !== selected.latest_version_id ? <button className="text-button" disabled={!!busy || workspaceDirty} onClick={() => setRestoreTarget(version.id)}><RotateCcw size={13} />恢复</button> : (version as TextVersion & { parent_version_id?: string }).parent_version_id && <button className="text-button" disabled={!!busy || workspaceDirty} onClick={() => setRestoreTarget((version as TextVersion & { parent_version_id: string }).parent_version_id)}><RotateCcw size={13} />撤销更改</button>}</div></div>
            <div hidden={tab !== "body"}><ChapterWorkbench key={`${selected.id}:${chapterReset}`} documentId={selected.id} version={version} onDirty={setChapterDirty} onVersion={async (id) => { dirtyRef.current = false; setChapterDirty(false); await choose(selected.id, id, true); void load(); }} /></div>
            {tab === "volumes" && <VolumePanel key={`${selected.id}:${version.id}:${chapterReset}`} documentId={selected.id} versionId={version.id} onDirty={setVolumeDirty} />}
            {tab === "files" && <DocumentSources document={selected} versionId={version.id} onUpload={() => openUpload(selected)} />}
            <div className="document-footer"><span>版本 {version.version_number} · {dateLabel(version.created_at)}{workspaceDirty && <strong className="document-unsaved">{volumeDirty ? "分卷尚未保存" : "正文尚未保存"}</strong>}</span><button className="button secondary compact-button" title="导出当前已保存的正文" disabled={!!busy || workspaceDirty} onClick={() => void exportText()}><ArrowDownToLine size={14} />导出正文 TXT</button></div>
          </>}
        </>}
      </section>
    </div>
    {uploadOpen && <DocumentUpload items={items} target={uploadTarget} onClose={() => setUploadOpen(false)} onSubmitted={(task) => { setUploadOpen(false); setMessage(""); receiveTask(task); }} />}
    {startOpen && <Modal title="开始全文分析" onClose={() => setStartOpen(false)} busy={busy === "start"}>
      <div className="modal-body"><div className="run-source"><FileText size={20} /><div><strong>{selected?.title}</strong><span>版本 {version?.version_number} · {Number(version?.characters ?? selected?.current_characters ?? 0).toLocaleString()} 字</span></div></div><SelectField label="工作流版本" value={workflow} onChange={setWorkflow}>{workflows.map((item) => <option key={`${item.id}@${item.version}`} value={`${item.id}@${item.version}`}>{item.name} · v{item.version}</option>)}</SelectField><SelectField label="模型配置" value={profile} onChange={setProfile}>{!profiles.length && <option value="">尚未配置模型</option>}{profiles.map((item) => <option value={item.id} key={item.id}>{item.name} · {item.model}</option>)}</SelectField><div className="document-model-actions"><button className="text-button" onClick={() => { continueAfterModels.current = true; setStartOpen(false); navigate("models"); }}><SlidersHorizontal size={14} />{profiles.length ? "管理模型配置" : "配置本地模型或商业 API"}<ArrowRight size={13} /></button><button className="text-button" disabled={!!busy} onClick={() => void startDialog()}><RefreshCw size={14} />刷新配置</button></div>{selectedProfile && <div className={`model-destination ${remote ? "remote" : "local"}`}><div><Server size={18} /><strong>{remote ? "使用远程 API 分析" : "使用本机模型分析"}</strong></div><dl><dt>目标地址</dt><dd>{target}</dd><dt>模型</dt><dd>{selectedProfile.model}</dd></dl>{remote && <><p>点击“发送并运行”将向此服务发送全文分块、提示词与 Skill。</p>{!selectedProfile.target_fingerprint && <p>请刷新模型配置。</p>}</>}</div>}<Notice error>{error}</Notice>{busy === "config" && <Busy label="正在刷新模型配置" />}{busy === "start" && <Busy label="正在创建分析任务" />}</div>
      <div className="modal-footer"><button className="button secondary" disabled={!!busy} onClick={() => setStartOpen(false)}>取消</button><button className="button primary" disabled={!profile || !workflow || !!busy || (remote && !selectedProfile?.target_fingerprint)} onClick={() => void begin()}>{remote ? "发送并运行" : "创建并运行"}<ArrowRight size={15} /></button></div>
    </Modal>}
    {restoreTarget && <Modal title="恢复历史版本" busy={busy === "restore"} onClose={() => setRestoreTarget("")}><div className="modal-body"><p>将所选历史版本恢复为新的最新版本，已有修订保留。</p><Notice error>{error}</Notice>{busy === "restore" && <Busy label="正在恢复全文版本" />}</div><div className="modal-footer"><button className="button secondary" disabled={!!busy} onClick={() => setRestoreTarget("")}>取消</button><button className="button primary" disabled={!!busy} onClick={() => void restore()}><RotateCcw size={15} />恢复此版本</button></div></Modal>}
    {discardAction && <Modal title={volumeDirty ? "分卷修改尚未保存" : "正文修改尚未保存"} onClose={() => setDiscardAction(null)}><div className="modal-body"><p>保存当前{volumeDirty ? "分卷" : "正文"}后再继续，或放弃此次修改。</p></div><div className="modal-footer"><button className="button secondary" onClick={() => setDiscardAction(null)}>继续编辑</button><button className="button danger" onClick={() => { const action = discardAction; setDiscardAction(null); setChapterDirty(false); setVolumeDirty(false); dirtyRef.current = false; setChapterReset((value) => value + 1); action(); }}>放弃修改</button></div></Modal>}
  </main>;
}
