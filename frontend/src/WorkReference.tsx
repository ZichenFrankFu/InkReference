import { useEffect, useRef, useState } from "react";
import { ArrowUpRight, BookOpen, ChevronRight, FileText, Link2Off, Sparkles, UsersRound, Layers3 } from "lucide-react";
import { Busy } from "./ui";
import { jsonBody, request } from "./api";
import { ChronicleEntities, ChroniclePlot, EraBackgroundEditor, LanguageAnalysisEditor } from "./ChronicleEditors";
import "./reference-analysis.css";
import { NarrativeProvider, ProgressInput } from "./NarrativePosition";

type R = Record<string, unknown>;
type Document = {
  id: string; title: string; latest_version_id: string | null; current_characters: number;
  completed_version_id?: string | null; completed_at?: string | null;
  analysis?: { revision: number; stale?: boolean; source_version_ids?: string[]; counts: { plot: number; characters: number; settings: number } } | null;
};
type Summary = { work_id: string; title: string; documents: Document[]; counts: { documents: number; analyses: number } };
type Analysis = { narrative_context?: { chapter_count: number }; document_id: string; latest_version_id: string | null; projection: { characters: R[]; settings: R[]; plot: R; era_background?: R; style?: R; rhythm?: R; opening?: R } };
const list = (value: unknown): R[] => Array.isArray(value) ? value.filter(v => v && typeof v === "object" && !Array.isArray(v)) : [];
const str = (value: unknown) => typeof value === "string" || typeof value === "number" ? String(value) : "";
const issue = (error: unknown) => error instanceof Error ? error.message : "读取失败";
function navigate(section: "documents" | "reference" | "jobs", doc: Document) {
  window.dispatchEvent(new CustomEvent("inkreference:navigate", { detail: { section, documentId: doc.id, versionId: doc.latest_version_id } }));
}
function Chapter({ value }: { value: unknown }) {
  const text = str(value);
  return text ? <span className="reference-chapter-tag">{/^\d+$/.test(text) ? `第 ${text} 章` : text}</span> : null;
}
function Timeline({ value }: { value: unknown }) {
  const facts = list(value);
  return facts.length ? <ol className="catalog-fact-timeline">{facts.map((fact, index) => <li key={index}><Chapter value={fact.chapter} /><span>{str(fact.text)}</span></li>)}</ol> : null;
}

export default function WorkReference({ workId }: { workId: string }) {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [documentId, setDocumentId] = useState("");
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [view, setView] = useState<"plot" | "characters" | "settings" | "era_background" | "language">("plot");
  const [chapter, setChapter] = useState("");
  const [chapterFilter, setChapterFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const [unlinkBusy, setUnlinkBusy] = useState("");
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const previousId = useRef("");
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const host = root.current?.closest<HTMLElement>(".workspace-page");
    const refreshWhenVisible = () => { if (host && !host.hidden) setRefresh(value => value + 1); };
    const focus = (event: Event) => { const target = (event as CustomEvent<{ section?: string; workId?: string }>).detail; if (target?.section === "library" && target.workId === workId) setRefresh(value => value + 1); };
    const update = () => setRefresh(value => value + 1);
    const observer = new MutationObserver(refreshWhenVisible);
    if (host) observer.observe(host, { attributes: true, attributeFilter: ["hidden"] });
    window.addEventListener("inkreference:library-refresh", update);
    window.addEventListener("inkreference:catalog-focus", focus);
    return () => { observer.disconnect(); window.removeEventListener("inkreference:library-refresh", update); window.removeEventListener("inkreference:catalog-focus", focus); };
  }, [workId]);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setError("");
    void request<Summary>(`/studio/catalog/works/${encodeURIComponent(workId)}`, { signal: controller.signal }).then(next => {
      if (controller.signal.aborted) return;
      setSummary(next);
      setDocumentId(current => next.documents.some(d => d.id === current) ? current : next.documents.find(d => d.analysis)?.id || next.documents[0]?.id || "");
    }).catch(e => { if (!controller.signal.aborted) setError(issue(e)); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [workId, refresh]);
  const selected = summary?.documents.find(d => d.id === documentId);
  useEffect(() => {
    if (previousId.current !== documentId) { setChapter(""); setChapterFilter(""); previousId.current = documentId; }
    if (!documentId || !selected?.analysis) { setAnalysis(null); return; }
    const controller = new AbortController(); setAnalysisLoading(true); setError("");
    const params = new URLSearchParams({ document_id: documentId, schema_version: "4" });
    if (chapterFilter) params.set("progress", chapterFilter);
    void request<Analysis>(`/studio/catalog/works/${encodeURIComponent(workId)}/analysis?${params}`, { signal: controller.signal }).then(next => { if (!controller.signal.aborted) setAnalysis(next); }).catch(e => { if (!controller.signal.aborted) setError(issue(e)); }).finally(() => { if (!controller.signal.aborted) setAnalysisLoading(false); });
    return () => controller.abort();
  }, [workId, documentId, selected?.analysis?.revision, chapterFilter]);
  const unlink = async (doc: Document) => {
    setUnlinkBusy(doc.id); setError("");
    try {
      await request(`/studio/documents/${encodeURIComponent(doc.id)}/catalog`, { method: "PUT", ...jsonBody({ work_id: null, expected_work_id: workId, expected_version_id: doc.latest_version_id }) });
      setRefresh(value => value + 1);
      window.dispatchEvent(new Event("inkreference:library-refresh"));
    } catch (e) { setError(issue(e)); } finally { setUnlinkBusy(""); }
  };
  const projection = analysis?.projection;
  const plot = projection?.plot || {};
  const categoryLabel = (value: unknown) => ({ power_system: "力量体系", factions: "势力组织", geography: "地理", social_rules: "社会规则", history: "历史背景", worldview: "世界观", other: "其他" } as Record<string, string>)[str(value)] || str(value);
  return <NarrativeProvider chapterCount={analysis?.narrative_context?.chapter_count || 0}><div ref={root} className="detail-scroll catalog-reference">
    {(loading || analysisLoading || !!unlinkBusy) && <Busy label={unlinkBusy ? "正在更新作品关联" : "正在读取文本与分析"} compact />}
    {error && <div className="notice error" role="alert">{error}<button className="text-button" onClick={() => setRefresh(value => value + 1)}>重试</button></div>}
    {loading && !summary ? <div className="catalog-empty"><span>正在读取…</span></div> : !summary?.documents.length ? <div className="catalog-empty"><FileText size={28} /><h3>暂无关联文本</h3></div> : <>
      <section className="catalog-documents" aria-label="关联文本">{summary.documents.map(doc => <article className={documentId === doc.id ? "selected" : ""} key={doc.id}>
        <button className="catalog-document-select" onClick={() => setDocumentId(doc.id)} aria-pressed={documentId === doc.id}><FileText size={17} /><span><strong>{doc.title}</strong><small>{(Number(doc.current_characters || 0) / 10000).toFixed(1)} 万字{doc.completed_version_id === doc.latest_version_id && doc.completed_version_id ? " · 已清理" : ""}{doc.analysis ? " · 有分析" : ""}</small></span><ChevronRight size={15} /></button>
        <div className="catalog-document-actions"><button className="text-button" onClick={() => navigate("documents", doc)}><BookOpen size={13} />文本</button><button className="text-button" onClick={() => navigate("reference", doc)}><Sparkles size={13} />分析<ArrowUpRight size={12} /></button><button className="text-button catalog-unlink" disabled={!!unlinkBusy} aria-label={`解除 ${doc.title} 的关联`} onClick={() => void unlink(doc)}><Link2Off size={13} />解除关联</button></div>
      </article>)}</section>
      {selected?.analysis ? <section className="catalog-analysis" aria-label="参考分析">
        {selected.analysis.stale && <div className="catalog-stale-state">分析来源包含旧版文本</div>}
        <div className="catalog-analysis-tabs" role="tablist" aria-label="分析内容">{([
          ["plot", "剧情大纲", BookOpen], ["characters", "角色", UsersRound], ["settings", "设定", Layers3], ["era_background", "时代背景事件", Sparkles], ["language", "语言分析", FileText],
        ] as const).map(([key, label, Icon]) => <button key={key} role="tab" aria-selected={view === key} className={view === key ? "active" : ""} onClick={() => setView(key)}><Icon size={14} />{label}{["plot", "characters", "settings"].includes(key) && <small>{selected.analysis?.counts[key as "plot" | "characters" | "settings"] || 0}</small>}</button>)}</div>
        <form className="catalog-chapter-filter" onSubmit={event => { event.preventDefault(); setChapterFilter(chapter); }}><ProgressInput label="截至叙事进度" nullable value={chapter === "" ? null : Number(chapter)} onChange={value => setChapter(value == null ? "" : String(value))} /><button className="button secondary" type="submit" disabled={analysisLoading}>查看</button>{chapterFilter && <button className="text-button" type="button" onClick={() => { setChapter(""); setChapterFilter(""); }}>全部</button>}</form>
        {analysisLoading && !analysis ? <div className="catalog-empty"></div> : projection && <div className={`catalog-analysis-content ${analysisLoading ? "loading" : ""}`}>
          {view === "plot" && Number(plot.schema_version) >= 2 ? <ChroniclePlot value={plot} characters={projection.characters} settings={projection.settings} readOnly /> : view === "plot" && <>{str(plot.logline) && <p className="catalog-logline">{str(plot.logline)}</p>}{list(plot.epochs).map((epoch, ei) => <section className="catalog-epoch" key={ei}><h3>{str(epoch.title) || `阶段 ${ei + 1}`}</h3>{list(epoch.periods).map((period, pi) => <div className="catalog-period" key={pi}>{str(period.time) && <h4>{str(period.time)}</h4>}{list(period.events).map((event, index) => <article className="catalog-event" key={index}><div><Chapter value={event.first_chapter} />{(str(event.time_marker) || (Array.isArray(event.time_markers) ? event.time_markers.map(str).filter(Boolean).join(" · ") : "")) && <span className="catalog-story-time">{str(event.time_marker) || (event.time_markers as unknown[]).map(str).filter(Boolean).join(" · ")}</span>}</div><strong>{str(event.name) || str(event.subject)}</strong>{str(event.description) && <p>{str(event.description)}</p>}</article>)}</div>)}</section>)}{!list(plot.epochs).length && !str(plot.logline) && <p className="catalog-no-results">暂无剧情大纲</p>}</>}
          {view === "characters" && projection.characters.some(c => Number(c.schema_version) >= 2) ? <ChronicleEntities kind="characters" value={projection.characters} plot={plot} characters={projection.characters} settings={projection.settings} readOnly /> : view === "characters" && <>{projection.characters.map((character, index) => <details className="catalog-entity" key={index}><summary><UsersRound size={15} /><strong>{str(character.name) || "未命名角色"}</strong><Chapter value={character.first_chapter} /><ChevronRight size={14} /></summary><div className="catalog-entity-body">{str(character.intro) && <p>{str(character.intro)}</p>}{str(character.role_tag) && <span className="tag">{str(character.role_tag)}</span>}{str(character.first_seen_at) && <p className="catalog-story-time">{str(character.first_seen_at)}</p>}{([["appearance", "外貌"], ["personality", "性格"], ["experiences", "经历"]] as const).map(([key, label]) => list(character[key]).length ? <section key={key}><h4>{label}</h4><Timeline value={character[key]} /></section> : null)}</div></details>)}{!projection.characters.length && <p className="catalog-no-results">暂无角色分析</p>}</>}
          {view === "settings" && projection.settings.some(c => Number(c.schema_version) >= 2) ? <ChronicleEntities kind="settings" value={projection.settings} plot={plot} characters={projection.characters} settings={projection.settings} readOnly /> : view === "settings" && <>{projection.settings.map((setting, index) => <details className="catalog-entity" key={index}><summary><Layers3 size={15} /><strong>{str(setting.title) || "未命名设定"}</strong><Chapter value={setting.first_chapter} /><ChevronRight size={14} /></summary><div className="catalog-entity-body">{str(setting.category) && <span className="tag">{categoryLabel(setting.category)}</span>}{str(setting.content) && <p>{str(setting.content)}</p>}{str(setting.first_introduced_at) && <p className="catalog-story-time">{str(setting.first_introduced_at)}</p>}<Timeline value={setting.updates} /></div></details>)}{!projection.settings.length && <p className="catalog-no-results">暂无设定分析</p>}</>}
          {view === "era_background" && <EraBackgroundEditor value={projection.era_background} characters={projection.characters} />}
          {view === "language" && <LanguageAnalysisEditor value={projection.style} rhythm={projection.rhythm} opening={projection.opening} characters={projection.characters} />}
        </div>}
      </section> : <div className="catalog-empty"><Sparkles size={25} /><h3>暂无参考分析</h3>{selected && <button className="button secondary" onClick={() => navigate("reference", selected)}>开始分析<ArrowUpRight size={14} /></button>}</div>}
    </>}
  </div></NarrativeProvider>;
}
