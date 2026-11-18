import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowRight, Check, CheckCircle2, ChevronDown, FileSearch, FileText, Pause, Play, Plus, Save, Sparkles, Square, Trash2, GitCompareArrows } from "lucide-react";
import { Busy, Modal, Notice, Progress, Status, dateLabel, messageOf } from "./ui";
import { normalizeList, send, studio, type DocumentMeta, type List, type ModelProfile, type Run, type RunSummary, type TextVersion, type Workflow } from "./studio";
import { isRemoteModel, modelEndpoint } from "./model-security";
import "./jobs.css";
import { CollectionEditor } from "./ReferenceEditors";
import { ChronicleEntities, ChroniclePlot, EraBackgroundEditor, LanguageAnalysisEditor } from "./ChronicleEditors";
import { ExtractionComparison } from "./ExtractionComparison";
import { DynamicModeSwitch, usePromptWorkbench } from "./DynamicFunction";
import ManualStageForm from "./ManualStageForm";
import { NarrativeProvider } from "./NarrativePosition";
import { record, records, stringValue, type ReferenceResult, type ReferenceSection, type ReferenceState } from "./reference-types";

type Props = {
  document: DocumentMeta; state: ReferenceState; runs: RunSummary[]; initialRunId?: string;
  navigate: (id: "library" | "documents" | "reference" | "jobs" | "workflows" | "models" | "backup") => void;
  onRun: (run: Run) => void; onState: (state: ReferenceState) => void; onRefresh: () => Promise<void>; onDirty: (dirty: boolean) => void;
};
type Chunk = Run["candidates"][number] & { index?: number; start?: number; end?: number };
const sectionNames: Record<string, string> = { plot: "剧情事件", characters: "角色", settings: "设定", style: "语言分析", era_background: "时代背景事件", rhythm: "章节节奏", opening: "开篇模式", setting_features: "设定特征" };
function candidateReference(result: unknown, chunk: Chunk): Partial<Record<ReferenceSection, unknown>> {
  const r = record(result); const rich = { ...record(r.reference) };
  if (!rich.characters && records(r.characters).length) rich.characters = records(r.characters).map(c => ({ ...c, intro: c.description }));
  if (!rich.plot && records(r.events).length) rich.plot = { logline: "", epochs: [{ title: "正文", periods: [{ time: `切块 ${Number(chunk.index || 0) + 1}`, events: r.events }] }] };
  return rich;
}
function textWorkflowVersion(value: unknown) { return Number(stringValue(value).split(".")[0]) || 1; }
function size(section: string, value: unknown) {
  if (section === "era_background") return records(record(value).items).length;
  if (section === "plot" && Array.isArray(record(value).events)) return records(record(value).events).length;
  if (section === "plot") return records(record(value).epochs).reduce((n, e) => n + records(e.periods).reduce((sum, p) => sum + records(p.events).length, 0), 0);
  return Array.isArray(value) ? value.length : Object.keys(record(value)).length;
}
export function ReferenceExtraction({ document: doc, state, runs, initialRunId, navigate, onRun, onState, onRefresh, onDirty }: Props) {
  const [run, setRun] = useState<Run | null>(null);
  const [chunkId, setChunkId] = useState("");
  const [version, setVersion] = useState<TextVersion | null>(null);
  const [mode, setMode] = useState("api");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [newOpen, setNewOpen] = useState(false);
  const [profiles, setProfiles] = useState<ModelProfile[]>([]);
  const [profileId, setProfileId] = useState("");
  const [profileB, setProfileB] = useState("");
  const [comparisonId, setComparisonId] = useState("");
  const [comparisonContext, setComparisonContext] = useState<{ id: string; runId: string } | null>(null);
  const [scopeMode, setScopeMode] = useState("all");
  const [scopeRanges, setScopeRanges] = useState([{ start_chapter: 1, end_chapter: 1 }]);
  const [chapterDirectory, setChapterDirectory] = useState<ReturnType<typeof record>[]>([]);
  const [reuseExisting, setReuseExisting] = useState(true);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [workflowId, setWorkflowId] = useState("");
  const [createMode, setCreateMode] = useState("api");
  const [source, setSource] = useState("");
  const [paste, setPaste] = useState("");
  const [manualTemplate, setManualTemplate] = useState<{stage: string | null; schema: ReturnType<typeof record> | null; template: ReturnType<typeof record> | null} | null>(null);
  const [manualData, setManualData] = useState<ReturnType<typeof record>>({});
  const [manualDirty, setManualDirty] = useState(false);
  const [manualLoading, setManualLoading] = useState(false);
  const [preview, setPreview] = useState<ReferenceResult | null>(null);
  const [legacyJSON, setLegacyJSON] = useState("");
  const [adapted, setAdapted] = useState<{ source: ReferenceResult; sections: Partial<Record<ReferenceSection, unknown>> } | null>(null);
  const [adapting, setAdapting] = useState(false);
  const [previewDirty, setPreviewDirty] = useState(false);
  const [editorDirty, setEditorDirty] = useState(false);
  const [openSection, setOpenSection] = useState<string | null>(null);
  const [pending, setPending] = useState<{ runId?: string; chunkId?: string } | null>(null);
  const initialApplied = useRef("");
  const runRef = useRef(run); runRef.current = run;
  const chunkRef = useRef(chunkId); chunkRef.current = chunkId;
  const dirtyRef = useRef(false); dirtyRef.current = previewDirty || editorDirty || manualDirty;
  const ticket = useRef(0);
  const collection = state.structure_type === "setting_collection";
  const candidate = run?.candidates.find(c => c.chunk_id === chunkId) as Chunk | undefined;
  const running = !!run && ["queued", "running", "cancelling"].includes(run.status) && run.status !== "queued";
  const manual = record(record(run?.snapshots).model).provider === "manual";
  const rawRich = candidate && preview ? candidateReference(preview, candidate) : {};
  const snapshot = record(record(run?.snapshots).model);
  const workflowSnapshot = record(record(run?.snapshots).workflow);
  const fixed = workflowSnapshot.fixed_pipeline === true;
  const contractVersion = Number(record(run?.snapshots).contract_version || record(record(run?.snapshots).skill).contract_version || workflowSnapshot.contract_version || textWorkflowVersion(workflowSnapshot.version || run?.workflow_version));
  const legacyContract = contractVersion < 4;
  const rich = legacyContract && adapted?.source === preview ? adapted.sections : rawRich;
  const richReady = !legacyContract || adapted?.source === preview;
  useEffect(() => {
    if (!legacyContract || !preview || !candidate || !run) { setAdapted(null); setAdapting(false); return; }
    const controller = new AbortController(); setAdapting(true);
    void studio<{ sections: Partial<Record<ReferenceSection, unknown>> }>(`/documents/${doc.id}/reference/preview`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sections: candidateReference(preview, candidate), source_version_id: run.version_id }), signal: controller.signal }).then(next => { if (!controller.signal.aborted) setAdapted({ source: preview, sections: next.sections }); }).catch(error => { if (!controller.signal.aborted) setError(messageOf(error)); }).finally(() => { if (!controller.signal.aborted) setAdapting(false); });
    return () => controller.abort();
  // A selected candidate is immutable unless its local JSON or server result changes.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [legacyContract, preview, doc.id, run?.version_id]);
  useEffect(() => {
    if (mode !== "manual" || !run?.id || !chunkId || legacyContract) { setManualTemplate(null); return; }
    const controller = new AbortController(); setManualLoading(true);
    void studio<{stage: string | null; schema: ReturnType<typeof record> | null; template: ReturnType<typeof record> | null}>(`/jobs/${run.id}/chunks/${chunkId}/template`, {signal: controller.signal}).then(next => { if (!controller.signal.aborted) { setManualTemplate(next); setManualData(next.template || {}); setManualDirty(false); } }).catch(e => { if (!controller.signal.aborted) setError(messageOf(e)); }).finally(() => { if (!controller.signal.aborted) setManualLoading(false); });
    return () => controller.abort();
  }, [mode, run?.id, run?.revision, chunkId, legacyContract]);
  useEffect(() => { let live = true; void studio<List<ModelProfile>>("/models").then(result => { if (live) { const items = normalizeList(result); setProfiles(items); setProfileId(current => current || items[0]?.id || ""); } }).catch(e => { if (live) setError(messageOf(e)); }); return () => { live = false; }; }, []);
  const taskStages = records(record(run).stages);
  const currentStage = stringValue(record(run).current_stage);
  const sections = collection ? ["settings", "characters", "setting_features"] : ["plot", "characters", "settings", "style", ...(rich.era_background !== undefined || fixed ? ["era_background"] : [])];
  const committed = (section: string, id = chunkId) => state.commits.some(c => c.run_id === run?.id && c.chunk_id === id && c.section === section);
  const chunkChapter = useCallback((c: Chunk) => {
    const chapters = (version?.chapters || []).map((ch, i) => ({ ...ch, index: i + 1, start: ch.start ?? ch.start_offset ?? 0, end: ch.end ?? ch.end_offset ?? 0 }));
    const hits = chapters.filter(ch => ch.end > Number(c.start || 0) && ch.start < Number(c.end || 0));
    return { first: Number(record(hits[0]).number) || hits[0]?.index || 1, last: Number(record(hits.at(-1)).number) || hits.at(-1)?.index || 1, title: hits[0]?.title || "正文" };
  }, [version]);
  const chooseChunk = useCallback((id: string, selectedRun?: Run) => {
    const current = selectedRun || runRef.current;
    if (!current) return;
    if (chunkRef.current === id && runRef.current?.id === current.id && runRef.current?.revision === current.revision) return;
    const c = current.candidates.find(c => c.chunk_id === id);
    setChunkId(id); setPreview(c?.result ? structuredClone(record(c.result)) as ReferenceResult : null); setLegacyJSON(c?.result ? JSON.stringify(c.result, null, 2) : ""); setPreviewDirty(false); setEditorDirty(false); setManualDirty(false); setManualTemplate(null); setPaste(""); setOpenSection(null); setSource(""); setError(""); setMessage("");
  }, []);
  const chooseRun = useCallback(async (id: string, discard = false) => {
    if (dirtyRef.current && !discard) { setPending({ runId: id }); return; }
    const currentTicket = ++ticket.current; setBusy("load"); setError("");
    try { const next = await studio<Run>(`/jobs/${id}`); if (currentTicket !== ticket.current) return; setRun(next); setMode(record(next.snapshots).interaction_mode === "manual" ? "manual" : record(record(next.snapshots).model).provider === "manual" ? "web" : "api"); chooseChunk(next.candidates[0]?.chunk_id || "", next); const v = await studio<TextVersion>(`/documents/${doc.id}/versions/${next.version_id}?include_text=false`); if (currentTicket === ticket.current) setVersion(v); }
    catch (e) { if (currentTicket === ticket.current) setError(messageOf(e)); }
    finally { if (currentTicket === ticket.current) setBusy(""); }
  }, [chooseChunk, doc.id]);
  useEffect(() => { if (initialRunId && initialApplied.current !== initialRunId && runs.some(r => r.id === initialRunId) && !dirtyRef.current) { initialApplied.current = initialRunId; void chooseRun(initialRunId); } else if (!runRef.current && runs.length) void chooseRun(runs[0].id); }, [runs, initialRunId, chooseRun]);
  useEffect(() => { const focus = (event: Event) => { const id = (event as CustomEvent<{ runId?: string }>).detail?.runId; if (id && runs.some(r => r.id === id) && !dirtyRef.current) void chooseRun(id); }; window.addEventListener("inkreference:focus-run", focus); return () => window.removeEventListener("inkreference:focus-run", focus); }, [runs, chooseRun]);
  useEffect(() => { onDirty(previewDirty || editorDirty || manualDirty); return () => onDirty(false); }, [previewDirty, editorDirty, manualDirty, onDirty]);
  useEffect(() => {
    const interval = window.setInterval(() => {
      const current = runRef.current;
      if (!current || !["running", "cancelling"].includes(current.status)) return;
      void studio<Run>(`/jobs/${current.id}`).then(next => {
        if (runRef.current?.id !== next.id) return;
        setRun(next);
        if (!dirtyRef.current) { const c = next.candidates.find(c => c.chunk_id === chunkRef.current); if (c?.result) setPreview(structuredClone(record(c.result)) as ReferenceResult); }
        if (!["running", "cancelling"].includes(next.status)) void onRefresh();
      }).catch(e => setError(messageOf(e)));
    }, 1200);
    return () => window.clearInterval(interval);
  }, [onRefresh]);
  const promptForChunk = async () => { if (!run || !chunkId) return; const id = chunkId; setBusy("prompt"); setError(""); try { const data = await studio<{ prompt: string; text: string }>(`/jobs/${run.id}/chunks/${chunkId}/prompt`); if (chunkRef.current === id) { setSource(data.text); } } catch (e) { setError(messageOf(e)); } finally { setBusy(""); } };
  const openCreate = async () => { setNewOpen(true); setBusy("config"); setError(""); try { const [p, w, index] = await Promise.all([studio<List<ModelProfile>>("/models"), studio<List<Workflow>>("/workflows"), collection ? Promise.resolve({chapters: []}) : studio<{chapters: unknown[]}>(`/documents/${doc.id}/versions/${doc.latest_version_id}/chapters`)]); const ps = normalizeList(p), ws = normalizeList(w).filter(workflow => workflow.fixed_pipeline && Number(record(workflow).contract_version || workflow.version.split(".")[0]) >= 4); setProfiles(ps); setWorkflows(ws); setProfileId(ps[0]?.id || ""); setProfileB(ps[1]?.id || ""); const directory = records(index.chapters).filter(chapter => !["preamble", "fallback"].includes(stringValue(chapter.kind)) && (chapter.raw_marker || chapter.placeholder)); setChapterDirectory(directory); setScopeRanges([{start_chapter: 1, end_chapter: Math.max(1, directory.length)}]); setScopeMode("all"); const preferred = ws.find(w => w.id === "default" && w.version === "4.0.0") || ws.find(w => w.id === "default" && w.version === "1.1.0") || ws[0]; setWorkflowId(preferred ? `${preferred.id}@${preferred.version}` : ""); } catch (e) { setError(messageOf(e)); } finally { setBusy(""); } };
  const selectedWorkflow = workflows.find(w => `${w.id}@${w.version}` === workflowId);
  const stageProfileIds = record(selectedWorkflow?.stage_models);
  const targetStages = (selectedWorkflow?.fixed_pipeline ? [["plot", "剧情大纲"], ["characters", "角色"], ["settings", "设定"], ["language", "语言分析与时代背景事件"]] : [["default", "全文提取"]]).map(([id, label]) => ({ id, label, profile: profiles.find(p => p.id === (stringValue(stageProfileIds[id]) || profileId)) }));
  const remoteTargets = targetStages.some(stage => isRemoteModel(stage.profile));
  const targetsReady = targetStages.every(stage => !!stage.profile && (!isRemoteModel(stage.profile) || !!stage.profile.target_fingerprint));
  const create = async () => {
    const profile = profiles.find(p => p.id === profileId), workflow = workflows.find(w => `${w.id}@${w.version}` === workflowId);
    setBusy("create"); setError("");
    try {
      let versionId = doc.latest_version_id;
      if (collection) { const source = await send<{ version: TextVersion }>(`/documents/${doc.id}/reference/prepare-source`, { expected_revision: state.revision }); versionId = source.version.id; }
      if (createMode === "ab") {
        const variants = [profileId, profileB].map((id, index) => { const targets = targetStages.map(stage => ({ ...stage, profile: profiles.find(profile => profile.id === (stringValue(stageProfileIds[stage.id]) || id)) })); const selected = profiles.find(profile => profile.id === id); return { label: index ? "B" : "A", profile_id: id, remote_consent: targets.some(target => isRemoteModel(target.profile)), expected_model_fingerprint: selected?.target_fingerprint, expected_stage_fingerprints: Object.fromEntries(targets.map(target => [target.id, target.profile?.target_fingerprint])) }; });
        const group = await send<{id: string; variants: {run_id: string}[]}>("/jobs/comparisons", { document_id: doc.id, version_id: versionId, workflow_id: workflow?.id || "default", workflow_version: workflow?.version || "4.0.0", chapter_ranges: scopeMode === "range" ? scopeRanges : undefined, reuse_existing: reuseExisting, variants, start: false });
        setComparisonId(group.id); setNewOpen(false); await onRefresh(); if (group.variants[0]) await chooseRun(group.variants[0].run_id, true); return;
      }
      const next = await send<Run>("/jobs", { document_id: doc.id, version_id: versionId, workflow_id: workflow?.id || "default", workflow_version: workflow?.version || "4.0.0", profile_id: ["web", "manual"].includes(createMode) ? undefined : profileId, manual: ["web", "manual"].includes(createMode), interaction_mode: createMode === "manual" ? "manual" : createMode === "web" ? "semi" : "automatic", start: false, chapter_ranges: scopeMode === "range" ? scopeRanges : undefined, reuse_existing: reuseExisting, remote_consent: createMode === "api" && remoteTargets, expected_model_fingerprint: createMode === "api" ? profile?.target_fingerprint : undefined, expected_stage_fingerprints: createMode === "api" && workflow?.fixed_pipeline ? Object.fromEntries(targetStages.map(stage => [stage.id, stage.profile?.target_fingerprint])) : undefined });
      setNewOpen(false); setMessage(record(next).deduplicated ? "已复用相同范围的已有任务" : ""); setRun(next); setMode(createMode); chooseChunk(next.candidates[0]?.chunk_id || "", next); await onRefresh(); await chooseRun(next.id, true); if (record(next).deduplicated) setMessage("已复用相同范围的已有任务");
    } catch (e) { setError(messageOf(e)); }
    finally { setBusy(""); }
  };
  const runAction = async (kind: "all" | "one" | "pause" | "terminate") => {
    if (!run) return; if (kind === "all" || kind === "one") { await openExtraction(kind === "all"); return; } setBusy(kind); setError("");
    try { const path = `/jobs/${run.id}/${kind}`; const next = await send<Run>(path, {}); setRun(next); await onRefresh(); } catch (e) { setError(messageOf(e)); } finally { setBusy(""); }
  };
  const savePaste = async (edited = false, contentOverride?: string) => {
    if (!run) return; setBusy("paste"); setError("");
    try { const next = await send<Run>(`/jobs/${run.id}/chunks/${chunkId}/paste`, { content: contentOverride ?? (edited ? JSON.stringify(preview) : paste), expected_revision: run.revision }); setRun(next); const c = next.candidates.find(c => c.chunk_id === chunkId); setPreview(c?.result ? structuredClone(record(c.result)) as ReferenceResult : null); setLegacyJSON(c?.result ? JSON.stringify(c.result, null, 2) : ""); setPreviewDirty(false); setEditorDirty(false); setManualDirty(false); if (!edited) { setPaste(""); } setMessage(edited ? "候选修改已保存" : "结果已解析，请逐区审核"); await onRefresh(); } catch (e) { setError(messageOf(e)); } finally { setBusy(""); }
  };
  const commit = async (section: string) => {
    if (!run) return; setBusy(`commit:${section}`); setError("");
    try { const next = await send<ReferenceState>(`/jobs/${run.id}/chunks/${chunkId}/commit`, { sections: section === "style" ? ["style", ...(rich.rhythm ? ["rhythm"] : []), ...(rich.opening ? ["opening"] : [])] : [section], expected_revision: state.revision, comparison_id: comparisonContext?.runId === run.id ? comparisonContext.id : undefined }); onState(next); setMessage(`${sectionNames[section]}已确认入库`); } catch (e) { setError(messageOf(e)); } finally { setBusy(""); }
  };
  const editSection = useCallback(async (section: ReferenceSection, value: unknown) => { setPreview(p => ({ ...p, reference: { ...candidateReference(p, (runRef.current?.candidates.find(c => c.chunk_id === chunkRef.current) || { chunk_id: "" }) as Chunk), [section]: value } })); setPreviewDirty(true); setEditorDirty(false); }, []);
  const completed = run?.candidates.filter(c => sections.every(s => state.commits.some(k => k.run_id === run.id && k.chunk_id === c.chunk_id && k.section === s))).length || 0;
  const narrativeContext = record(record(record(run?.snapshots).document).narrative_context);
  const sourceChapterNumbers = records(record(record(run?.snapshots).document).progress_chapters);
  const scopeChapterLabel = (ordinal: unknown) => { const index = Number(ordinal) - 1; return stringValue(sourceChapterNumbers[index]?.number) || stringValue(ordinal); };
  const chapterCount = Number(narrativeContext.chapter_count || 0) || records(record(record(run?.snapshots).document).progress_chapters).length || records(version?.chapters).filter(chapter => chapter.kind !== "preamble" && chapter.kind !== "fallback" && chapter.raw_marker).length;
  type ExtractionPreview = { run_id: string; chunk_id: string; expected_revision: number; content: string; stage?: string; data: unknown };
  const workbench = usePromptWorkbench<ExtractionPreview>({ operation: "extraction", title: "提取提示词与回复", mode: mode === "web" ? "semi" : "automatic", profiles, profileId, onProfileChange: setProfileId, applyLabel: "确认候选", resumeScope: {run_id: run?.id}, onApply: async (result, preparedId) => { const next = await send<Run>(`/jobs/${result.run_id}/chunks/${result.chunk_id}/paste`, { content: result.content, expected_revision: result.expected_revision, prepared_id: preparedId }); setRun(next); chooseChunk(result.chunk_id, next); const selected = next.candidates.find(item => item.chunk_id === result.chunk_id); setPreview(selected?.result ? structuredClone(record(selected.result)) as ReferenceResult : null); setMessage("候选已保存"); await onRefresh(); }, renderPreview: result => <><strong>{({plot:"剧情大纲",characters:"角色",settings:"设定",language:"语言分析与时代背景事件"} as Record<string,string>)[result.stage || ""] || "提取结果"}</strong><pre>{JSON.stringify(result.data,null,2)}</pre></> });
  const openExtraction = async (nextStage = false) => {
    if (!run) return;
    const stageKey = currentStage === "language" ? "style" : currentStage;
    const target = nextStage ? run.candidates.find(item => item.status !== "completed" && (!fixed || record(record(item.result).reference)[stageKey] === undefined)) || run.candidates.find(item => item.status !== "completed") : candidate;
    if (!target) return;
    if (target.chunk_id !== chunkId) chooseChunk(target.chunk_id);
    const targetProfile = [stringValue(record(record(record(run.snapshots).stage_models)[currentStage]).id), stringValue(record(run).profile_id), profileId].find(id => !!id && id !== "manual") || "";
    await workbench.open({ run_id: run.id, chunk_id: target.chunk_id, expected_revision: run.revision }, targetProfile);
  };
  useEffect(() => {
    const focus = (event?: Event) => { const detail = event ? (event as CustomEvent<{runId?: string}>).detail : null; const id = detail?.runId || sessionStorage.getItem("inkreference.extraction-prompt-focus"); if (!id || id !== run?.id || !chunkId || dirtyRef.current) return; sessionStorage.removeItem("inkreference.extraction-prompt-focus"); void openExtraction(true); };
    focus(); window.addEventListener("inkreference:extraction-prompt-focus", focus); return () => window.removeEventListener("inkreference:extraction-prompt-focus", focus);
  // Wait for this task and its chunk selection before consuming navigation.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run?.id, chunkId]);
  return <NarrativeProvider chapterCount={chapterCount} chapterNumbers={Array.isArray(narrativeContext.chapter_numbers) ? narrativeContext.chapter_numbers as number[] : undefined}><section className={`ref-extraction ${running ? "is-running" : ""}`}><div className="ref-section-heading"><div><h3>提取任务</h3></div><button className="button primary" onClick={() => void openCreate()} disabled={!!busy || dirtyRef.current}><Plus size={15} />新建提取任务</button></div>
    <Notice error={!!error} success={!error && !!message}>{error || message}</Notice>
    <ExtractionComparison documentId={doc.id} initialId={comparisonId} onReview={(id, groupId) => { setComparisonContext({ id: groupId, runId: id }); void chooseRun(id, true); }} />
    {!run ? <div className="ref-extract-empty"><Sparkles size={30} /><h3>暂无提取任务</h3><div className="inline-actions"><button className="button primary" onClick={() => { setCreateMode("web"); void openCreate(); }}><FileText size={15} />复制提示词与粘贴</button><button className="button secondary" onClick={() => { setCreateMode("api"); void openCreate(); }}><Sparkles size={15} />模型自动提取</button></div></div> : <>
      <div className="ref-extract-runbar"><label className="studio-field"><span>提取任务</span><select aria-label="提取任务" value={run.id} disabled={!!busy || running} onChange={e => void chooseRun(e.target.value)}>{runs.some(r => r.id === run.id) ? null : <option value={run.id}>{dateLabel(run.created_at)} · {run.workflow_id}</option>}{runs.map(r => <option key={r.id} value={r.id}>{dateLabel(r.created_at)} · {r.workflow_id} · {r.id.slice(0, 6)}</option>)}</select></label><Status value={run.status} /><button className="text-button" onClick={() => onRun(run)} disabled={dirtyRef.current}>素材入库与运行记录<ArrowRight size={13} /></button></div>
      {record(run).scope != null && <div className="extraction-scope-summary">{records(record(record(run).scope).chapter_ranges).map((range, index) => <span key={index}>第 {scopeChapterLabel(range.start_chapter)}–{scopeChapterLabel(range.end_chapter)} 章</span>)}{records(record(record(run).scope).skipped_chapters).length > 0 && <span>跳过 {records(record(record(run).scope).skipped_chapters).length} 个空白章节</span>}{records(record(record(run).scope).reused_chunks).length > 0 && <span>已复用 {records(record(record(run).scope).reused_chunks).length} 个审核切块</span>}</div>}
      {records(record(record(run).scope).overlapping_runs).length > 0 && <details className="extraction-scope-history"><summary>历史重叠 · {records(record(record(run).scope).overlapping_runs).length} 个任务</summary>{records(record(record(run).scope).overlapping_runs).map(item => <article key={stringValue(item.run_id)}><button className="text-button" disabled={dirtyRef.current || running} onClick={() => void chooseRun(stringValue(item.run_id))}>{records(item.chapter_ranges).map(range => `第 ${scopeChapterLabel(range.start_chapter)}–${scopeChapterLabel(range.end_chapter)} 章`).join("、")}</button><span>{item.status === "needs_review" ? "待审核，未复用" : records(record(record(run).scope).reused_chunks).some(chunk => chunk.run_id === item.run_id) ? "完整切块已复用" : "切块边界不同，未复用"}</span></article>)}</details>}
      {fixed && <div className="task-stage-grid">{[["plot", "剧情大纲"], ["characters", "角色"], ["settings", "设定"], ["language", "语言分析与时代背景事件"]].map(([id, label], i) => { const stage = taskStages.find(s => s.id === id || s.stage === id), total = Number(stage?.total || run.total), completed = Number(stage?.completed || 0); return <section className={`${currentStage === id ? "current" : ""} ${total && completed >= total ? "complete" : ""}`} key={id}><span>{i + 1}</span><div><strong>{label}</strong><small>{completed} / {total}</small><Progress completed={completed} total={total} stage="" busy={running && currentStage === id} /></div></section>; })}</div>}
      <div className="ref-extract-overview"><Progress completed={completed} total={run.total} stage="分区审核进度" /><div className="ref-extract-counters"><span><b>{run.completed}</b>已提取</span><span><b>{run.candidates.filter(c => c.status === "failed").length}</b>失败</span><span><b>{run.total - run.completed}</b>待处理</span></div></div>
      <div className="ref-extract-actions"><DynamicModeSwitch value={mode} onChange={setMode} disabled={running || dirtyRef.current} items={[{value:"api",label:"自动",disabled:manual},{value:"web",label:"半自动"},{value:"manual",label:"手动",disabled:legacyContract}]} /><div className="task-controls">{running ? <button className="icon-button task-control pause" aria-label="暂停提取" title="暂停提取" disabled={!!busy || run.status === "cancelling"} onClick={() => void runAction("pause")}><Pause size={17} fill="currentColor" /></button> : !manual && !["approved", "needs_review", "terminated"].includes(run.status) && <button className="icon-button task-control resume" aria-label={run.status === "queued" ? "开始提取" : "恢复提取"} title={run.status === "queued" ? "开始提取" : "恢复提取"} disabled={!!busy || dirtyRef.current || run.completed === run.total || ["approved", "terminated"].includes(run.status)} onClick={() => void runAction("all")}><Play size={17} fill="currentColor" /></button>}{!["approved", "needs_review", "terminated"].includes(run.status) && <button className="icon-button task-control terminate" aria-label="终止提取" title="终止提取" disabled={!!busy || run.stage === "terminating"} onClick={() => void runAction("terminate")}><Square size={15} fill="currentColor" /></button>}</div></div>
      {mode === "api" && <div className="ref-model-target"><span>{isRemoteModel(snapshot) ? "远程 API" : "本机模型"}</span><strong>{modelEndpoint(stringValue(snapshot.base_url))}</strong><small>{stringValue(snapshot.model)}</small></div>}
      <div className="ref-chunk-workspace"><aside className="ref-chunk-index"><div className="task-chunks-title">切块 <span>{run.total}</span></div>{run.candidates.map(raw => { const c = raw as Chunk, chapter = chunkChapter(c); const done = sections.filter(s => state.commits.some(k => k.run_id === run.id && k.chunk_id === c.chunk_id && k.section === s)).length; return <button key={c.chunk_id} className={`ref-chunk-item ${chunkId === c.chunk_id ? "active" : ""} ${done === sections.length ? "complete" : ""}`} onClick={() => { if (dirtyRef.current) setPending({ chunkId: c.chunk_id }); else chooseChunk(c.chunk_id); }}><div><strong>切块 {Number(c.index || 0) + 1}</strong>{done === sections.length ? <CheckCircle2 size={14} /> : <span className={`ref-chunk-status ${c.status}`} />}</div><small>{version?.chapters.length ? `第 ${chapter.first}–${chapter.last} 章 · ` : ""}{(Math.max(0, Number(c.end || 0) - Number(c.start || 0)) / 10000).toFixed(1)} 万字</small><div className="ref-chunk-section-dots">{sections.map(s => <span className={state.commits.some(k => k.run_id === run.id && k.chunk_id === c.chunk_id && k.section === s) ? "done" : ""} key={s} title={sectionNames[s]} />)}<small>{done}/{sections.length}</small></div></button>; })}</aside>
      <div className="ref-chunk-detail"><div className={`ref-inline-operation ${busy ? "visible" : ""}`}>{busy && <Busy label={busy === "load" ? "正在读取提取任务" : busy === "paste" ? "正在解析并校验候选" : busy.startsWith("commit:") ? "正在确认分区入库" : busy === "prompt" ? "正在准备提示词" : "正在更新任务"} compact />}</div>
        {candidate && <><div className="ref-chunk-detail-heading"><div><span className="eyebrow">CHUNK {Number(candidate.index || 0) + 1}</span><h4>第 {chunkChapter(candidate).first}–{chunkChapter(candidate).last} 章</h4><small>{Math.max(0, Number(candidate.end || 0) - Number(candidate.start || 0)).toLocaleString()} 字</small></div>{mode === "api" && <button className="button primary" disabled={!!busy || running || dirtyRef.current || candidate.status === "completed" || ["approved", "terminated"].includes(run.status)} onClick={() => void runAction(fixed ? "all" : "one")}><Sparkles size={15} />{candidate.status === "failed" ? "重试本块" : "提取本块"}</button>}</div>
          {candidate.error && <Notice error>{candidate.error}</Notice>}
          {mode === "web" && <div className="ref-paste-workspace"><button className="button primary" disabled={!!busy || running || dirtyRef.current || ["approved","terminated"].includes(run.status) || candidate.status === "completed"} onClick={() => void openExtraction(true)}><FileText size={14} />提示词与回复</button></div>}
          {mode === "manual" && <section className="ref-manual-workspace"><div className="ref-mini-heading"><h4>手动录入{manualTemplate?.stage ? ` · ${{plot:"剧情大纲",characters:"角色",settings:"设定",language:"语言分析与时代背景事件"}[manualTemplate.stage] || ""}` : ""}</h4>{manualDirty && <span className="ref-tag">未保存</span>}</div>{manualLoading ? <Busy label="读取填写模板" compact /> : manualTemplate?.schema ? <><ManualStageForm schema={manualTemplate.schema} value={manualData} sourceVersionId={run.version_id} disabled={!!busy || running || ["approved","terminated"].includes(run.status)} onChange={value => { setManualData(value); setManualDirty(true); }} /><div className="ref-editor-actions"><button className="button secondary" disabled={!!busy || !manualDirty} onClick={() => { setManualData(structuredClone(manualTemplate.template || {})); setManualDirty(false); }}>重置</button><button className="button primary" disabled={!!busy || running || ["approved","terminated"].includes(run.status)} onClick={() => void savePaste(false, JSON.stringify(manualData))}><FileSearch size={14} />校验并预览</button></div></> : <div className="ref-empty">本切块已完成</div>}</section>}

          {source ? <details className="ref-source-preview"><summary>本块原文 · {Array.from(source).length.toLocaleString()} 字<ChevronDown size={14} /></summary><pre>{source}</pre></details> : <button className="text-button" disabled={!!busy} onClick={() => void promptForChunk()}><FileText size={13} />查看本块原文</button>}
          {preview ? <>
            <div className="ref-preview-heading"><h4>逐区审核</h4>{legacyContract && <span className="ref-tag">历史合同 v{contractVersion}</span>}{previewDirty && !legacyContract && <button className="button primary" disabled={!!busy || editorDirty} onClick={() => void savePaste(true)}><Save size={14} />保存候选修改</button>}</div>
            {legacyContract && <details className="ref-source-preview legacy-candidate-json"><summary>候选 JSON<ChevronDown size={14} /></summary><textarea aria-label="历史候选 JSON" value={legacyJSON} rows={8} disabled={running || !!busy || ["approved", "terminated"].includes(run.status)} onChange={event => { setLegacyJSON(event.target.value); setPreviewDirty(true); }} /><button className="button secondary" disabled={running || !!busy || !previewDirty || ["approved", "terminated"].includes(run.status)} onClick={() => void savePaste(true, legacyJSON)}><Save size={14} />保存候选修改</button></details>}
            {adapting && <Busy label="正在读取候选视图" compact />}
            {sections.map(section => {
              const data = rich[section as ReferenceSection], exists = rawRich[section as ReferenceSection] !== undefined, done = committed(section), candidateEditable = !legacyContract && !done && !running && run.status !== "terminated" && !!preview;
              return <section className={`ref-section-review ${done ? "committed" : ""}`} key={section}><header><button className="ref-section-review-title" aria-expanded={openSection === section} onClick={() => { if (!editorDirty) setOpenSection(openSection === section ? null : section); }}><ChevronDown size={15} className={openSection === section ? "rotated" : ""} /><strong>{sectionNames[section]}</strong>{section !== "style" && <span>{size(section, data)}</span>}</button><button className={`button ${done ? "secondary" : "primary"}`} disabled={!!busy || !exists || previewDirty || editorDirty || manualDirty || done || running || candidate.status !== "completed"} onClick={() => void commit(section)}>{done ? <Check size={13} /> : <CheckCircle2 size={13} />}{done ? "已入库" : "确认入库"}</button></header>{openSection === section && <div className="ref-section-review-body">{!exists ? <div className="ref-empty">本次结果未包含此分区</div> : !richReady ? <Busy label="正在读取候选视图" compact /> : section === "plot" ? <ChroniclePlot sourceVersionId={run.version_id} value={data} onSave={candidateEditable ? value => editSection("plot", value) : undefined} busy={!!busy} onDirty={setEditorDirty} characters={records(rich.characters)} settings={records(rich.settings)} /> : section === "characters" || section === "settings" ? <ChronicleEntities sourceVersionId={run.version_id} kind={section} value={data} onSave={candidateEditable ? value => editSection(section, value) : undefined} busy={!!busy} onDirty={setEditorDirty} plot={rich.plot} characters={records(rich.characters)} settings={records(rich.settings)} /> : section === "setting_features" ? <CollectionEditor kind="setting_features" value={data} onSave={value => editSection("setting_features", value)} busy={!!busy || done || legacyContract} onDirty={setEditorDirty} settingCollection={collection} /> : section === "era_background" ? <EraBackgroundEditor sourceVersionId={run.version_id} value={data} plot={rich.plot} onSave={candidateEditable ? value => editSection("era_background", value) : undefined} characters={records(rich.characters)} busy={!!busy} onDirty={setEditorDirty} /> : <LanguageAnalysisEditor sourceVersionId={run.version_id} value={data} rhythm={rich.rhythm} opening={rich.opening} characters={records(rich.characters)} onSave={candidateEditable ? value => editSection("style", value) : undefined} onSaveRhythm={candidateEditable ? value => editSection("rhythm", value) : undefined} onSaveOpening={candidateEditable ? value => editSection("opening", value) : undefined} busy={!!busy} onDirty={setEditorDirty} />}</div>}</section>;
            })}
          </> : <div className="ref-no-candidate"><FileSearch size={25} /><span>{running ? "正在提取，结果会在这里出现" : "本块尚无提取结果"}</span></div>}
          {running && <Progress completed={run.completed} total={run.total} stage={run.stage === "retrying" ? "正在重试" : ({ plot: "剧情大纲", characters: "角色", settings: "设定", style: "语言分析", era_background: "时代背景事件" } as Record<string, string>)[run.stage] || "正在提取"} busy />}
        </>}
      </div></div>
    </>}
    {newOpen && <Modal title="新建提取任务" onClose={() => setNewOpen(false)} busy={!!busy}>
      <div className="modal-body"><DynamicModeSwitch value={createMode === "ab" ? "api" : createMode} onChange={setCreateMode} disabled={!!busy} items={[{value:"api",label:"自动"},{value:"web",label:"半自动"},{value:"manual",label:"手动"}]} /><button className={`button ${createMode === "ab" ? "primary" : "secondary"}`} aria-pressed={createMode === "ab"} disabled={!!busy} onClick={() => setCreateMode(createMode === "ab" ? "api" : "ab")}><GitCompareArrows size={14} />A/B 测试</button><label className="studio-field"><span>Agent 工作流</span><select value={workflowId} onChange={e => setWorkflowId(e.target.value)}>{workflows.map(w => <option key={`${w.id}@${w.version}`} value={`${w.id}@${w.version}`}>{w.name} · v{w.version}</option>)}</select></label>
        {!["web", "manual"].includes(createMode) && <><label className="studio-field"><span>{createMode === "ab" ? "方案 A" : "模型配置"}</span><select aria-label={createMode === "ab" ? "方案 A 模型" : "模型配置"} value={profileId} onChange={e => setProfileId(e.target.value)}>{!profiles.length && <option value="">尚未配置模型</option>}{profiles.map(p => <option key={p.id} value={p.id}>{p.name} · {p.model}</option>)}</select></label>{createMode === "ab" && <label className="studio-field"><span>方案 B</span><select aria-label="方案 B 模型" value={profileB} onChange={event => setProfileB(event.target.value)}><option value="">选择另一模型</option>{profiles.filter(profile => profile.id !== profileId).map(profile => <option value={profile.id} key={profile.id}>{profile.name} · {profile.model}</option>)}</select></label>}<button className="text-button" onClick={() => { setNewOpen(false); navigate("models"); }}>管理本地模型与 API<ArrowRight size={13} /></button><div className="ref-stage-targets">{targetStages.filter((stage, index, stages) => stages.findIndex(other => other.profile?.id === stage.profile?.id) === index).map(stage => <div className="ref-model-target" key={stage.profile?.id || stage.id}><span>{targetStages.filter(other => other.profile?.id === stage.profile?.id).map(other => other.label).join(" · ")}</span><strong>{stage.profile ? `${isRemoteModel(stage.profile) ? "远程 API" : "本机模型"} · ${modelEndpoint(stage.profile.base_url)}` : "尚未配置模型"}</strong><small>{stage.profile?.name} · {stage.profile?.model}</small></div>)}</div></>}

        {createMode === "ab" && <div className="ref-model-target"><span>方案 B · {isRemoteModel(profiles.find(profile => profile.id === profileB)) ? "远程 API" : "本机模型"}</span><strong>{modelEndpoint(profiles.find(profile => profile.id === profileB)?.base_url)}</strong><small>{profiles.find(profile => profile.id === profileB)?.model}</small></div>}
        <section className="extraction-scope-picker"><div className="ref-segment-control"><button className={scopeMode === "all" ? "active" : ""} onClick={() => setScopeMode("all")}>全书</button><button className={scopeMode === "range" ? "active" : ""} disabled={!chapterDirectory.length || collection} onClick={() => setScopeMode("range")}>指定章节</button></div>{scopeMode === "range" && <div className="extraction-range-list">{scopeRanges.map((range, index) => <div key={index}><label className="studio-field"><span>开始章节</span><select aria-label={`范围 ${index + 1} 开始章节`} value={range.start_chapter} onChange={event => setScopeRanges(values => values.map((value, i) => i === index ? { ...value, start_chapter: Number(event.target.value) } : value))}>{chapterDirectory.map((chapter, i) => <option value={i + 1} key={i}>{stringValue(chapter.display_label) || `第 ${Number(chapter.number || i + 1)} 章 ${stringValue(chapter.title) || (chapter.placeholder ? "待补充" : "无标题")}`}</option>)}</select></label><span>—</span><label className="studio-field"><span>结束章节</span><select aria-label={`范围 ${index + 1} 结束章节`} value={range.end_chapter} onChange={event => setScopeRanges(values => values.map((value, i) => i === index ? { ...value, end_chapter: Number(event.target.value) } : value))}>{chapterDirectory.map((chapter, i) => <option value={i + 1} key={i}>{stringValue(chapter.display_label) || `第 ${Number(chapter.number || i + 1)} 章 ${stringValue(chapter.title) || (chapter.placeholder ? "待补充" : "无标题")}`}</option>)}</select></label><button className="icon-button" aria-label={`移除范围 ${index + 1}`} disabled={scopeRanges.length === 1} onClick={() => setScopeRanges(values => values.filter((_, i) => i !== index))}><Trash2 size={15} /></button></div>)}<button className="text-button" onClick={() => setScopeRanges(values => [...values, { start_chapter: 1, end_chapter: chapterDirectory.length }])}><Plus size={14} />添加范围</button></div>}<label className="extraction-reuse-toggle"><input type="checkbox" checked={reuseExisting} onChange={event => setReuseExisting(event.target.checked)} />复用相同范围</label></section>
        <Notice error>{error}</Notice>{busy && <Busy label={busy === "config" ? "正在读取模型与工作流" : "正在准备来源并建立切块"} compact />}
      </div><div className="modal-footer"><button className="button secondary" disabled={!!busy} onClick={() => setNewOpen(false)}>取消</button><button className="button primary" disabled={!!busy || !workflowId || (!["web", "manual"].includes(createMode) && (!profileId || !targetsReady)) || (createMode === "ab" && (!profileB || profileB === profileId)) || (scopeMode === "range" && scopeRanges.some(range => range.start_chapter > range.end_chapter))} onClick={() => void create()}>{createMode === "ab" ? "建立 A/B 测试" : createMode === "api" && remoteTargets ? "确认目标并准备分析" : "准备分析"}</button></div>
    </Modal>}
    {pending && <Modal title="有未保存的候选修改" onClose={() => setPending(null)}><div className="modal-body"><p>保存候选修改后可继续审核。</p></div><div className="modal-footer"><button className="button secondary" onClick={() => setPending(null)}>继续编辑</button><button className="button danger" onClick={() => { const next = pending; setPending(null); setPreviewDirty(false); setEditorDirty(false); dirtyRef.current = false; if (next.runId) void chooseRun(next.runId, true); else if (next.chunkId) chooseChunk(next.chunkId); }}>放弃并切换</button></div></Modal>}
  {workbench.modal}</section></NarrativeProvider>;
}
