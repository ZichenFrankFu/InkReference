import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowRight, BookOpen, CheckCircle2, Download, FileSearch, History, Layers3, MessageSquare, RefreshCw, Search, SlidersHorizontal, Sparkles, Upload, X, Play } from "lucide-react";
import { Busy, Empty, Modal, Notice, dateLabel, messageOf } from "./ui";
import { normalizeList, send, studio, type DocumentMeta, type List, type ModelProfile, type Run, type RunSummary } from "./studio";
import { download, request, type Item, type Page } from "./api";
import { isRemoteModel, modelEndpoint } from "./model-security";
import { CollectionEditor, FactsEditor, PlotEditor, PromptPanel, ReferenceField } from "./ReferenceEditors";
import { ChronicleEntities, ChroniclePlot, EraBackgroundEditor, LanguageAnalysisEditor } from "./ChronicleEditors";
import { record, records, sectionCount, stringValue, type ReferenceRecord, type ReferenceSection, type ReferenceState } from "./reference-types";
import "./reference-analysis.css";

type Navigation = (id: "library" | "documents" | "reference" | "jobs" | "workflows" | "models" | "backup") => void;
type Props = { docs: DocumentMeta[]; runs: RunSummary[]; loading: boolean; navigate: Navigation; onRun: (run: Run) => void; onRefresh: () => Promise<void>; onDirtyChange: (dirty: boolean) => void };
type Transform = { kind: "summarize" | "translate"; entryIndex?: number };
const narrativeTabs = [["plot", "剧情大纲"], ["characters", "角色"], ["settings", "设定"], ["era_background", "时代背景"], ["features", "语言分析"]];
const settingTabs = [["raw_entries", "原始文本"], ["settings", "设定"], ["characters", "角色"], ["setting_features", "设定特征"]];

export default function ReferenceAnalysis({ docs, runs, loading, navigate, onRefresh, onDirtyChange }: Props) {
  const [documentId, setDocumentId] = useState("");
  const [state, setState] = useState<ReferenceState | null>(null);
  const [tab, setTab] = useState("plot");
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
  const [chapter, setChapter] = useState("");
  const [chapterFilter, setChapterFilter] = useState("");
  const [projection, setProjection] = useState<ReferenceState | null>(null);
  const [catalogCharacter, setCatalogCharacter] = useState<ReferenceRecord | null>(null);
  const [catalogCharacters, setCatalogCharacters] = useState<Item[]>([]);
  const [catalogCharacterQuery, setCatalogCharacterQuery] = useState("");
  const [catalogCharacterChoice, setCatalogCharacterChoice] = useState("__new__");
  const [catalogRevision, setCatalogRevision] = useState("");
  const [catalogBound, setCatalogBound] = useState("");
  const [catalogSearching, setCatalogSearching] = useState(false);
  const [scopeOpen, setScopeOpen] = useState(false);
  const [metadataOpen, setMetadataOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [history, setHistory] = useState<{ revision: number; updated_at: string }[]>([]);
  const [detailRuns, setDetailRuns] = useState<Run[]>([]);
  const requestId = useRef(0);
  const attemptedInitial = useRef(new Set<string>());
  const dirtyRef = useRef(false);
  const selectedRef = useRef(documentId);
  const stateRef = useRef(state);
  dirtyRef.current = dirty; selectedRef.current = documentId; stateRef.current = state;
  const selected = docs.find(d => d.id === documentId);
  const collection = state?.structure_type === "setting_collection";
  const tabs = collection ? settingTabs : narrativeTabs;
  const shownState = projection;
  const shown = shownState?.sections;
  const readOnly = !!chapterFilter;
  const segmentStyles = tab === "features" && featureView === "segment" && !chapterFilter;
  const detailIds = state && segmentStyles ? [...new Set(state.commits.filter(commit => commit.section === "style").map(commit => commit.run_id))].sort() : [];
  const detailKey = detailIds.join(":");
  const detailRevisionKey = runs.filter(run => run.document_id === documentId && detailIds.includes(run.id)).map(run => `${run.id}:${run.revision}`).join(":");
  useEffect(() => {
    const controller = new AbortController();
    setDetailRuns([]); setBusy(current => current === "sources" ? "" : current);
    if (!documentId || !detailKey) return;
    setBusy("sources");
    void Promise.all(detailKey.split(":").map(id => studio<Run>(`/jobs/${encodeURIComponent(id)}`, { signal: controller.signal }))).then(items => {
      if (!controller.signal.aborted) setDetailRuns(items.filter(run => run.document_id === documentId));
    }).catch(e => { if (!controller.signal.aborted) setError(messageOf(e)); }).finally(() => { if (!controller.signal.aborted) setBusy(current => current === "sources" ? "" : current); });
    return () => { controller.abort(); setBusy(current => current === "sources" ? "" : current); };
  }, [documentId, detailKey, detailRevisionKey]);
  const select = useCallback(async (id: string, discard = false, focusTab?: string) => {
    if (dirtyRef.current && !discard) { setPending({ documentId: id, tab: focusTab }); return; }
    const ticket = ++requestId.current; setBusy("load"); setError(""); setMessage("");
    try { const next = await studio<ReferenceState>(`/documents/${id}/reference`); if (ticket !== requestId.current) return; setDocumentId(id); setState(next); setDetailRuns([]); setProjection(null); setChapter(""); setChapterFilter(""); setDirty(false); const available = next.structure_type === "setting_collection" ? settingTabs : narrativeTabs; setTab(focusTab && available.some(([key]) => key === focusTab) ? focusTab : next.structure_type === "setting_collection" ? "raw_entries" : "plot"); }
    catch (e) { if (ticket === requestId.current) setError(messageOf(e)); }
    finally { if (ticket === requestId.current) setBusy(""); }
  }, []);
  useEffect(() => {
    const focus = (target: unknown) => {
      const request = record(target);
      if (!["jobs", "reference"].includes(stringValue(request.section)) || typeof request.documentId !== "string" || !docs.some(doc => doc.id === request.documentId)) return false;
      try { sessionStorage.removeItem("inkreference.catalog-focus"); } catch { /* in-memory event still works */ }
      void select(request.documentId, false, stringValue(request.tab));
      return true;
    };
    let requested = false;
    try { requested = focus(JSON.parse(sessionStorage.getItem("inkreference.catalog-focus") || "null")); } catch { /* malformed target does not block the workspace */ }
    if (!requested && !documentId && docs.length && !attemptedInitial.current.has(docs[0].id)) { attemptedInitial.current.add(docs[0].id); void select(docs[0].id); }
    const open = (event: Event) => { focus((event as CustomEvent).detail); };
    window.addEventListener("inkreference:catalog-focus", open);
    return () => window.removeEventListener("inkreference:catalog-focus", open);
  }, [docs, documentId, select]);
  useEffect(() => { const before = (e: BeforeUnloadEvent) => { if (dirtyRef.current) e.preventDefault(); }; window.addEventListener("beforeunload", before); return () => window.removeEventListener("beforeunload", before); }, []);
  useEffect(() => { onDirtyChange(dirty); return () => onDirtyChange(false); }, [dirty, onDirtyChange]);
  const reload = useCallback(async () => {
    if (!selectedRef.current) return;
    const id = selectedRef.current;
    const next = await studio<ReferenceState>(`/documents/${id}/reference`);
    if (selectedRef.current === id) setState(next);
  }, []);
  useEffect(() => { const refresh = () => { if (!selectedRef.current || dirtyRef.current) return; setBusy("load"); void reload().catch(e => setError(messageOf(e))).finally(() => setBusy("")); }; window.addEventListener("inkreference:reference-refresh", refresh); return () => window.removeEventListener("inkreference:reference-refresh", refresh); }, [reload]);
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
    try { const next = await send<ReferenceState>(`/documents/${documentId}/reference`, { structure_type: collection ? "narrative" : "setting_collection", expected_revision: state.revision }, "PUT"); setState(next); setTab(collection ? "plot" : "raw_entries"); setStructureDialog(false); }
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
  const showHistory = async () => { setHistoryOpen(true); setBusy("history"); setError(""); try { const response = await studio<{ items: { revision: number; updated_at: string }[] }>(`/documents/${documentId}/reference/history`); setHistory(response.items); } catch (e) { setError(messageOf(e)); } finally { setBusy(""); } };
  const restore = async (revision: number) => { setBusy("restore"); setError(""); try { const next = await send<ReferenceState>(`/documents/${documentId}/reference/restore`, { target_revision: revision, expected_revision: stateRef.current?.revision }); setState(next); stateRef.current = next; setTab(next.structure_type === "setting_collection" ? "raw_entries" : "plot"); setHistoryOpen(false); setMessage("历史内容已恢复为新版本"); } catch (e) { setError(messageOf(e)); } finally { setBusy(""); } };
  useEffect(() => { if (!documentId) { setProjection(null); return; } let active = true; setProjection(null); setBusy("projection"); void studio<ReferenceState>(`/documents/${documentId}/reference/projection?schema_version=3${chapterFilter ? `&progress=${encodeURIComponent(chapterFilter)}` : ""}`).then(next => { if (active) setProjection(next); }).catch(e => { if (active) setError(messageOf(e)); }).finally(() => { if (active) setBusy(""); }); return () => { active = false; }; }, [documentId, chapterFilter, state?.revision]);
  const openCatalogCharacter = async (character: ReferenceRecord) => {
    if (!state || !selected) return;
    setCatalogCharacter(character); setCatalogCharacters([]); setCatalogCharacterQuery(""); setCatalogCharacterChoice("__new__"); setCatalogBound(""); setCatalogRevision(""); setError("");
    if (!selected.work_id) { setError("请先将文本关联到作品"); return; }
    setBusy("catalog-character");
    try {
      const [links, related, matching] = await Promise.all([
        studio<{ catalog_revision: string; links: { extracted_character_id: string; character_id: string }[] }>(`/documents/${documentId}/reference/characters/catalog`),
        request<Page>(`/records/characters?${new URLSearchParams({ work_id: selected.work_id, limit: "100" })}`),
        request<Page>(`/records/characters?${new URLSearchParams({ q: stringValue(character.name), limit: "100" })}`),
      ]);
      const bound = links.links.find(link => link.extracted_character_id === character.id)?.character_id || "";
      const all = [...related.items, ...matching.items];
      if (bound && !all.some(item => item.id === bound)) all.unshift(await request<Item>(`/records/characters/${encodeURIComponent(bound)}`));
      const names = [stringValue(character.name), ...((Array.isArray(character.aliases) ? character.aliases : []) as string[])].map(name => name.normalize("NFKC").toLocaleLowerCase());
      const choices = [...new Map(all.map(item => [item.id, item])).values()].sort((left, right) => Number(names.includes(right.values["角色名"]?.normalize("NFKC").toLocaleLowerCase())) - Number(names.includes(left.values["角色名"]?.normalize("NFKC").toLocaleLowerCase())));
      setCatalogCharacters(choices); setCatalogRevision(links.catalog_revision); setCatalogBound(bound); setCatalogCharacterChoice(bound || "__new__");
    } catch (error) { setError(messageOf(error)); } finally { setBusy(""); }
  };
  useEffect(() => {
    if (!catalogCharacter || !catalogCharacterQuery.trim()) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => { setCatalogSearching(true); void request<Page>(`/records/characters?${new URLSearchParams({ q: catalogCharacterQuery.trim(), limit: "100" })}`, { signal: controller.signal }).then(response => { if (!controller.signal.aborted) setCatalogCharacters(current => [...new Map([...current.filter(item => item.id === catalogCharacterChoice || item.id === catalogBound), ...response.items].map(item => [item.id, item])).values()]); }).catch(error => { if (!controller.signal.aborted) setError(messageOf(error)); }).finally(() => { if (!controller.signal.aborted) setCatalogSearching(false); }); }, 180);
    return () => { controller.abort(); window.clearTimeout(timer); setCatalogSearching(false); };
  }, [catalogCharacter, catalogCharacterQuery, catalogCharacterChoice, catalogBound]);
  const importCatalogCharacter = async () => {
    const current = stateRef.current; if (!current || !catalogCharacter) return;
    setBusy("catalog-character-save"); setError("");
    try { await send(`/documents/${current.document_id}/reference/characters/${encodeURIComponent(stringValue(catalogCharacter.id))}/catalog`, { character_id: catalogCharacterChoice === "__new__" ? undefined : catalogCharacterChoice, expected_reference_revision: current.revision, expected_catalog_revision: catalogRevision }); setCatalogCharacter(null); setMessage("角色已加入人物库"); window.dispatchEvent(new CustomEvent("inkreference:library-refresh")); window.dispatchEvent(new CustomEvent("inkreference:reference-refresh")); await onRefresh(); }
    catch (error) { setError(messageOf(error)); } finally { setBusy(""); }
  };
  const sectionDirty = useCallback((value: boolean) => setDirty(value), []);
  const styleChunks = detailRuns.flatMap(run => run.candidates.filter(c => state?.commits.some(k => k.run_id === run.id && k.chunk_id === c.chunk_id && k.section === "style")).map(c => ({ run, chunk: c, style: record(record(c.result).reference).style })));
  return <div className="reference-layout">
    <aside className="surface reference-index"><div className="surface-heading"><h2>参考作品库</h2><span>{docs.length}</span><button className="icon-button" aria-label="刷新参考作品库" onClick={() => void onRefresh().catch(e => setError(messageOf(e)))}><RefreshCw size={15} /></button></div><label className="index-search"><Search size={15} /><input aria-label="搜索参考作品" placeholder="搜索书名、作者或类型" value={query} onChange={e => setQuery(e.target.value)} />{query && <button className="icon-button" aria-label="清除搜索" onClick={() => setQuery("")}><X size={13} /></button>}</label>
      <div className="reference-work-list">
      {loading && !docs.length ? <Busy label="正在读取作品" /> : docs.filter(d => `${d.title} ${d.catalog_author || d.author || ""} ${d.work_category || d.book_type || ""}`.normalize("NFKC").toLocaleLowerCase().includes(query.trim().normalize("NFKC").toLocaleLowerCase())).map(d => <button className={`reference-work-item ${documentId === d.id ? "active" : ""}`} key={d.id} onClick={() => void select(d.id)}><span className="reference-work-icon"><BookOpen size={19} /></span><span><strong>{d.title}</strong><small>{(Number(d.current_characters ?? d.source_characters ?? 0) / 10000).toFixed(1)} 万字<span>{d.work_category || d.book_type || "未分类"}</span></small></span>{documentId === d.id && <span className="reference-active-dot" />}</button>)}
      {!loading && docs.length > 0 && !docs.some(d => `${d.title} ${d.catalog_author || d.author || ""} ${d.work_category || d.book_type || ""}`.normalize("NFKC").toLocaleLowerCase().includes(query.trim().normalize("NFKC").toLocaleLowerCase())) && <div className="index-empty">未找到作品<button className="text-button" onClick={() => setQuery("")}>清除搜索</button></div>}
      </div>
    </aside>
    <section className="surface reference-workspace" aria-busy={!!busy}>
      <div className={`reference-operation ${busy ? "visible" : ""}`} aria-hidden={!busy}>{busy && <Busy label={{ load: "正在读取作品分析", save: "正在保存修改", structure: "正在切换作品类型", import: "正在导入文本" }[busy] || "正在处理"} compact />}</div>
      {!state && <Notice error>{error}</Notice>}
      {!state || !selected ? <Empty icon={FileSearch} title="暂无参考作品" action={<button className="button primary" onClick={() => navigate("documents")}>导入作品<ArrowRight size={15} /></button>} /> : <>
        <header className="reference-work-heading"><div className="reference-heading-copy"><h2 className="reference-desktop-title">{selected.title}</h2><label className="reference-title-picker"><span>参考作品库</span><select aria-label="参考作品库" value={documentId} disabled={dirty} onChange={event => void select(event.target.value)}>{docs.map(doc => <option key={doc.id} value={doc.id}>{doc.title} · {(Number(doc.current_characters ?? doc.source_characters ?? 0) / 10000).toFixed(1)} 万字 · {doc.work_category || doc.book_type || "未分类"}</option>)}</select></label></div><div className="inline-actions"><button className={`icon-button ${scopeOpen || chapterFilter ? "active" : ""}`} aria-label="叙事进度筛选" title="叙事进度筛选" disabled={dirty} onClick={() => setScopeOpen(!scopeOpen)}><SlidersHorizontal size={16} /></button>{selected.work_id && <button className="icon-button" title="作品词条" aria-label="作品词条" disabled={!!busy || dirty} onClick={() => window.dispatchEvent(new CustomEvent("inkreference:navigate", { detail: { section: "library", workId: selected.work_id } }))}><BookOpen size={16} /></button>}<button className="icon-button" aria-label="作品笔记" title="作品笔记" disabled={!!busy || dirty} onClick={() => setMetadataOpen(true)}><MessageSquare size={16} /></button><button className="icon-button" aria-label="分析历史版本" title="分析历史版本" disabled={!!busy || dirty} onClick={() => void showHistory()}><History size={16} /></button><button className="icon-button" title={collection ? "切换设定集结构" : "切换叙事作品结构"} aria-label="作品结构" disabled={!!busy || dirty} onClick={() => setStructureDialog(true)}><Layers3 size={16} /></button><button className="button primary" disabled={dirty} onClick={() => { sessionStorage.setItem("inkreference.task-document", documentId); window.dispatchEvent(new CustomEvent("inkreference:task-document", { detail: { documentId } })); navigate("jobs"); }}><Play size={14} /><span className="reference-task-label">分析任务</span></button></div></header>
        {!collection && scopeOpen && <form className="reference-progress-scope" onSubmit={event => { event.preventDefault(); if (!dirty) setChapterFilter(chapter); }}><label>叙事进度<input aria-label="分析截至叙事进度" type="number" min="0" max="100" step="0.1" placeholder="100" value={chapter} disabled={dirty} onChange={event => setChapter(event.target.value)} /><span>%</span></label><button className="button secondary" type="submit" disabled={dirty || !!busy}>查看</button>{chapterFilter && <button className="text-button" type="button" disabled={dirty} onClick={() => { setChapter(""); setChapterFilter(""); }}>全书</button>}</form>}
        {chapterFilter && !scopeOpen && <div className="reference-active-scope"><span>截至 {chapterFilter}%</span><button className="icon-button" aria-label="查看全书" onClick={() => { setChapter(""); setChapterFilter(""); }}><X size={13} /></button></div>}
        <div className="reference-tabs" role="tablist" aria-label="参考数据库功能">{tabs.map(([key, label]) => <button role="tab" aria-selected={tab === key} className={tab === key ? "active" : ""} key={key} onClick={() => changeTab(key)}>{label}{["plot", "characters", "settings", "setting_features", "raw_entries"].includes(key) && <span>{sectionCount(shownState, key as ReferenceSection)}</span>}</button>)}</div>
        <div className="reference-notices"><Notice error={!!error} success={!error && !!message}>{error || message}</Notice></div>
        <div className="reference-tab-body" key={`${documentId}:${tab}`}>
          {!projection ? <Busy label={readOnly ? "正在读取叙事进度视图" : "正在读取作品分析"} /> : shown && <>
          {tab === "plot" && <ChroniclePlot sourceVersionId={selected.latest_version_id || ""} value={shown.plot} rawValue={shown.plot} onSave={readOnly ? undefined : data => save("plot", data)} busy={!!busy} onDirty={sectionDirty} characters={records(shown.characters)} settings={records(shown.settings)} readOnly={readOnly} />}
          {(["characters", "settings"] as string[]).includes(tab) && <ChronicleEntities sourceVersionId={selected.latest_version_id || ""} kind={tab as "characters" | "settings"} value={shown[tab as ReferenceSection]} rawValue={shown[tab as ReferenceSection]} onSave={readOnly ? undefined : data => save(tab as ReferenceSection, data)} busy={!!busy} onDirty={sectionDirty} plot={shown.plot} characters={records(shown.characters)} settings={records(shown.settings)} readOnly={readOnly} onCatalog={readOnly ? undefined : item => void openCatalogCharacter(item)} />}
          {tab === "setting_features" && <CollectionEditor kind="setting_features" value={shown.setting_features} onSave={items => save("setting_features", items)} busy={!!busy || readOnly} onDirty={sectionDirty} />}
          {tab === "era_background" && <EraBackgroundEditor sourceVersionId={selected.latest_version_id || ""} value={shown.era_background} plot={shown.plot} characters={records(shown.characters)} onSave={readOnly ? undefined : data => save("era_background", data)} busy={!!busy} onDirty={sectionDirty} />}
          {tab === "raw_entries" && <><div className="ref-raw-tools"><button className="button secondary" disabled={!!busy || dirty} onClick={() => setImportDialog(true)}><Upload size={14} />导入设定集 TXT</button><button className="button secondary" disabled={!!busy} onClick={() => void exportEntries()}><Download size={14} />导出设定集 TXT</button></div><CollectionEditor kind="raw_entries" value={state.sections.raw_entries} onSave={items => save("raw_entries", items)} busy={!!busy} onDirty={sectionDirty} settingCollection onExtra={(_, index) => <div className="ref-raw-entry-actions"><button className="text-button" disabled={!!busy || dirty} onClick={() => setTransform({ kind: "translate", entryIndex: index })}><Sparkles size={14} />翻译与预览</button></div>} /></>}
          {tab === "features" && <><div className="ref-collection-tools"><h3>语言分析</h3><div className="ref-segment-control"><button className={featureView === "book" ? "active" : ""} disabled={dirty} onClick={() => setFeatureView("book")}>全书视图</button><button className={featureView === "segment" ? "active" : ""} disabled={dirty || readOnly} onClick={() => setFeatureView("segment")}>分段视图</button></div></div>{featureView === "book" || readOnly ? <LanguageAnalysisEditor sourceVersionId={selected.latest_version_id || ""} value={shown.style} rhythm={shown.rhythm} opening={shown.opening} characters={records(shown.characters)} onSave={readOnly ? undefined : data => save("style", data)} onSaveRhythm={readOnly ? undefined : data => save("rhythm", data)} onSaveOpening={readOnly ? undefined : data => save("opening", data)} busy={!!busy} onDirty={sectionDirty} /> : <div className="ref-chunk-style-list">{styleChunks.map(({ run, chunk, style }) => <details key={`${run.id}:${chunk.chunk_id}`}><summary><span><FileSearch size={15} />{chunk.chunk_id}</span><span>{dateLabel(run.created_at)}</span><CheckCircle2 size={15} /></summary><LanguageAnalysisEditor value={style} /></details>)}{!styleChunks.length && <div className="ref-empty">暂无分段语言分析</div>}</div>}</>}
          </>}
        </div>
      </>}
    </section>
    {catalogCharacter && <Modal title="加入角色库" busy={busy.startsWith("catalog-character")} onClose={() => setCatalogCharacter(null)}><div className="modal-body"><h3 className="catalog-character-name">{stringValue(catalogCharacter.name)}</h3><label className="studio-field"><span>搜索人物</span><input aria-label="搜索复用人物" value={catalogCharacterQuery} disabled={!!busy || !!catalogBound} onChange={event => setCatalogCharacterQuery(event.target.value)} /></label>{catalogSearching && <Busy label="正在搜索人物" compact />}<div className="catalog-character-options">{!catalogBound && <label><input type="radio" name="catalog-character" value="__new__" checked={catalogCharacterChoice === "__new__"} onChange={() => setCatalogCharacterChoice("__new__")} /><span><strong>新建人物</strong><small>{stringValue(catalogCharacter.name)} · {stringValue(catalogCharacter.id)}</small></span></label>}{catalogCharacters.map(item => <label key={item.id}><input type="radio" name="catalog-character" value={item.id} checked={catalogCharacterChoice === item.id} disabled={!!catalogBound && catalogBound !== item.id} onChange={() => setCatalogCharacterChoice(item.id)} /><span><strong>{item.values["角色名"] || item.id}</strong><small>{item.values["别名"] ? `${item.values["别名"]} · ` : ""}{item.id}</small></span>{catalogBound === item.id && <span className="ref-tag">已关联</span>}</label>)}</div><Notice error>{error}</Notice>{busy.startsWith("catalog-character") && <Busy label={busy.endsWith("save") ? "正在同步人物" : "正在读取角色库"} compact />}</div><div className="modal-footer"><button className="button secondary" disabled={!!busy} onClick={() => setCatalogCharacter(null)}>取消</button><button className="button primary" disabled={!!busy || !catalogRevision || !selected?.work_id} onClick={() => void importCatalogCharacter()}>{catalogBound ? "同步人物" : catalogCharacterChoice === "__new__" ? "新建并关联" : "复用并关联"}</button></div></Modal>}
    {metadataOpen && state && <MetadataDialog value={state.sections.metadata} busy={!!busy} onClose={() => setMetadataOpen(false)} onSave={async data => { await save("metadata", data); setMetadataOpen(false); }} />}
    {historyOpen && <Modal title="分析历史版本" onClose={() => setHistoryOpen(false)} busy={!!busy}><div className="modal-body"><Notice error>{error}</Notice>{busy && <Busy label={busy === "restore" ? "正在恢复分析版本" : "正在读取历史版本"} compact />}<div className="ref-history-list">{history.map(h => <div key={h.revision}><span><strong>版本 {h.revision}</strong><small>{dateLabel(h.updated_at)}</small></span><button className="button secondary" disabled={!!busy || h.revision === state?.revision} onClick={() => void restore(h.revision)}>{h.revision === state?.revision ? "当前版本" : "恢复为新版本"}</button></div>)}</div></div><div className="modal-footer"><button className="button secondary" disabled={!!busy} onClick={() => setHistoryOpen(false)}>关闭</button></div></Modal>}
    {pending && <Modal title="有未保存的修改" onClose={() => setPending(null)}><div className="modal-body"><p>当前编辑尚未保存。</p></div><div className="modal-footer"><button className="button secondary" onClick={() => setPending(null)}>继续编辑</button><button className="button danger" onClick={() => { const next = pending; setPending(null); setDirty(false); dirtyRef.current = false; if (next.documentId) void select(next.documentId, true, next.tab); else if (next.tab) setTab(next.tab); }}>放弃并切换</button></div></Modal>}
    {structureDialog && <Modal title="切换作品结构" onClose={() => setStructureDialog(false)} busy={!!busy}><div className="modal-body"><p>{collection ? "切换为叙事作品，使用剧情大纲、角色、设定、时代背景和语言分析。" : "切换为设定集，使用原始条目、角色、设定和设定特征。"}</p><Notice error>{error}</Notice>{busy && <Busy label="正在切换作品结构" compact />}</div><div className="modal-footer"><button className="button secondary" disabled={!!busy} onClick={() => setStructureDialog(false)}>取消</button><button className="button primary" disabled={!!busy} onClick={() => void configure()}>切换为{collection ? "叙事作品" : "设定集"}</button></div></Modal>}
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
