import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowRight, Check, ExternalLink, Plus, Save, Search, Send, Settings2, Sparkles, Square, Trash2, X } from "lucide-react";
import { request, type Item, type Page } from "./api";
import { normalizeList, send, studio, type List, type ModelProfile } from "./studio";
import { Busy, Empty, Modal, Notice, PageTitle, dateLabel, messageOf } from "./ui";
import "./research.css";

type Navigation = (id: "library" | "documents" | "reference" | "jobs" | "research" | "workflows" | "models" | "backup") => void;
type Source = { id: string; url: string; title: string; excerpt: string; fetch_status: string; error?: string };
type Heat = { min: number; max: number; source_ids: string[]; rationale: string };
type CandidateCharacter = { candidate_id: string; name: string; aliases: string[]; role: string; temperament: string; visual_design: string; source_ids: string[] };
type CandidateCliche = { candidate_id: string; content: string; character_trait: string; plot_mechanism: string; character_ids: string[]; source_ids: string[] };
type Draft = { work: { title: string; author: string; category: string; publication_date: string; synopsis: string; discussion_heat: Heat | null; market_scale: Heat | null; source_ids: string[] }; characters: CandidateCharacter[]; cliches: CandidateCliche[]; identity: { confirmed: boolean; alternatives: { title: string; author: string; source_ids: string[] }[]; notes: string }; warnings: string[] };
type Match = { candidate_id: string; recommendation: string; matches: { id: string; content: string; similarity: number }[]; candidate_matches: { candidate_id: string; similarity: number }[] };
type Decision = { action: "reuse" | "new"; id?: string; acknowledge_similar?: boolean };
type Decisions = { work_id?: string; allow_new_edition?: boolean; characters: Record<string, Decision>; cliches: Record<string, Decision> };
type Target = { profile_id?: string; model?: string; connection_mode?: string; provider?: string; endpoint?: string; fingerprint: string };
type Targets = { model: Target; embedding: Target; search: Target };
type CharacterMatch = { candidate_id: string; matches: { id: string; name: string; aliases: string[]; work_ids: string[] }[] };
type WorkMatch = { id: string; title: string; author: string; category: string; publication_date: string };
type Job = { work_matches?: WorkMatch[]; character_matches?: CharacterMatch[]; id: string; revision: number; title: string; author: string; target_work_id?: string; created_at: string; status: string; stage: string; progress: { completed: number; total: number; label: string }; messages: { role: string; content: string; created_at: string }[]; draft: Draft | null; sources: Source[]; semantic_matches: Match[]; catalog_revision: string; targets: Targets; error?: string; usage?: unknown; commit_result?: unknown };
type JobSummary = Pick<Job, "id" | "revision" | "title" | "author" | "created_at" | "status" | "stage">;
type SearchSettings = { provider: "tavily" | "brave" | "searxng"; endpoint: string; credential_mode: "session" | "vault" | "environment"; key_env: string; key_available: boolean; target_fingerprint: string };
const traits = ["强大", "智谋", "利他", "共情", "利己", "执念", "反差", "宿命"];
const mechanisms = ["信息反转", "机械降神", "顿悟突破", "代价交换"];
const endpoints = { tavily: "https://api.tavily.com/search", brave: "https://api.search.brave.com/res/v1/web/search", searxng: "http://127.0.0.1:8080/search" };
const statusNames: Record<string, string> = { queued: "等待开始", running: "联网整理中", paused: "已暂停", cancelled: "已取消", cancelling: "正在暂停", failed: "更新中断", needs_review: "待确认", approved: "已入库" };
const running = (job: Job | null) => !!job && ["queued", "running", "cancelling"].includes(job.status);
const itemValues = (item: Item) => (item as Item & { catalog_values?: Record<string, string> }).catalog_values || item.values;
const preferences = () => { try { return JSON.parse(localStorage.getItem("inkreference.research-models") || "{}"); } catch { return {}; } };

function defaults(job: Job): Decisions {
  const workId = job.target_work_id || (job.work_matches?.length === 1 ? job.work_matches[0].id : "");
  const result: Decisions = { ...(workId ? { work_id: workId } : {}), characters: {}, cliches: {} };
  for (const character of job.draft?.characters || []) {
    const matches = job.character_matches?.find(item => item.candidate_id === character.candidate_id)?.matches || [];
    result.characters[character.candidate_id] = matches.length === 1 ? { action: "reuse", id: matches[0].id } : { action: "new" };
  }
  for (const cliche of job.draft?.cliches || []) {
    const match = job.semantic_matches.find(item => item.candidate_id === cliche.candidate_id);
    result.cliches[cliche.candidate_id] = match?.recommendation === "reuse" && match.matches[0] ? { action: "reuse", id: match.matches[0].id } : { action: "new" };
  }
  return result;
}

export default function Research({ navigate }: { navigate: Navigation }) {
  const [jobs, setJobs] = useState<JobSummary[]>([]), [job, setJob] = useState<Job | null>(null);
  const [models, setModels] = useState<ModelProfile[]>([]), [settings, setSettings] = useState<SearchSettings | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null), [decisions, setDecisions] = useState<Decisions>({ characters: {}, cliches: {} });
  const [characters, setCharacters] = useState<Item[]>([]), [workOptions, setWorkOptions] = useState<Item[]>([]);
  const [title, setTitle] = useState(""), [author, setAuthor] = useState(""), [requestText, setRequestText] = useState("");
  const [targetWork, setTargetWork] = useState(""), [modelId, setModelId] = useState<string>(() => preferences().modelId || ""), [embeddingId, setEmbeddingId] = useState<string>(() => preferences().embeddingId || ""), [embeddingModel, setEmbeddingModel] = useState<string>(() => preferences().embeddingModel || "");
  const [targets, setTargets] = useState<Targets | null>(null), [configOpen, setConfigOpen] = useState(false), [confirmOpen, setConfirmOpen] = useState(false);
  const [busy, setBusy] = useState(""), [error, setError] = useState(""), [message, setMessage] = useState("");
  const [dirty, setDirty] = useState(false), [pending, setPending] = useState<(() => void) | null>(null);
  const [panel, setPanel] = useState("chat"), [tab, setTab] = useState("work"), [chat, setChat] = useState("");
  const jobRef = useRef(job), dirtyRef = useRef(dirty), loadTicket = useRef(0), chatEnd = useRef<HTMLDivElement>(null);
  jobRef.current = job; dirtyRef.current = dirty;
  const isRunning = running(job), immutable = job?.status === "approved";
  const applyJob = useCallback((next: Job, keepDecisions = false) => {
    setJob(next); setDraft(next.draft ? structuredClone(next.draft) : null); setDirty(false);
    if (!keepDecisions) setDecisions(defaults(next));
    setJobs(previous => [next, ...previous.filter(item => item.id !== next.id)]);
  }, []);
  const guard = (action: () => void) => dirtyRef.current ? setPending(() => action) : action();
  const select = useCallback(async (id: string) => {
    const ticket = ++loadTicket.current; setBusy("load"); setError(""); setMessage("");
    try { const next = await studio<Job>(`/research/jobs/${id}`); if (loadTicket.current === ticket) { applyJob(next); setPanel(next.draft ? "draft" : "chat"); } }
    catch (e) { if (loadTicket.current === ticket) setError(messageOf(e)); }
    finally { if (loadTicket.current === ticket) setBusy(""); }
  }, [applyJob]);
  const load = useCallback(async () => {
    const [history, profiles, searchSettings, works, roles] = await Promise.all([
      studio<List<JobSummary>>("/research/jobs"), studio<List<ModelProfile>>("/models"), studio<SearchSettings>("/research/settings"),
      request<Page>("/records/works?limit=200"), request<Page>("/records/characters?limit=200"),
    ]);
    const ps = normalizeList(profiles); setJobs(normalizeList(history)); setModels(ps); setSettings(searchSettings); setWorkOptions(works.items); setCharacters(roles.items);
    setModelId(value => value || ps[0]?.id || ""); setEmbeddingId(value => value || ps[0]?.id || "");
  }, []);
  useEffect(() => { setBusy("init"); void load().catch(e => setError(messageOf(e))).finally(() => setBusy("")); const refresh = () => { void load().catch(e => setError(messageOf(e))); }; window.addEventListener("inkreference:models-changed", refresh); return () => window.removeEventListener("inkreference:models-changed", refresh); }, [load]);
  useEffect(() => { try { localStorage.setItem("inkreference.research-models", JSON.stringify({ modelId, embeddingId, embeddingModel })); } catch { /* Optional local preferences. */ } }, [modelId, embeddingId, embeddingModel]);
  useEffect(() => {
    if (!modelId || !embeddingId) { setTargets(null); return; }
    const controller = new AbortController();
    const params = new URLSearchParams({ model_profile_id: modelId, embedding_profile_id: embeddingId, ...(embeddingModel ? { embedding_model: embeddingModel } : {}) });
    void studio<Targets>(`/research/targets?${params}`, { signal: controller.signal }).then(setTargets).catch(e => { if (!controller.signal.aborted) { setTargets(null); setError(messageOf(e)); } });
    return () => controller.abort();
  }, [modelId, embeddingId, embeddingModel, settings?.target_fingerprint]);
  useEffect(() => {
    const focus = (event?: Event) => {
      let target = (event as CustomEvent | undefined)?.detail;
      if (!target) { try { target = JSON.parse(sessionStorage.getItem("inkreference.catalog-focus") || "null"); } catch { return; } }
      if (target?.section !== "research" || dirtyRef.current) return;
      sessionStorage.removeItem("inkreference.catalog-focus");
      if (target.workId) {
        setBusy("work"); setMessage(""); void request<Item>(`/records/works/${encodeURIComponent(target.workId)}`).then(item => { const values = itemValues(item); setWorkOptions(previous => [item, ...previous.filter(value => value.id !== item.id)]); setTargetWork(item.id); setTitle(values["作品"] || ""); setAuthor(values["作者"] || ""); setJob(null); setDraft(null); setPanel("chat"); }).catch(e => setError(messageOf(e))).finally(() => setBusy(""));
      }
    };
    focus(); window.addEventListener("inkreference:catalog-focus", focus); return () => window.removeEventListener("inkreference:catalog-focus", focus);
  }, []);
  useEffect(() => {
    if (!isRunning || !job?.id) return;
    let live = true, controller: AbortController | undefined;
    const timer = window.setInterval(() => {
      if (controller) return; controller = new AbortController(); const id = job.id;
      void studio<Job>(`/research/jobs/${id}`, { signal: controller.signal }).then(next => {
        if (!live || jobRef.current?.id !== id) return;
        if (!dirtyRef.current) { applyJob(next); if (next.status === "needs_review") setPanel("draft"); }
      }).catch(e => { if (live && !controller?.signal.aborted) setError(messageOf(e)); }).finally(() => { controller = undefined; });
    }, 900);
    return () => { live = false; window.clearInterval(timer); controller?.abort(); };
  }, [isRunning, job?.id, applyJob]);
  useEffect(() => { chatEnd.current?.scrollIntoView({ block: "nearest" }); }, [job?.messages.length]);
  useEffect(() => { const unload = (e: BeforeUnloadEvent) => { if (dirtyRef.current) e.preventDefault(); }; window.addEventListener("beforeunload", unload); return () => window.removeEventListener("beforeunload", unload); }, []);
  const newResearch = () => guard(() => { ++loadTicket.current; setJob(null); setDraft(null); setTitle(""); setAuthor(""); setTargetWork(""); setRequestText(""); setDirty(false); setPanel("chat"); setError(""); setMessage(""); });
  const start = async () => {
    if (!targets) return; setBusy("start"); setError(""); setMessage("");
    try {
      const next = await send<Job>("/research/jobs", { title, author, message: requestText, ...(targetWork ? { target_work_id: targetWork } : {}), model_profile_id: modelId, embedding_profile_id: embeddingId, ...(embeddingModel ? { embedding_model: embeddingModel } : {}), remote_consent: true, expected_fingerprints: { model: targets.model.fingerprint, embedding: targets.embedding.fingerprint, search: targets.search.fingerprint } });
      applyJob(next); setConfirmOpen(false); setPanel("chat");
    } catch (e) { setError(messageOf(e)); } finally { setBusy(""); }
  };
  const save = async () => {
    if (!job || !draft) return; setBusy("save"); setError("");
    try { const next = await send<Job>(`/research/jobs/${job.id}/draft`, { draft, expected_revision: job.revision }, "PUT"); applyJob(next, true); setMessage("候选已保存"); }
    catch (e) { setError(messageOf(e)); } finally { setBusy(""); }
  };
  const action = async (kind: string) => {
    if (!job) return; setBusy(kind); setError("");
    try { applyJob(await send<Job>(`/research/jobs/${job.id}/${kind}`, {})); }
    catch (e) { setError(messageOf(e)); } finally { setBusy(""); }
  };
  const sendChat = async () => {
    if (!job || !chat.trim() || dirty) return; setBusy("chat"); setError("");
    try { applyJob(await send<Job>(`/research/jobs/${job.id}/chat`, { message: chat.trim(), expected_revision: job.revision })); setChat(""); setPanel("chat"); }
    catch (e) { setError(messageOf(e)); } finally { setBusy(""); }
  };
  const approve = async () => {
    if (!job || !draft) return; setBusy("approve"); setError("");
    try { applyJob(await send<Job>(`/research/jobs/${job.id}/approve`, { expected_revision: job.revision, expected_catalog_revision: job.catalog_revision, decisions })); setMessage("作品、角色与桥段已入库"); window.dispatchEvent(new Event("inkreference:library-refresh")); }
    catch (e) { setError(messageOf(e)); } finally { setBusy(""); }
  };
  const edit = (next: Draft) => { setDraft(next); setDirty(true); setMessage(""); };
  const removeCharacter = (id: string) => {
    if (!draft) return;
    edit({ ...draft, characters: draft.characters.filter(item => item.candidate_id !== id), cliches: draft.cliches.map(item => ({ ...item, character_ids: item.character_ids.filter(value => value !== id) })) });
    setDecisions(value => ({ ...value, characters: Object.fromEntries(Object.entries(value.characters).filter(([key]) => key !== id)) }));
  };
  const removeCliche = (id: string) => {
    if (!draft) return;
    edit({ ...draft, cliches: draft.cliches.filter(item => item.candidate_id !== id) });
    setDecisions(value => ({ ...value, cliches: Object.fromEntries(Object.entries(value.cliches).filter(([key]) => key !== id)) }));
  };
  const progress = job?.progress;
  return <main className="studio-main research-page">
    <PageTitle title="AI 更新" action={<div className="inline-actions"><button className="button secondary" onClick={() => setConfigOpen(true)}><Settings2 size={15} />配置</button><button className="button primary" onClick={newResearch}><Plus size={15} />新建</button></div>} />
    {(!!busy || isRunning) && <Busy label={progress?.label || "正在处理"} />}
    <Notice error={!!error} success={!error && !!message}>{error || message}</Notice>
    <div className="research-layout">
      <aside className="surface research-history"><div className="surface-heading"><h2>更新记录</h2><span>{jobs.length}</span></div><div>{jobs.map(item => <button className={`research-history-item ${job?.id === item.id ? "active" : ""}`} key={item.id} onClick={() => guard(() => void select(item.id))}><strong>{item.title}</strong><span>{item.author}</span><small>{statusNames[item.status] || item.status} · {dateLabel(item.created_at)}</small></button>)}</div></aside>
      <section className="surface research-workspace">
        <div className="research-mobile-history"><select aria-label="更新记录" value={job?.id || ""} onChange={e => e.target.value && guard(() => void select(e.target.value))}><option value="">新建更新</option>{jobs.map(item => <option key={item.id} value={item.id}>{item.title} · {statusNames[item.status]}</option>)}</select></div>
        {!job ? <form className="research-intake" onSubmit={e => { e.preventDefault(); setError(""); setConfirmOpen(true); }}><span className="research-intake-icon"><Sparkles size={26} /></span><h2>作品资料</h2><div className="research-two-fields"><label className="studio-field"><span>作品名</span><input autoComplete="off" value={title} onChange={e => setTitle(e.target.value)} required maxLength={500} /></label><label className="studio-field"><span>作者</span><input autoComplete="off" value={author} onChange={e => setAuthor(e.target.value)} required maxLength={500} /></label></div><label className="studio-field"><span>更新至</span><select value={targetWork} onChange={e => { const item = workOptions.find(item => item.id === e.target.value); setTargetWork(e.target.value); if (item) { const values = itemValues(item); setTitle(values["作品"] || ""); setAuthor(values["作者"] || ""); } }}><option value="">新建作品</option>{workOptions.map(item => <option key={item.id} value={item.id}>{itemValues(item)["作品"]} · {itemValues(item)["作者"]}</option>)}</select></label><label className="studio-field"><span>更新要求</span><textarea rows={4} value={requestText} maxLength={4000} onChange={e => setRequestText(e.target.value)} /></label><div className="research-intake-models"><span>{models.find(item => item.id === modelId)?.name || "未选择模型"}</span><button type="button" className="text-button" onClick={() => setConfigOpen(true)}>配置<Search size={13} /></button></div><button className="button primary" type="submit" disabled={!title.trim() || !author.trim() || !targets || !settings?.key_available || !!busy}><Search size={16} />联网检索</button>{(!models.length || !settings?.key_available) && <button className="button secondary" type="button" onClick={() => setConfigOpen(true)}>配置模型与搜索</button>}</form> : <>
          <header className="research-work-heading"><div><h2>{job.title}</h2><span>{job.author} · {statusNames[job.status]}</span></div><div className="inline-actions">{isRunning ? <button className="button secondary" disabled={!!busy} onClick={() => void action("cancel")}><Square size={13} />暂停</button> : ["paused", "failed", "cancelled"].includes(job.status) && <button className="button secondary" disabled={!!busy} onClick={() => void action("resume")}><ArrowRight size={14} />继续</button>}<button className="icon-button" aria-label="关闭当前更新" onClick={newResearch}><X size={16} /></button></div></header>
          {progress && <div className="research-progress"><span>{progress.label}</span><strong>{progress.completed} / {progress.total}</strong></div>}
          <div className="research-pane-tabs" role="tablist"><button role="tab" aria-selected={panel === "chat"} className={panel === "chat" ? "active" : ""} onClick={() => setPanel("chat")}>对话</button><button role="tab" aria-selected={panel === "draft"} disabled={!draft} className={panel === "draft" ? "active" : ""} onClick={() => setPanel("draft")}>候选{dirty && <i />}</button></div>
          <Notice error>{job.error}</Notice>
          <div className={`research-content research-show-${panel}`}>
            <section className="research-chat"><div className="research-messages">{job.messages.map((item, i) => <article className={`research-message ${item.role}`} key={`${i}:${item.created_at}`}><span>{item.role === "user" ? "你" : "AI"}</span><p>{item.content}</p></article>)}{isRunning && <div className="research-message-pending"><i /><i /><i /></div>}<div ref={chatEnd} /></div><div className="research-composer"><textarea aria-label="继续对话" placeholder="补充要求" rows={3} maxLength={4000} value={chat} disabled={isRunning || immutable} onChange={e => setChat(e.target.value)} onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void sendChat(); } }} /><button className="icon-button" aria-label="发送" title="发送" disabled={!chat.trim() || !!busy || isRunning || immutable || dirty} onClick={() => void sendChat()}><Send size={17} /></button></div></section>
            <section className="research-draft">{!draft ? <Empty icon={Search} title={isRunning ? "正在整理来源" : "暂无候选"} /> : <>
              <div className="research-draft-tabs" role="tablist">{[["work", "作品"], ["characters", `角色 ${draft.characters.length}`], ["cliches", `桥段 ${draft.cliches.length}`], ["sources", `来源 ${job.sources.length}`]].map(([id, label]) => <button role="tab" key={id} aria-selected={tab === id} className={tab === id ? "active" : ""} onClick={() => setTab(id)}>{label}</button>)}</div>
              <div className="research-draft-scroll"><fieldset disabled={isRunning || immutable || !!busy}>
                {tab === "work" && <><label className="studio-field"><span>入库作品</span><select aria-label="入库作品" value={decisions.work_id || ""} disabled={!!job.target_work_id} onChange={e => setDecisions(value => ({ ...value, work_id: e.target.value || undefined, allow_new_edition: false }))}><option value="">新建作品</option>{[...(job.work_matches || []), ...workOptions.map(item => ({ id: item.id, title: itemValues(item)["作品"], author: itemValues(item)["作者"] }))].filter((item, i, all) => all.findIndex(value => value.id === item.id) === i).map(item => <option key={item.id} value={item.id}>{item.title} · {item.author} · {item.id}</option>)}</select></label>{!decisions.work_id && !!job.work_matches?.length && <label className="check-line"><input type="checkbox" checked={!!decisions.allow_new_edition} onChange={e => setDecisions(value => ({ ...value, allow_new_edition: e.target.checked }))} />保存为独立版本</label>}<div className="research-two-fields"><Field label="作品名" value={draft.work.title} onChange={value => edit({ ...draft, work: { ...draft.work, title: value } })} /><Field label="作者" value={draft.work.author} onChange={value => edit({ ...draft, work: { ...draft.work, author: value } })} /><Field label="作品类型" value={draft.work.category} onChange={value => edit({ ...draft, work: { ...draft.work, category: value } })} /><Field label="发布日期" value={draft.work.publication_date} onChange={value => edit({ ...draft, work: { ...draft.work, publication_date: value } })} /></div><Field label="剧情梗概" value={draft.work.synopsis} area onChange={value => edit({ ...draft, work: { ...draft.work, synopsis: value } })} /><SourceLinks ids={draft.work.source_ids} sources={job.sources} /><HeatEditor label="讨论热度" value={draft.work.discussion_heat} sources={job.sources} onChange={value => edit({ ...draft, work: { ...draft.work, discussion_heat: value } })} /><HeatEditor label="商业规模" value={draft.work.market_scale} sources={job.sources} onChange={value => edit({ ...draft, work: { ...draft.work, market_scale: value } })} /><div className="research-identity"><label className="check-line"><input type="checkbox" checked={draft.identity.confirmed} onChange={e => edit({ ...draft, identity: { ...draft.identity, confirmed: e.target.checked } })} />作品身份确认</label>{draft.identity.notes && <p>{draft.identity.notes}</p>}{draft.identity.alternatives.map((alternative, i) => <div key={i}><strong>{alternative.title} · {alternative.author}</strong><SourceLinks ids={alternative.source_ids} sources={job.sources} /></div>)}</div></>}
                {tab === "characters" && draft.characters.map((character, index) => <article className="research-candidate" key={character.candidate_id}><header><h3>{character.name || `角色 ${index + 1}`}</h3><span>{character.role}</span><button className="icon-button danger-text" aria-label={`移除候选角色 ${character.name}`} onClick={() => removeCharacter(character.candidate_id)}><Trash2 size={14} /></button></header><div className="research-two-fields"><Field label="角色名" value={character.name} onChange={value => edit({ ...draft, characters: draft.characters.map((item, i) => i === index ? { ...item, name: value } : item) })} /><Field label="别名" value={character.aliases.join("、")} onChange={value => edit({ ...draft, characters: draft.characters.map((item, i) => i === index ? { ...item, aliases: value.split(/[、,，\n]/).map(v => v.trim()).filter(Boolean) } : item) })} /><Field label="角色定位" value={character.role} onChange={value => edit({ ...draft, characters: draft.characters.map((item, i) => i === index ? { ...item, role: value } : item) })} /><Choice label="性格基调" value={character.temperament} options={traits} onChange={value => edit({ ...draft, characters: draft.characters.map((item, i) => i === index ? { ...item, temperament: value } : item) })} /></div><Field label="视觉元素设计" value={character.visual_design} area onChange={value => edit({ ...draft, characters: draft.characters.map((item, i) => i === index ? { ...item, visual_design: value } : item) })} /><SourceLinks ids={character.source_ids} sources={job.sources} /><label className="studio-field"><span>入库人物</span><select value={decisions.characters[character.candidate_id]?.id || ""} onChange={e => setDecisions(value => ({ ...value, characters: { ...value.characters, [character.candidate_id]: e.target.value ? { action: "reuse", id: e.target.value } : { action: "new" } } }))}><option value="">新建人物</option>{(job.character_matches?.find(item => item.candidate_id === character.candidate_id)?.matches || []).filter(match => !characters.some(item => item.id === match.id)).map(match => <option key={match.id} value={match.id}>{match.name} · {match.id}</option>)}{characters.map(item => <option key={item.id} value={item.id}>{itemValues(item)["角色名"] || itemValues(item)["角色"]} · {item.id}</option>)}</select></label></article>)}
                {tab === "cliches" && draft.cliches.map((cliche, index) => <article className="research-candidate" key={cliche.candidate_id}><header><h3>桥段 {index + 1}</h3><button className="icon-button danger-text" aria-label={`移除候选桥段 ${index + 1}`} onClick={() => removeCliche(cliche.candidate_id)}><Trash2 size={14} /></button></header><Field label="桥段内容" value={cliche.content} area onChange={value => edit({ ...draft, cliches: draft.cliches.map((item, i) => i === index ? { ...item, content: value } : item) })} /><div className="research-two-fields"><Choice label="人设塑造" value={cliche.character_trait} options={traits} onChange={value => edit({ ...draft, cliches: draft.cliches.map((item, i) => i === index ? { ...item, character_trait: value } : item) })} /><Choice label="剧情机制" value={cliche.plot_mechanism} options={mechanisms} onChange={value => edit({ ...draft, cliches: draft.cliches.map((item, i) => i === index ? { ...item, plot_mechanism: value } : item) })} /></div><div className="research-character-links">{draft.characters.map(character => <label className="check-line" key={character.candidate_id}><input type="checkbox" checked={cliche.character_ids.includes(character.candidate_id)} onChange={e => edit({ ...draft, cliches: draft.cliches.map((item, i) => i === index ? { ...item, character_ids: e.target.checked ? [...item.character_ids, character.candidate_id] : item.character_ids.filter(id => id !== character.candidate_id) } : item) })} />{character.name}</label>)}</div><SourceLinks ids={cliche.source_ids} sources={job.sources} /><SemanticChoices value={decisions.cliches[cliche.candidate_id] || { action: "new" }} match={job.semantic_matches.find(item => item.candidate_id === cliche.candidate_id)} onChange={choice => setDecisions(value => ({ ...value, cliches: { ...value.cliches, [cliche.candidate_id]: choice } }))} /></article>)}
              </fieldset>{tab === "sources" && <div className="research-sources">{job.sources.map(source => <article key={source.id}><header><a href={source.url} target="_blank" rel="noopener noreferrer">{source.title || source.url}<ExternalLink size={12} /></a><span className={source.fetch_status === "verified" ? "read" : "failed"}>{source.fetch_status === "verified" ? "已读取" : "未读取"}</span></header><p>{source.excerpt || source.error}</p></article>)}</div>}{draft.warnings.length > 0 && <div className="research-warnings">{draft.warnings.map((warning, i) => <p key={i}>{warning}</p>)}</div>}</div>
              <footer className="research-review-footer"><span>{immutable ? "已入库" : dirty ? "有未保存的修改" : "候选已保存"}</span>{!immutable && <div className="inline-actions">{dirty && <button className="button secondary" disabled={!!busy || isRunning} onClick={() => void save()}><Save size={14} />保存</button>}<button className="button primary" disabled={!!busy || isRunning || dirty || job.status !== "needs_review" || !draft.identity.confirmed} onClick={() => void approve()}><Check size={15} />确认入库</button></div>}{immutable && <button className="button secondary" onClick={() => navigate("library")}>查看作品库<ArrowRight size={14} /></button>}</footer>
            </>}</section>
          </div>
        </>}
      </section>
    </div>
    {configOpen && <SearchConfiguration settings={settings} models={models} modelId={modelId} embeddingId={embeddingId} embeddingModel={embeddingModel} onModel={setModelId} onEmbedding={setEmbeddingId} onEmbeddingModel={setEmbeddingModel} onSave={value => { setSettings(value); setConfigOpen(false); setError(""); }} onClose={() => setConfigOpen(false)} navigate={navigate} />}
    {confirmOpen && targets && <Modal title="开始联网更新" onClose={() => setConfirmOpen(false)} busy={!!busy}><div className="modal-body"><h3>{title} · {author}</h3><TargetList targets={targets} /><Notice error>{error}</Notice></div><div className="modal-footer"><button className="button secondary" disabled={!!busy} onClick={() => setConfirmOpen(false)}>取消</button><button className="button primary" disabled={!!busy} onClick={() => void start()}><Search size={15} />确认并开始</button></div></Modal>}
    {pending && <Modal title="有未保存的修改" onClose={() => setPending(null)}><div className="modal-footer"><button className="button secondary" onClick={() => setPending(null)}>继续编辑</button><button className="button danger" onClick={() => { const action = pending; setPending(null); setDirty(false); dirtyRef.current = false; action(); }}>放弃并切换</button></div></Modal>}
  </main>;
}

function Field({ label, value, onChange, area = false }: { label: string; value: string; onChange: (value: string) => void; area?: boolean }) { return <label className="studio-field"><span>{label}</span>{area ? <textarea aria-label={label} rows={4} value={value} onChange={e => onChange(e.target.value)} /> : <input aria-label={label} value={value} onChange={e => onChange(e.target.value)} />}</label>; }
function Choice({ label, value, onChange, options }: { label: string; value: string; onChange: (value: string) => void; options: string[] }) { return <label className="studio-field"><span>{label}</span><select aria-label={label} value={value} onChange={e => onChange(e.target.value)}><option value="">未分类</option>{options.map(option => <option key={option}>{option}</option>)}</select></label>; }
function SourceLinks({ ids, sources }: { ids: string[]; sources: Source[] }) { return <div className="research-source-links">{ids.map(id => { const source = sources.find(item => item.id === id); return source && <a href={source.url} target="_blank" rel="noopener noreferrer" key={id} title={source.title}>{source.title || id}<ExternalLink size={10} /></a>; })}</div>; }
function HeatEditor({ label, value, sources, onChange }: { label: string; value: Heat | null; sources: Source[]; onChange: (value: Heat | null) => void }) { return <details className="research-heat"><summary>{label}<strong>{value ? `${value.min}–${value.max}` : "未评分"}</strong></summary><label className="check-line"><input type="checkbox" checked={!!value} onChange={e => onChange(e.target.checked ? { min: 1, max: 1, source_ids: [], rationale: "" } : null)} />评分</label>{value && <><div className="research-two-fields"><label className="studio-field"><span>最低</span><input type="number" min={1} max={10} value={value.min} onChange={e => onChange({ ...value, min: Number(e.target.value) })} /></label><label className="studio-field"><span>最高</span><input type="number" min={1} max={10} value={value.max} onChange={e => onChange({ ...value, max: Number(e.target.value) })} /></label></div><Field label="评分依据" value={value.rationale} area onChange={rationale => onChange({ ...value, rationale })} /><div className="research-heat-sources">{sources.filter(source => source.fetch_status === "verified").map(source => <label className="check-line" key={source.id}><input type="checkbox" checked={value.source_ids.includes(source.id)} onChange={e => onChange({ ...value, source_ids: e.target.checked ? [...value.source_ids, source.id] : value.source_ids.filter(id => id !== source.id) })} />{source.title}</label>)}</div><SourceLinks ids={value.source_ids} sources={sources} /></>}</details>; }
function SemanticChoices({ value, match, onChange }: { value: Decision; match?: Match; onChange: (value: Decision) => void }) { const similar = ["reuse", "review"].includes(match?.recommendation || "") || !!match?.candidate_matches.length; return <section className="research-semantic"><h4>语义匹配</h4>{match?.matches.map(candidate => <label className={`research-match ${value.id === candidate.id ? "selected" : ""}`} key={candidate.id}><input type="radio" name={`match-${match.candidate_id}`} checked={value.action === "reuse" && value.id === candidate.id} onChange={() => onChange({ action: "reuse", id: candidate.id })} /><span>{candidate.content}<small>{candidate.id}</small></span><b>{(candidate.similarity * 100).toFixed(1)}%</b></label>)}{match?.candidate_matches.map(candidate => <div className="research-candidate-match" key={candidate.candidate_id}>本次候选 {candidate.candidate_id}<span>{(candidate.similarity * 100).toFixed(1)}%</span></div>)}<label className="check-line"><input type="radio" name={`match-${match?.candidate_id || "new"}`} checked={value.action === "new"} onChange={() => onChange({ action: "new" })} />新增桥段</label>{value.action === "new" && similar && <label className="check-line"><input type="checkbox" checked={!!value.acknowledge_similar} onChange={e => onChange({ ...value, acknowledge_similar: e.target.checked })} />已核对相似桥段，保留为独立条目</label>}</section>; }
function TargetList({ targets }: { targets: Targets }) { return <dl className="research-targets">{([ ["model", "资料模型"], ["embedding", "向量模型"], ["search", "搜索服务"] ] as const).map(([key, label]) => <div key={key}><dt>{label}</dt><dd>{targets[key].model || targets[key].provider}<small>{targets[key].endpoint || targets[key].profile_id}</small></dd></div>)}</dl>; }
function SearchConfiguration({ settings, models, modelId, embeddingId, embeddingModel, onModel, onEmbedding, onEmbeddingModel, onSave, onClose, navigate }: { settings: SearchSettings | null; models: ModelProfile[]; modelId: string; embeddingId: string; embeddingModel: string; onModel: (value: string) => void; onEmbedding: (value: string) => void; onEmbeddingModel: (value: string) => void; onSave: (settings: SearchSettings) => void; onClose: () => void; navigate: Navigation }) {
  const [draft, setDraft] = useState(settings || { provider: "tavily" as const, endpoint: endpoints.tavily, credential_mode: "session" as const, key_env: "", key_available: false, target_fingerprint: "" }), [key, setKey] = useState("");
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const save = async () => { setBusy(true); setError(""); try { const next = await send<SearchSettings>("/research/settings", { provider: draft.provider, endpoint: draft.endpoint, credential_mode: draft.credential_mode, key_env: draft.key_env, ...(key ? { api_key: key } : {}) }, "PUT"); setKey(""); onSave(next); } catch (e) { setError(messageOf(e)); } finally { setBusy(false); } };
  return <Modal title="AI 更新配置" onClose={() => { setKey(""); onClose(); }} busy={busy}><div className="modal-body research-config"><label className="studio-field"><span>资料模型</span><select value={modelId} onChange={e => onModel(e.target.value)}><option value="">选择模型</option>{models.map(model => <option key={model.id} value={model.id}>{model.name} · {model.model}</option>)}</select></label><label className="studio-field"><span>向量模型连接</span><select value={embeddingId} onChange={e => onEmbedding(e.target.value)}><option value="">选择连接</option>{models.map(model => <option key={model.id} value={model.id}>{model.name}</option>)}</select></label><Field label="向量模型名称" value={embeddingModel} onChange={onEmbeddingModel} /><button className="text-button" onClick={() => { onClose(); navigate("models"); }}>模型设置<ArrowRight size={13} /></button><div className="research-config-divider" /><label className="studio-field"><span>搜索服务</span><select value={draft.provider} onChange={e => { const provider = e.target.value as SearchSettings["provider"]; setDraft({ ...draft, provider, endpoint: endpoints[provider] }); }}><option value="tavily">Tavily</option><option value="brave">Brave Search</option><option value="searxng">SearXNG</option></select></label><Field label="搜索地址" value={draft.endpoint} onChange={endpoint => setDraft({ ...draft, endpoint })} />{draft.provider !== "searxng" && <><label className="studio-field"><span>密钥保存</span><select value={draft.credential_mode} onChange={e => setDraft({ ...draft, credential_mode: e.target.value as SearchSettings["credential_mode"] })}><option value="session">当前会话</option><option value="vault">系统凭据</option><option value="environment">环境变量</option></select></label>{draft.credential_mode === "environment" ? <Field label="环境变量名" value={draft.key_env} onChange={key_env => setDraft({ ...draft, key_env })} /> : <label className="studio-field"><span>搜索 API Key</span><input type="password" autoComplete="new-password" value={key} placeholder={draft.key_available ? "已配置" : ""} onChange={e => setKey(e.target.value)} /></label>}</>}<Notice error>{error}</Notice>{busy && <Busy label="正在保存配置" />}</div><div className="modal-footer"><button className="button secondary" disabled={busy} onClick={onClose}>取消</button><button className="button primary" disabled={busy} onClick={() => void save()}>保存</button></div></Modal>;
}
