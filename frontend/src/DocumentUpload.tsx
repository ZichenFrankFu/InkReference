import { useEffect, useRef, useState } from "react";
import { BookOpen, Check, FileText, Files, LoaderCircle, RefreshCw, Upload, X } from "lucide-react";
import { normalizeList, studio, type DocumentMeta, type List } from "./studio";
import { Busy, Modal, Notice, SelectField, dateLabel, messageOf } from "./ui";

export type IngestionTask = {
  id: string; filename: string; kind: string; status: string; stage: string;
  progress: number | null; completed?: number | null; total?: number | null;
  document_id?: string; version_id?: string; target_document_id?: string;
  error?: string; mode?: "new" | "replace" | "append";
  options?: Record<string, unknown>; created_at: string; updated_at: string;
};
export const ingestionRunning = (task: IngestionTask) => ["queued", "running", "cancelling"].includes(task.status);
export const ingestionStage = (task: IngestionTask) => ({ queued: "等待处理", decoding: "读取文本", converting: `${task.kind?.toUpperCase() || "文件"} 转换为 TXT`, chapter_parse: "识别并解析章节", scan: "检查数据损坏与非正文", completed: "文本已就绪", failed: "处理未完成", cancelled: "已取消", interrupted: "处理中断" } as Record<string, string>)[task.stage] || "正在处理文本";
export function IngestionStatus({ status }: { status: string }) {
  const labels: Record<string, string> = { queued: "等待处理", running: "正在处理", cancelling: "正在取消", completed: "已完成", failed: "未完成", cancelled: "已取消", interrupted: "已中断" };
  return <span className={`status-pill status-${status}`}>{status === "running" && <LoaderCircle size={11} className="spin" />}{labels[status] || "处理中"}</span>;
}

export function IngestionProgress({ task, action, busy }: { task: IngestionTask; action: (kind: "cancel" | "retry") => void; busy: boolean }) {
  const working = ingestionRunning(task);
  const fraction = task.progress === null ? null : Math.max(0, Math.min(1, task.progress));
  const steps = ["上传原件", "转为文本", "识别章节", "检查标记"];
  const current = ({ queued: 0, decoding: 1, converting: 1, chapter_parse: 2, scan: 3, completed: 4 } as Record<string, number>)[task.stage] ?? 0;
  return <section className={`ingestion-progress ${working ? "processing" : task.status}`} aria-label="文本处理进度" aria-busy={working}>
    <div className="ingestion-progress-heading"><span className="ingestion-file-icon">{working ? <LoaderCircle size={19} className="spin" /> : task.status === "completed" ? <Check size={19} /> : <FileText size={19} />}</span><div><strong>{task.filename}</strong><span>{ingestionStage(task)}</span></div><div className="ingestion-progress-actions">{working ? <button className="icon-button" aria-label="取消文本处理" disabled={busy} onClick={() => action("cancel")}><X size={16} /></button> : ["failed", "cancelled", "interrupted"].includes(task.status) ? <button className="button secondary compact-button" disabled={busy} onClick={() => action("retry")}><RefreshCw size={14} />重试</button> : null}</div></div>
    <div className={`ingestion-meter ${fraction === null && working ? "indeterminate" : ""}`} role="progressbar" aria-label={ingestionStage(task)} aria-valuemin={0} aria-valuemax={100} aria-valuenow={fraction === null ? undefined : Math.round(fraction * 100)}><i style={fraction === null ? undefined : { width: `${fraction * 100}%` }} /></div>
    <ol className="ingestion-steps">{steps.map((label, index) => <li key={label} className={index < current ? "done" : index === current && working ? "current" : ""}><span>{index < current ? <Check size={10} /> : index + 1}</span>{label}</li>)}</ol>
    {task.error && <Notice error>{task.error}</Notice>}
  </section>;
}

export default function DocumentUpload({ items, target, onClose, onSubmitted }: { items: DocumentMeta[]; target?: DocumentMeta | null; onClose: () => void; onSubmitted: (task: IngestionTask) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState(target?.title || "");
  const destination = target?.id || "";
  const [mode, setMode] = useState<"replace" | "append">("replace");
  const [encoding, setEncoding] = useState("auto");
  const [workId, setWorkId] = useState(target?.work_id || "");
  const [tier, setTier] = useState("flash");
  const [ocr, setOcr] = useState("txt");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const format = file?.name.split(".").at(-1)?.toLowerCase();
  const duplicate = !destination && title.trim() ? items.filter((item) => item.title.trim() === title.trim()) : [];
  const chooseFile = (next: File | null) => {
    setError("");
    if (next && !/\.(txt|pdf|epub)$/i.test(next.name)) { setError("请选择 TXT、PDF 或 EPUB 文件。"); return; }
    setFile(next);
    if (!destination) setTitle(next?.name.replace(/\.[^.]+$/, "") || "");
  };
  const submit = async () => {
    if (!file) return;
    setBusy(true); setError("");
    try {
      const form = new FormData();
      form.append("file", file); form.append("title", title.trim() || file.name.replace(/\.[^.]+$/, ""));
      form.append("encoding", encoding); form.append("mode", destination ? mode : "new");
      if (workId.trim()) form.append("work_id", workId.trim());
      if (target) { form.append("target_document_id", target.id); form.append("base_version_id", target.latest_version_id); }
      form.append("options", JSON.stringify(format === "pdf" ? { tier, ocr_mode: ocr } : {}));
      const result = await studio<IngestionTask>("/ingestions", { method: "POST", body: form });
      onSubmitted(result);
    } catch (e) { setError(messageOf(e)); } finally { setBusy(false); }
  };
  return <Modal title={target ? "上传文件" : "新建作品"} onClose={onClose} busy={busy}>
    <div className="modal-body document-upload-form">
      {target && <div className="document-upload-target"><BookOpen size={18} /><div><span>上传至作品</span><strong>{target.title}</strong></div></div>}
      <input ref={input} type="file" accept=".txt,.pdf,.epub" hidden onChange={(event) => chooseFile(event.target.files?.[0] || null)} />
      <button className={`document-file-drop ${file ? "has-file" : ""}`} disabled={busy} onClick={() => input.current?.click()} onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); if (!busy) chooseFile(event.dataTransfer.files[0] || null); }}><span className="document-drop-icon"><Upload size={23} /></span><strong>{file?.name || "选择或拖入原始文件"}</strong><span>{file ? `${(file.size / 1024 / 1024).toFixed(2)} MB · 点击更换` : "TXT · PDF · EPUB"}</span></button>
      {destination && <fieldset className="document-upload-mode"><legend>上传后如何处理现有正文？</legend><label className={mode === "replace" ? "selected" : ""}><input type="radio" name="upload-mode" value="replace" checked={mode === "replace"} disabled={busy} onChange={() => setMode("replace")} /><span><strong>更新正文</strong><small>用新文件替换当前全部正文</small></span></label><label className={mode === "append" ? "selected" : ""}><input type="radio" name="upload-mode" value="append" checked={mode === "append"} disabled={busy} onChange={() => setMode("append")} /><span><strong>补充章节</strong><small>将新文件追加到当前正文末尾</small></span></label>{mode === "replace" && <p>替换前的正文仍可从历史版本恢复。</p>}</fieldset>}
      {!destination && <label className="studio-field"><span>文档标题</span><input value={title} disabled={busy} onChange={(event) => setTitle(event.target.value)} placeholder="作品名称" /></label>}
      {duplicate.length > 0 && <Notice>已有同名作品，本次将新建独立记录。更新已有作品时，请使用作品旁的“上传文件”。</Notice>}
      {format === "pdf" && <section className="document-format-options"><div className="document-option-heading"><FileText size={16} /><strong>PDF 转换设置</strong><span>MinerU</span></div><div className="form-grid"><SelectField label="解析模式" value={tier} disabled={busy} onChange={(value) => { setTier(value); if (value === "flash") setOcr("txt"); }}><option value="flash">读取原生文字层</option><option value="standard">完整解析</option></SelectField><SelectField label="文字识别" value={ocr} disabled={busy || tier === "flash"} onChange={setOcr}><option value="txt">使用文字层</option><option value="ocr">OCR 扫描文字</option></SelectField></div></section>}
      {format === "epub" && <section className="document-format-options epub"><div className="document-option-heading"><Files size={16} /><strong>EPUB 转换</strong></div><p>按书籍阅读顺序提取正文与章节标题，转为 TXT 后自动识别章节。</p></section>}
      <div className="form-grid">{format === "txt" && <SelectField label="文本编码" value={encoding} onChange={setEncoding} disabled={busy}><option value="auto">自动识别</option><option value="utf-8">UTF-8</option><option value="gb18030">GB18030 / GBK</option><option value="utf-16">UTF-16</option><option value="big5">Big5</option></SelectField>}{!destination && <label className="studio-field"><span>关联作品 ID（可选）</span><input value={workId} disabled={busy} onChange={(event) => setWorkId(event.target.value)} placeholder="例如 W001" /></label>}</div>
      <Notice error>{error}</Notice>{busy && <Busy inline delay={0} label="正在上传原始文件" />}
    </div>
    <div className="modal-footer"><button className="button secondary" disabled={busy} onClick={onClose}>取消</button><button className="button primary" disabled={!file || busy} onClick={() => void submit()}><Upload size={15} />{destination ? mode === "replace" ? "上传并更新正文" : "上传并补充章节" : "上传并识别章节"}</button></div>
  </Modal>;
}

type Source = { id: string; filename: string; bytes?: number; original_bytes?: number; created_at?: string; mode?: string; operation?: string; active?: boolean; superseded?: boolean; derived_format?: string | null };
export function DocumentSources({ document, versionId, onUpload }: { document: DocumentMeta; versionId: string; onUpload: () => void }) {
  const [sources, setSources] = useState<Source[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  useEffect(() => { let active = true; setLoading(true); setError(""); studio<List<Source>>(`/documents/${document.id}/sources`).then((data) => { if (active) setSources(normalizeList(data)); }).catch((e) => { if (active) setError(messageOf(e)); }).finally(() => { if (active) setLoading(false); }); return () => { active = false; }; }, [document.id, versionId]);
  return <section className="document-sources" aria-label="原始文件"><div className="document-sources-heading"><div><h3>原始文件</h3><span>{sources.length} 次上传 · 当前 {Number(document.current_characters ?? document.source_characters ?? 0).toLocaleString()} 字</span></div><button className="button secondary" onClick={onUpload}><Upload size={15} />上传文件</button></div><Notice error>{error}</Notice>{loading && <Busy label="正在读取文件记录" />}{!loading && <div className="document-source-timeline">{sources.map((source, index) => <article key={source.id} className={source.superseded ? "superseded" : ""}><span className="document-source-number">{index + 1}</span><span className="document-file-icon"><FileText size={19} /></span><div><strong>{source.filename}</strong><span>{({ new: "初始上传", replace: "替换正文", append: "补充章节" } as Record<string, string>)[source.mode || source.operation || ""] || (index ? "后续上传" : "初始上传")} · {dateLabel(source.created_at || document.created_at)} · {((source.original_bytes ?? source.bytes ?? 0) / 1024).toFixed(1)} KB{source.derived_format === "txt" && " · 已转为 TXT"}</span>{source.superseded && <small className="document-source-history">历史原件 · 已由后续更新替换</small>}</div><span className="document-source-format">{source.filename.split(".").at(-1)?.toUpperCase()}</span></article>)}</div>}</section>;
}
