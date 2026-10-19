import { DynamicModeSwitch, PromptWorkbenchModal, type DynamicPrepared } from "./DynamicFunction";
import { useEffect, useMemo, useRef, useState } from "react";
import { CheckCircle2, ChevronDown, Download, FileText, Plus, Save, Search, Settings2, Trash2 } from "lucide-react";
import { studio, send, normalizeList, type ModelProfile } from "./studio";
import { Busy, Empty, Modal, Notice, Progress, messageOf } from "./ui";
import { isRemoteModel } from "./model-security";
import SearchConfiguration, { type SearchSettings } from "./SearchConfiguration";
import "./volume-panel.css";

type Volume = { title: string; start_chapter: number; end_chapter: number };
type DirectoryEntry = { ordinal: number; heading: string; number?: number | null; placeholder?: boolean };
type Plan = { revision: number; chapter_count: number; volumes: Volume[]; method: string; directory?: DirectoryEntry[] };
type VolumeSource = { id?: string; url: string; title?: string; excerpt?: string; fetch_status?: string };
type VolumePreview = { volumes: Volume[]; sources?: VolumeSource[]; base_revision?: number };
type LegacyVolumeTask = { id: string; document_id: string; version_id: string; status: string; prompt?: string; response?: string; error?: string; progress?: { completed?: number; total?: number; label?: string } };
export function VolumePanel({ documentId, versionId, onDirty, onActivity }: { documentId: string; versionId: string; onDirty?: (dirty: boolean) => void; onActivity?: (label: string) => void }) {
  const [plan, setPlan] = useState<Plan | null>(null), [rows, setRows] = useState<Volume[]>([]), [reload, setReload] = useState(0);
  const [busy, setBusy] = useState(""), [error, setError] = useState(""), [message, setMessage] = useState("");
  const [mode, setMode] = useState<"auto" | "assisted" | "manual">("auto");
  const [models, setModels] = useState<ModelProfile[]>([]), [profile, setProfile] = useState("");
  const [settings, setSettings] = useState<SearchSettings | null>(null), [configOpen, setConfigOpen] = useState(false), [configLoading, setConfigLoading] = useState(false), [configBusy, setConfigBusy] = useState(false);
  const [promptOpen, setPromptOpen] = useState(false), [prepared, setPrepared] = useState<DynamicPrepared | null>(null);
  const [prompt, setPrompt] = useState(""), [reply, setReply] = useState(""), [preview, setPreview] = useState<VolumePreview | null>(null);
  const [sources, setSources] = useState<VolumeSource[]>([]), [discardMode, setDiscardMode] = useState<"auto" | "assisted" | "manual" | null>(null);
  const [legacyTask, setLegacyTask] = useState<LegacyVolumeTask | null>(null), [legacyOpen, setLegacyOpen] = useState(false), [legacyLoading, setLegacyLoading] = useState(false);
  const ticket = useRef(0), base = `/documents/${documentId}/versions/${versionId}/volumes`;
  const onDirtyRef = useRef(onDirty); onDirtyRef.current = onDirty;
  const activityRef = useRef(onActivity); activityRef.current = onActivity;
  const dirty = !!plan && JSON.stringify(rows) !== JSON.stringify(plan.volumes);
  const blocked = !!busy || configLoading;
  useEffect(() => { onDirtyRef.current?.(dirty); }, [dirty]);
  useEffect(() => { activityRef.current?.(busy || (configLoading ? "正在读取模型与搜索服务" : "")); }, [busy, configLoading]);
  useEffect(() => () => { onDirtyRef.current?.(false); activityRef.current?.(""); }, []);
  useEffect(() => {
    const id = ++ticket.current; setBusy("正在读取分卷"); setError(""); setPlan(null); setRows([]); setMessage(""); setSources([]); setPromptOpen(false); setPrepared(null); setPreview(null);
    void studio<Plan>(`${base}?coordinate_version=2`).then(next => { if (id === ticket.current) { setPlan(next); setRows(next.volumes); } }).catch(e => { if (id === ticket.current) setError(messageOf(e)); }).finally(() => { if (id === ticket.current) setBusy(""); });
    return () => { ticket.current++; };
  }, [base, reload]);
  useEffect(() => {
    if (mode === "manual") return;
    let alive = true; setConfigLoading(true);
    void Promise.all([studio<{ items: ModelProfile[] }>("/models"), studio<SearchSettings>("/research/settings")]).then(([choices, nextSettings]) => { if (alive) { const next = normalizeList(choices); setModels(next); setProfile(current => next.some(item => item.id === current) ? current : next[0]?.id || ""); setSettings(nextSettings); } }).catch(e => { if (alive) setError(messageOf(e)); }).finally(() => { if (alive) setConfigLoading(false); });
    return () => { alive = false; };
  }, [mode]);
  useEffect(() => {
    let alive = true;
    const focus = (event?: Event) => {
      let target: { operation?: string; prepared_id?: string } | null = null;
      try { target = event ? (event as CustomEvent).detail : JSON.parse(sessionStorage.getItem("inkreference.prompt-focus") || "null"); } catch { /* Optional task navigation. */ }
      if (target?.operation !== "volumes" || !target.prepared_id) return;
      const id = target.prepared_id;
      try { sessionStorage.removeItem("inkreference.prompt-focus"); } catch { /* Optional task navigation. */ }
      setPromptOpen(true); setBusy("正在恢复分卷提示词"); setError(""); setPreview(null);
      void (async () => {
        try {
          const saved = await studio<DynamicPrepared & { operation: string; mode: string; payload: { document_id: string; version_id: string }; raw_reply?: string; reply?: string }>(`/dynamic-functions/preparations/${encodeURIComponent(id)}`);
          if (saved.operation !== "volumes" || saved.payload.document_id !== documentId || saved.payload.version_id !== versionId) throw new Error("提示词任务与当前正文版本不符");
          await send(`/dynamic-functions/preparations/${encodeURIComponent(id)}/control`, { action: "resume" });
          if (!alive) return;
          setPrepared(saved); setPrompt(saved.prompt); setReply(saved.raw_reply ?? saved.reply ?? ""); setMode(saved.mode === "automatic" ? "auto" : "assisted"); if (saved.profile_id) setProfile(saved.profile_id);
        } catch (e) { if (alive) setError(messageOf(e)); } finally { if (alive) setBusy(""); }
      })();
    };
    focus(); window.addEventListener("inkreference:prompt-focus", focus);
    return () => { alive = false; window.removeEventListener("inkreference:prompt-focus", focus); };
  }, [documentId, versionId]);
  useEffect(() => {
    let alive = true;
    const focus = (event?: Event) => {
      let target: { taskId?: string; documentId?: string; versionId?: string } | null = null;
      try { target = event ? (event as CustomEvent).detail : JSON.parse(sessionStorage.getItem("inkreference.volume-task-focus") || "null"); } catch { /* Optional task navigation. */ }
      if (!target?.taskId || target.documentId && target.documentId !== documentId || target.versionId && target.versionId !== versionId) return;
      const taskId = target.taskId;
      setLegacyLoading(true); setLegacyOpen(true); setLegacyTask(null); setError("");
      void studio<LegacyVolumeTask>(`${base}/research/${encodeURIComponent(taskId)}`).then(task => {
        if (!alive) return;
        setLegacyTask(task);
        try { sessionStorage.removeItem("inkreference.volume-task-focus"); } catch { /* Optional task navigation. */ }
      }).catch(e => { if (alive) setError(messageOf(e)); }).finally(() => { if (alive) setLegacyLoading(false); });
    };
    focus(); window.addEventListener("inkreference:volume-task-focus", focus);
    return () => { alive = false; window.removeEventListener("inkreference:volume-task-focus", focus); };
  }, [base, documentId, versionId]);
  useEffect(() => {
    if (!legacyTask || !["queued", "running"].includes(legacyTask.status)) return;
    let alive = true;
    const interval = window.setInterval(() => {
      void studio<LegacyVolumeTask>(`${base}/research/${encodeURIComponent(legacyTask.id)}`).then(task => { if (alive) setLegacyTask(task); }).catch(e => { if (alive) setError(messageOf(e)); });
    }, 1200);
    return () => { alive = false; window.clearInterval(interval); };
  }, [base, legacyTask?.id, legacyTask?.status]);
  const run = async (label: string, action: () => Promise<void>) => { setBusy(label); setError(""); setMessage(""); try { await action(); } catch (e) { setError(messageOf(e)); } finally { setBusy(""); } };
  const prepare = async (id: string, resetPrompt: boolean, requestMode = mode) => {
    const next = await send<DynamicPrepared>("/dynamic-functions/volumes/prepare", { payload: { document_id: documentId, version_id: versionId, base_revision: plan?.revision, ...(requestMode === "auto" && settings?.target_fingerprint ? { expected_search_fingerprint: settings.target_fingerprint } : {}) }, ...(id ? { profile_id: id } : {}), mode: requestMode === "auto" ? "automatic" : "semi" });
    setPrepared(next); if (resetPrompt) setPrompt(next.prompt); setPreview(null);
  };
  const prepareFromHistory = () => {
    setLegacyOpen(false); setMode("assisted"); setPromptOpen(true); setPrepared(null); setPrompt(""); setReply(""); setPreview(null);
    void run("正在准备分卷提示词", () => prepare(profile, true, "assisted"));
  };
  const openPrompt = () => { setPromptOpen(true); setPrepared(null); setPrompt(""); setReply(""); setPreview(null); void run("正在准备分卷提示词", () => prepare(profile, true)); };
  const changeProfile = (id: string) => { setProfile(id); setPrepared(null); setPreview(null); void run("正在更新分卷模型", () => prepare(id, true)); };
  const execute = () => run("正在生成分卷回复", async () => {
    if (!prepared) throw new Error("请重新准备分卷提示词");
    const selected = models.find(item => item.id === profile);
    const result = await send<{ raw_reply?: string; reply?: string; content?: string }>("/dynamic-functions/volumes/execute", { prepared_id: prepared.prepared_id, prompt_override: prompt, model_profile_id: profile, remote_consent: isRemoteModel(selected), expected_model_fingerprint: prepared.model_fingerprint || selected?.target_fingerprint });
    setReply(result.raw_reply ?? result.reply ?? result.content ?? ""); setPreview(null);
  });
  const parse = () => run("正在校验分卷回复", async () => {
    if (!prepared) throw new Error("请重新准备分卷提示词");
    const result = await send<{ domain_result?: VolumePreview; preview?: VolumePreview }>("/dynamic-functions/volumes/preview", { prepared_id: prepared.prepared_id, reply });
    const next = result.domain_result || result.preview;
    if (!next || !Array.isArray(next.volumes)) throw new Error("分卷回复缺少有效分卷");
    setPreview(next);
  });
  const saveVolumes = async (volumes: Volume[], expectedRevision = plan?.revision ?? 0, preparedId?: string) => {
    const next = await send<Plan>(base, { volumes, expected_revision: expectedRevision, coordinate_version: 2, ...(preparedId ? { prepared_id: preparedId } : {}) }, "PUT");
    setPlan(next); setRows(next.volumes); setMessage("分卷已保存");
  };
  const save = () => run("正在保存分卷", () => saveVolumes(rows));
  const applyPreview = () => run("正在应用分卷", async () => {
    if (!preview || !prepared) return;
    const parsed = JSON.parse(reply.trim().replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/, ""));
    const reviewedReply = JSON.stringify({ ...parsed, volumes: preview.volumes }, null, 2);
    const checked = await send<{ domain_result: VolumePreview }>("/dynamic-functions/volumes/preview", { prepared_id: prepared.prepared_id, reply: reviewedReply });
    setReply(reviewedReply); setPreview(checked.domain_result);
    await saveVolumes(checked.domain_result.volumes, checked.domain_result.base_revision ?? plan?.revision ?? 0, prepared.prepared_id);
    setSources(checked.domain_result.sources || []); setPromptOpen(false); setPreview(null);
  });
  const setNextMode = (next: "auto" | "assisted" | "manual") => { if (dirty && next !== mode) setDiscardMode(next); else setMode(next); };
  const editRows = (values: Volume[], change: (next: Volume[]) => void) => <div className="volume-table"><div className="volume-row volume-head"><span>卷名</span><span>起始章</span><span>结束章</span><span /></div>{values.map((row, index) => <div className="volume-row" key={index}><input aria-label={`第${index + 1}卷名称`} value={row.title} disabled={blocked} onChange={event => change(values.map((item, at) => at === index ? { ...item, title: event.target.value } : item))} /><VolumeBoundary label={`第${index + 1}卷起始章`} value={row.start_chapter} count={plan?.chapter_count || 0} directory={plan?.directory || []} disabled={blocked} onChange={start_chapter => change(values.map((item, at) => at === index ? { ...item, start_chapter } : item))} /><VolumeBoundary label={`第${index + 1}卷结束章`} value={row.end_chapter} count={plan?.chapter_count || 0} directory={plan?.directory || []} disabled={blocked} onChange={end_chapter => change(values.map((item, at) => at === index ? { ...item, end_chapter } : item))} /><button className="icon-button" aria-label={`删除第${index + 1}卷`} disabled={blocked} onClick={() => change(values.filter((_, at) => at !== index))}><Trash2 size={14} /></button></div>)}{!values.length && <div className="volume-empty">尚未设置分卷</div>}</div>;
  const add = (values: Volume[]) => {
    const count = plan?.chapter_count || 1;
    if (!values.length) return [{ title: "第1卷", start_chapter: 1, end_chapter: count }];
    const last = values[values.length - 1];
    if (last.end_chapter < count) return [...values, { title: `第${values.length + 1}卷`, start_chapter: last.end_chapter + 1, end_chapter: count }];
    for (let at = values.length - 1; at >= 0; at--) {
      const row = values[at];
      if (row.end_chapter > row.start_chapter) return [...values.slice(0, at), { ...row, end_chapter: row.end_chapter - 1 }, { title: `第${at + 2}卷`, start_chapter: row.end_chapter, end_chapter: row.end_chapter }, ...values.slice(at + 1)];
    }
    return values;
  };
  return <section className="volume-panel" aria-label="分卷规划" aria-busy={blocked}>
    <div className="volume-toolbar"><div><h3>作品分卷</h3><span>{plan?.chapter_count ?? "—"} 个目录项 · {rows.length} 卷</span>{dirty && <strong className="volume-unsaved">未保存</strong>}</div>{mode === "manual" && dirty && <button className="button primary compact-button" disabled={blocked} onClick={save}><Save size={14} />保存分卷</button>}</div>
    {legacyTask && <button className="button secondary compact-button" disabled={blocked} onClick={() => setLegacyOpen(true)}><FileText size={14} />历史分卷任务</button>}
    <DynamicModeSwitch value={mode} onChange={setNextMode} disabled={blocked} label="分卷模式" items={[{ value: "auto", label: "自动" }, { value: "assisted", label: "半自动" }, { value: "manual", label: "手动" }]} />
    {mode !== "manual" && <div className={`volume-mode-controls ${mode}`}><button className="button primary compact-button" disabled={blocked || !plan || (mode === "auto" && !settings?.key_available)} onClick={openPrompt}><FileText size={14} />{mode === "auto" ? "准备联网分卷" : "准备分卷提示词"}</button>{mode === "auto" && <button className="button secondary compact-button volume-search-config" aria-label="搜索服务设置" disabled={blocked} onClick={() => setConfigOpen(true)}><Settings2 size={15} />搜索服务</button>}</div>}
    {mode === "auto" && settings && !settings.key_available && <Notice error>搜索服务尚未配置<button className="text-button" onClick={() => setConfigOpen(true)}>配置搜索服务</button></Notice>}
    {busy && !promptOpen && <Busy label={busy} inline delay={0} />}
    {message && <div className="volume-feedback" role="status"><CheckCircle2 size={13} />{message}</div>}
    {error && !promptOpen && <Notice error>{error}{!plan && <button className="text-button" disabled={blocked} onClick={() => setReload(value => value + 1)}>重试</button>}</Notice>}
    {mode === "manual" ? <>{editRows(rows, setRows)}<button className="button secondary compact-button volume-add" disabled={blocked || !plan || rows.length >= plan.chapter_count} onClick={() => setRows(add(rows))}><Plus size={14} />添加分卷</button></> : <div className="volume-saved-plan">{rows.map((row, index) => <div key={index}><strong>{row.title}</strong><span>第 {row.start_chapter} — {row.end_chapter} 章</span></div>)}{!rows.length && plan && <div className="volume-empty">尚未设置分卷</div>}</div>}
    {!!sources.length && <details className="volume-sources"><summary>检索来源 · {sources.length}</summary>{sources.map((source, index) => <a key={source.id || index} href={/^https?:\/\//i.test(source.url) ? source.url : undefined} target="_blank" rel="noreferrer"><strong>{source.title || source.url}</strong><span>{source.excerpt || source.url}</span></a>)}</details>}
    {promptOpen && <PromptWorkbenchModal title="作品分卷" mode={mode === "auto" ? "automatic" : "semi"} prompt={prompt} onPromptChange={value => { setPrompt(value); setPreview(null); }} reply={reply} onReplyChange={value => { setReply(value); setPreview(null); }} profiles={models} profileId={profile} onProfileChange={changeProfile} onSend={execute} onPreview={parse} onApply={applyPreview} onClose={() => { setPromptOpen(false); setPreview(null); setError(""); }} busy={!!busy} error={error} canApply={!!preview} applyLabel="确认应用分卷" preview={preview ? <div className="volume-preview-editor">{editRows(preview.volumes, volumes => setPreview(current => current ? { ...current, volumes } : null))}<button className="button secondary compact-button" disabled={blocked || !!plan && preview.volumes.length >= plan.chapter_count} onClick={() => setPreview(current => current ? { ...current, volumes: add(current.volumes) } : null)}><Plus size={14} />添加分卷</button></div> : undefined} />}
    {discardMode && <Modal title="分卷修改尚未保存" onClose={() => setDiscardMode(null)}><div className="modal-footer"><button className="button secondary" onClick={() => setDiscardMode(null)}>继续编辑</button><button className="button danger" onClick={() => { setRows(plan?.volumes || []); setMode(discardMode); setDiscardMode(null); }}>放弃修改</button></div></Modal>}
    {legacyOpen && <Modal title="历史分卷任务" onClose={() => setLegacyOpen(false)} busy={legacyLoading} wide><div className="modal-body">{legacyLoading ? <Busy label="正在读取分卷任务" inline delay={0} /> : legacyTask && <><div className="volume-feedback"><strong>{({queued:"等待开始",running:"进行中",needs_review:"待确认",failed:"已中断",cancelled:"已终止"} as Record<string,string>)[legacyTask.status] || legacyTask.status}</strong></div><Progress completed={legacyTask.progress?.completed} total={legacyTask.progress?.total} stage={legacyTask.progress?.label || "分卷检索"} busy={["queued","running"].includes(legacyTask.status)} /><label className="studio-field"><span>历史提示词</span><textarea aria-label="历史分卷提示词" value={legacyTask.prompt || ""} readOnly rows={7} /></label><label className="studio-field"><span>历史回复</span><textarea aria-label="历史分卷回复" value={legacyTask.response || ""} readOnly rows={7} /></label><Notice error>{legacyTask.error}</Notice></>}<Notice error>{error}</Notice></div><div className="modal-footer"><button className="button secondary" disabled={legacyLoading} onClick={() => setLegacyOpen(false)}>关闭</button><button className="button primary" disabled={legacyLoading || !plan || !!busy} onClick={prepareFromHistory}>准备半自动提示词</button></div></Modal>}
    {configOpen && <Modal title="搜索服务" busy={configBusy} onClose={() => setConfigOpen(false)}><SearchConfiguration hideHeading onBusy={setConfigBusy} settings={settings} onSave={next => { setSettings(next); setConfigOpen(false); setConfigBusy(false); }} onClose={() => setConfigOpen(false)} /></Modal>}
  </section>;
}

function VolumeBoundary({ label, value, count, directory, disabled, onChange }: { label: string; value: number; count: number; directory: DirectoryEntry[]; disabled: boolean; onChange: (value: number) => void }) {
  const [open, setOpen] = useState(false), [query, setQuery] = useState(""), [page, setPage] = useState(0);
  const current = directory.find(item => item.ordinal === value);
  const matches = useMemo(() => { const needle = query.trim().toLocaleLowerCase(); if (!needle) return directory; const result = directory.filter(item => item.heading.toLocaleLowerCase().includes(needle) || String(item.ordinal).includes(needle) || String(item.number ?? "").includes(needle)); const number = /^\d+$/.test(needle) ? Number(needle) : null; return number == null ? result : result.sort((a, b) => Number(b.ordinal === number || b.number === number) - Number(a.ordinal === number || a.number === number)); }, [directory, query]);
  const pageCount = Math.ceil(matches.length / 40), visible = matches.slice(page * 40, page * 40 + 40);
  const show = () => { setQuery(""); setPage(Math.max(0, Math.floor(directory.findIndex(item => item.ordinal === value) / 40))); setOpen(true); };
  return <div className="volume-boundary"><div><input aria-label={label} type="number" min={1} max={count} value={value} disabled={disabled} onChange={event => onChange(Number(event.target.value))} /><button className="icon-button" aria-label={`选择${label}`} title={current?.heading || label} disabled={disabled || !directory.length} onClick={show}><ChevronDown size={12} /></button></div><button className={`volume-boundary-caption ${current?.placeholder ? "missing" : ""}`} disabled={disabled || !directory.length} onClick={show} title={current?.heading}>{current?.heading || `#${value}`}</button>{open && <Modal title={`选择${label}`} onClose={() => setOpen(false)}><div className="modal-body volume-boundary-picker"><label className="volume-directory-search"><Search size={15} /><input autoFocus aria-label="搜索章节边界" placeholder="搜索章号或标题" value={query} onChange={event => { setQuery(event.target.value); setPage(0); }} /></label><div className="volume-directory-count">{matches.length} 项</div><div className="volume-directory-list" role="listbox" aria-label={label}>{visible.map(item => <button key={item.ordinal} role="option" aria-selected={item.ordinal === value} className={`${item.ordinal === value ? "selected" : ""} ${item.placeholder ? "missing" : ""}`} onClick={() => { onChange(item.ordinal); setOpen(false); }}><span>#{item.ordinal}</span><strong>{item.heading}</strong>{item.placeholder && <small>待补充</small>}{item.ordinal === value && <CheckCircle2 size={14} />}</button>)}{!visible.length && <div className="volume-empty">没有匹配的章节</div>}</div></div><div className="modal-footer volume-directory-pagination"><span>{pageCount ? page + 1 : 0} / {pageCount}</span><button className="button secondary compact-button" disabled={page <= 0} onClick={() => setPage(value => value - 1)}>上一页</button><button className="button secondary compact-button" disabled={page + 1 >= pageCount} onClick={() => setPage(value => value + 1)}>下一页</button></div></Modal>}</div>;
}

type Source = { id:string; filename:string; bytes?:number; original_bytes?:number; created_at?:string; sha256?:string; original_sha256?:string };
export function SourceFiles({documentId,versionId}:{documentId:string;versionId?:string}) {
  const [files,setFiles]=useState<Source[]>([]),[busy,setBusy]=useState(true),[error,setError]=useState('');
  useEffect(()=>{let active=true;setBusy(true);studio<{items:Source[]}>(`/documents/${documentId}/sources`).then(data=>{if(active)setFiles(normalizeList(data));}).catch(e=>{if(active)setError(messageOf(e));}).finally(()=>{if(active)setBusy(false);});return()=>{active=false;};},[documentId,versionId]);
  return <section className="source-files" aria-label="原始文件">{busy&&<Busy label="读取原始文件" inline/>}{error&&<Notice error>{error}</Notice>}{!busy&&!files.length&&<Empty icon={FileText} title="暂无原始文件"/>}{files.map((file,i)=><article className="source-file" key={file.id}><FileText size={22}/><div><strong>{file.filename}</strong><span>{i===0?'初始上传':'接续上传'}{file.original_bytes!==undefined||file.bytes!==undefined?` · ${((file.original_bytes??file.bytes??0)/1024).toFixed(1)} KB`:''}</span><code>{file.sha256||file.original_sha256}</code></div><a className="button" href={`/api/studio/documents/${documentId}/sources/${file.id}/download`} download><Download size={15}/>下载原件</a></article>)}</section>;
}
