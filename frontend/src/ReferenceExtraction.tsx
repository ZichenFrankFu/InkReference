import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowRight, Check, CheckCircle2, ChevronDown, FileSearch, FileText, Play, Plus, Save, Sparkles, Square } from "lucide-react";
import { Busy, Modal, Notice, Progress, Status, dateLabel, messageOf } from "./ui";
import { normalizeList, send, studio, type DocumentMeta, type List, type ModelProfile, type Run, type TextVersion, type Workflow } from "./studio";
import { isRemoteModel, modelEndpoint } from "./model-security";
import { CollectionEditor, PlotEditor, PromptPanel, RhythmEditor, StyleEditor } from "./ReferenceEditors";
import { record, records, stringValue, type ReferenceRecord, type ReferenceResult, type ReferenceSection, type ReferenceState } from "./reference-types";

type Props = {
  document: DocumentMeta; state: ReferenceState; runs: Run[];
  navigate: (id: "library" | "documents" | "jobs" | "workflows" | "models" | "backup") => void;
  onRun: (run: Run) => void; onState: (state: ReferenceState) => void; onRefresh: () => Promise<void>; onDirty: (dirty: boolean) => void;
};
type Chunk = Run["candidates"][number] & { index?: number; start?: number; end?: number };
const sectionNames: Record<string, string> = { plot: "剧情事件", characters: "角色", settings: "设定", style: "风格与节奏", rhythm: "章节节奏", opening: "开篇模式", setting_features: "设定特征" };
function candidateReference(result: unknown, chunk: Chunk): Partial<Record<ReferenceSection, unknown>> {
  const r = record(result); const rich = { ...record(r.reference) };
  if (!rich.characters && records(r.characters).length) rich.characters = records(r.characters).map(c => ({ ...c, intro: c.description }));
  if (!rich.plot && records(r.events).length) rich.plot = { logline: "", epochs: [{ title: "正文", periods: [{ time: `第 ${Number(chunk.index || 0) + 1} 段`, events: r.events }] }] };
  return rich;
}
function size(section: string, value: unknown) {
  if (section === "plot") return records(record(value).epochs).reduce((n, e) => n + records(e.periods).reduce((sum, p) => sum + records(p.events).length, 0), 0);
  return Array.isArray(value) ? value.length : Object.keys(record(value)).length;
}
export function ReferenceExtraction({ document: doc, state, runs, navigate, onRun, onState, onRefresh, onDirty }: Props) {
  const [run, setRun] = useState<Run | null>(null);
  const [chunkId, setChunkId] = useState("");
  const [version, setVersion] = useState<TextVersion | null>(null);
  const [volumePlan, setVolumePlan] = useState<ReferenceRecord[]>([]);
  const [volume, setVolume] = useState("");
  const [mode, setMode] = useState("api");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [newOpen, setNewOpen] = useState(false);
  const [profiles, setProfiles] = useState<ModelProfile[]>([]);
  const [profileId, setProfileId] = useState("");
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [workflowId, setWorkflowId] = useState("");
  const [createMode, setCreateMode] = useState("api");
  const [prompt, setPrompt] = useState("");
  const [source, setSource] = useState("");
  const [paste, setPaste] = useState("");
  const [preview, setPreview] = useState<ReferenceResult | null>(null);
  const [previewDirty, setPreviewDirty] = useState(false);
  const [editorDirty, setEditorDirty] = useState(false);
  const [openSection, setOpenSection] = useState<string | null>(null);
  const [pending, setPending] = useState<{ runId?: string; chunkId?: string } | null>(null);
  const runRef = useRef(run); runRef.current = run;
  const chunkRef = useRef(chunkId); chunkRef.current = chunkId;
  const dirtyRef = useRef(false); dirtyRef.current = previewDirty || editorDirty;
  const ticket = useRef(0);
  const collection = state.structure_type === "setting_collection";
  const candidate = run?.candidates.find(c => c.chunk_id === chunkId) as Chunk | undefined;
  const running = !!run && ["queued", "running", "cancelling"].includes(run.status) && run.status !== "queued";
  const manual = record(record(run?.snapshots).model).provider === "manual";
  const rich = candidate && preview ? candidateReference(preview, candidate) : {};
  const snapshot = record(record(run?.snapshots).model);
  const sections = collection ? ["settings", "characters", "setting_features"] : ["plot", "characters", "settings", "style"];
  const committed = (section: string, id = chunkId) => state.commits.some(c => c.run_id === run?.id && c.chunk_id === id && c.section === section);
  const someCommitted = !!run && state.commits.some(c => c.run_id === run.id && c.chunk_id === chunkId);
  const chunkChapter = useCallback((c: Chunk) => {
    const chapters = (version?.chapters || []).map((ch, i) => ({ ...ch, index: i + 1, start: ch.start ?? ch.start_offset ?? 0, end: ch.end ?? ch.end_offset ?? 0 }));
    const hits = chapters.filter(ch => ch.end > Number(c.start || 0) && ch.start < Number(c.end || 0));
    return { first: hits[0]?.index || 1, last: hits.at(-1)?.index || 1, title: hits[0]?.title || "正文" };
  }, [version]);
  const chooseChunk = useCallback((id: string, selectedRun?: Run) => {
    const current = selectedRun || runRef.current;
    if (!current) return;
    const c = current.candidates.find(c => c.chunk_id === id);
    setChunkId(id); setPreview(c?.result ? structuredClone(record(c.result)) as ReferenceResult : null); setPreviewDirty(false); setEditorDirty(false); setPaste(""); setOpenSection(null); setPrompt(""); setSource(""); setError(""); setMessage("");
  }, []);
  const chooseRun = useCallback(async (id: string, discard = false) => {
    if (dirtyRef.current && !discard) { setPending({ runId: id }); return; }
    const currentTicket = ++ticket.current; setBusy("load"); setError("");
    try { const next = await studio<Run>(`/jobs/${id}`); if (currentTicket !== ticket.current) return; setRun(next); setMode(record(record(next.snapshots).model).provider === "manual" ? "web" : "api"); chooseChunk(next.candidates[0]?.chunk_id || "", next); const [v, volumes] = await Promise.all([studio<TextVersion>(`/documents/${doc.id}/versions/${next.version_id}?include_text=false`), studio<{ volumes: ReferenceRecord[] }>(`/documents/${doc.id}/versions/${next.version_id}/volumes`)]); if (currentTicket === ticket.current) { setVersion(v); setVolumePlan(volumes.volumes); setVolume(""); } }
    catch (e) { if (currentTicket === ticket.current) setError(messageOf(e)); }
    finally { if (currentTicket === ticket.current) setBusy(""); }
  }, [chooseChunk, doc.id]);
  useEffect(() => { if (!run && runs.length) void chooseRun(runs[0].id); }, [runs, run, chooseRun]);
  useEffect(() => { onDirty(previewDirty || editorDirty); return () => onDirty(false); }, [previewDirty, editorDirty, onDirty]);
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
  const promptForChunk = async () => { if (!run || !chunkId) return; const id = chunkId; setBusy("prompt"); setError(""); try { const data = await studio<{ prompt: string; text: string }>(`/jobs/${run.id}/chunks/${chunkId}/prompt`); if (chunkRef.current === id) { setPrompt(data.prompt); setSource(data.text); } } catch (e) { setError(messageOf(e)); } finally { setBusy(""); } };
  const openCreate = async () => { setNewOpen(true); setBusy("config"); setError(""); try { const [p, w] = await Promise.all([studio<List<ModelProfile>>("/models"), studio<List<Workflow>>("/workflows")]); const ps = normalizeList(p), ws = normalizeList(w); setProfiles(ps); setWorkflows(ws); setProfileId(ps[0]?.id || ""); const preferred = ws.find(w => w.id === "default" && w.version === "1.1.0") || ws[0]; setWorkflowId(preferred ? `${preferred.id}@${preferred.version}` : ""); } catch (e) { setError(messageOf(e)); } finally { setBusy(""); } };
  const create = async () => {
    const profile = profiles.find(p => p.id === profileId), workflow = workflows.find(w => `${w.id}@${w.version}` === workflowId);
    setBusy("create"); setError("");
    try {
      let versionId = doc.latest_version_id;
      if (collection) { const source = await send<{ version: TextVersion }>(`/documents/${doc.id}/reference/prepare-source`, { expected_revision: state.revision }); versionId = source.version.id; }
      const next = await send<Run>("/jobs", { document_id: doc.id, version_id: versionId, workflow_id: workflow?.id || "default", workflow_version: workflow?.version || "1.1.0", profile_id: createMode === "web" ? undefined : profileId, manual: createMode === "web", start: false, remote_consent: createMode === "api" && isRemoteModel(profile), expected_model_fingerprint: createMode === "api" ? profile?.target_fingerprint : undefined });
      setNewOpen(false); setRun(next); setMode(createMode); chooseChunk(next.candidates[0]?.chunk_id || "", next); await onRefresh(); await chooseRun(next.id, true);
    } catch (e) { setError(messageOf(e)); }
    finally { setBusy(""); }
  };
  const runAction = async (kind: "all" | "one" | "cancel") => {
    if (!run) return; setBusy(kind); setError("");
    try { const path = kind === "one" ? `/jobs/${run.id}/chunks/${chunkId}/start` : kind === "cancel" ? `/jobs/${run.id}/cancel` : `/jobs/${run.id}/${run.status === "queued" ? "start" : "resume"}`; const next = await send<Run>(path, {}); setRun(next); await onRefresh(); } catch (e) { setError(messageOf(e)); } finally { setBusy(""); }
  };
  const savePaste = async (edited = false) => {
    if (!run) return; setBusy("paste"); setError("");
    try { const next = await send<Run>(`/jobs/${run.id}/chunks/${chunkId}/paste`, { content: edited ? JSON.stringify(preview) : paste, expected_revision: run.revision }); setRun(next); const c = next.candidates.find(c => c.chunk_id === chunkId); setPreview(c?.result ? structuredClone(record(c.result)) as ReferenceResult : null); setPreviewDirty(false); setEditorDirty(false); setMessage(edited ? "候选修改已保存" : "结果已解析，请逐区审核"); await onRefresh(); } catch (e) { setError(messageOf(e)); } finally { setBusy(""); }
  };
  const commit = async (section: string) => {
    if (!run) return; setBusy(`commit:${section}`); setError("");
    try { const next = await send<ReferenceState>(`/jobs/${run.id}/chunks/${chunkId}/commit`, { sections: section === "style" ? ["style", ...(rich.rhythm ? ["rhythm"] : []), ...(rich.opening ? ["opening"] : [])] : [section], expected_revision: state.revision }); onState(next); setMessage(`${sectionNames[section]}已确认入库`); } catch (e) { setError(messageOf(e)); } finally { setBusy(""); }
  };
  const editSection = useCallback(async (section: ReferenceSection, value: unknown) => { setPreview(p => ({ ...p, reference: { ...candidateReference(p, (runRef.current?.candidates.find(c => c.chunk_id === chunkRef.current) || { chunk_id: "" }) as Chunk), [section]: value } })); setPreviewDirty(true); setEditorDirty(false); }, []);
  const completed = run?.candidates.filter(c => sections.every(s => state.commits.some(k => k.run_id === run.id && k.chunk_id === c.chunk_id && k.section === s))).length || 0;
  return <section className="ref-extraction"><div className="ref-section-heading"><div><span className="eyebrow">EXTRACTION STUDIO</span><h3>特征提取</h3></div><button className="button primary" onClick={() => void openCreate()} disabled={!!busy || dirtyRef.current}><Plus size={15} />新建提取任务</button></div>
    <Notice error={!!error} success={!error && !!message}>{error || message}</Notice>
    {!run ? <div className="ref-extract-empty"><Sparkles size={30} /><h3>把阅读转化为素材</h3><p>{collection ? "从原始文本、设定与角色中提炼核心冲突和高概念" : "逐段提取剧情事件、角色、设定与风格"}</p><div className="inline-actions"><button className="button primary" onClick={() => { setCreateMode("web"); void openCreate(); }}><FileText size={15} />复制提示词与粘贴</button><button className="button secondary" onClick={() => { setCreateMode("api"); void openCreate(); }}><Sparkles size={15} />模型自动提取</button></div></div> : <>
      <div className="ref-extract-runbar"><label className="studio-field"><span>提取任务</span><select aria-label="提取任务" value={run.id} disabled={!!busy || running} onChange={e => void chooseRun(e.target.value)}>{runs.some(r => r.id === run.id) ? null : <option value={run.id}>{dateLabel(run.created_at)} · {run.workflow_id}</option>}{runs.map(r => <option key={r.id} value={r.id}>{dateLabel(r.created_at)} · {r.workflow_id} · {r.id.slice(0, 6)}</option>)}</select></label><Status value={run.status} /><button className="text-button" onClick={() => onRun(run)} disabled={dirtyRef.current}>运行记录与素材入库<ArrowRight size={13} /></button></div>
      <div className="ref-extract-overview"><Progress completed={completed} total={run.total} stage="分区审核进度" /><div className="ref-extract-counters"><span><b>{run.completed}</b>已提取</span><span><b>{run.candidates.filter(c => c.status === "failed").length}</b>失败</span><span><b>{run.total - run.completed}</b>待处理</span></div></div>
      <div className="ref-extract-actions"><div className="ref-segment-control"><button className={mode === "api" ? "active" : ""} disabled={manual || running || dirtyRef.current} onClick={() => setMode("api")}>本地模型 / API</button><button className={mode === "web" ? "active" : ""} disabled={running || dirtyRef.current} onClick={() => setMode("web")}>提示词 / 粘贴</button></div>{running ? <button className="button secondary" disabled={!!busy || run.status === "cancelling"} onClick={() => void runAction("cancel")}><Square size={13} />停止提取</button> : !manual && <button className="button secondary" disabled={!!busy || dirtyRef.current || run.completed === run.total || run.status === "approved"} onClick={() => void runAction("all")}><Play size={14} />提取全部未完成分段</button>}</div>
      {mode === "api" && <div className="ref-model-target"><span>{isRemoteModel(snapshot) ? "远程 API" : "本机模型"}</span><strong>{modelEndpoint(stringValue(snapshot.base_url))}</strong><small>{stringValue(snapshot.model)}</small></div>}
      <div className="ref-chunk-workspace"><aside className="ref-chunk-index"><label className="studio-field"><span>分卷</span><select aria-label="按分卷筛选提取段" value={volume} onChange={e => setVolume(e.target.value)} disabled={running || dirtyRef.current}><option value="">全书</option>{volumePlan.map((v, i) => <option key={i} value={i}>{stringValue(v.title)} · 第 {stringValue(v.start_chapter)}–{stringValue(v.end_chapter)} 章</option>)}</select></label>{run.candidates.map(raw => { const c = raw as Chunk, chapter = chunkChapter(c); const vol = volumePlan.find(v => chapter.first >= Number(v.start_chapter) && chapter.first <= Number(v.end_chapter)); if (volume !== "" && volumePlan[Number(volume)] !== vol) return null; const done = sections.filter(s => state.commits.some(k => k.run_id === run.id && k.chunk_id === c.chunk_id && k.section === s)).length; return <button key={c.chunk_id} className={`ref-chunk-item ${chunkId === c.chunk_id ? "active" : ""} ${done === sections.length ? "complete" : ""}`} onClick={() => { if (dirtyRef.current) setPending({ chunkId: c.chunk_id }); else chooseChunk(c.chunk_id); }}><div><strong>第 {Number(c.index || 0) + 1} 段</strong>{done === sections.length ? <CheckCircle2 size={14} /> : <span className={`ref-chunk-status ${c.status}`} />}</div><small>{stringValue(vol?.title) || "全书"} · 第 {chapter.first}–{chapter.last} 章</small><div className="ref-chunk-section-dots">{sections.map(s => <span className={state.commits.some(k => k.run_id === run.id && k.chunk_id === c.chunk_id && k.section === s) ? "done" : ""} key={s} title={sectionNames[s]} />)}<small>{done}/{sections.length}</small></div></button>; })}</aside>
      <div className="ref-chunk-detail"><div className={`ref-inline-operation ${busy ? "visible" : ""}`}>{busy && <Busy label={busy === "load" ? "正在读取提取任务" : busy === "paste" ? "正在解析并校验候选" : busy.startsWith("commit:") ? "正在确认分区入库" : busy === "prompt" ? "正在准备提示词" : "正在更新任务"} compact />}</div>
        {candidate && <><div className="ref-chunk-detail-heading"><div><span className="eyebrow">CHUNK {Number(candidate.index || 0) + 1}</span><h4>第 {chunkChapter(candidate).first}–{chunkChapter(candidate).last} 章</h4><small>{Math.max(0, Number(candidate.end || 0) - Number(candidate.start || 0)).toLocaleString()} 字</small></div>{mode === "api" && <button className="button primary" disabled={!!busy || running || dirtyRef.current || candidate.status === "completed" || run.status === "approved"} onClick={() => void runAction("one")}><Sparkles size={15} />{candidate.status === "failed" ? "重试本段" : "提取本段"}</button>}</div>
          {candidate.error && <Notice error>{candidate.error}</Notice>}
          {mode === "web" && <div className="ref-paste-workspace">{!prompt && <button className="button secondary" disabled={!!busy} onClick={() => void promptForChunk()}><FileText size={14} />准备本段提示词</button>}{(prompt || busy === "prompt") && <PromptPanel prompt={prompt} busy={busy === "prompt"} />}<label className="studio-field"><span>粘贴模型返回的 JSON</span><textarea aria-label="粘贴提取 JSON" rows={6} value={paste} disabled={run.status === "approved" || running} onChange={e => setPaste(e.target.value)} placeholder={collection ? '{"settings":[],"characters":[],"setting_features":[]}' : '{"events":[],"characters":[],"settings":[],"style":{}}'} /></label><button className="button primary" disabled={!!busy || !paste.trim() || running || run.status === "approved"} onClick={() => void savePaste()}><FileSearch size={14} />解析并预览</button></div>}
          {source ? <details className="ref-source-preview"><summary>本段原文 · {Array.from(source).length.toLocaleString()} 字<ChevronDown size={14} /></summary><pre>{source}</pre></details> : <button className="text-button" disabled={!!busy} onClick={() => void promptForChunk()}><FileText size={13} />查看本段原文</button>}
          {preview ? <><div className="ref-preview-heading"><h4>逐区审核</h4>{previewDirty && <button className="button primary" disabled={!!busy || editorDirty} onClick={() => void savePaste(true)}><Save size={14} />保存候选修改</button>}</div>{sections.map(section => { const data = rich[section as ReferenceSection], exists = data !== undefined, done = committed(section); return <section className={`ref-section-review ${done ? "committed" : ""}`} key={section}><header><button className="ref-section-review-title" aria-expanded={openSection === section} onClick={() => { if (!editorDirty) setOpenSection(openSection === section ? null : section); }}><ChevronDown size={15} className={openSection === section ? "rotated" : ""} /><strong>{sectionNames[section]}</strong><span>{size(section, data)}</span></button><button className={`button ${done ? "secondary" : "primary"}`} disabled={!!busy || !exists || previewDirty || editorDirty || done || running} onClick={() => void commit(section)}>{done ? <Check size={13} /> : <CheckCircle2 size={13} />}{done ? "已入库" : "确认入库"}</button></header>{openSection === section && <div className="ref-section-review-body">{!exists ? <div className="ref-empty">本次结果未包含此分区</div> : section === "plot" ? <PlotEditor value={data} onSave={d => editSection("plot", d)} busy={!!busy || done} onDirty={setEditorDirty} /> : section === "characters" || section === "settings" || section === "setting_features" ? <CollectionEditor kind={section} value={data} onSave={d => editSection(section, d)} busy={!!busy || done} onDirty={setEditorDirty} settingCollection={collection} /> : <><StyleEditor value={data} onSave={d => editSection("style", d)} busy={!!busy || done} onDirty={setEditorDirty} />{rich.rhythm && <RhythmEditor value={rich.rhythm} opening={rich.opening} onSave={d => editSection("rhythm", d)} onSaveOpening={d => editSection("opening", d)} busy={!!busy || done} onDirty={setEditorDirty} />}</>}</div>}</section>; })}{previewDirty && <p className="ref-inline-note">先保存候选修改，再逐区确认入库。</p>}{someCommitted && <p className="ref-inline-note">已确认的分段可在作品的对应页继续编辑。</p>}</> : <div className="ref-no-candidate"><FileSearch size={25} /><span>{running ? "正在提取，结果会在这里出现" : "本段尚无提取结果"}</span></div>}
          {running && <Progress completed={run.completed} total={run.total} stage={run.stage === "retrying" ? "正在重试" : "正在提取文本分段"} busy />}
        </>}
      </div></div>
    </>}
    {newOpen && <Modal title="新建提取任务" onClose={() => setNewOpen(false)} busy={!!busy}>
      <div className="modal-body"><div className="ref-segment-control"><button className={createMode === "api" ? "active" : ""} disabled={!!busy} onClick={() => setCreateMode("api")}>本地模型 / API</button><button className={createMode === "web" ? "active" : ""} disabled={!!busy} onClick={() => setCreateMode("web")}>复制提示词与粘贴</button></div><label className="studio-field"><span>Agent 工作流</span><select value={workflowId} onChange={e => setWorkflowId(e.target.value)}>{workflows.map(w => <option key={`${w.id}@${w.version}`} value={`${w.id}@${w.version}`}>{w.name} · v{w.version}</option>)}</select></label>
        {createMode === "api" && <><label className="studio-field"><span>模型配置</span><select value={profileId} onChange={e => setProfileId(e.target.value)}>{!profiles.length && <option value="">尚未配置模型</option>}{profiles.map(p => <option key={p.id} value={p.id}>{p.name} · {p.model}</option>)}</select></label><button className="text-button" onClick={() => { setNewOpen(false); navigate("models"); }}>管理本地模型与 API<ArrowRight size={13} /></button>{profiles.find(p => p.id === profileId) && <div className="ref-model-target"><span>{isRemoteModel(profiles.find(p => p.id === profileId)) ? "远程 API" : "本机模型"}</span><strong>{modelEndpoint(profiles.find(p => p.id === profileId)?.base_url)}</strong><small>{profiles.find(p => p.id === profileId)?.model}</small></div>}</>}
        {collection && <div className="ref-source-summary"><LayersNotice /><span>将当前原始文本、设定与角色保存为本次分析的独立来源版本。</span></div>}
        <Notice error>{error}</Notice>{busy && <Busy label={busy === "config" ? "正在读取模型与工作流" : "正在准备来源并建立分段"} compact />}
      </div><div className="modal-footer"><button className="button secondary" disabled={!!busy} onClick={() => setNewOpen(false)}>取消</button><button className="button primary" disabled={!!busy || !workflowId || (createMode === "api" && (!profileId || (isRemoteModel(profiles.find(p => p.id === profileId)) && !profiles.find(p => p.id === profileId)?.target_fingerprint)))} onClick={() => void create()}>{createMode === "api" && isRemoteModel(profiles.find(p => p.id === profileId)) ? "确认目标并准备分析" : "准备分析"}</button></div>
    </Modal>}
    {pending && <Modal title="有未保存的候选修改" onClose={() => setPending(null)}><div className="modal-body"><p>保存候选修改后可继续审核。</p></div><div className="modal-footer"><button className="button secondary" onClick={() => setPending(null)}>继续编辑</button><button className="button danger" onClick={() => { const next = pending; setPending(null); setPreviewDirty(false); setEditorDirty(false); dirtyRef.current = false; if (next.runId) void chooseRun(next.runId, true); else if (next.chunkId) chooseChunk(next.chunkId); }}>放弃并切换</button></div></Modal>}
  </section>;
}
function LayersNotice() { return <FileText size={16} />; }
