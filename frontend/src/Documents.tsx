import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowDownToLine, ArrowRight, BookOpen, CheckCircle2, ChevronDown, Link2, FileText, History, Layers3, PencilLine, RefreshCw, RotateCcw, Search, Server, SlidersHorizontal, Upload, X } from "lucide-react";
import { ApiError, download } from "./api";
import ChapterWorkbench from "./ChapterWorkbench";
import ChapterRecognitionSettings from "./ChapterRecognitionSettings";
import DocumentUpload, { DocumentSources, IngestionProgress, IngestionStatus, ingestionRunning, ingestionStage, type IngestionTask } from "./DocumentUpload";
import { VolumePanel } from "./VolumePanel";
import { isRemoteModel, modelEndpoint } from "./model-security";
import { studio, send, normalizeList, type DocumentMeta, type TextVersion, type ModelProfile, type Workflow, type List, type Run } from "./studio";
import { Busy, Empty, Modal, Notice, PageTitle, Progress, SelectField, Status, dateLabel, messageOf } from "./ui";
import "./documents.css";

type Props = { navigate: (id: "library" | "documents" | "jobs" | "workflows" | "models" | "backup") => void; onJob: (id: string) => void };
type CatalogWork = { id: string; title: string; revision: number };
type CatalogState = { document_id: string; work_id: string | null; work: CatalogWork | null; completion: { version_id: string; completed_at: string } | null; latest_version_id: string };
type VersionSummary = TextVersion & { chapter_count?: number; placeholder_count?: number; parent_version_id?: string };
type BookMetadata = { document_id: string; book_title: string; title?: string; author: string; earliest_update_at: string | null; book_type: string; metadata_revision: number; version_id: string; storage_folder?: string | null; storage_state?: string; storage_error?: string };
const documentType = (doc: DocumentMeta) => (doc as DocumentMeta & { work_category?: string; book_type?: string }).work_category || (doc as DocumentMeta & { book_type?: string }).book_type || "未分类";
const chapterLabel = (version: VersionSummary | null) => {
  const count = version?.chapter_count ?? version?.chapters?.length ?? "—";
  const missing = version?.placeholder_count ?? 0;
  return missing > 0 ? `实际 ${count} 章 · 待补全 ${missing} 章` : `${count} 章`;
};
const wordCount = (value: number | undefined) => `${(Math.max(0, Number(value) || 0) / 10000).toFixed(1)} 万字`;
const activeRun = (run: Run) => ["queued", "running", "cancelling"].includes(run.status);
const runStage: Record<string, string> = { queued: "等待分析", preparing: "准备分析", prepare: "准备分析", preprocess: "准备文本", extract: "提取作品特征", extracting: "提取作品特征", retrying: "重试未完成的分析", validating: "核对结果与原文", aggregate: "汇总分析结果", review: "等待审核", partial_review: "部分分析等待审核", complete: "分析完成", completed: "分析完成", failed: "分析中断", cancelled: "分析已取消", cancelling: "正在取消分析", interrupted: "分析中断", paused: "分析已暂停", approved: "已审核入库", approving: "正在审核入库" };

export default function Documents({ navigate, onJob }: Props) {
  const [items, setItems] = useState<DocumentMeta[]>([]);
  const [selected, setSelected] = useState<DocumentMeta | null>(null);
  const [version, setVersion] = useState<VersionSummary | null>(null);
  const [tab, setTab] = useState("body");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [uploadOpen, setUploadOpen] = useState(false);
  const [uploadMenuOpen, setUploadMenuOpen] = useState(false);
  const uploadMenu = useRef<HTMLDivElement>(null);
  const [uploadTarget, setUploadTarget] = useState<DocumentMeta | null>(null);
  const [ingestions, setIngestions] = useState<IngestionTask[]>([]);
  const [ingestion, setIngestion] = useState<IngestionTask | null>(null);
  const [chapterDirty, setChapterDirty] = useState(false);
  const [volumeDirty, setVolumeDirty] = useState(false);
  const [recognitionDirty, setRecognitionDirty] = useState(false);
  const workspaceDirty = chapterDirty || volumeDirty || recognitionDirty;
  const [chapterReset, setChapterReset] = useState(0);
  const [discardAction, setDiscardAction] = useState<(() => void) | null>(null);
  const [restoreTarget, setRestoreTarget] = useState("");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const [exported, setExported] = useState(false);
  const [exportFormat, setExportFormat] = useState("merged");
  const [metadataOpen, setMetadataOpen] = useState(false);
  const [metadata, setMetadata] = useState<BookMetadata | null>(null);
  const [metadataDraft, setMetadataDraft] = useState({ book_title: "", author: "", earliest_update_at: "", book_type: "" });
  const [catalog, setCatalog] = useState<CatalogState | null>(null);
  const [catalogWorks, setCatalogWorks] = useState<CatalogWork[]>([]);
  const [catalogQuery, setCatalogQuery] = useState("");
  const [catalogChoice, setCatalogChoice] = useState("new");
  const [catalogChoiceWork, setCatalogChoiceWork] = useState<CatalogWork | null>(null);
  const catalogInitialWorks = useRef<CatalogWork[]>([]);
  const [catalogSearching, setCatalogSearching] = useState(false);
  const [activities, setActivities] = useState<Record<string, string>>({});
  const activity = useCallback((key: string, label: string) => setActivities((current) => current[key] === label ? current : { ...current, [key]: label }), []);
  const chapterActivity = useCallback((label: string) => activity("chapters", label), [activity]);
  const recognitionActivity = useCallback((label: string) => activity("recognition", label), [activity]);
  const volumeActivity = useCallback((label: string) => activity("volumes", label), [activity]);
  const sourcesActivity = useCallback((label: string) => activity("sources", label), [activity]);
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
  const itemsRef = useRef(items); itemsRef.current = items;
  const versionRef = useRef(version); versionRef.current = version;
  const versionCache = useRef(new Map<string, VersionSummary>());
  const chooseRef = useRef<(id: string, vid?: string, discard?: boolean, known?: DocumentMeta) => Promise<void>>(async () => {});
  const ingestionRef = useRef(ingestion); ingestionRef.current = ingestion;
  const handledIngestions = useRef(new Set<string>());
  const dirtyRef = useRef(workspaceDirty); dirtyRef.current = workspaceDirty;
  const selectedProfile = profiles.find((item) => item.id === profile);
  const selectedWorkflow = workflows.find(item => `${item.id}@${item.version}` === workflow);
  const stageKeys = ["plot", "characters", "settings", "language"] as const;
  const stageProfiles = stageKeys.map(stage => ({ stage, profile: profiles.find(item => item.id === (selectedWorkflow?.stage_models?.[stage] || profile)) }));
  const modelTargets = [...new Map([selectedProfile, ...stageProfiles.map(item => item.profile)].filter((item): item is ModelProfile => !!item).map(item => [item.id, item])).values()];
  const remote = modelTargets.some(isRemoteModel);
  const modelTargetsReady = !!selectedProfile && stageProfiles.every(item => item.profile && (!isRemoteModel(item.profile) || !!item.profile.target_fingerprint)) && modelTargets.every(item => !isRemoteModel(item) || !!item.target_fingerprint);
  const guard = (action: () => void) => dirtyRef.current ? setDiscardAction(() => action) : action();
  const openUpload = (doc: DocumentMeta | null = null) => { setUploadMenuOpen(false); guard(() => { setUploadTarget(doc); setUploadOpen(true); setError(""); }); };
  useEffect(() => {
    if (!uploadMenuOpen) return;
    uploadMenu.current?.querySelector<HTMLButtonElement>('[role="menuitem"]')?.focus();
    const outside = (event: PointerEvent) => { if (!uploadMenu.current?.contains(event.target as Node)) setUploadMenuOpen(false); };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") { setUploadMenuOpen(false); uploadMenu.current?.querySelector<HTMLButtonElement>('[aria-haspopup="menu"]')?.focus(); } };
    window.addEventListener("pointerdown", outside); window.addEventListener("keydown", escape);
    return () => { window.removeEventListener("pointerdown", outside); window.removeEventListener("keydown", escape); };
  }, [uploadMenuOpen]);

  const load = useCallback(async () => {
    const documents = studio<List<DocumentMeta>>("/documents").then((data) => {
      const next = normalizeList(data); setItems(next); itemsRef.current = next;
      const current = selectedRef.current;
      if (!current && !selectionRequest.current && next.length) {
        let remembered = ""; try { remembered = sessionStorage.getItem("inkreference:current-document") || ""; } catch { /* Optional navigation memory. */ }
        const first = next.find((item) => item.id === remembered) || next[0];
        void chooseRef.current(first.id, undefined, true, first);
      } else if (current) {
        const updated = next.find((item) => item.id === current.id);
        if (updated) setSelected((prior) => prior?.id === updated.id ? { ...prior, ...updated } : prior);
      }
    }).catch((e) => setError(messageOf(e))).finally(() => setLoading(false));
    const tasks = studio<List<IngestionTask>>("/ingestions").then((data) => {
      const next = normalizeList(data); setIngestions(next);
      if (!ingestionRef.current) { const pending = next.find(ingestionRunning) || next.find((task) => ["failed", "interrupted"].includes(task.status)); if (pending) setIngestion(pending); }
    }).catch((e) => setError(messageOf(e)));
    await Promise.allSettled([documents, tasks]);
  }, []);
  useEffect(() => { void load(); const refresh = () => void load(); window.addEventListener("inkreference:documents-refresh", refresh); return () => window.removeEventListener("inkreference:documents-refresh", refresh); }, [load]);
  useEffect(() => { if (!message) return; const timer = window.setTimeout(() => setMessage(""), 3500); return () => window.clearTimeout(timer); }, [message]);
  useEffect(() => { const beforeUnload = (event: BeforeUnloadEvent) => { if (dirtyRef.current) event.preventDefault(); }; window.addEventListener("beforeunload", beforeUnload); return () => window.removeEventListener("beforeunload", beforeUnload); }, []);

  const choose = async (id: string, vid?: string, discard = false, known?: DocumentMeta) => {
    if (dirtyRef.current && !discard) { setDiscardAction(() => () => void choose(id, vid, true, known)); return; }
    const doc = known || itemsRef.current.find((item) => item.id === id);
    const targetId = vid || doc?.latest_version_id;
    if (selectedRef.current?.id === id && versionRef.current?.id === targetId) return;
    const ticket = ++selectionRequest.current; setError("");
    if (doc && selectedRef.current?.id !== id) { setSelected(doc); setVersion(null); }
    setBusy("version");
    try {
      let nextDoc = doc || await studio<DocumentMeta>(`/documents/${encodeURIComponent(id)}`);
      const requestedVersionId = targetId || nextDoc.latest_version_id;
      let meta: VersionSummary;
      let reclaimed = false;
      try {
        meta = (!vid || requestedVersionId === nextDoc.latest_version_id ? versionCache.current.get(requestedVersionId) : undefined) || await studio<VersionSummary>(`/documents/${encodeURIComponent(id)}/versions/${encodeURIComponent(requestedVersionId)}?summary=true`);
      } catch (e) {
        if (!(e instanceof ApiError) || ![404, 410].includes(e.status)) throw e;
        nextDoc = await studio<DocumentMeta>(`/documents/${encodeURIComponent(id)}`);
        if (nextDoc.latest_version_id === requestedVersionId) throw e;
        versionCache.current.delete(requestedVersionId);
        meta = await studio<VersionSummary>(`/documents/${encodeURIComponent(id)}/versions/${encodeURIComponent(nextDoc.latest_version_id)}?summary=true`);
        reclaimed = true;
      }
      if (ticket !== selectionRequest.current) return;
      if (reclaimed) setMessage("历史版本已回收，已打开最新版本");
      versionCache.current.set(meta.id, meta);
      while (versionCache.current.size > 8) versionCache.current.delete(versionCache.current.keys().next().value!);
      setSelected(nextDoc); setVersion(meta); setRun(null); setChapterDirty(false); setVolumeDirty(false); setRecognitionDirty(false); dirtyRef.current = false;
      try { sessionStorage.setItem("inkreference:current-document", id); } catch { /* Optional navigation memory. */ }
    } catch (e) { if (ticket === selectionRequest.current) setError(messageOf(e)); }
    finally { if (ticket === selectionRequest.current) setBusy(""); }
  };
  chooseRef.current = choose;
  const receiveVersion = async (id: string, received?: TextVersion) => {
    const doc = selectedRef.current; if (!doc) return;
    const meta: VersionSummary = received || versionCache.current.get(id) || await studio<VersionSummary>(`/documents/${doc.id}/versions/${id}?summary=true`);
    if (selectedRef.current?.id !== doc.id) return;
    dirtyRef.current = false; setChapterDirty(false); setRecognitionDirty(false);
    versionCache.current.set(id, meta); while (versionCache.current.size > 8) versionCache.current.delete(versionCache.current.keys().next().value!); setVersion(meta);
    const next = { ...doc, latest_version_id: id, current_characters: meta.characters ?? Number(meta.coverage?.characters || doc.current_characters || 0) };
    setSelected(next); selectedRef.current = next;
    setItems((all) => all.map((item) => item.id === doc.id ? next : item));
  };
  const openHistory = async () => {
    if (!selected) return; setHistoryOpen(true); setBusy("history"); setError("");
    try { const doc = await studio<DocumentMeta>(`/documents/${selected.id}`); if (selectedRef.current?.id === doc.id) setSelected(doc); }
    catch (e) { setError(messageOf(e)); } finally { setBusy(""); }
  };
  const openMetadata = async () => {
    if (!selected) return;
    setMetadataOpen(true); setMetadata(null); setBusy("metadata"); setError("");
    try {
      const next = await studio<BookMetadata>(`/documents/${selected.id}/book-metadata`);
      setMetadata(next); setMetadataDraft({ book_title: next.book_title || next.title || selected.title, author: next.author || "", earliest_update_at: next.earliest_update_at || "", book_type: next.book_type || "" });
    } catch (e) { setError(messageOf(e)); } finally { setBusy(""); }
  };
  const saveMetadata = async () => {
    if (!selected || !metadata || !metadataDraft.book_title.trim()) return;
    setBusy("metadata-save"); setError("");
    try {
      const next = await send<BookMetadata>(`/documents/${selected.id}/book-metadata`, { values: { ...metadataDraft, book_title: metadataDraft.book_title.trim(), earliest_update_at: metadataDraft.earliest_update_at.trim() || null }, expected_version_id: metadata.version_id, expected_revision: metadata.metadata_revision }, "PUT");
      const updated = { ...selected, title: next.book_title || next.title || selected.title, author: next.author, earliest_update_at: next.earliest_update_at, book_type: next.book_type, metadata_revision: next.metadata_revision, storage_folder: next.storage_folder || undefined, storage_state: next.storage_state };
      setSelected(updated); selectedRef.current = updated; setItems((current) => current.map((item) => item.id === updated.id ? updated : item));
      setMetadataOpen(false); window.dispatchEvent(new Event("inkreference:documents-refresh")); setMessage("作品信息已保存");
    } catch (e) { setError(messageOf(e)); } finally { setBusy(""); }
  };
  useEffect(() => {
    const open = (event: Event) => { const detail = (event as CustomEvent<{ document_id: string; tab?: string }>).detail; if (detail?.document_id) { const show = () => { setTab(detail.tab === "files" ? "files" : detail.tab === "volumes" ? "volumes" : "body"); void chooseRef.current(detail.document_id, undefined, true); }; if (dirtyRef.current) setDiscardAction(() => show); else show(); } };
    const focus = (event: Event) => { const target = (event as CustomEvent<{ section?: string; documentId?: string; versionId?: string }>).detail; if (target?.section !== "documents" || !target.documentId) return; try { sessionStorage.removeItem("inkreference.catalog-focus"); } catch { /* Optional navigation memory. */ } const show = () => { setTab("body"); void chooseRef.current(target.documentId!, target.versionId, true); }; if (dirtyRef.current) setDiscardAction(() => show); else show(); };
    window.addEventListener("inkreference:document-open", open);
    window.addEventListener("inkreference:catalog-focus", focus);
    try { const target = sessionStorage.getItem("inkreference.catalog-focus"); if (target) focus(new CustomEvent("inkreference:catalog-focus", { detail: JSON.parse(target) })); } catch { /* Optional navigation memory. */ }
    try { const pending = sessionStorage.getItem("inkreference:pending-document"); if (pending) { sessionStorage.removeItem("inkreference:pending-document"); open(new CustomEvent("inkreference:document-open", { detail: JSON.parse(pending) })); } } catch { /* Navigation hints never change the source text. */ }
    return () => { window.removeEventListener("inkreference:document-open", open); window.removeEventListener("inkreference:catalog-focus", focus); };
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
    handledIngestions.current.add(ingestion.id); setTab("body"); const documentId = ingestion.document_id, versionId = ingestion.version_id; void load().then(() => chooseRef.current(documentId, versionId)); setMessage("文件已处理");
  }, [ingestion, load]);
  const ingestionAction = async (kind: "cancel" | "retry") => { if (!ingestion) return; setBusy(`ingestion-${kind}`); setError(""); try { receiveTask(await send<IngestionTask>(`/ingestions/${ingestion.id}/${kind}`, {})); } catch (e) { setError(messageOf(e)); } finally { setBusy(""); } };

  useEffect(() => {
    if (!selected || !version) { setRun(null); return; }
    let active = true;
    void studio<List<Run>>(`/jobs?document_id=${encodeURIComponent(selected.id)}&version_id=${encodeURIComponent(version.id)}&summary=true&limit=1`).then((response) => { if (active) setRun(normalizeList(response).filter((item) => item.document_id === selected.id && item.version_id === version.id).sort((a, b) => (b.created_at || "").localeCompare(a.created_at || ""))[0] || null); }).catch((e) => { if (active) setError(messageOf(e)); });
    return () => { active = false; };
  }, [selected?.id, version?.id]);
  useEffect(() => {
    if (!run || !activeRun(run)) return;
    let active = true;
    const timer = window.setInterval(() => { void studio<Run>(`/jobs/${run.id}?summary=true`).then((next) => { if (active) setRun(next); }).catch((e) => { if (active) setError(messageOf(e)); }); }, 1500);
    return () => { active = false; window.clearInterval(timer); };
  }, [run?.id, run?.status]);
  const begin = async () => {
    if (!selected || !version || !modelTargetsReady) return;
    setBusy("start"); setError("");
    try { const chosen = workflows.find((item) => `${item.id}@${item.version}` === workflow); if (!chosen) throw new Error("请选择固定流程配置"); const next = await send<Run>("/jobs", { document_id: selected.id, version_id: version.id, workflow_id: chosen.id, workflow_version: chosen.version, profile_id: profile, start: true, remote_consent: remote, expected_model_fingerprint: selectedProfile?.target_fingerprint, expected_stage_fingerprints: Object.fromEntries(stageProfiles.filter(item => item.profile?.target_fingerprint).map(item => [item.stage, item.profile!.target_fingerprint])) }); setRun(next); setStartOpen(false); window.dispatchEvent(new Event("inkreference:jobs-refresh")); }
    catch (e) { setError(messageOf(e)); } finally { setBusy(""); }
  };
  const startDialog = async () => {
    setBusy("config"); setError("");
    try { const [models, definitions] = await Promise.all([studio<List<ModelProfile>>("/models"), studio<List<Workflow>>("/workflows")]); const ps = normalizeList(models), ws = normalizeList(definitions).filter((item) => item.fixed_pipeline); setProfiles(ps); setWorkflows(ws); setProfile((current) => ps.some((item) => item.id === current) ? current : ps[0]?.id || ""); setWorkflow(ws[0] ? `${ws[0].id}@${ws[0].version}` : ""); setStartOpen(true); }
    catch (e) { setError(messageOf(e)); } finally { setBusy(""); }
  };
  useEffect(() => { const resume = () => { if (continueAfterModels.current && selectedRef.current) { continueAfterModels.current = false; void startDialog(); } }; window.addEventListener("inkreference:documents-refresh", resume); return () => window.removeEventListener("inkreference:documents-refresh", resume); });
  useEffect(() => { const refresh = () => { void studio<List<ModelProfile>>("/models").then((result) => { const next = normalizeList(result); setProfiles(next); setProfile((current) => next.some((item) => item.id === current) ? current : next[0]?.id || ""); }).catch((e) => setError(messageOf(e))); }; window.addEventListener("inkreference:models-changed", refresh); return () => window.removeEventListener("inkreference:models-changed", refresh); }, []);
  const openFinish = async () => {
    if (!selected || !version) return;
    const documentId = selected.id;
    setExported(false); setExportFormat("merged"); setExportOpen(true); setCatalog(null); setCatalogQuery(""); setBusy("catalog"); setError("");
    try {
      const [next, choices] = await Promise.all([studio<CatalogState>(`/documents/${documentId}/catalog`), studio<{ items: CatalogWork[] }>("/catalog/works?limit=100")]);
      if (selectedRef.current?.id !== documentId) return;
      catalogInitialWorks.current = choices.items; setCatalog(next); setCatalogChoice(next.work_id || "new"); setCatalogChoiceWork(next.work); setCatalogWorks(choices.items);
    } catch (e) { setError(messageOf(e)); } finally { setBusy(""); }
  };
  useEffect(() => {
    if (!exportOpen) return;
    if (!catalogQuery.trim()) { setCatalogWorks(catalogInitialWorks.current); return; }
    let active = true; setCatalogSearching(true);
    const timer = window.setTimeout(() => { void studio<{ items: CatalogWork[] }>(`/catalog/works?q=${encodeURIComponent(catalogQuery.trim())}&limit=100`).then((result) => { if (active) setCatalogWorks(result.items); }).catch((e) => { if (active) setError(messageOf(e)); }).finally(() => { if (active) setCatalogSearching(false); }); }, 200);
    return () => { active = false; window.clearTimeout(timer); setCatalogSearching(false); };
  }, [exportOpen, catalogQuery]);
  const exportText = async () => {
    if (!selected || !version || !catalog) return;
    setBusy("export"); setError("");
    try {
      const nextCatalog = await send<CatalogState>(`/documents/${selected.id}/catalog`, { ...(catalogChoice === "new" ? { new_work: { title: selected.title } } : { work_id: catalogChoice }), expected_work_id: catalog.work_id, expected_version_id: version.id, complete: true }, "PUT");
      setCatalog(nextCatalog); setCatalogChoice(nextCatalog.work_id || "new"); setCatalogChoiceWork(nextCatalog.work);
      const nextDocument = { ...selected, work_id: nextCatalog.work_id || undefined };
      setSelected(nextDocument); selectedRef.current = nextDocument; setItems((current) => current.map((item) => item.id === selected.id ? nextDocument : item));
      window.dispatchEvent(new Event("inkreference:library-refresh"));
      await download(exportFormat === "chapters" ? `/studio/documents/${selected.id}/export/chapters?version_id=${encodeURIComponent(version.id)}` : `/studio/documents/${selected.id}/versions/${version.id}/download?format=clean`, exportFormat === "chapters" ? `${selected.title}-逐章正文.zip` : `${selected.title}.txt`); setExported(true);
    } catch (e) { setError(messageOf(e)); } finally { setBusy(""); }
  };
  const restore = async () => { if (!selected || !version) return; setBusy("restore"); setError(""); try { const next = await send<TextVersion>(`/documents/${selected.id}/chapters/restore?summary=true`, { base_version_id: selected.latest_version_id, target_version_id: restoreTarget }); await receiveVersion(next.id, next); setRestoreTarget(""); setHistoryOpen(false); } catch (e) { setError(messageOf(e)); } finally { setBusy(""); } };
  const visibleItems = items.filter((doc) => `${doc.title} ${documentType(doc)}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  const progressLabel = loading ? "正在读取作品" : busy === "version" ? "正在读取正文" : busy ? ({ catalog: "正在读取作品库", export: "正在关联作品与导出正文", config: "正在读取分析配置", history: "正在读取历史版本", restore: "正在恢复版本", start: "正在创建分析任务", metadata: "正在读取作品信息", "metadata-save": "正在保存作品信息" } as Record<string, string>)[busy] || "正在处理" : Object.values(activities).find(Boolean) || (ingestion && ingestionRunning(ingestion) ? ingestionStage(ingestion) : "");
  const finishWorks = [catalogChoiceWork, catalog?.work, ...catalogWorks].filter((work, index, all): work is CatalogWork => !!work && all.findIndex((item) => item?.id === work.id) === index);
  const pendingTasks = ingestions.filter((task) => ingestionRunning(task) || ["failed", "interrupted"].includes(task.status));

  return <main className="studio-main documents-page">
    <PageTitle title="文本清理" />
    {progressLabel && <Busy label={progressLabel} delay={150} />}
    <Notice error={!!error} success={!error && !!message}>{error || message}</Notice>
    <div className="document-layout">
      <aside className="surface document-index">
        <div className="surface-heading"><h2>参考作品库</h2><span>{items.length}</span><button className="icon-button" onClick={() => void load()} aria-label="刷新参考作品库"><RefreshCw size={15} /></button></div>
        {!!items.length && <label className="index-search"><Search size={15} /><input aria-label="搜索参考作品库" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索作品、类型" />{query && <button className="icon-button" aria-label="清除作品搜索" onClick={() => setQuery("")}><X size={13} /></button>}</label>}
        {loading ? <div className="document-list-skeleton" aria-label="正在读取作品"><i /><i /><i /></div> : !items.length ? <div className="index-empty">尚未上传文件</div> : <div className="document-list">{visibleItems.map((doc) => <div key={doc.id} className={`document-entry ${selected?.id === doc.id ? "active" : ""}`}><button className="document-item" aria-current={selected?.id === doc.id ? "true" : undefined} onClick={() => void choose(doc.id)}><strong>{doc.title || doc.filename}</strong><small><span>{wordCount(doc.current_characters ?? doc.source_characters)}</span><span>{documentType(doc)}</span></small></button></div>)}{query && !visibleItems.length && <div className="index-empty">没有匹配的作品</div>}</div>}
        {!!pendingTasks.length && <section className="document-pending-tasks"><h3>处理任务</h3>{pendingTasks.map((task) => <button key={task.id} className={ingestion?.id === task.id ? "active" : ""} onClick={() => setIngestion(task)}><span>{task.filename}</span><IngestionStatus status={task.status} /></button>)}</section>}
      </aside>
      <section className="surface document-content">
        {!!pendingTasks.length && <div className="document-task-picker"><span>处理任务</span><div>{pendingTasks.map((task) => <button key={task.id} className={ingestion?.id === task.id ? "active" : ""} onClick={() => setIngestion(task)}>{task.filename}<IngestionStatus status={task.status} /></button>)}</div></div>}
        {ingestion && ingestion.status !== "completed" && <IngestionProgress task={ingestion} action={(kind) => void ingestionAction(kind)} busy={!!busy} />}
        {!selected ? loading || busy === "version" ? <div className="document-first-load"><div className="document-loading-skeleton"><i /><i /><i /></div></div> : <Empty icon={BookOpen} title={ingestion && ingestionRunning(ingestion) ? "正在准备正文" : "尚未上传文件"} action={<button className="button secondary" onClick={() => openUpload()}><Upload size={16} />上传文件</button>} /> : <>
          <div className="document-detail-header"><div className="document-heading-copy"><h2 className="document-desktop-title">{selected.title}</h2><label className="document-title-picker"><span>参考作品库</span><select aria-label="参考作品库" value={selected.id} onChange={(event) => { if (event.target.value) void choose(event.target.value); }}>{items.map((doc) => <option key={doc.id} value={doc.id}>{doc.title} · {wordCount(doc.current_characters ?? doc.source_characters)} · {documentType(doc)}</option>)}</select></label></div><div className="inline-actions"><button className="button primary compact-button document-analyze-button" aria-label="开始分析" title="开始分析" disabled={!!busy || !version || workspaceDirty || (!!run && activeRun(run))} onClick={() => void startDialog()}><ArrowRight size={15} /><span className="document-analyze-label">开始分析</span></button></div></div>
          {run && <section className="document-analysis-progress" aria-label="全文分析进度"><div className="document-analysis-heading"><strong>作品分析</strong><Status value={run.status} /><button className="text-button" onClick={() => onJob(run.id)}>查看任务<ArrowRight size={13} /></button></div><Progress completed={run.total ? run.completed : undefined} total={run.total || undefined} stage={runStage[run.stage] || (activeRun(run) ? "正在分析" : "分析任务")} busy={activeRun(run)} />{run.error && <Notice error>{run.error}</Notice>}</section>}
          <div className="document-subbar"><div className="document-review-tabs" role="tablist" aria-label="文本清理工作区">{([["files", "元数据", FileText], ["volumes", "分卷与章节", Layers3], ["body", "正文清理", BookOpen]] as const).map(([id, label, TabIcon], index) => <button key={id} role="tab" aria-selected={tab === id} className={tab === id ? "active" : ""} onClick={() => guard(() => setTab(id))}><span className="document-step-number">{index + 1}</span><TabIcon size={14} />{label}</button>)}</div></div>
          {version ? <>
            {tab === "body" && <ChapterWorkbench key={`${selected.id}:${chapterReset}`} documentId={selected.id} version={version} onDirty={setChapterDirty} onActivity={chapterActivity} onVersion={receiveVersion} onComplete={() => guard(() => void openFinish())} />}
            {tab === "volumes" && <div className="document-structure"><VolumePanel key={`${selected.id}:${version.id}:${chapterReset}`} documentId={selected.id} versionId={version.id} onDirty={setVolumeDirty} onActivity={volumeActivity} /><ChapterRecognitionSettings key={`recognition:${selected.id}:${chapterReset}`} documentId={selected.id} version={version} onVersion={receiveVersion} onDirty={setRecognitionDirty} onActivity={recognitionActivity} blocked={volumeDirty} /></div>}
            {tab === "files" && <div className="document-metadata-workspace" aria-label="元数据"><section className="document-metadata-card"><div className="document-metadata-heading"><h3>作品信息</h3><div className="document-metadata-actions"><button className="button secondary compact-button" aria-label="编辑信息" disabled={!!busy} onClick={() => void openMetadata()}><PencilLine size={14} />编辑信息</button><button className="button secondary compact-button" aria-label="版本历史" disabled={!!busy} onClick={() => guard(() => void openHistory())}><History size={14} />版本历史</button><div className="document-upload-split" ref={uploadMenu}><button className="button primary compact-button" aria-label="上传文件" disabled={!!busy} onClick={() => openUpload(selected)}><Upload size={14} />上传文件</button><button className="button primary compact-button document-upload-options" aria-label="更多上传选项" title="更多上传选项" aria-haspopup="menu" aria-expanded={uploadMenuOpen} disabled={!!busy} onClick={() => setUploadMenuOpen(value => !value)}><ChevronDown size={14} /></button>{uploadMenuOpen && <div role="menu" className="document-upload-menu"><button role="menuitem" onClick={() => { setUploadMenuOpen(false); openUpload(); }}><Upload size={14} />上传其他作品</button></div>}</div></div></div><dl className="document-metadata-values"><div><dt>书名</dt><dd>{selected.book_title || selected.title}</dd></div><div><dt>作者</dt><dd>{selected.author || selected.catalog_author || "—"}</dd></div><div><dt>作品类型</dt><dd>{documentType(selected)}</dd></div><div><dt>最早更新时间</dt><dd>{selected.earliest_update_at || "—"}</dd></div><div><dt>字数</dt><dd>{wordCount(selected.current_characters ?? selected.source_characters)}</dd></div><div><dt>当前版本</dt><dd>版本 {version.version_number} · {chapterLabel(version)}</dd></div></dl>{selected.storage_folder && <div className="document-storage-location"><span>章节文件夹</span><code>{selected.storage_folder}</code></div>}</section><DocumentSources document={selected} versionId={version.id} onActivity={sourcesActivity} /></div>}
          </> : <div className="document-version-loading"><div className="document-loading-skeleton"><i /><i /><i /></div></div>}
        </>}
      </section>
    </div>
    {historyOpen && selected && version && <Modal title="历史版本" onClose={() => setHistoryOpen(false)} busy={busy === "history" || busy === "restore"}><div className="modal-body"><div className="document-history-list">{(selected.versions || []).map((item) => <article key={item.id}><div><strong>版本 {item.version_number}{item.id === selected.latest_version_id && <span>最新</span>}{item.id === version.id && <span>当前查看</span>}</strong><small>{dateLabel(item.created_at)}</small></div><button className="button secondary compact-button" disabled={!!busy || item.id === version.id} onClick={() => { setHistoryOpen(false); void choose(selected.id, item.id); }}>查看</button>{item.id !== selected.latest_version_id && <button className="text-button" disabled={!!busy} onClick={() => setRestoreTarget(item.id)}><RotateCcw size={13} />恢复</button>}</article>)}</div><Notice error>{error}</Notice>{busy === "history" && <Busy label="正在读取版本记录" inline />}</div><div className="modal-footer"><button className="button secondary" disabled={!!busy} onClick={() => setHistoryOpen(false)}>关闭</button></div></Modal>}
    {exportOpen && selected && version && <Modal title={exported ? "正文已导出" : "完成清理"} busy={!!busy} onClose={() => setExportOpen(false)}><div className="modal-body document-finish"><span className="document-finish-icon"><CheckCircle2 size={28} /></span><h3>{selected.title}</h3><p>{chapterLabel(version)} · {Number(version.characters ?? selected.current_characters ?? 0).toLocaleString()} 字 · 版本 {version.version_number}</p>{catalog && <div className="document-finish-link"><label className="studio-field"><span><Link2 size={14} />关联作品</span><input aria-label="搜索关联作品" value={catalogQuery} disabled={!!busy || exported} placeholder="搜索作品库" onChange={(event) => setCatalogQuery(event.target.value)} /></label><SelectField label="作品" value={catalogChoice} onChange={(id) => { setCatalogChoice(id); setCatalogChoiceWork(finishWorks.find((work) => work.id === id) || null); }} disabled={!!busy || exported}><option value="new">新建作品：《{selected.title}》</option>{finishWorks.map((work) => <option key={work.id} value={work.id}>{work.title} · {work.id}</option>)}</SelectField>{catalogSearching && <Busy label="正在搜索作品" inline delay={0} />}{exported && catalog.work_id && <button className="text-button" onClick={() => { setExportOpen(false); window.dispatchEvent(new CustomEvent("inkreference:navigate", { detail: { section: "library", workId: catalog.work_id } })); }}>查看作品<ArrowRight size={14} /></button>}</div>}{catalog && <SelectField label="导出格式" value={exportFormat} onChange={setExportFormat} disabled={!!busy}><option value="merged">合并正文 TXT</option><option value="chapters">逐章 TXT + metadata.json（ZIP）</option></SelectField>}{exported && <Notice success>正文已导出 · 已关联作品库</Notice>}<Notice error>{error}</Notice>{busy === "catalog" && <Busy label="正在读取关联作品" inline delay={0} />}{busy === "export" && <Busy label="正在关联作品与导出正文" inline delay={0} />}</div><div className="modal-footer"><button className="button secondary" disabled={!!busy} onClick={() => setExportOpen(false)}>{exported ? "关闭" : "继续清理"}</button><button className="button primary" disabled={!!busy || !catalog} onClick={() => void exportText()}><ArrowDownToLine size={15} />{exported ? "再次导出" : "完成并导出"}</button></div></Modal>}
    {metadataOpen && <Modal title="作品信息" busy={busy === "metadata" || busy === "metadata-save"} onClose={() => setMetadataOpen(false)}><div className="modal-body document-metadata-form">{metadata && <><label className="studio-field"><span>书名</span><input disabled={!!busy} aria-label="书名" value={metadataDraft.book_title} maxLength={300} onChange={(event) => setMetadataDraft((value) => ({ ...value, book_title: event.target.value }))} /></label><div className="form-grid"><label className="studio-field"><span>作者</span><input disabled={!!busy} aria-label="作者" value={metadataDraft.author} maxLength={300} onChange={(event) => setMetadataDraft((value) => ({ ...value, author: event.target.value }))} /></label><label className="studio-field"><span>作品类型</span><input disabled={!!busy} aria-label="作品类型" list="document-book-types" value={metadataDraft.book_type} maxLength={100} onChange={(event) => setMetadataDraft((value) => ({ ...value, book_type: event.target.value }))} /></label></div><label className="studio-field"><span>最早更新时间</span><input disabled={!!busy} aria-label="最早更新时间" value={metadataDraft.earliest_update_at} placeholder="YYYY / YYYY-MM / YYYY-MM-DD" maxLength={100} onChange={(event) => setMetadataDraft((value) => ({ ...value, earliest_update_at: event.target.value }))} /></label>{metadata.storage_folder && <div className="document-storage-location"><span>章节文件夹</span><code>{metadata.storage_folder}</code></div>}{metadata.storage_error && <Notice error>{metadata.storage_error}</Notice>}</>}<Notice error>{error}</Notice>{(busy === "metadata" || busy === "metadata-save") && <Busy label={busy === "metadata" ? "正在读取作品信息" : "正在保存作品信息"} inline delay={0} />}</div><div className="modal-footer"><button className="button secondary" disabled={!!busy} onClick={() => setMetadataOpen(false)}>取消</button><button className="button primary" disabled={!!busy || !metadata || !metadataDraft.book_title.trim()} onClick={() => void saveMetadata()}>保存</button></div></Modal>}
    <datalist id="document-book-types">{["中文小说", "外国小说", "电影", "电视剧", "动画", "漫画", "游戏"].map((type) => <option key={type} value={type} />)}</datalist>
    {uploadOpen && <DocumentUpload items={items} target={uploadTarget} onClose={() => setUploadOpen(false)} onSubmitted={(task) => { setUploadOpen(false); setMessage(""); receiveTask(task); }} />}
    {startOpen && <Modal title="开始全文分析" onClose={() => setStartOpen(false)} busy={busy === "start"}>
      <div className="modal-body"><div className="run-source"><FileText size={20} /><div><strong>{selected?.title}</strong><span>版本 {version?.version_number} · {Number(version?.characters ?? selected?.current_characters ?? 0).toLocaleString()} 字</span></div></div><SelectField label="工作流版本" value={workflow} onChange={setWorkflow}>{workflows.map((item) => <option key={`${item.id}@${item.version}`} value={`${item.id}@${item.version}`}>{item.name} · v{item.version}</option>)}</SelectField><SelectField label="模型配置" value={profile} onChange={setProfile}>{!profiles.length && <option value="">尚未配置模型</option>}{profiles.map((item) => <option value={item.id} key={item.id}>{item.name} · {item.model}</option>)}</SelectField><div className="document-model-actions"><button className="text-button" onClick={() => { continueAfterModels.current = true; setStartOpen(false); navigate("models"); }}><SlidersHorizontal size={14} />{profiles.length ? "管理模型配置" : "配置本地模型或商业 API"}<ArrowRight size={13} /></button><button className="text-button" disabled={!!busy} onClick={() => void startDialog()}><RefreshCw size={14} />刷新配置</button></div>{modelTargets.map(targetProfile => <div className={`model-destination ${isRemoteModel(targetProfile) ? "remote" : "local"}`} key={targetProfile.id}><div><Server size={18} /><strong>{targetProfile.name}</strong></div><dl><dt>目标地址</dt><dd>{modelEndpoint(targetProfile.base_url)}</dd><dt>模型</dt><dd>{targetProfile.model}</dd></dl></div>)}{remote && <p className="model-target-consent">发送正文分块至以上所选服务。</p>}{!modelTargetsReady && selectedProfile && <Notice error>模型配置已变化或缺失，请刷新配置。</Notice>}<Notice error>{error}</Notice>{busy === "config" && <Busy label="正在刷新模型配置" inline />}{busy === "start" && <Busy label="正在创建分析任务" inline />}</div>
      <div className="modal-footer"><button className="button secondary" disabled={!!busy} onClick={() => setStartOpen(false)}>取消</button><button className="button primary" disabled={!profile || !workflow || !!busy || !modelTargetsReady} onClick={() => void begin()}>{remote ? "发送并运行" : "创建并运行"}<ArrowRight size={15} /></button></div>
    </Modal>}
    {restoreTarget && <Modal title="恢复历史版本" busy={busy === "restore"} onClose={() => setRestoreTarget("")}><div className="modal-body"><p>将所选历史版本恢复为新的最新版本，已有修订保留。</p><Notice error>{error}</Notice>{busy === "restore" && <Busy label="正在恢复全文版本" inline />}</div><div className="modal-footer"><button className="button secondary" disabled={!!busy} onClick={() => setRestoreTarget("")}>取消</button><button className="button primary" disabled={!!busy} onClick={() => void restore()}><RotateCcw size={15} />恢复此版本</button></div></Modal>}
    {discardAction && <Modal title={volumeDirty || recognitionDirty ? "识别与分卷修改尚未保存" : "正文修改尚未保存"} onClose={() => setDiscardAction(null)}><div className="modal-body"><p>保存当前{volumeDirty || recognitionDirty ? "识别与分卷设置" : "正文"}后再继续，或放弃此次修改。</p></div><div className="modal-footer"><button className="button secondary" onClick={() => setDiscardAction(null)}>继续编辑</button><button className="button danger" onClick={() => { const action = discardAction; setDiscardAction(null); setChapterDirty(false); setVolumeDirty(false); setRecognitionDirty(false); dirtyRef.current = false; setChapterReset((value) => value + 1); action(); }}>放弃修改</button></div></Modal>}
  </main>;
}
