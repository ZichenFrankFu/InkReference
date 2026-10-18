import { useEffect, useRef, useState } from "react";
import { AlertCircle, ArrowLeft, ArrowRight, Check, CheckCircle2, ChevronDown, Copy, Download, FileCode2, RefreshCw, Undo2, Wrench } from "lucide-react";
import { download, jsonBody } from "./api";
import { diagnosticEvents } from "./diagnostics";
import { send, studio } from "./studio";
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
};
type PreviewResult = {
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
type Draft = {
  description: string;
  topic: string;
  prompt: PromptResult | null;
  reply: string;
  preview: PreviewResult | null;
  operation: RepairOperation | null;
  step: number;
};
let retained: Draft = { description: "", topic: "chapters", prompt: null, reply: "", preview: null, operation: null, step: 0 };

function FileDiff({ diff }: { diff: string }) {
  return <pre className="repair-diff">{diff.split("\n").map((line, index) => <span key={index} className={line.startsWith("@@") ? "repair-diff-range" : line.startsWith("+") && !line.startsWith("+++") ? "repair-diff-add" : line.startsWith("-") && !line.startsWith("---") ? "repair-diff-remove" : ""}>{line || " "}{"\n"}</span>)}</pre>;
}

export default function QuickRepair({ onClose }: { onClose: () => void }) {
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
  const [copied, setCopied] = useState(false);
  const [copyFallback, setCopyFallback] = useState(false);
  const [selectedFile, setSelectedFile] = useState(0);
  const [previous, setPrevious] = useState<RepairOperation[]>([]);
  const [documents, setDocuments] = useState<{id: string; title: string}[] | null>(null);
  const [documentId, setDocumentId] = useState("");
  const [documentsLoading, setDocumentsLoading] = useState(false);
  const promptRef = useRef<HTMLTextAreaElement>(null);
  const events = useRef(diagnosticEvents());
  const applying = operation?.status === "running";
  const locked = Boolean(busy) || applying;

  useEffect(() => {
    retained = { description, topic, prompt, reply, preview, operation, step };
  }, [description, topic, prompt, reply, preview, operation, step]);

  const loadCapabilities = async () => {
    setCapsLoading(true);
    setError("");
    try {
      const result = await studio<Capabilities>("/repair/capabilities");
      setCaps(result);
      if (!result.topics.some((item) => item.id === topic)) setTopic(result.topics[0]?.id || "app");
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
      setPreview(null);
      setOperation(null);
      setCopied(false);
      setCopyFallback(false);
      setStep(1);
    } catch (failure) { setError(messageOf(failure)); }
    finally { setBusy(""); }
  };

  const copyPrompt = async () => {
    if (!prompt) return;
    try {
      if (!navigator.clipboard?.writeText) throw new Error("clipboard unavailable");
      await navigator.clipboard.writeText(prompt.prompt);
      setCopied(true);
      setCopyFallback(false);
    } catch {
      setCopyFallback(true);
      promptRef.current?.focus();
      promptRef.current?.select();
    }
  };

  const parseReply = async () => {
    setBusy("正在校验修复方案");
    setError("");
    try {
      const result = await send<PreviewResult>("/repair/preview", { reply, session_id: prompt?.session_id });
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
      setOperation(await send<RepairOperation>("/repair/apply", { preview_id: preview.preview_id }));
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
    try { await download("/studio/repair/export", "InkReference-repair.json", { method: "POST", ...jsonBody({ preview_id: preview.preview_id }) }); }
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

  return <Modal title="快捷修复" onClose={onClose} busy={Boolean(busy)} wide>
    <div className="repair-workbench">
      <nav className="repair-steps" aria-label="修复步骤">
        {["问题描述", "交换方案", "检查与应用"].map((label, index) => <button type="button" key={label} className={step === index ? "active" : step > index ? "done" : ""} disabled={locked || (index === 1 && !prompt) || (index === 2 && !preview && !operation)} onClick={() => { setStep(index); setError(""); }}><span>{step > index ? <Check size={13} /> : index + 1}</span>{label}</button>)}
        {caps && <span className="repair-mode">{caps.mode === "source" ? "源码版" : "安装版"}</span>}
      </nav>
      <div className="repair-loading-line">{(capsLoading || busy) && <div className="indeterminate" role="progressbar" aria-label={busy || "正在读取修复能力"}><i /></div>}</div>
      <div className="repair-body">
        {error && <div className="repair-error" role="alert"><strong>操作未完成</strong><span>{error}</span>{!caps && <button type="button" className="button secondary" onClick={() => void loadCapabilities()} disabled={capsLoading}>重试</button>}</div>}
        {step === 0 && <>
          <div className="repair-section-heading"><h3>遇到了什么问题？</h3><span>{events.current.length} 条近期诊断</span></div>
          <label className="studio-field"><span>功能位置</span><select aria-label="功能位置" value={topic} onChange={(event) => setTopic(event.target.value)} disabled={!caps || locked}>{(caps?.topics || [{ id: "chapters", label: "正文与清洗" }]).map((item) => <option value={item.id} key={item.id}>{item.label}</option>)}</select></label>
          <label className="studio-field"><span>问题与预期结果</span><textarea aria-label="问题与预期结果" className="repair-description" value={description} onChange={(event) => setDescription(event.target.value)} placeholder="操作步骤、出现的问题、希望怎样调整" maxLength={6000} disabled={locked} /></label>
          <details className="repair-diagnostics"><summary>近期诊断 <ChevronDown size={14} /></summary><ul>{events.current.length ? events.current.slice().reverse().map((event, index) => <li key={index}><code>{event.kind}</code><span>{event.message}</span>{event.path && <small>{event.path}</small>}</li>) : <li className="muted">暂无错误记录</li>}</ul></details>
          {(caps?.can_rebuild_cache || previous.length > 0) && <details className="repair-recovery" onToggle={(event) => { if (event.currentTarget.open && caps?.can_rebuild_cache) void loadDocuments(); }}><summary>本机恢复<ChevronDown size={14} /></summary><div>
            {previous.length > 0 && <div className="repair-recovery-row"><span>上次源码修复</span><button className="button secondary" type="button" onClick={() => { setOperation(previous[0]); setPreview(null); setStep(2); }}><Undo2 size={14} />查看与回滚</button></div>}
            {caps?.can_rebuild_cache && <div className="repair-recovery-row"><select aria-label="重建索引的作品" value={documentId} onChange={(event) => setDocumentId(event.target.value)} disabled={documentsLoading || locked}><option value="">{documentsLoading ? "正在读取作品" : "选择作品"}</option>{documents?.map((item) => <option value={item.id} key={item.id}>{item.title}</option>)}</select><button className="button secondary" type="button" onClick={() => void rebuildCache()} disabled={!documentId || locked}><RefreshCw size={14} />重建章节索引</button></div>}
            {documentsLoading && <Busy label="正在读取作品" compact inline delay={0} />}
          </div></details>}
        </>}
        {step === 1 && prompt && <>
          <div className="repair-section-heading"><h3>诊断提示词</h3><button type="button" className="button secondary" onClick={() => void copyPrompt()}><Copy size={15} />{copied ? "已复制" : "复制提示词"}</button></div>
          <textarea ref={promptRef} aria-label="诊断提示词" className="repair-prompt" value={prompt.prompt} readOnly spellCheck={false} />
          {copyFallback && <p className="repair-status" role="status">文本已选中，请按 Ctrl+C / ⌘C 复制。</p>}
          <details className="repair-sources"><summary>{prompt.source_files.length} 个源码文件{prompt.truncated ? " · 上下文已截取" : ""}<ChevronDown size={14} /></summary><div>{prompt.source_files.map((file) => <code key={file.path}>{file.path}</code>)}</div></details>
          <label className="studio-field repair-reply"><span>粘贴模型返回的修复 JSON</span><textarea aria-label="修复 JSON" value={reply} onChange={(event) => { setReply(event.target.value); setPreview(null); }} placeholder='{"protocol":"inkreference.repair.v1", …}' spellCheck={false} disabled={locked} /></label>
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
          {step === 0 && <button type="button" className="button primary" disabled={locked || !caps || !description.trim()} onClick={() => void makePrompt()}>生成诊断提示词<ArrowRight size={15} /></button>}
          {step === 1 && <button type="button" className="button primary" disabled={locked || !reply.trim()} onClick={() => void parseReply()}>检查修复方案<ArrowRight size={15} /></button>}
          {step === 2 && (preview || operation) && <>
            {applying && <button type="button" className="button secondary" onClick={onClose}>后台运行</button>}
            {operation?.rollback_available && operation.status !== "rolled_back" && <button type="button" className="button secondary" disabled={locked} onClick={() => void rollback()}><Undo2 size={15} />回滚本次修复</button>}
            {preview && !preview.can_apply && <button type="button" className="button primary" disabled={locked} onClick={() => void exportPatch()}><Download size={15} />导出修复方案</button>}
            {preview?.can_apply && !operation && <button type="button" className="button primary" disabled={locked} onClick={() => void apply()}><Wrench size={15} />应用修复</button>}
            {operation && operation.status !== "running" && <button type="button" className="button primary" onClick={onClose} disabled={locked}>完成</button>}
          </>}
        </div>
      </footer>
    </div>
  </Modal>;
}
