import { useEffect, useRef, useState } from "react";
import { AlertCircle, ArrowLeft, ArrowRight, Check, CheckCircle2, ChevronDown, Copy, Download, FileCode2, Plus, RefreshCw, Square, Trash2, Undo2, Wrench } from "lucide-react";
import { download, jsonBody } from "./api";
import { diagnosticEvents } from "./diagnostics";
import { send, studio, type ModelProfile } from "./studio";
import { DynamicModeSwitch, DynamicModelSelector, usePromptWorkbench, type DynamicMode } from "./DynamicFunction";
import { isRemoteModel } from "./model-security";
import { Busy, Modal, Progress, messageOf } from "./ui";
import "./quick-repair.css";

type Capabilities = {
  mode: "source" | "packaged";
  can_apply_source: boolean;
  can_rebuild_cache?: boolean;
  source_root_label: string;
  protocol: string;
  topics: { id: string; label: string }[];
};
type PromptResult = {
  session_id: string;
  prompt: string;
  diagnostics: unknown;
  source_files: { path: string; sha256: string }[];
  truncated: boolean;
  template?: RepairTemplate;
};
type PreviewResult = {
  prepared_id?: string;
  preview_id: string;
  session_id: string;
  summary: string;
  files: { path: string; diff: string; old_sha256: string; new_sha256: string; additions: number; deletions: number; unverified?: boolean }[];
  checks: string[];
  can_apply: boolean;
  restart_required: boolean;
  expires_at: string;
};
type RepairOperation = {
  kind?: "source" | "chapter_cache";
  operation_id: string;
  status: "running" | "succeeded" | "failed" | "rolled_back";
  progress: number;
  stage: string;
  checks?: { name: string; ok: boolean; detail: string }[];
  error?: string;
  rollback_available?: boolean;
  restart_required?: boolean;
  frontend_rebuilt?: boolean;
  auto_rolled_back?: boolean;
};
type RepairTemplate = { protocol: string; session_id: string; summary: string; files: { path: string; sha256: string; edits: {before: string; after: string}[] }[] };
type Generation = PromptResult & { id: string; status: "queued" | "running" | "needs_review" | "failed" | "cancelled"; stage: string; progress: { completed: number; total: number; label: string }; reply?: string; error?: string; usage?: unknown };
type GenerationSummary = Pick<Generation, "id" | "status" | "stage" | "progress" | "error"> & { updated_at: number };
type ManualFile = { id: string; path: string; sha256: string; edits: { before: string; after: string }[] };
type SourceContext = { path: string; sha256: string; content: string; truncated: boolean };
const sourceName = (path: string) => path.split("/").slice(-2).join("/");
type Draft = {
  mode: DynamicMode;
  profileId: string;
  generation: Generation | null;
  manualFiles: ManualFile[];
  manualSummary: string;
  description: string;
  topic: string;
  prompt: PromptResult | null;
  reply: string;
  preview: PreviewResult | null;
  operation: RepairOperation | null;
  step: number;
};
let retained: Draft = { mode: "automatic", profileId: "", generation: null, manualFiles: [], manualSummary: "", description: "", topic: "chapters", prompt: null, reply: "", preview: null, operation: null, step: 0 };

function FileDiff({ diff }: { diff: string }) {
  return <pre className="repair-diff">{diff.split("\n").map((line, index) => <span key={index} className={line.startsWith("@@") ? "repair-diff-range" : line.startsWith("+") && !line.startsWith("+++") ? "repair-diff-add" : line.startsWith("-") && !line.startsWith("---") ? "repair-diff-remove" : ""}>{line || " "}{"\n"}</span>)}</pre>;
}

export default function QuickRepair({ onClose }: { onClose: () => void }) {
  const [mode, setMode] = useState<DynamicMode>(retained.mode);
  const [profiles, setProfiles] = useState<ModelProfile[]>([]);
  const [profileId, setProfileId] = useState(retained.profileId);
  const [generation, setGeneration] = useState<Generation | null>(retained.generation);
  const [manualFiles, setManualFiles] = useState<ManualFile[]>(retained.manualFiles);
  const [manualSummary, setManualSummary] = useState(retained.manualSummary);
  const [sourceContext, setSourceContext] = useState<SourceContext | null>(null);
  const [sourceLoading, setSourceLoading] = useState(false);
  const [copiedDiagnostic, setCopiedDiagnostic] = useState("");
  const [selectedDiagnostic, setSelectedDiagnostic] = useState("");
  const [sourceError, setSourceError] = useState("");
  const [manualIndex, setManualIndex] = useState(0);
  const [caps, setCaps] = useState<Capabilities | null>(null);
  const [capsLoading, setCapsLoading] = useState(true);
  const [description, setDescription] = useState(retained.description);
  const [topic, setTopic] = useState(retained.topic);
  const [prompt, setPrompt] = useState<PromptResult | null>(retained.prompt);
  const [reply, setReply] = useState(retained.reply);
  const [preview, setPreview] = useState<PreviewResult | null>(retained.preview);
  const [operation, setOperation] = useState<RepairOperation | null>(retained.operation);
  const [step, setStep] = useState(retained.step);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [selectedFile, setSelectedFile] = useState(0);
  const [generations, setGenerations] = useState<GenerationSummary[] | null>(null);
  const [generationsLoading, setGenerationsLoading] = useState(false);
  const [previous, setPrevious] = useState<RepairOperation[]>([]);
  const [documents, setDocuments] = useState<{id: string; title: string}[] | null>(null);
  const [documentId, setDocumentId] = useState("");
  const [documentsLoading, setDocumentsLoading] = useState(false);
  const handledGeneration = useRef(generation?.status === "needs_review" ? generation.id : "");
  const events = useRef(diagnosticEvents());
  const recentEvents = step === 0 ? diagnosticEvents() : events.current;
  const applying = operation?.status === "running";
  const generating = Boolean(generation && ["queued", "running"].includes(generation.status));
  const locked = Boolean(busy) || applying || generating;
  const selectedProfile = profiles.find(profile => profile.id === profileId);
  const selectedManual = manualFiles[manualIndex];

  useEffect(() => {
    retained = { mode, profileId, generation, manualFiles, manualSummary, description, topic, prompt, reply, preview, operation, step };
  }, [mode, profileId, generation, manualFiles, manualSummary, description, topic, prompt, reply, preview, operation, step]);

  const loadCapabilities = async () => {
    setCapsLoading(true);
    setError("");
    try {
      const result = await studio<Capabilities>("/repair/capabilities");
      setCaps(result);
      if (!result.topics.some((item) => item.id === topic)) setTopic(result.topics[0]?.id || "app");
      void studio<{items: ModelProfile[]}>("/models").then(result => { setProfiles(result.items); setProfileId(current => result.items.some(profile => profile.id === current) ? current : result.items[0]?.id || ""); }).catch(failure => setError(messageOf(failure)));
      const history = await studio<{operations: RepairOperation[]}>("/repair/operations");
      setPrevious(history.operations.filter((item) => item.rollback_available));
    } catch (failure) {
      setError(messageOf(failure));
    } finally {
      setCapsLoading(false);
    }
  };
  useEffect(() => { void loadCapabilities(); }, []);

  useEffect(() => {
    if (!operation || operation.status !== "running") return;
    let cancelled = false;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const result = await studio<RepairOperation>(`/repair/operations/${encodeURIComponent(operation.operation_id)}`);
        if (cancelled) return;
        setOperation(result);
        setError("");
        if (result.status === "failed") setError(result.error || "修复未完成");
        if (result.status === "running") timer = window.setTimeout(() => void poll(), 900);
      } catch (failure) {
        if (cancelled) return;
        setError(messageOf(failure));
        timer = window.setTimeout(() => void poll(), 2500);
      }
    };
    timer = window.setTimeout(() => void poll(), 400);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [operation?.operation_id, operation?.status]);

  const makePrompt = async () => {
    setBusy("正在整理诊断与源码");
    setError("");
    try {
      events.current = diagnosticEvents();
      const result = await send<PromptResult>("/repair/prompt", { description, topic, frontend_events: events.current });
      setPrompt(result);
      setReply("");
      setPreview(null);
      setOperation(null);
      setGeneration(null);
      setManualSummary("");
      setManualFiles(result.source_files.length ? [{ id: crypto.randomUUID(), path: result.source_files[0].path, sha256: result.source_files[0].sha256, edits: [{ before: "", after: "" }] }] : [{ id: crypto.randomUUID(), path: "", sha256: "", edits: [{ before: "", after: "" }] }]);
      setManualIndex(0);
      setStep(1);
    } catch (failure) { setError(messageOf(failure)); }
    finally { setBusy(""); }
  };

  const acceptGeneration = (result: Generation) => {
    setGeneration(result);
    if (result.session_id && result.prompt) setPrompt({ session_id: result.session_id, prompt: result.prompt, diagnostics: result.diagnostics, source_files: result.source_files || [], truncated: result.truncated, template: result.template });
    if (["needs_review", "failed", "cancelled"].includes(result.status) && result.reply && handledGeneration.current !== result.id) {
      handledGeneration.current = result.id;
      setReply(result.reply || ""); setPreview(null); setOperation(null); setStep(1);
    }
    if (result.status === "failed") setError(result.error || "修复方案生成失败");
  };
  useEffect(() => {
    if (!generation || !["queued", "running"].includes(generation.status)) return;
    let cancelled = false, timer: number | undefined;
    const poll = async () => {
      try {
        const next = await studio<Generation>(`/repair/generations/${encodeURIComponent(generation.id)}`);
        if (cancelled) return;
        acceptGeneration(next);
        if (["queued", "running"].includes(next.status)) timer = window.setTimeout(() => void poll(), 700);
      } catch (failure) { if (!cancelled) { setError(messageOf(failure)); timer = window.setTimeout(() => void poll(), 2200); } }
    };
    timer = window.setTimeout(() => void poll(), 250);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [generation?.id, generation?.status]);
  useEffect(() => {
    setSourceContext(null); setSourceError("");
    if (mode !== "manual" || step !== 1 || !prompt || !selectedManual?.path || caps?.mode !== "source") return;
    const controller = new AbortController(); setSourceLoading(true);
    const query = new URLSearchParams({ path: selectedManual.path });
    void studio<SourceContext>(`/repair/sessions/${encodeURIComponent(prompt.session_id)}/source?${query}`, { signal: controller.signal }).then(result => {
      if (controller.signal.aborted) return;
      setSourceContext(result);
      setManualFiles(current => current.map(item => item.id === selectedManual.id ? { ...item, sha256: result.sha256 } : item));
    }).catch(failure => { if (!controller.signal.aborted) setSourceError(messageOf(failure)); }).finally(() => { if (!controller.signal.aborted) setSourceLoading(false); });
    return () => { controller.abort(); setSourceLoading(false); };
  }, [mode, step, prompt?.session_id, selectedManual?.id, selectedManual?.path, caps?.mode]);
  const cancelGeneration = async () => {
    if (!generation) return;
    setBusy("正在取消生成"); setError("");
    try { acceptGeneration(await send<Generation>(`/repair/generations/${encodeURIComponent(generation.id)}/cancel`, {})); }
    catch (failure) { setError(messageOf(failure)); }
    finally { setBusy(""); }
  };
  const updateManual = (update: Partial<ManualFile>) => { setManualFiles(current => current.map((item, index) => index === manualIndex ? { ...item, ...update } : item)); setPreview(null); };
  const addManual = () => {
    const next = prompt?.source_files.find(file => !manualFiles.some(item => item.path === file.path));
    setManualFiles(current => [...current, { id: crypto.randomUUID(), path: next?.path || "", sha256: next?.sha256 || "", edits: [{ before: "", after: "" }] }]);
    setManualIndex(manualFiles.length); setPreview(null);
  };
  const manualReply = () => JSON.stringify({ protocol: caps?.protocol || "inkreference.repair.v1", session_id: prompt?.session_id, summary: manualSummary, files: manualFiles.map(({ path, sha256, edits }) => ({ path, sha256, edits })) });

  const parseReply = async () => {
    setBusy("正在校验修复方案");
    setError("");
    try {
      const content = mode === "manual" ? manualReply() : reply;
      const result = await send<PreviewResult>("/repair/preview", { reply: content, session_id: prompt?.session_id });
      if (mode === "manual") setReply(content);
      setPreview(result);
      setSelectedFile(0);
      setOperation(null);
      setStep(2);
    } catch (failure) { setError(messageOf(failure)); }
    finally { setBusy(""); }
  };

  const apply = async () => {
    if (!preview) return;
    setBusy("正在启动修复");
    setError("");
    try {
      setOperation(await send<RepairOperation>("/repair/apply", { preview_id: preview.preview_id, ...(preview.prepared_id ? {prepared_id: preview.prepared_id} : {}) }));
    } catch (failure) { setError(messageOf(failure)); }
    finally { setBusy(""); }
  };
  const rollback = async () => {
    if (!operation) return;
    setBusy("正在启动回滚");
    setError("");
    try { setOperation(await send<RepairOperation>("/repair/rollback", { operation_id: operation.operation_id })); }
    catch (failure) { setError(messageOf(failure)); }
    finally { setBusy(""); }
  };
  const exportPatch = async () => {
    if (!preview) return;
    setBusy("正在导出方案");
    setError("");
    try { await download("/studio/repair/export", "InkReference-repair.json", { method: "POST", ...jsonBody({ preview_id: preview.preview_id, ...(preview.prepared_id ? {prepared_id:preview.prepared_id} : {}) }) }); if (preview.prepared_id) await send(`/dynamic-functions/preparations/${preview.prepared_id}/control`, {action:"complete"}); }
    catch (failure) { setError(messageOf(failure)); }
    finally { setBusy(""); }
  };
  const loadDocuments = async () => {
    if (documents || documentsLoading) return;
    setDocumentsLoading(true);
    try {
      const result = await studio<{items: {id: string; title: string}[]}>("/documents");
      setDocuments(result.items);
      setDocumentId(result.items[0]?.id || "");
    } catch (failure) { setError(messageOf(failure)); }
    finally { setDocumentsLoading(false); }
  };
  const copyDiagnostic = async (id: string) => {
    try { if (!navigator.clipboard?.writeText) throw new Error("clipboard unavailable"); await navigator.clipboard.writeText(id); setCopiedDiagnostic(id); setSelectedDiagnostic(""); }
    catch { setSelectedDiagnostic(id); }
  };
  const loadGenerations = async () => {
    if (generations || generationsLoading) return;
    setGenerationsLoading(true);
    try { setGenerations((await studio<{ items: GenerationSummary[] }>("/repair/generations?summary=true")).items); }
    catch (failure) { setError(messageOf(failure)); }
    finally { setGenerationsLoading(false); }
  };
  const openGeneration = async (id: string) => {
    setBusy("正在读取修复方案"); setError("");
    try { const next = await studio<Generation>(`/repair/generations/${encodeURIComponent(id)}`); handledGeneration.current = ""; setMode("automatic"); setPreview(null); setOperation(null); setStep(1); acceptGeneration(next); }
    catch (failure) { setError(messageOf(failure)); }
    finally { setBusy(""); }
  };
  const rebuildCache = async () => {
    if (!documentId) return;
    setBusy("正在启动索引重建");
    setError("");
    try {
      const result = await send<RepairOperation>("/repair/cache-rebuild", { document_id: documentId });
      setOperation(result);
      setPreview(null);
      setStep(2);
    } catch (failure) { setError(messageOf(failure)); }
    finally { setBusy(""); }
  };

  const workbench = usePromptWorkbench<PreviewResult>({ operation: "repair", title: "修复提示词与回复", completeOnApply: false, mode: mode === "semi" ? "semi" : "automatic", profiles, profileId, onProfileChange: setProfileId, applyLabel: "确认方案", onApply: async (result, preparedId) => { setPreview({...result, prepared_id: preparedId}); setSelectedFile(0); setOperation(null); setStep(2); }, renderPreview: result => <><p className="repair-summary">{result.summary}</p>{result.files.map(file => <section key={file.path}><strong>{file.path}</strong><FileDiff diff={file.diff}/></section>)}</> });
  const openWorkbench = async () => { events.current = diagnosticEvents(); await workbench.open({ description, topic, frontend_events: events.current }); };

  return <><Modal title="快捷修复" onClose={onClose} busy={Boolean(busy)} wide>
    <div className="repair-workbench">
      <nav className="repair-steps" aria-label="修复步骤">
        {["问题描述", "交换方案", "检查与应用"].map((label, index) => <button type="button" key={label} className={step === index ? "active" : step > index ? "done" : ""} disabled={locked || (index === 1 && !prompt) || (index === 2 && !preview && !operation)} onClick={() => { setStep(index); setError(""); }}><span>{step > index ? <Check size={13} /> : index + 1}</span>{label}</button>)}
        {caps && <span className="repair-mode">{caps.mode === "source" ? "源码版" : "安装版"}</span>}
      </nav>
      {capsLoading && <Busy label="正在读取修复能力" compact inline delay={0} />}
      <div className="repair-body">
        <DynamicModeSwitch value={mode} disabled={locked} label="修复模式" onChange={next => { if (next === mode) return; setMode(next);  setError(""); setPreview(null); setOperation(null); setPrompt(null); setGeneration(null); setReply(""); setStep(0); }} />
        {error && <div className="repair-error" role="alert"><strong>操作未完成</strong><span>{error}</span>{!caps && <button type="button" className="button secondary" onClick={() => void loadCapabilities()} disabled={capsLoading}>重试</button>}</div>}
        {step === 0 && <>
          <div className="repair-section-heading"><h3>遇到了什么问题？</h3><span>{recentEvents.length} 条近期诊断</span></div>
          <label className="studio-field"><span>功能位置</span><select aria-label="功能位置" value={topic} onChange={(event) => setTopic(event.target.value)} disabled={!caps || locked}>{(caps?.topics || [{ id: "chapters", label: "正文与清洗" }]).map((item) => <option value={item.id} key={item.id}>{item.label}</option>)}</select></label>
          <label className="studio-field"><span>问题与预期结果</span><textarea aria-label="问题与预期结果" className="repair-description" value={description} onChange={(event) => setDescription(event.target.value)} placeholder="操作步骤、出现的问题、希望怎样调整" maxLength={6000} disabled={locked} /></label>
          {mode === "automatic" && <DynamicModelSelector profiles={profiles} value={profileId} onChange={id => { setProfileId(id);  }} disabled={locked} label="修复模型" onConfigure={() => { onClose(); window.dispatchEvent(new CustomEvent("inkreference:navigate", { detail: { section: "models" } })); }} />}

          <details className="repair-diagnostics"><summary>近期诊断 <ChevronDown size={14} /></summary><ul>{recentEvents.length ? recentEvents.slice().reverse().map((event, index) => { const id = event.diagnostic_id || event.request_id || ""; return <li key={index}><code>{event.kind}</code><span>{event.message}</span>{id && <button type="button" className="repair-diagnostic-id" title={id} aria-label={`复制诊断 ID ${id}`} onClick={() => void copyDiagnostic(id)}>{copiedDiagnostic === id ? <Check size={12} /> : <Copy size={12} />}<code>{id.slice(0, 14)}</code></button>}{selectedDiagnostic === id && id && <input className="repair-diagnostic-copy" aria-label="诊断 ID" value={id} readOnly autoFocus onFocus={event => event.target.select()} />}{(event.route || event.path) && <small>{event.route || event.path}</small>}</li>; }) : <li className="muted">暂无错误记录</li>}</ul></details>
          {(caps?.can_rebuild_cache || previous.length > 0) && <details className="repair-recovery" onToggle={(event) => { if (event.currentTarget.open) { if (caps?.can_rebuild_cache) void loadDocuments(); void loadGenerations(); } }}><summary>本机恢复<ChevronDown size={14} /></summary><div>
            {previous.length > 0 && <div className="repair-recovery-row"><span>上次源码修复</span><button className="button secondary" type="button" onClick={() => { setOperation(previous[0]); setPreview(null); setStep(2); }}><Undo2 size={14} />查看与回滚</button></div>}
            {caps?.can_rebuild_cache && <div className="repair-recovery-row"><select aria-label="重建索引的作品" value={documentId} onChange={(event) => setDocumentId(event.target.value)} disabled={documentsLoading || locked}><option value="">{documentsLoading ? "正在读取作品" : "选择作品"}</option>{documents?.map((item) => <option value={item.id} key={item.id}>{item.title}</option>)}</select><button className="button secondary" type="button" onClick={() => void rebuildCache()} disabled={!documentId || locked}><RefreshCw size={14} />重建章节索引</button></div>}
            {generations && generations.length > 0 && <div className="repair-generation-history"><strong>生成记录</strong>{generations.slice(0, 8).map(item => <button type="button" key={item.id} disabled={locked} onClick={() => void openGeneration(item.id)}><span>{new Date(item.updated_at * 1000).toLocaleString()}</span><small>{item.progress?.label || item.stage}</small><ArrowRight size={13} /></button>)}</div>}
            {generationsLoading && <Busy label="正在读取修复记录" compact inline delay={0} />}
            {documentsLoading && <Busy label="正在读取作品" compact inline delay={0} />}
          </div></details>}
        </>}
        {step === 1 && <>
          {generating && generation && <div className="repair-generation"><Progress completed={generation.progress.completed} total={generation.progress.total} stage={generation.progress.label || generation.stage || "正在生成修复方案"} busy /><button type="button" className="icon-button repair-cancel" aria-label="取消生成" title="取消生成" disabled={!!busy} onClick={() => void cancelGeneration()}><Square size={15} /></button></div>}
          {generation?.status === "cancelled" && <div className="repair-status" role="status">已取消生成</div>}
          {mode !== "manual" && <button type="button" className="button primary" disabled={locked} onClick={() => void openWorkbench()}>打开提示词</button>}
          {prompt && mode === "manual" && <>
            <label className="studio-field"><span>变更摘要</span><input aria-label="变更摘要" value={manualSummary} onChange={event => { setManualSummary(event.target.value); setPreview(null); }} disabled={locked} maxLength={2000} /></label>
            <div className="repair-manual-tabs" role="tablist" aria-label="手动修复文件">{manualFiles.map((file, index) => <button type="button" role="tab" key={file.id} aria-selected={manualIndex === index} className={manualIndex === index ? "active" : ""} onClick={() => setManualIndex(index)}>{sourceName(file.path) || `文件 ${index + 1}`}</button>)}<button type="button" className="icon-button" aria-label="增加文件" title="增加文件" disabled={locked || manualFiles.length >= 12} onClick={addManual}><Plus size={15} /></button></div>
            {selectedManual && <section className="repair-manual-file">
              <div className="repair-manual-path"><label className="studio-field"><span>源码文件</span>{caps?.mode === "source" && prompt.source_files.length ? <select aria-label="源码文件" value={selectedManual.path} onChange={event => { const file = prompt.source_files.find(item => item.path === event.target.value); updateManual({ path: event.target.value, sha256: file?.sha256 || "" }); }} disabled={locked}><option value="">选择源码文件</option>{prompt.source_files.map(file => <option key={file.path} value={file.path} disabled={manualFiles.some((other, index) => index !== manualIndex && other.path === file.path)}>{file.path}</option>)}</select> : <input aria-label="源码文件" value={selectedManual.path} onChange={event => updateManual({ path: event.target.value })} disabled={locked} placeholder="frontend/src/App.tsx" />}</label>{manualFiles.length > 1 && <button type="button" className="icon-button danger" aria-label="移除文件" title="移除文件" disabled={locked} onClick={() => { setManualFiles(current => current.filter((_, index) => index !== manualIndex)); setManualIndex(Math.max(0, manualIndex - 1)); setPreview(null); }}><Trash2 size={16} /></button>}</div>
              <label className="studio-field repair-hash"><span>SHA256</span><input aria-label="原文件 SHA256" value={selectedManual.sha256} readOnly={caps?.mode === "source"} onChange={event => updateManual({ sha256: event.target.value })} disabled={locked} maxLength={64} /></label>
              {sourceLoading && <Busy label="正在读取源码" compact inline delay={0} />}
              {sourceError && <div className="repair-source-error" role="alert">{sourceError}</div>}
              {sourceContext && <details className="repair-source-context"><summary>原文件<ChevronDown size={14} /></summary><textarea aria-label="原文件内容" value={sourceContext.content} readOnly spellCheck={false} /></details>}
              {selectedManual.edits.map((edit, index) => <div className="repair-manual-edit" key={index}><div className="repair-section-heading"><h3>替换 {index + 1}</h3>{selectedManual.edits.length > 1 && <button type="button" className="icon-button danger" aria-label={`移除替换 ${index + 1}`} disabled={locked} onClick={() => updateManual({ edits: selectedManual.edits.filter((_, at) => at !== index) })}><Trash2 size={15} /></button>}</div><div className="repair-edit-pair"><label className="studio-field"><span>原代码</span><textarea aria-label={`原代码 ${index + 1}`} value={edit.before} onChange={event => updateManual({ edits: selectedManual.edits.map((item, at) => at === index ? { ...item, before: event.target.value } : item) })} disabled={locked} spellCheck={false} /></label><label className="studio-field"><span>替换代码</span><textarea aria-label={`替换代码 ${index + 1}`} value={edit.after} onChange={event => updateManual({ edits: selectedManual.edits.map((item, at) => at === index ? { ...item, after: event.target.value } : item) })} disabled={locked} spellCheck={false} /></label></div></div>)}
              <button type="button" className="button secondary" disabled={locked || selectedManual.edits.length >= 100} onClick={() => updateManual({ edits: [...selectedManual.edits, { before: "", after: "" }] })}><Plus size={14} />增加替换</button>
            </section>}
          </>}
        </>}
        {step === 2 && (preview || operation) && <>
          <div className="repair-section-heading"><h3>{operation?.status === "succeeded" ? operation.kind === "chapter_cache" ? "索引已重建" : "修复已应用" : operation?.status === "rolled_back" ? "已回滚" : preview ? "变更预览" : "修复记录"}</h3>{preview && <span>{preview.files.length} 个文件</span>}</div>
          {preview && <p className="repair-summary">{preview.summary}</p>}
          {operation && <div className={`repair-operation ${operation.status}`}>
            {operation.status === "running" ? <Progress completed={operation.progress} total={100} stage={operation.stage || "正在处理"} busy /> : <div className="repair-result">{operation.status === "failed" ? <AlertCircle size={20} /> : <CheckCircle2 size={20} />}<strong>{operation.status === "succeeded" ? operation.kind === "chapter_cache" ? "章节索引已更新" : "校验通过，文件已更新" : operation.status === "rolled_back" ? "文件已恢复" : "修复未完成"}</strong></div>}
            {operation.status === "failed" && operation.error && <p className="repair-operation-error">{operation.error}</p>}
            {operation.auto_rolled_back && <p className="repair-status">已自动恢复原文件</p>}
            {operation.checks && operation.checks.length > 0 && <ul className="repair-checks">{operation.checks.map((check, index) => <li key={index} className={check.ok ? "passed" : "failed"}><span>{check.ok ? "✓" : "×"}</span><strong>{check.name}</strong><small>{check.detail}</small></li>)}</ul>}
            {operation.status === "succeeded" && (operation.restart_required || operation.frontend_rebuilt || operation.kind === "chapter_cache") && <div className="repair-restart">{operation.restart_required && <span>待重启应用</span>}{(operation.frontend_rebuilt || operation.kind === "chapter_cache") && <button type="button" className="button secondary" onClick={() => window.location.reload()}><RefreshCw size={14} />{operation.kind === "chapter_cache" ? "刷新内容" : "刷新界面"}</button>}</div>}
          </div>}
          {preview && preview.files.length > 0 && <div className="repair-files"><div className="repair-file-list" role="tablist" aria-label="修改文件">{preview.files.map((file, index) => <button key={file.path} role="tab" type="button" aria-selected={selectedFile === index} className={selectedFile === index ? "active" : ""} onClick={() => setSelectedFile(index)}><FileCode2 size={15} /><span>{file.path}</span><small><b>+{file.additions}</b> <em>−{file.deletions}</em></small></button>)}</div><div className="repair-file-content" role="tabpanel">{preview.files[selectedFile] && <FileDiff diff={preview.files[selectedFile].diff} />}</div></div>}
          {preview && preview.checks.length > 0 && !operation && <div className="repair-planned-checks"><strong>应用后校验</strong><span>{preview.checks.join(" · ")}</span></div>}
          {preview && !preview.can_apply && <p className="repair-status">当前应用仅支持导出修复方案。</p>}
        </>}
      </div>
      <footer className="repair-footer">
        {step > 0 ? <button type="button" className="button secondary" onClick={() => { setStep(step === 2 && !preview ? 0 : step - 1); setError(""); }} disabled={locked}><ArrowLeft size={15} />上一步</button> : <button type="button" className="button secondary" onClick={onClose} disabled={locked}>关闭</button>}
        <div>
          {step === 0 && <button type="button" className="button primary" disabled={locked || !caps || !description.trim() || (mode === "automatic" && (!selectedProfile || (isRemoteModel(selectedProfile) && !selectedProfile.target_fingerprint)))} onClick={() => mode === "manual" ? void makePrompt() : void openWorkbench()}>{mode === "manual" ? "准备修复模板" : "准备提示词"}<ArrowRight size={15} /></button>}
          {step === 1 && mode === "manual" && <button type="button" className="button primary" disabled={locked || !prompt || (mode === "manual" && (sourceLoading || !!sourceError)) || (mode === "manual" ? !manualSummary.trim() || !manualFiles.length || manualFiles.some(file => !file.path || !/^[0-9a-f]{64}$/.test(file.sha256) || file.edits.some(edit => !edit.before)) : !reply.trim())} onClick={() => void parseReply()}>检查修复方案<ArrowRight size={15} /></button>}
          {step === 2 && (preview || operation) && <>
            {applying && <button type="button" className="button secondary" onClick={onClose}>后台运行</button>}
            {operation?.rollback_available && operation.status !== "rolled_back" && <button type="button" className="button secondary" disabled={locked} onClick={() => void rollback()}><Undo2 size={15} />回滚本次修复</button>}
            {preview && !preview.can_apply && <button type="button" className="button primary" disabled={locked} onClick={() => void exportPatch()}><Download size={15} />导出修复方案</button>}
            {preview?.can_apply && !operation && <button type="button" className="button primary" disabled={locked} onClick={() => void apply()}><Wrench size={15} />确认应用修复</button>}
            {operation && operation.status !== "running" && <button type="button" className="button primary" onClick={onClose} disabled={locked}>完成</button>}
          </>}
        </div>
      </footer>
    </div>
  </Modal>{workbench.modal}</>;
}
