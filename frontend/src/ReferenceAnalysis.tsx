import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowRight, BookOpen, CheckCircle2, Download, FileSearch, History, Layers3, MessageSquare, Plus, RefreshCw, Search, Sparkles, Upload } from "lucide-react";
import { Busy, Empty, Modal, Notice, dateLabel, messageOf } from "./ui";
import { normalizeList, send, studio, type DocumentMeta, type List, type ModelProfile, type Run, type TextVersion } from "./studio";
import { download } from "./api";
import { isRemoteModel, modelEndpoint } from "./model-security";
import { CollectionEditor, FactsEditor, PlotEditor, PromptPanel, ReferenceField, RhythmEditor, StyleEditor } from "./ReferenceEditors";
import { ReferenceExtraction } from "./ReferenceExtraction";
import { record, records, sectionCount, stringValue, type ReferenceChunkRange, type ReferenceRecord, type ReferenceSection, type ReferenceState } from "./reference-types";
import "./reference-analysis.css";

type Navigation = (id: "library" | "documents" | "jobs" | "workflows" | "models" | "backup") => void;
type Props = { docs: DocumentMeta[]; runs: Run[]; loading: boolean; navigate: Navigation; onRun: (run: Run) => void; onRefresh: () => Promise<void>; onDirtyChange: (dirty: boolean) => void };
type Transform = { kind: "summarize" | "translate"; entryIndex?: number };
const narrativeTabs = [["extract", "特征提取"], ["plot", "剧情大纲"], ["characters", "角色"], ["settings", "设定"], ["features", "文本特征"]];
const settingTabs = [["raw_entries", "原始文本"], ["settings", "设定"], ["characters", "角色"], ["extract", "特征提取"], ["setting_features", "设定特征"]];

export default function ReferenceAnalysis({ docs, runs, loading, navigate, onRun, onRefresh, onDirtyChange }: Props) {
  const [documentId, setDocumentId] = useState("");
  const [state, setState] = useState<ReferenceState | null>(null);
  const [tab, setTab] = useState("extract");
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [featureView, setFeatureView] = useState("book");
  const [dirty, setDirty] = useState(false);
  const [pending, setPending] = useState<{ documentId?: string; tab?: string } | null>(null);
  const [transform, setTransform] = useState<Transform | null>(null);
  const [structureDialog, setStructureDialog] = useState(false);
  const [importDialog, setImportDialog] = useState(false);
  const [importMode, setImportMode] = useState("merge");
  const [importFile, setImportFile] = useState<File | null>(null);
  const [createCollection, setCreateCollection] = useState(false);
  const [collectionTitle, setCollectionTitle] = useState("");
  const [metadataOpen, setMetadataOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [history, setHistory] = useState<{ revision: number; updated_at: string }[]>([]);
  const [chunkRanges, setChunkRanges] = useState<ReferenceChunkRange[]>([]);
  const requestId = useRef(0);
  const attemptedInitial = useRef(new Set<string>());
  const dirtyRef = useRef(false);
  const selectedRef = useRef(documentId);
  const stateRef = useRef(state);
  dirtyRef.current = dirty; selectedRef.current = documentId; stateRef.current = state;
  const selected = docs.find(d => d.id === documentId);
  const collection = state?.structure_type === "setting_collection";
  const tabs = collection ? settingTabs : narrativeTabs;
  const docRuns = runs.filter(r => r.document_id === documentId);
  const select = useCallback(async (id: string, discard = false) => {
    if (dirtyRef.current && !discard) { setPending({ documentId: id }); return; }
    const ticket = ++requestId.current; setBusy("load"); setError(""); setMessage("");
    try { const next = await studio<ReferenceState>(`/documents/${id}/reference`); if (ticket !== requestId.current) return; setDocumentId(id); setState(next); setDirty(false); setTab(next.structure_type === "setting_collection" ? "raw_entries" : "extract"); }
    catch (e) { if (ticket === requestId.current) setError(messageOf(e)); }
    finally { if (ticket === requestId.current) setBusy(""); }
  }, []);
  useEffect(() => { if (!documentId && docs.length && !attemptedInitial.current.has(docs[0].id)) { attemptedInitial.current.add(docs[0].id); void select(docs[0].id); } }, [docs, documentId, select]);
  const versionIds = [...new Set(docRuns.map(run => run.version_id))].join(":");
  useEffect(() => {
    if (!documentId) return; let active = true; setChunkRanges([]);
    void Promise.all([...new Set(docRuns.map(run => run.version_id))].map(async id => {
      const [v, plan] = await Promise.all([studio<TextVersion>(`/documents/${documentId}/versions/${id}?include_text=false`), studio<{ volumes: ReferenceRecord[] }>(`/documents/${documentId}/versions/${id}/volumes`)]);
      return { id, version: v, volumes: plan.volumes };
    })).then(versions => {
      if (!active) return; const ranges: ReferenceChunkRange[] = [];
      for (const run of docRuns) {
        const v = versions.find(v => v.id === run.version_id); if (!v) continue;
        run.candidates.forEach((candidate, i) => {
          const c = candidate as typeof candidate & { start?: number; end?: number };
          const hits = v.version.chapters.map((ch, index) => ({ ...ch, index: index + 1 })).filter(ch => Number(ch.end ?? ch.end_offset ?? 0) > Number(c.start || 0) && Number(ch.start ?? ch.start_offset ?? 0) < Number(c.end || 0));
          if (!hits.length) return;
          const first = hits[0].index, last = hits.at(-1)!.index;
          if (ranges.some(r => r.startChapter === first && r.endChapter === last)) return;
          const volume = v.volumes.find(vol => first >= Number(vol.start_chapter) && first <= Number(vol.end_chapter));
          ranges.push({ key: `${run.id}:${c.chunk_id}`, title: `${stringValue(volume?.title) || "全书"} · 第 ${i + 1} 段（第 ${first}–${last} 章）`, startChapter: first, endChapter: last });
        });
      }
      setChunkRanges(ranges);
    }).catch(e => { if (active) setError(messageOf(e)); });
    return () => { active = false; };
  // Chunk boundaries and source versions are immutable after a task is created.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [documentId, versionIds]);
  useEffect(() => { const before = (e: BeforeUnloadEvent) => { if (dirtyRef.current) e.preventDefault(); }; window.addEventListener("beforeunload", before); return () => window.removeEventListener("beforeunload", before); }, []);
  useEffect(() => { onDirtyChange(dirty); return () => onDirtyChange(false); }, [dirty, onDirtyChange]);
  const reload = useCallback(async () => {
    if (!selectedRef.current) return;
    const id = selectedRef.current;
    const next = await studio<ReferenceState>(`/documents/${id}/reference`);
    if (selectedRef.current === id) setState(next);
  }, []);
  const save = useCallback(async (section: ReferenceSection, data: unknown) => {
    const current = stateRef.current; if (!current) return;
    setBusy("save"); setError("");
    try { const next = await send<ReferenceState>(`/documents/${current.document_id}/reference/${section}`, { data, expected_revision: current.revision }, "PUT"); if (selectedRef.current === next.document_id) { setState(next); stateRef.current = next; setMessage("修改已保存"); setDirty(false); } }
    catch (e) { setError(messageOf(e)); throw e; }
    finally { setBusy(""); }
  }, []);
  const changeTab = (next: string) => { if (dirty) { setPending({ tab: next }); return; } setTab(next); setError(""); setMessage(""); };
  const configure = async () => {
    if (!state) return;
    setBusy("structure"); setError("");
    try { const next = await send<ReferenceState>(`/documents/${documentId}/reference`, { structure_type: collection ? "narrative" : "setting_collection", expected_revision: state.revision }, "PUT"); setState(next); setTab(collection ? "extract" : "raw_entries"); setStructureDialog(false); }
    catch (e) { setError(messageOf(e)); }
    finally { setBusy(""); }
  };
  const exportEntries = async () => {
    setBusy("export"); setError("");
    try { await download(`/studio/documents/${documentId}/reference/export`, `${selected?.title || "设定集"}.txt`); }
    catch (e) { setError(messageOf(e)); }
    finally { setBusy(""); }
  };
  const importEntries = async () => {
    if (!importFile) return; setError(""); setBusy("import");
    try { const content = new TextDecoder("utf-8", { fatal: true }).decode(await importFile.arrayBuffer()).replace(/^\uFEFF/, ""); if (!content.trim()) throw new Error("文件内容为空"); const next = await send<ReferenceState>(`/documents/${documentId}/reference/import`, { text: content, mode: importMode, title: importFile.name.replace(/\.txt$/i, ""), expected_revision: stateRef.current?.revision }); setState(next); stateRef.current = next; setImportDialog(false); setImportFile(null); }
    catch (e) { setError(e instanceof TypeError ? "文件不是 UTF-8 编码，请转为 UTF-8 后导入。" : messageOf(e)); }
    finally { setBusy(""); }
  };
  const newCollection = async () => { setBusy("create"); setError(""); try { const document = await send<DocumentMeta>("/reference-collections", { title: collectionTitle }); setCreateCollection(false); setCollectionTitle(""); await onRefresh(); await select(document.id, true); } catch (e) { setError(messageOf(e)); } finally { setBusy(""); } };
  const showHistory = async () => { setHistoryOpen(true); setBusy("history"); setError(""); try { const response = await studio<{ items: { revision: number; updated_at: string }[] }>(`/documents/${documentId}/reference/history`); setHistory(response.items); } catch (e) { setError(messageOf(e)); } finally { setBusy(""); } };
  const restore = async (revision: number) => { setBusy("restore"); setError(""); try { const next = await send<ReferenceState>(`/documents/${documentId}/reference/restore`, { target_revision: revision, expected_revision: stateRef.current?.revision }); setState(next); stateRef.current = next; setTab(next.structure_type === "setting_collection" ? "raw_entries" : "extract"); setHistoryOpen(false); setMessage("历史内容已恢复为新版本"); } catch (e) { setError(messageOf(e)); } finally { setBusy(""); } };
  const sectionDirty = useCallback((value: boolean) => setDirty(value), []);
  const styleChunks = docRuns.flatMap(run => run.candidates.filter(c => state?.commits.some(k => k.run_id === run.id && k.chunk_id === c.chunk_id && k.section === "style")).map(c => ({ run, chunk: c, style: record(record(c.result).reference).style })));
  return <div className="reference-layout">
    <aside className="surface reference-index"><div className="surface-heading"><h2>参考作品</h2><span>{docs.length}</span><button className="icon-button" aria-label="刷新参考作品" onClick={() => void onRefresh().catch(e => setError(messageOf(e)))}><RefreshCw size={15} /></button></div><label className="index-search"><Search size={15} /><input aria-label="搜索参考作品" placeholder="搜索作品" value={query} onChange={e => setQuery(e.target.value)} /></label>
      {loading && !docs.length ? <Busy label="正在读取作品" /> : docs.filter(d => `${d.title} ${d.filename}`.toLocaleLowerCase().includes(query.toLocaleLowerCase())).map(d => <button className={`reference-work-item ${documentId === d.id ? "active" : ""}`} key={d.id} onClick={() => void select(d.id)}><span className="reference-work-icon"><BookOpen size={19} /></span><span><strong>{d.title}</strong><small>{Number(d.current_characters ?? d.source_characters ?? 0).toLocaleString()} 字 · {d.format.toUpperCase()}</small></span>{documentId === d.id && <span className="reference-active-dot" />}</button>)}
      <div className="reference-add"><button className="button secondary" onClick={() => navigate("documents")}><Upload size={15} />导入作品</button><button className="text-button" onClick={() => setCreateCollection(true)}><Plus size={15} />新建设定集</button></div>
    </aside>
    <section className="surface reference-workspace" aria-busy={!!busy}>
      <div className={`reference-operation ${busy ? "visible" : ""}`} aria-hidden={!busy}>{busy && <Busy label={{ load: "正在读取作品分析", save: "正在保存修改", structure: "正在切换作品类型", import: "正在导入文本" }[busy] || "正在处理"} compact />}</div>
      {!state && <Notice error>{error}</Notice>}
      {!state || !selected ? <Empty icon={FileSearch} title="建立你的参考作品库" description="导入作品后，按章节整理与审核创作素材。" action={<button className="button primary" onClick={() => navigate("documents")}>导入作品<ArrowRight size={15} /></button>} /> : <>
        <header className="reference-work-heading"><div><span className="eyebrow">REFERENCE LIBRARY</span><h2>{selected.title}</h2><div className="inline-meta"><span>{collection ? "设定集" : "叙事作品"}</span><span>{Number(selected.current_characters ?? selected.source_characters ?? 0).toLocaleString()} 字</span><span>{dateLabel(selected.created_at)}</span>{state.commits.length > 0 && <span>分析来源 {new Set(state.commits.map(c => c.source_version_id).filter(Boolean)).size || 1} 个正文版本</span>}</div></div><div className="inline-actions"><button className="icon-button" aria-label="作品笔记" disabled={!!busy || dirty} onClick={() => setMetadataOpen(true)}><MessageSquare size={17} /></button><button className="icon-button" aria-label="分析历史版本" disabled={!!busy || dirty} onClick={() => void showHistory()}><History size={17} /></button><button className="button secondary" disabled={!!busy || dirty} onClick={() => setStructureDialog(true)}><Layers3 size={15} />{collection ? "设定集" : "叙事作品"}</button></div></header>
        <div className="reference-tabs" role="tablist" aria-label="参考数据库功能">{tabs.map(([key, label]) => <button role="tab" aria-selected={tab === key} className={tab === key ? "active" : ""} key={key} onClick={() => changeTab(key)}>{label}{["plot", "characters", "settings", "setting_features", "raw_entries"].includes(key) && <span>{sectionCount(state, key as ReferenceSection)}</span>}</button>)}</div>
        <div className="reference-notices"><Notice error={!!error} success={!error && !!message}>{error || message}</Notice></div>
        <div className="reference-tab-body" key={`${documentId}:${tab}`}>
          {tab === "extract" && <ReferenceExtraction document={selected} state={state} runs={docRuns} navigate={navigate} onRun={onRun} onState={next => { setState(next); stateRef.current = next; }} onRefresh={onRefresh} onDirty={sectionDirty} />}
          {tab === "plot" && <PlotEditor value={state.sections.plot} chunkRanges={chunkRanges} onSave={data => save("plot", data)} busy={!!busy} onDirty={sectionDirty} summaryAction={<button className="button secondary" disabled={!!busy || dirty} onClick={() => setTransform({ kind: "summarize" })}><Sparkles size={14} />调整大纲粒度</button>} />}
          {(["characters", "settings", "setting_features"] as string[]).includes(tab) && <CollectionEditor kind={tab as "characters" | "settings" | "setting_features"} value={state.sections[tab as ReferenceSection]} onSave={items => save(tab as ReferenceSection, items)} busy={!!busy} onDirty={sectionDirty} settingCollection={collection} chunkRanges={chunkRanges} />}
          {tab === "raw_entries" && <><div className="ref-raw-tools"><button className="button secondary" disabled={!!busy || dirty} onClick={() => setImportDialog(true)}><Upload size={14} />导入设定集 TXT</button><button className="button secondary" disabled={!!busy} onClick={() => void exportEntries()}><Download size={14} />导出设定集 TXT</button></div><CollectionEditor kind="raw_entries" value={state.sections.raw_entries} onSave={items => save("raw_entries", items)} busy={!!busy} onDirty={sectionDirty} settingCollection onExtra={(_, index) => <div className="ref-raw-entry-actions"><button className="text-button" disabled={!!busy || dirty} onClick={() => setTransform({ kind: "translate", entryIndex: index })}><Sparkles size={14} />翻译与预览</button></div>} /></>}
          {tab === "features" && <><div className="ref-collection-tools"><h3>文本特征</h3><div className="ref-segment-control"><button className={featureView === "book" ? "active" : ""} onClick={() => { if (!dirty) setFeatureView("book"); }}>全书视图</button><button className={featureView === "segment" ? "active" : ""} onClick={() => { if (!dirty) setFeatureView("segment"); }}>分段视图</button></div></div>{featureView === "book" ? <><StyleEditor value={state.sections.style} onSave={data => save("style", data)} busy={!!busy} onDirty={sectionDirty} /><RhythmEditor value={state.sections.rhythm} opening={state.sections.opening} onSave={data => save("rhythm", data)} onSaveOpening={data => save("opening", data)} busy={!!busy} onDirty={sectionDirty} /></> : <div className="ref-chunk-style-list">{styleChunks.map(({ run, chunk, style }) => <details key={`${run.id}:${chunk.chunk_id}`}><summary><span><FileSearch size={15} />{chunk.chunk_id}</span><span>{dateLabel(run.created_at)}</span><CheckCircle2 size={15} /></summary><StyleEditor value={style} /></details>)}{!styleChunks.length && <div className="ref-empty">还没有确认入库的分段风格</div>}</div>}</>}
        </div>
      </>}
    </section>
    {createCollection && <Modal title="新建设定集" onClose={() => setCreateCollection(false)} busy={!!busy}><div className="modal-body"><label className="studio-field"><span>设定集名称</span><input aria-label="设定集名称" value={collectionTitle} onChange={e => setCollectionTitle(e.target.value)} autoFocus /></label><Notice error>{error}</Notice>{busy && <Busy label="正在创建设定集" compact />}</div><div className="modal-footer"><button className="button secondary" disabled={!!busy} onClick={() => setCreateCollection(false)}>取消</button><button className="button primary" disabled={!!busy || !collectionTitle.trim()} onClick={() => void newCollection()}>创建</button></div></Modal>}
    {metadataOpen && state && <MetadataDialog value={state.sections.metadata} busy={!!busy} onClose={() => setMetadataOpen(false)} onSave={async data => { await save("metadata", data); setMetadataOpen(false); }} />}
    {historyOpen && <Modal title="分析历史版本" onClose={() => setHistoryOpen(false)} busy={!!busy}><div className="modal-body"><Notice error>{error}</Notice>{busy && <Busy label={busy === "restore" ? "正在恢复分析版本" : "正在读取历史版本"} compact />}<div className="ref-history-list">{history.map(h => <div key={h.revision}><span><strong>版本 {h.revision}</strong><small>{dateLabel(h.updated_at)}</small></span><button className="button secondary" disabled={!!busy || h.revision === state?.revision} onClick={() => void restore(h.revision)}>{h.revision === state?.revision ? "当前版本" : "恢复为新版本"}</button></div>)}</div></div><div className="modal-footer"><button className="button secondary" disabled={!!busy} onClick={() => setHistoryOpen(false)}>关闭</button></div></Modal>}
    {pending && <Modal title="有未保存的修改" onClose={() => setPending(null)}><div className="modal-body"><p>当前编辑尚未保存。</p></div><div className="modal-footer"><button className="button secondary" onClick={() => setPending(null)}>继续编辑</button><button className="button danger" onClick={() => { const next = pending; setPending(null); setDirty(false); dirtyRef.current = false; if (next.documentId) void select(next.documentId, true); else if (next.tab) setTab(next.tab); }}>放弃并切换</button></div></Modal>}
    {structureDialog && <Modal title="切换作品结构" onClose={() => setStructureDialog(false)} busy={!!busy}><div className="modal-body"><p>{collection ? "切换为叙事作品，使用剧情大纲、角色、设定和文本特征。" : "切换为设定集，使用原始条目、角色、设定和设定特征。"}</p><Notice error>{error}</Notice>{busy && <Busy label="正在切换作品结构" compact />}</div><div className="modal-footer"><button className="button secondary" disabled={!!busy} onClick={() => setStructureDialog(false)}>取消</button><button className="button primary" disabled={!!busy} onClick={() => void configure()}>切换为{collection ? "叙事作品" : "设定集"}</button></div></Modal>}
    {importDialog && <Modal title="导入原始文本" onClose={() => setImportDialog(false)} busy={!!busy}><div className="modal-body"><label className="studio-field"><span>UTF-8 文本文件</span><input type="file" accept=".txt,text/plain" onChange={e => setImportFile(e.target.files?.[0] || null)} /></label><label className="studio-field"><span>导入方式</span><select value={importMode} onChange={e => setImportMode(e.target.value)}><option value="merge">合并导入</option><option value="replace">替换现有内容</option></select></label><Notice error>{error}</Notice>{busy && <Busy label="正在读取并保存文本" compact />}</div><div className="modal-footer"><button className="button secondary" onClick={() => setImportDialog(false)} disabled={!!busy}>取消</button><button className="button primary" onClick={() => void importEntries()} disabled={!!busy || !importFile}>导入</button></div></Modal>}
    {transform && state && <TransformDialog transform={transform} state={state} navigate={navigate} onClose={() => setTransform(null)} onApply={async data => { if (transform.kind === "summarize") await save("plot", data); else { const entries = records(state.sections.raw_entries); entries[transform.entryIndex || 0] = { ...entries[transform.entryIndex || 0], content: stringValue(data.text) }; await save("raw_entries", entries); } setTransform(null); await reload(); }} />}
  </div>;
}

function MetadataDialog({ value, busy, onClose, onSave }: { value: unknown; busy: boolean; onClose: () => void; onSave: (data: ReferenceRecord) => Promise<void> }) {
  const [draft, setDraft] = useState<ReferenceRecord>(() => structuredClone(record(value)));
  const [error, setError] = useState("");
  return <Modal title="作品笔记" onClose={onClose} busy={busy} wide><div className="modal-body"><ReferenceField field={{ key: "user_rating", label: "个人评分", options: [["", "尚未评分"], ["1", "★"], ["2", "★★"], ["3", "★★★"], ["4", "★★★★"], ["5", "★★★★★"]] }} value={draft.user_rating} onChange={user_rating => setDraft(d => ({ ...d, user_rating }))} /><ReferenceField field={{ key: "user_why_i_like", label: "喜欢这部作品的原因", area: true }} value={draft.user_why_i_like} onChange={user_why_i_like => setDraft(d => ({ ...d, user_why_i_like }))} /><FactsEditor label="章节笔记" value={draft.chapter_comments} onChange={chapter_comments => setDraft(d => ({ ...d, chapter_comments }))} /><Notice error>{error}</Notice>{busy && <Busy label="正在保存作品笔记" compact />}</div><div className="modal-footer"><button className="button secondary" onClick={onClose} disabled={busy}>取消</button><button className="button primary" disabled={busy} onClick={async () => { try { await onSave(draft); } catch (e) { setError(messageOf(e)); } }}>保存笔记</button></div></Modal>;
}

function TransformDialog({ transform, state, navigate, onClose, onApply }: { transform: Transform; state: ReferenceState; navigate: Navigation; onClose: () => void; onApply: (data: ReferenceRecord) => Promise<void> }) {
  const [mode, setMode] = useState("web");
  const [profiles, setProfiles] = useState<ModelProfile[]>([]);
  const [profileId, setProfileId] = useState("");
  const [granularity, setGranularity] = useState("major_event");
  const [language, setLanguage] = useState("简体中文");
  const [prompt, setPrompt] = useState("");
  const [paste, setPaste] = useState("");
  const [preview, setPreview] = useState<ReferenceRecord | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const profile = profiles.find(p => p.id === profileId);
  const remote = isRemoteModel(profile);
  const title = transform.kind === "summarize" ? "调整大纲粒度" : "翻译原始文本";
  useEffect(() => { void studio<List<ModelProfile>>("/models").then(r => { const list = normalizeList(r); setProfiles(list); setProfileId(list[0]?.id || ""); }).catch(e => setError(messageOf(e))); }, []);
  const payload = { granularity, target_language: language, entry_index: transform.entryIndex };
  const prepare = async (api = false) => { setBusy(api ? "run" : "prompt"); setError(""); setPreview(null); try { const response = await send<{ prompt: string; data?: ReferenceRecord; text?: string }>(`/documents/${state.document_id}/reference/transform`, { kind: transform.kind, profile_id: api ? profileId : undefined, payload: { ...payload, prompt_only: !api }, remote_consent: remote && api, expected_model_fingerprint: api ? profile?.target_fingerprint : undefined }); setPrompt(response.prompt); if (api) setPreview(transform.kind === "summarize" ? response.data || {} : { text: response.text || "" }); } catch (e) { setError(messageOf(e)); } finally { setBusy(""); } };
  const parse = () => { setError(""); try { if (!paste.trim()) throw new Error("请先粘贴结果"); if (transform.kind === "translate") { setPreview({ text: paste }); return; } const content = paste.trim().replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/, ""); const parsed = JSON.parse(content); if (!parsed || typeof parsed !== "object" || !Array.isArray(parsed.epochs)) throw new Error("结果需包含 epochs 剧情大纲数组"); setPreview(parsed); } catch (e) { setError(messageOf(e)); } };
  return <Modal title={title} onClose={onClose} busy={!!busy} wide><div className="modal-body ref-transform"><div className="ref-segment-control"><button className={mode === "web" ? "active" : ""} onClick={() => setMode("web")} disabled={!!busy}>复制提示词与粘贴</button><button className={mode === "api" ? "active" : ""} onClick={() => setMode("api")} disabled={!!busy}>调用本地模型或 API</button></div>{transform.kind === "summarize" ? <label className="studio-field"><span>大纲粒度</span><select value={granularity} onChange={e => { setGranularity(e.target.value); setPreview(null); setPrompt(""); }}><option value="chapter">按章节</option><option value="major_event">按重要事件</option><option value="volume">按卷</option><option value="book">全书概括</option></select></label> : <label className="studio-field"><span>翻译为</span><input value={language} onChange={e => { setLanguage(e.target.value); setPrompt(""); setPreview(null); }} /></label>}
    {mode === "web" ? <><button className="button secondary" onClick={() => void prepare()} disabled={!!busy}>生成提示词</button>{(prompt || busy === "prompt") && <PromptPanel prompt={prompt} busy={busy === "prompt"} />}<label className="studio-field"><span>{transform.kind === "summarize" ? "粘贴返回的 JSON" : "粘贴译文"}</span><textarea aria-label={transform.kind === "summarize" ? "粘贴大纲 JSON" : "粘贴译文"} value={paste} rows={7} onChange={e => setPaste(e.target.value)} /></label><button className="button primary" onClick={parse} disabled={!!busy || !paste.trim()}>解析并预览</button></> : <><label className="studio-field"><span>模型配置</span><select value={profileId} onChange={e => setProfileId(e.target.value)}>{!profiles.length && <option value="">尚未配置模型</option>}{profiles.map(p => <option key={p.id} value={p.id}>{p.name} · {p.model}</option>)}</select></label><button className="text-button" onClick={() => { onClose(); navigate("models"); }}>管理模型配置<ArrowRight size={13} /></button>{profile && <div className="ref-model-target"><span>{remote ? "远程 API" : "本机模型"}</span><strong>{modelEndpoint(profile.base_url)}</strong><small>{profile.model}</small></div>}<button className="button primary" onClick={() => void prepare(true)} disabled={!!busy || !profileId || (remote && !profile?.target_fingerprint)}><Sparkles size={15} />{remote ? "发送并生成预览" : "生成预览"}</button></>}
    <Notice error>{error}</Notice>{busy && <Busy label={busy === "apply" ? "正在保存预览结果" : busy === "prompt" ? "正在准备提示词" : "正在等待模型返回"} compact />}
    {preview && <section className="ref-transform-preview"><div className="ref-mini-heading"><h3>结果预览</h3><span className="ref-tag">尚未应用</span></div>{transform.kind === "summarize" ? <PlotEditor value={preview} onSave={async data => setPreview(data)} busy={!!busy} /> : <textarea aria-label="译文预览" rows={12} value={stringValue(preview.text)} onChange={e => setPreview({ text: e.target.value })} />}</section>}
  </div><div className="modal-footer"><button className="button secondary" onClick={onClose} disabled={!!busy}>取消</button><button className="button primary" disabled={!preview || !!busy} onClick={async () => { if (!preview) return; setBusy("apply"); try { await onApply(preview); } catch (e) { setError(messageOf(e)); } finally { setBusy(""); } }}>应用结果</button></div></Modal>;
}
