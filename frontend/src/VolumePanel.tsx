import { DynamicModeSwitch } from "./DynamicFunction";
import { useEffect, useMemo, useRef, useState } from "react";
import { CheckCircle2, ChevronDown, Copy, Download, FileText, Plus, Save, Search, Settings2, Sparkles, Square, Trash2 } from "lucide-react";
import { studio, send, normalizeList, type ModelProfile } from "./studio";
import { Busy, Empty, Modal, Notice, Progress, messageOf } from "./ui";
import { isRemoteModel, modelEndpoint } from "./model-security";
import SearchConfiguration, { type SearchSettings } from "./SearchConfiguration";
import "./volume-panel.css";

type Volume = { title: string; start_chapter: number; end_chapter: number };
type DirectoryEntry = { ordinal: number; heading: string; number?: number | null; placeholder?: boolean };
type Plan = { revision: number; chapter_count: number; volumes: Volume[]; method: string; directory?: DirectoryEntry[] };
type VolumeSource = { id?: string; url: string; title?: string; excerpt?: string; fetch_status?: string };
type ResearchJob = { id: string; status: "queued" | "running" | "completed" | "failed" | "cancelled"; stage: string; progress: { completed: number; total: number; label: string }; volumes?: Volume[]; sources?: VolumeSource[]; error?: string };
const running = (job: ResearchJob | null) => !!job && ["queued", "running"].includes(job.status);
export function VolumePanel({ documentId, versionId, onDirty, onActivity }: { documentId: string; versionId: string; onDirty?: (dirty: boolean) => void; onActivity?: (label: string) => void }) {
  const [plan, setPlan] = useState<Plan | null>(null), [rows, setRows] = useState<Volume[]>([]), [reload, setReload] = useState(0);
  const [busy, setBusy] = useState(""), [error, setError] = useState(""), [message, setMessage] = useState("");
  const [mode, setMode] = useState<"auto" | "assisted" | "manual">("auto");
  const [paste, setPaste] = useState(""), [showPaste, setShowPaste] = useState(false), [prompt, setPrompt] = useState("");
  const [models, setModels] = useState<ModelProfile[]>([]), [profile, setProfile] = useState(""), [confirm, setConfirm] = useState(false);
  const [settings, setSettings] = useState<SearchSettings | null>(null), [configOpen, setConfigOpen] = useState(false), [configLoading, setConfigLoading] = useState(false), [configBusy, setConfigBusy] = useState(false);
  const [job, setJob] = useState<ResearchJob | null>(null), [sources, setSources] = useState<VolumeSource[]>([]);
  const ticket = useRef(0), handledJob = useRef(""), base = `/documents/${documentId}/versions/${versionId}/volumes`;
  const onDirtyRef = useRef(onDirty); onDirtyRef.current = onDirty;
  const activityRef = useRef(onActivity); activityRef.current = onActivity;
  const selected = models.find((model) => model.id === profile);
  const dirty = !!plan && JSON.stringify(rows) !== JSON.stringify(plan.volumes);
  const active = running(job), blocked = !!busy || active || configLoading;
  useEffect(() => { onDirtyRef.current?.(dirty); }, [dirty]);
  useEffect(() => { activityRef.current?.(busy || (configLoading ? "正在读取模型与搜索服务" : "") || (active ? job?.progress?.label || "正在检索分卷" : "")); }, [busy, configLoading, active, job?.progress?.label]);
  useEffect(() => () => { onDirtyRef.current?.(false); activityRef.current?.(""); }, []);
  useEffect(() => {
    const id = ++ticket.current; setBusy("正在读取分卷"); setError(""); setPlan(null); setRows([]); setMessage(""); setJob(null); setSources([]); handledJob.current = "";
    void studio<Plan>(`${base}?coordinate_version=2`).then((next) => {
      if (id === ticket.current) { setPlan(next); setRows(next.volumes); }
    }).catch((e) => { if (id === ticket.current) setError(messageOf(e)); }).finally(() => { if (id === ticket.current) setBusy(""); });
    let saved = ""; try { saved = sessionStorage.getItem(`inkreference:volume-research:${documentId}:${versionId}`) || ""; } catch { /* Optional task navigation memory. */ }
    if (saved) void studio<ResearchJob>(`${base}/research/${encodeURIComponent(saved)}`).then(next => { if (id === ticket.current) { setJob(next); if (next.status === "completed") { handledJob.current = next.id; setSources(next.sources || []); } } }).catch(() => { try { sessionStorage.removeItem(`inkreference:volume-research:${documentId}:${versionId}`); } catch { /* Optional task navigation memory. */ } });
    return () => { ticket.current++; };
  }, [base, documentId, versionId, reload]);
  useEffect(() => {
    if (mode !== "auto") return;
    let alive = true; setConfigLoading(true);
    void Promise.all([studio<{ items: ModelProfile[] }>("/models"), studio<SearchSettings>("/research/settings")]).then(([choices, nextSettings]) => { if (alive) { const next = normalizeList(choices); setModels(next); setProfile(current => next.some(item => item.id === current) ? current : next[0]?.id || ""); setSettings(nextSettings); } }).catch(e => { if (alive) setError(messageOf(e)); }).finally(() => { if (alive) setConfigLoading(false); });
    return () => { alive = false; };
  }, [mode]);
  useEffect(() => {
    if (!job || !active || !plan) return;
    let alive = true, timer = 0;
    const poll = async () => { try { const next = await studio<ResearchJob>(`${base}/research/${encodeURIComponent(job.id)}`); if (alive) { setJob(next); if (next.status === "completed" && next.id !== handledJob.current) { handledJob.current = next.id; setRows(next.volumes || []); setSources(next.sources || []); setMessage("分卷预览已更新"); } if (next.status === "failed") setError(next.error || "分卷检索失败"); } } catch (e) { if (alive) setError(messageOf(e)); } finally { if (alive) timer = window.setTimeout(() => void poll(), 1000); } };
    timer = window.setTimeout(() => void poll(), 400);
    return () => { alive = false; window.clearTimeout(timer); };
  }, [base, job?.id, active, !!plan]);
  const run = async (label: string, action: () => Promise<void>) => { setBusy(label); setError(""); setMessage(""); try { await action(); } catch (e) { setError(messageOf(e)); } finally { setBusy(""); } };
  const detect = () => run("正在识别正文分卷", async () => { const result = await send<{ volumes: Volume[] }>(`${base}/detect?coordinate_version=2`, {}); setRows(result.volumes); setSources([]); setMessage("分卷预览已更新"); });
  const modelDetect = () => run("正在创建分卷检索", async () => {
    if (!selected || !settings || !plan) return;
    setConfirm(false);
    const next = await send<ResearchJob>(`${base}/research`, { profile_id: profile, remote_consent: isRemoteModel(selected), expected_model_fingerprint: selected.target_fingerprint, expected_search_fingerprint: settings.target_fingerprint, base_revision: plan.revision });
    setJob(next); handledJob.current = "";
    try { sessionStorage.setItem(`inkreference:volume-research:${documentId}:${versionId}`, next.id); } catch { /* Optional task navigation memory. */ }
    if (next.status === "completed") { handledJob.current = next.id; setRows(next.volumes || []); setSources(next.sources || []); }
  });
  const save = () => run("正在保存分卷", async () => { const next = await send<Plan>(base, { volumes: rows, expected_revision: plan?.revision ?? 0, coordinate_version: 2 }, "PUT"); setPlan(next); setRows(next.volumes); setMessage("分卷已保存"); });
  const copy = () => run("正在准备提示词", async () => { const result = await studio<{ prompt: string }>(`${base}/prompt`); try { await navigator.clipboard.writeText(result.prompt); setMessage("提示词已复制"); } catch { setPrompt(result.prompt); } });
  const update = (index: number, change: Partial<Volume>) => setRows((current) => current.map((row, position) => position === index ? { ...row, ...change } : row));
  const jobProgress = job?.progress;
  return <section className="volume-panel" aria-label="分卷规划" aria-busy={blocked}>
    <div className="volume-toolbar"><div><h3>作品分卷</h3><span>{plan?.chapter_count ?? "—"} 个目录项 · {rows.length} 卷</span>{dirty && <strong className="volume-unsaved">未保存</strong>}</div>{dirty && <button className="button primary compact-button" disabled={blocked} onClick={save}><Save size={14} />保存分卷</button>}</div>
    <DynamicModeSwitch value={mode} onChange={setMode} disabled={blocked} label="分卷模式" items={[{value:"auto",label:"自动"},{value:"assisted",label:"半自动"},{value:"manual",label:"手动"}]} />
    <div className="volume-mode-controls">
      {mode === "auto" && <><label className="studio-field"><span>模型</span><select aria-label="分卷识别模型" value={profile} disabled={blocked} onChange={event => setProfile(event.target.value)}><option value="">选择模型</option>{models.map(model => <option key={model.id} value={model.id}>{model.name} · {model.model}</option>)}</select></label><button className="icon-button volume-search-config" aria-label="搜索服务设置" title="搜索服务设置" disabled={blocked} onClick={() => setConfigOpen(true)}><Settings2 size={16} /></button><button className="button primary compact-button" disabled={blocked || !selected || !plan || !settings?.key_available} onClick={() => setConfirm(true)}><Search size={14} />联网查找分卷</button></>}
      {mode === "assisted" && <><button className="button secondary compact-button" disabled={blocked || !plan} onClick={copy}><Copy size={14} />复制提示词</button><button className="button primary compact-button" disabled={blocked || !plan} onClick={() => setShowPaste(true)}><FileText size={14} />粘贴回复</button></>}
      {mode === "manual" && <button className="button secondary compact-button" disabled={blocked || !plan} onClick={detect}><Sparkles size={14} />从正文识别</button>}
    </div>
    {mode === "auto" && settings && !settings.key_available && <Notice error>搜索服务尚未配置<button className="text-button" onClick={() => setConfigOpen(true)}>配置搜索服务</button></Notice>}
    {job && (active || job.status === "failed" || job.status === "cancelled") && <div className="volume-research-progress"><div><Progress completed={jobProgress?.completed} total={jobProgress?.total} stage={jobProgress?.label || ({ failed: "检索失败", cancelled: "检索已取消" } as Record<string, string>)[job.status] || "正在检索分卷"} busy={active} /></div>{active && <button className="icon-button volume-stop" aria-label="终止分卷检索" title="终止分卷检索" disabled={!!busy} onClick={() => void run("正在终止分卷检索", async () => setJob(await send<ResearchJob>(`${base}/research/${encodeURIComponent(job.id)}/cancel`, {})))}><Square size={14} fill="currentColor" /></button>}</div>}
    {job?.status === "completed" && job.volumes && JSON.stringify(rows) !== JSON.stringify(job.volumes) && <button className="text-button volume-review-result" disabled={blocked} onClick={() => { setRows(job.volumes || []); setSources(job.sources || []); setMessage("分卷预览已更新"); }}>载入检索结果</button>}
    {message && <div className="volume-feedback" role="status" aria-live="polite"><CheckCircle2 size={13} />{message}</div>}
    {error && <Notice error>{error}{!plan && <button className="text-button" disabled={blocked} onClick={() => setReload(value => value + 1)}>重试</button>}</Notice>}
    <div className="volume-table"><div className="volume-row volume-head"><span>卷名</span><span>起始章</span><span>结束章</span><span /></div>
      {rows.map((row, index) => <div className="volume-row" key={index}><input aria-label={`第${index + 1}卷名称`} value={row.title} disabled={blocked} onChange={(event) => update(index, { title: event.target.value })} /><VolumeBoundary label={`第${index + 1}卷起始章`} value={row.start_chapter} count={plan?.chapter_count || 0} directory={plan?.directory || []} disabled={blocked} onChange={start_chapter => update(index, { start_chapter })} /><VolumeBoundary label={`第${index + 1}卷结束章`} value={row.end_chapter} count={plan?.chapter_count || 0} directory={plan?.directory || []} disabled={blocked} onChange={end_chapter => update(index, { end_chapter })} /><button className="icon-button" aria-label={`删除第${index + 1}卷`} disabled={blocked} onClick={() => setRows(rows.filter((_, position) => position !== index))}><Trash2 size={14} /></button></div>)}
      {!rows.length && !busy && <div className="volume-empty">尚未设置分卷</div>}
    </div><button className="button secondary compact-button volume-add" disabled={blocked || !plan} onClick={() => setRows([...rows, { title: `第${rows.length + 1}卷`, start_chapter: (rows.at(-1)?.end_chapter ?? 0) + 1, end_chapter: plan?.chapter_count ?? 1 }])}><Plus size={14} />添加分卷</button>
    {!!sources.length && <details className="volume-sources"><summary>检索来源 · {sources.length}</summary>{sources.map((source, index) => <a key={source.id || index} href={/^https?:\/\//i.test(source.url) ? source.url : undefined} target="_blank" rel="noreferrer"><strong>{source.title || source.url}</strong><span>{source.excerpt || source.url}</span></a>)}</details>}
    {showPaste && <Modal title="粘贴分卷回复" busy={!!busy} onClose={() => setShowPaste(false)}><div className="modal-body"><textarea aria-label="分卷 JSON" className="volume-paste" value={paste} onChange={(event) => setPaste(event.target.value)} placeholder="粘贴网页模型的 JSON 回复" /><Notice error>{error}</Notice>{busy && <Busy label={busy} inline delay={0} />}</div><div className="modal-footer"><button className="button secondary" disabled={!!busy} onClick={() => setShowPaste(false)}>取消</button><button className="button primary" disabled={!!busy || !paste.trim()} onClick={() => run("正在校验分卷回复", async () => { const result = await send<{ volumes: Volume[]; sources?: VolumeSource[] }>(`${base}/paste`, { content: paste, contract_version: 2 }); setRows(result.volumes); setSources(result.sources || []); setShowPaste(false); setPaste(""); setMessage("分卷预览已更新"); })}>预览分卷</button></div></Modal>}
    {prompt && <Modal title="分卷提示词" onClose={() => setPrompt("")}><div className="modal-body"><textarea className="volume-paste" aria-label="分卷提示词" readOnly value={prompt} onFocus={(event) => event.target.select()} /></div></Modal>}
    {confirm && selected && <Modal title="联网查找分卷" busy={!!busy} onClose={() => setConfirm(false)}><div className="modal-body volume-confirm"><dl><div><dt>模型</dt><dd>{selected.model}</dd></div><div><dt>模型地址</dt><dd>{modelEndpoint(selected.base_url)}</dd></div><div><dt>搜索服务</dt><dd>{settings?.provider} · {modelEndpoint(settings?.endpoint)}</dd></div></dl><Notice error>{error}</Notice></div><div className="modal-footer"><button className="button secondary" disabled={!!busy} onClick={() => setConfirm(false)}>取消</button><button className="button primary" disabled={!!busy} onClick={modelDetect}>开始检索</button></div></Modal>}
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
