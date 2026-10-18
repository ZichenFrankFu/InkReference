import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Check, ChevronDown, ChevronRight, Copy, Edit3, Plus, Save, Search, Trash2, X } from "lucide-react";
import { Busy, messageOf, Notice } from "./ui";
import { characterExperiences, chapterNumber, chapterTypes, record, records, referenceChunkLabel, roleTags, settingCategories, stringList, stringValue, type ReferenceChunkRange, type ReferenceRecord } from "./reference-types";

type Field = { key: string; label: string; area?: boolean; options?: string[][] | string[]; number?: boolean; lines?: boolean };
type FactField = { key: string; label: string; fields?: Field[] };
export type CollectionKind = "characters" | "settings" | "setting_features" | "raw_entries";
const facts: FactField[] = [
  { key: "appearance", label: "外貌" }, { key: "personality", label: "性格" }, { key: "experiences", label: "经历" },
];
const descriptions: Record<CollectionKind, { title: string; blank: ReferenceRecord; fields: Field[]; facts?: FactField[] }> = {
  characters: { title: "角色", blank: { name: "", intro: "", role_tag: "其他", appearance: [], personality: [], experiences: [], speech_samples: [] }, fields: [
    { key: "name", label: "姓名" }, { key: "role_tag", label: "角色定位", options: roleTags },
    { key: "intro", label: "人物简介", area: true }, { key: "first_seen_at", label: "故事中首次出现的时间" },
    { key: "first_chapter", label: "首次出现章节" }, { key: "appearance_chapters", label: "出场章节数", number: true },
    { key: "appearance_word_count", label: "出场篇幅（字）", number: true }, { key: "mentions", label: "提及次数", number: true },
    { key: "speech_samples", label: "语言样例（每行一条）", area: true, lines: true },
  ], facts },
  settings: { title: "设定", blank: { category: "worldview", title: "", content: "", updates: [] }, fields: [
    { key: "title", label: "名称" }, { key: "category", label: "类别", options: settingCategories },
    { key: "content", label: "内容", area: true }, { key: "first_introduced_at", label: "故事中引入时间" }, { key: "first_chapter", label: "首次出现章节" },
  ], facts: [{ key: "updates", label: "随章节演进" }] },
  setting_features: { title: "设定特征", blank: { category: "高概念", title: "", description: "" }, fields: [
    { key: "title", label: "名称" }, { key: "category", label: "类型", options: ["核心冲突", "高概念"] }, { key: "description", label: "描述", area: true },
  ] },
  raw_entries: { title: "文本条目", blank: { title: "", content: "" }, fields: [{ key: "title", label: "标题" }, { key: "content", label: "正文", area: true }] },
};
function choices(options: Field["options"]) { return (options || []).map(v => Array.isArray(v) ? v : [v, v]); }
export function ReferenceField({ field, value, onChange }: { field: Field; value: unknown; onChange: (value: unknown) => void }) {
  const id = `ref-${field.key}`;
  return <label className={`studio-field ref-field ${field.area ? "full" : ""}`}><span>{field.label}</span>{field.options ? <select aria-label={field.label} value={stringValue(value)} onChange={e => onChange(e.target.value)}>
    {!choices(field.options).some(([v]) => v === stringValue(value)) && <option value={stringValue(value)}>{stringValue(value) || "请选择"}</option>}{choices(field.options).map(([v, label]) => <option key={v} value={v}>{label}</option>)}
  </select> : field.area ? <textarea aria-label={field.label} rows={field.key === "content" ? 8 : 3} value={field.lines ? stringList(value).join("\n") : stringValue(value)} onChange={e => onChange(field.lines ? e.target.value.split("\n").filter(Boolean) : e.target.value)} /> : <input aria-label={field.label} id={id} type={field.number ? "number" : "text"} min={field.number ? 0 : undefined} value={stringValue(value)} onChange={e => onChange(field.number && e.target.value !== "" ? Number(e.target.value) : e.target.value)} />}</label>;
}
export function FactsEditor({ label, value, onChange, fields = [{ key: "chapter", label: "章节" }, { key: "text", label: "内容", area: true }] }: { label: string; value: unknown; onChange: (value: ReferenceRecord[]) => void; fields?: Field[] }) {
  const items = records(value);
  return <div className="ref-facts"><div className="ref-mini-heading"><strong>{label}</strong><button className="text-button" onClick={() => onChange([...items, Object.fromEntries(fields.map(f => [f.key, ""]))])}><Plus size={13} />添加</button></div>
    {items.map((item, i) => <div className="ref-fact-editor" key={i}><div className="ref-form-grid">{fields.map(field => <ReferenceField key={field.key} field={field} value={item[field.key]} onChange={value => onChange(items.map((r, j) => i === j ? { ...r, [field.key]: value } : r))} />)}</div><button className="icon-button danger-text" aria-label={`移除${label}第 ${i + 1} 条`} onClick={() => onChange(items.filter((_, j) => j !== i))}><Trash2 size={14} /></button></div>)}
  </div>;
}
export function FactView({ label, value }: { label: string; value: unknown }) {
  const items = records(value);
  if (!items.length) return null;
  return <div className="ref-fact-view"><h4>{label}</h4>{items.map((f, i) => <div key={i}><span className="ref-chapter-tag">{stringValue(f.chapter) || "未标章节"}</span><p>{stringValue(f.text || f.content)}</p></div>)}</div>;
}
export function CollectionEditor({ kind, value, onSave, busy, onDirty, onExtra, settingCollection = false, chunkRanges = [], plot }: { kind: CollectionKind; value: unknown; onSave: (items: ReferenceRecord[]) => Promise<void>; busy: boolean; onDirty?: (dirty: boolean) => void; onExtra?: (item: ReferenceRecord, index: number) => ReactNode; settingCollection?: boolean; chunkRanges?: ReferenceChunkRange[]; plot?: unknown }) {
  const config = descriptions[kind];
  const items = records(value);
  const [editing, setEditing] = useState<number | null>(null);
  const [draft, setDraft] = useState<ReferenceRecord>({});
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [view, setView] = useState("book");
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [localError, setLocalError] = useState("");
  useEffect(() => { onDirty?.(editing !== null); return () => onDirty?.(false); }, [editing, onDirty]);
  useEffect(() => { setEditing(null); setDraft({}); setQuery(""); setCategory(""); }, [kind]);
  const start = (index: number) => { setEditing(index); setDraft(structuredClone(index < items.length ? items[index] : config.blank)); setLocalError(""); };
  const save = async () => {
    try { const next = editing === items.length ? [...items, draft] : items.map((v, i) => i === editing ? draft : v); await onSave(next); setEditing(null); }
    catch (e) { setLocalError(messageOf(e)); }
  };
  const remove = async (index: number) => { try { await onSave(items.filter((_, i) => i !== index)); } catch (e) { setLocalError(messageOf(e)); } };
  const visible = items.map((v, i) => ({ value: v, index: i })).filter(({ value }) => (!query || JSON.stringify(value).toLocaleLowerCase().includes(query.toLocaleLowerCase())) && (!category || stringValue(value.category || value.role_tag) === category));
  const grouped = new Map<string, typeof visible>();
  for (const item of visible) {
    const v = item.value;
    if (view === "segment" && config.facts?.length && chunkRanges.length && editing !== item.index) {
      const occupied = chunkRanges.filter(range => {
        const first = chapterNumber(v.first_chapter);
        return (first !== null && first >= range.startChapter && first <= range.endChapter) || config.facts!.some(f => records(v[f.key]).some(fact => { const n = chapterNumber(fact.chapter); return n !== null && n >= range.startChapter && n <= range.endChapter; }));
      });
      if (occupied.length) {
        for (const range of occupied) {
          const projected = { ...v };
          for (const f of config.facts) projected[f.key] = records(v[f.key]).filter(fact => { const n = chapterNumber(fact.chapter); return n !== null && n >= range.startChapter && n <= range.endChapter; });
          grouped.set(range.title, [...(grouped.get(range.title) || []), { ...item, value: projected }]);
        }
        continue;
      }
    }
    const key = view === "segment" ? referenceChunkLabel(v.first_chapter || v.first_seen_at, chunkRanges) : kind === "settings" || kind === "setting_features" ? choices(config.fields.find(f => f.key === "category")?.options).find(([id]) => id === stringValue(v.category))?.[1] || stringValue(v.category) || "其他" : "";
    grouped.set(key, [...(grouped.get(key) || []), item]);
  }
  const fields = settingCollection && kind === "characters" ? [{ key: "name", label: "姓名" }, { key: "role", label: "定位" }, { key: "description", label: "描述", area: true }] : config.fields;
  return <section className="ref-section"><div className="ref-section-heading"><div><span className="eyebrow">{String(items.length).padStart(2, "0")} ITEMS</span><h3>{config.title}</h3></div><button className="button primary" onClick={() => start(items.length)} disabled={busy || editing !== null}><Plus size={15} />新增{config.title}</button></div>
    <Notice error>{localError}</Notice>
    <div className="ref-collection-tools"><label className="index-search"><Search size={15} /><input aria-label={`搜索${config.title}`} placeholder={`搜索${config.title}`} value={query} disabled={editing !== null} onChange={e => setQuery(e.target.value)} /></label>
      {(kind === "settings" || kind === "characters" || kind === "setting_features") && <select aria-label={`${config.title}筛选`} value={category} disabled={editing !== null} onChange={e => setCategory(e.target.value)}><option value="">全部{kind === "characters" ? "定位" : "类别"}</option>{choices(config.fields.find(f => f.key === (kind === "characters" ? "role_tag" : "category"))?.options).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select>}
      {!settingCollection && ["characters", "settings"].includes(kind) && <div className="ref-segment-control"><button className={view === "book" ? "active" : ""} onClick={() => setView("book")}>全书视图</button><button className={view === "segment" ? "active" : ""} onClick={() => setView("segment")}>分段视图</button></div>}
    </div>
    {editing === items.length && renderEdit()}
    {!visible.length && editing === null && <div className="ref-empty"><span>{query || category ? "没有匹配条目" : `还没有${config.title}`}</span><button className="text-button" onClick={() => start(items.length)}><Plus size={14} />手动添加</button></div>}
    {[...grouped].map(([group, entries]) => <div className="ref-card-group" key={group}>{group && <h4 className="ref-group-title">{group}<span>{entries.length}</span></h4>}{entries.map(({ value: item, index }) => editing === index ? <div key={index}>{renderEdit()}</div> : <article className="ref-entity-card" key={index}>
      <header><button className="ref-entity-title" onClick={() => setExpanded(prev => { const next = new Set(prev); next.has(index) ? next.delete(index) : next.add(index); return next; })} aria-expanded={expanded.has(index)}>{expanded.has(index) ? <ChevronDown size={16} /> : <ChevronRight size={16} />}<strong>{stringValue(item.name || item.title) || `未命名${config.title}`}</strong></button><div className="inline-actions">{item.role_tag ? <span className="ref-tag">{stringValue(item.role_tag)}</span> : null}<button className="icon-button" aria-label={`编辑${stringValue(item.name || item.title)}`} onClick={() => start(index)} disabled={busy || editing !== null}><Edit3 size={15} /></button><button className="icon-button danger-text" aria-label={`删除${stringValue(item.name || item.title)}`} onClick={() => void remove(index)} disabled={busy || editing !== null}><Trash2 size={15} /></button></div></header>
      <p className="ref-entity-intro">{stringValue(item.intro || item.content || item.description)}</p>
      <div className="ref-entity-meta">{item.first_chapter ? <span>{stringValue(item.first_chapter)}</span> : null}{item.first_seen_at || item.first_introduced_at ? <span>故事时间：{stringValue(item.first_seen_at || item.first_introduced_at)}</span> : null}{item.appearance_chapters ? <span>出场 {stringValue(item.appearance_chapters)} 章</span> : null}{item.appearance_word_count ? <span>{Number(item.appearance_word_count).toLocaleString()} 字</span> : null}</div>
      {expanded.has(index) && <div className="ref-entity-details">{config.facts?.map(f => <FactView key={f.key} label={f.label} value={kind === "characters" && f.key === "experiences" ? characterExperiences(item, plot) : item[f.key]} />)}{!!stringList(item.speech_samples).length && <div className="ref-fact-view"><h4>语言样例</h4>{stringList(item.speech_samples).map((s, i) => <blockquote key={i}>{s}</blockquote>)}</div>}{kind !== "raw_entries" && onExtra?.(item, index)}</div>}
      {kind === "raw_entries" && onExtra?.(item, index)}
    </article>)}</div>)}
  </section>;
  function renderEdit() { return <article className="ref-entity-card editing"><div className="ref-mini-heading"><strong>{editing === items.length ? `新增${config.title}` : `编辑${config.title}`}</strong><button className="icon-button" aria-label="取消编辑" onClick={() => setEditing(null)} disabled={busy}><X size={16} /></button></div><div className="ref-form-grid">{fields.map(field => <ReferenceField key={field.key} field={field} value={draft[field.key]} onChange={value => setDraft(d => ({ ...d, [field.key]: value }))} />)}</div>{(!settingCollection ? config.facts : [])?.map(f => <FactsEditor key={f.key} label={f.label} value={draft[f.key]} onChange={value => setDraft(d => ({ ...d, [f.key]: value }))} />)}<div className="ref-editor-actions"><button className="button secondary" onClick={() => setEditing(null)} disabled={busy}>取消</button><button className="button primary" onClick={() => void save()} disabled={busy || !stringValue(draft.name || draft.title).trim()}><Save size={14} />保存</button></div></article>; }
}

type EventPath = { epoch: number; period: number; event: number };
const eventFields: Field[] = [{ key: "name", label: "事件名称" }, { key: "subject", label: "主体" }, { key: "category", label: "类别" }, { key: "description", label: "事件描述", area: true }, { key: "time_marker", label: "故事时间" }, { key: "first_chapter", label: "首次出现章节" }, { key: "time_markers", label: "其他时间锚点（每行一个）", area: true, lines: true }];
export function PlotEditor({ value, onSave, busy, onDirty, summaryAction, chunkRanges = [] }: { value: unknown; onSave: (data: ReferenceRecord) => Promise<void>; busy: boolean; onDirty?: (dirty: boolean) => void; summaryAction?: ReactNode; chunkRanges?: ReferenceChunkRange[] }) {
  const plot = record(value);
  const epochs = records(plot.epochs);
  const [view, setView] = useState("chapter");
  const [editing, setEditing] = useState<EventPath | null>(null);
  const [draft, setDraft] = useState<ReferenceRecord>({});
  const [editLogline, setEditLogline] = useState(false);
  const [logline, setLogline] = useState("");
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("");
  useEffect(() => { onDirty?.(editing !== null || editLogline); return () => onDirty?.(false); }, [editing, editLogline, onDirty]);
  const save = async (next: ReferenceRecord) => { setError(""); try { await onSave(next); setEditing(null); setEditLogline(false); } catch (e) { setError(messageOf(e)); } };
  const changeEvent = () => {
    if (!editing) return;
    const next = structuredClone(epochs); const periods = records(next[editing.epoch].periods); const events = records(periods[editing.period].events);
    if (editing.event >= events.length) events.push(draft); else events[editing.event] = draft;
    periods[editing.period].events = events; next[editing.epoch].periods = periods;
    void save({ ...plot, epochs: next });
  };
  const remove = (ei: number, pi?: number, vi?: number) => {
    const next = structuredClone(epochs);
    if (pi === undefined) next.splice(ei, 1); else { const ps = records(next[ei].periods); if (vi === undefined) ps.splice(pi, 1); else ps[pi].events = records(ps[pi].events).filter((_, i) => i !== vi); next[ei].periods = ps; }
    void save({ ...plot, epochs: next });
  };
  const all = epochs.flatMap((ep, ei) => records(ep.periods).flatMap((p, pi) => records(p.events).map((e, vi) => ({ event: e, epoch: ei, period: pi, index: vi, epochTitle: stringValue(ep.title), periodTitle: stringValue(p.time) }))));
  const visible = all.filter(({ event }) => !filter || JSON.stringify(event).toLocaleLowerCase().includes(filter.toLocaleLowerCase()));
  const ordered = useMemo(() => [...visible].sort((a, b) => {
    const compared = view === "timeline"
      ? stringValue(a.event.time_marker || stringList(a.event.time_markers)[0] || a.periodTitle).localeCompare(stringValue(b.event.time_marker || stringList(b.event.time_markers)[0] || b.periodTitle), "zh-Hans-CN", { numeric: true })
      : (chapterNumber(a.event.first_chapter) || 0) - (chapterNumber(b.event.first_chapter) || 0);
    return compared || a.epoch - b.epoch || a.period - b.period || a.index - b.index;
  }), [visible, view]);
  return <section className="ref-section"><div className="ref-section-heading"><div><span className="eyebrow">{all.length} EVENTS · {epochs.length} EPOCHS</span><h3>剧情大纲</h3></div><div className="inline-actions">{summaryAction}<button className="button secondary" disabled={busy || !!editing} onClick={() => void save({ ...plot, epochs: [...epochs, { title: `新篇章 ${epochs.length + 1}`, periods: [] }] })}><Plus size={14} />添加篇章</button></div></div><Notice error>{error}</Notice>
    <div className="ref-logline"><div className="ref-mini-heading"><strong>一句话梗概</strong><button className="icon-button" aria-label="编辑一句话梗概" onClick={() => { setLogline(stringValue(plot.logline)); setEditLogline(true); }} disabled={busy || !!editing}><Edit3 size={14} /></button></div>{editLogline ? <><textarea aria-label="一句话梗概" value={logline} onChange={e => setLogline(e.target.value)} rows={3} /><div className="ref-editor-actions"><button className="text-button" onClick={() => setEditLogline(false)}>取消</button><button className="button primary" disabled={busy} onClick={() => void save({ ...plot, logline })}><Save size={14} />保存</button></div></> : <p>{stringValue(plot.logline) || "尚未填写"}</p>}</div>
    <div className="ref-collection-tools"><label className="index-search"><Search size={15} /><input aria-label="搜索剧情事件" placeholder="搜索事件、主体、时间" value={filter} onChange={e => setFilter(e.target.value)} /></label><div className="ref-segment-control"><button className={view === "chapter" ? "active" : ""} onClick={() => setView("chapter")}>分段视图</button><button className={view === "timeline" ? "active" : ""} onClick={() => setView("timeline")}>全书时间线</button><button className={view === "structure" ? "active" : ""} onClick={() => setView("structure")}>篇章结构</button></div></div>
    {view === "structure" ? <div className="ref-outline-tree">{epochs.map((ep, ei) => <section key={ei}><div className="ref-mini-heading"><strong>{stringValue(ep.title) || `篇章 ${ei + 1}`}</strong><div className="inline-actions"><button className="text-button" disabled={busy || !!editing} onClick={() => { const next = structuredClone(epochs); next[ei].periods = [...records(ep.periods), { time: "新时段", events: [] }]; void save({ ...plot, epochs: next }); }}><Plus size={13} />时段</button><button className="icon-button danger-text" aria-label={`删除篇章 ${ei + 1}`} onClick={() => remove(ei)} disabled={busy || !!editing}><Trash2 size={14} /></button></div></div>{records(ep.periods).map((p, pi) => <div className="ref-outline-period" key={pi}><div className="ref-mini-heading"><span>{stringValue(p.time) || "未命名时段"}</span><div className="inline-actions"><button className="text-button" disabled={busy || !!editing} onClick={() => { setEditing({ epoch: ei, period: pi, event: records(p.events).length }); setDraft({ name: "", description: "", subject: "", category: "", time_marker: stringValue(p.time), first_chapter: "" }); }}><Plus size={13} />事件</button><button className="icon-button danger-text" aria-label="删除时段" disabled={busy || !!editing} onClick={() => remove(ei, pi)}><Trash2 size={13} /></button></div></div>{records(p.events).map((ev, vi) => renderEvent(ev, { epoch: ei, period: pi, event: vi }))}{editing?.epoch === ei && editing.period === pi && editing.event >= records(p.events).length && renderEditing()}</div>)}</section>)}</div> : <div className="ref-timeline">{ordered.map(({ event, epoch, period, index }) => renderEvent(event, { epoch, period, event: index }))}</div>}
    {!all.length && view !== "structure" && <div className="ref-empty">还没有剧情事件<button className="text-button" onClick={() => setView("structure")}>编辑篇章结构</button></div>}
  </section>;
  function renderEditing() { return <div className="ref-event-edit"><div className="ref-form-grid">{eventFields.map(field => <ReferenceField key={field.key} field={field} value={draft[field.key]} onChange={value => setDraft(d => ({ ...d, [field.key]: value }))} />)}</div><div className="ref-editor-actions"><button className="button secondary" disabled={busy} onClick={() => setEditing(null)}>取消</button><button className="button primary" disabled={busy || !stringValue(draft.name).trim()} onClick={changeEvent}><Save size={14} />保存事件</button></div></div>; }
  function renderEvent(event: ReferenceRecord, path: EventPath) { const key = `${path.epoch}-${path.period}-${path.event}`; return <article className="ref-event" key={key}>{editing?.epoch === path.epoch && editing.period === path.period && editing.event === path.event ? renderEditing() : <><div className="ref-event-anchor"><span>{view === "timeline" ? stringValue(event.time_marker || stringList(event.time_markers)[0]) || "时间未详" : stringValue(event.first_chapter) || "未标章节"}</span><i /></div><div className="ref-event-body"><header><strong>{stringValue(event.name) || "未命名事件"}</strong><div className="inline-actions"><button className="icon-button" aria-label={`编辑事件${stringValue(event.name)}`} disabled={busy || !!editing} onClick={() => { setEditing(path); setDraft(structuredClone(event)); }}><Edit3 size={14} /></button><button className="icon-button danger-text" aria-label={`删除事件${stringValue(event.name)}`} disabled={busy || !!editing} onClick={() => remove(path.epoch, path.period, path.event)}><Trash2 size={14} /></button></div></header><p>{stringValue(event.description)}</p><div className="ref-tags">{[event.subject, event.category, event.time_marker, ...stringList(event.time_markers)].map(stringValue).filter(Boolean).map((v, i) => <span className="ref-tag" key={i}>{v}</span>)}{view === "chapter" && <span className="ref-tag">{referenceChunkLabel(event.first_chapter, chunkRanges)}</span>}</div></div></>}</article>; }
}

const metrics = [
  ["avg_sentence_length", "平均句长", "字", 60],
  ["dialogue_ratio", "对话占比", "", 1], ["description_density", "描写密度", "", 1],
  ["rhetoric_frequency", "修辞频率", "/千字", 20], ["payoff_density", "爽点密度", "/万字", 10],
  ["info_density", "信息密度", "", 1], ["hook_density", "钩子密度", "/章", 5],
] as const;
export function StyleEditor({ value, onSave, busy, onDirty }: { value: unknown; onSave?: (data: ReferenceRecord) => Promise<void>; busy?: boolean; onDirty?: (dirty: boolean) => void }) {
  const data = record(value);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<ReferenceRecord>({});
  const [error, setError] = useState("");
  useEffect(() => { onDirty?.(editing); return () => onDirty?.(false); }, [editing, onDirty]);
  const d = editing ? draft : data;
  return <section className="ref-feature-panel"><div className="ref-mini-heading"><h3>风格指纹</h3>{onSave && !editing && <button className="text-button" disabled={busy} onClick={() => { setDraft(structuredClone(data)); setEditing(true); }}><Edit3 size={14} />编辑</button>}</div><Notice error>{error}</Notice>
    <div className="ref-metric-grid">{metrics.map(([key, label, unit, max]) => <div className="ref-metric" key={key}><span>{label}</span>{editing ? <input aria-label={label} type="number" step="0.01" min="0" max={max === 1 ? 1 : undefined} value={stringValue(d[key])} onChange={e => setDraft(v => ({ ...v, [key]: e.target.value === "" ? null : Number(e.target.value) }))} /> : <strong>{typeof d[key] === "number" ? max === 1 ? `${(Number(d[key]) * 100).toFixed(0)}%` : Number(d[key]).toFixed(1) : "—"}<small>{unit}</small></strong>}<div className="ref-metric-track"><i style={{ width: `${Math.min(100, Math.max(0, Number(d[key] || 0) / max * 100))}%` }} /></div></div>)}</div>
    <div className="ref-pacing"><strong>节奏分布</strong><div>{[["fast", "快"], ["medium", "中"], ["slow", "慢"]].map(([key, label]) => <label key={key}><span>{label}</span>{editing ? <input aria-label={`${label}节奏占比`} type="number" min="0" max="1" step="0.01" value={stringValue(record(d.pacing_profile)[key])} onChange={e => setDraft(v => ({ ...v, pacing_profile: { ...record(v.pacing_profile), [key]: Number(e.target.value) } }))} /> : <b>{Math.round(Number(record(d.pacing_profile)[key] || 0) * 100)}%</b>}</label>)}</div></div>
    <div className="ref-punctuation"><strong>标点使用 · 每千字</strong>{[["ellipsis", "省略号"], ["dash", "破折号"], ["exclamation", "感叹号"], ["question", "问号"], ["comma", "逗号"]].map(([key, label]) => <span key={key}>{label} {editing ? <input aria-label={`${label}频率`} type="number" min="0" step="0.01" value={stringValue(record(d.punctuation_profile)[key])} onChange={e => setDraft(v => ({ ...v, punctuation_profile: { ...record(v.punctuation_profile), [key]: Number(e.target.value) } }))} /> : <b>{typeof record(d.punctuation_profile)[key] === "number" ? Number(record(d.punctuation_profile)[key]).toFixed(1) : "—"}</b>}</span>)}</div>
    {!!records(d.chapter_signals).length && <details className="ref-signals"><summary>每章语义特征 · {records(d.chapter_signals).length} 章</summary>{records(d.chapter_signals).map((s, i) => <div key={i}><span className="ref-chapter-tag">{stringValue(s.chapter)}</span><div><strong>{stringList(s.chapter_types).join(" · ")}</strong><p>{stringValue(s.summary)}</p><small>信息密度 {typeof s.info_density === "number" ? Number(s.info_density).toFixed(2) : "—"} · 爽点 {records(s.payoffs).length} · 钩子 {records(s.hooks).length}</small>{records(s.payoffs).map((p, j) => <p className="ref-signal-detail" key={`p${j}`}>爽点 · {stringValue(p.type)} {stringValue(p.plot)}</p>)}{records(s.hooks).map((h, j) => <p className="ref-signal-detail" key={`h${j}`}>钩子 · {stringValue(h.position)} {stringValue(h.content)}</p>)}</div></div>)}</details>}
    {editing && <div className="ref-editor-actions"><button className="button secondary" disabled={busy} onClick={() => setEditing(false)}>取消</button><button className="button primary" disabled={busy} onClick={async () => { try { await onSave?.(draft); setEditing(false); } catch (e) { setError(messageOf(e)); } }}><Save size={14} />保存风格</button></div>}
  </section>;
}
export function RhythmEditor({ value, opening, onSave, onSaveOpening, busy, onDirty }: { value: unknown; opening: unknown; onSave: (data: ReferenceRecord) => Promise<void>; onSaveOpening: (data: ReferenceRecord) => Promise<void>; busy: boolean; onDirty?: (dirty: boolean) => void }) {
  const data = record(value);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<ReferenceRecord>({});
  const [error, setError] = useState("");
  const [openingEdit, setOpeningEdit] = useState(false);
  const [openingText, setOpeningText] = useState("");
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  useEffect(() => { onDirty?.(editing || openingEdit); return () => onDirty?.(false); }, [editing, openingEdit, onDirty]);
  const d = editing ? draft : data;
  const chapterFeatures: ReferenceRecord[] = records(d.chapter_features).length ? records(d.chapter_features) : records(d.chapter_signals).map(s => ({ ...s, types: s.chapter_types }));
  const update = (i: number, patch: ReferenceRecord) => setDraft(v => ({ ...v, chapter_features: chapterFeatures.map((c, j) => i === j ? { ...c, ...patch } : c) }));
  const pattern = stringValue(record(opening).pattern || record(opening).opening_pattern || record(opening).description || data.opening_pattern || opening);
  return <><section className="ref-feature-panel"><div className="ref-mini-heading"><h3>开篇模式</h3><button className="text-button" disabled={busy || editing} onClick={() => { setOpeningText(pattern); setOpeningEdit(true); }}><Edit3 size={14} />编辑</button></div>{openingEdit ? <><textarea aria-label="开篇模式" rows={4} value={openingText} onChange={e => setOpeningText(e.target.value)} /><div className="ref-editor-actions"><button className="button secondary" onClick={() => setOpeningEdit(false)}>取消</button><button className="button primary" disabled={busy} onClick={async () => { try { await onSaveOpening({ ...record(opening), pattern: openingText }); setOpeningEdit(false); } catch (e) { setError(messageOf(e)); } }}>保存</button></div></> : <p className="ref-opening">{pattern || "尚未提取开篇模式"}</p>}</section>
    <section className="ref-feature-panel"><div className="ref-mini-heading"><h3>节奏与章节特征</h3>{!editing && <button className="text-button" disabled={busy || openingEdit} onClick={() => { setDraft({ ...structuredClone(data), chapter_features: structuredClone(chapterFeatures) }); setEditing(true); }}><Edit3 size={14} />编辑</button>}</div><Notice error>{error}</Notice><div className="ref-rhythm-stats"><span>覆盖 <b>{stringValue(record(d.coverage).chapters) || chapterFeatures.length}</b> 章</span><span>高潮 <b>{Array.isArray(d.climax_positions) ? d.climax_positions.length : 0}</b></span><span>爽点 <b>{records(d.shuangdian).length}</b></span><span>钩子 <b>{chapterFeatures.reduce((n, c) => n + records(c.hooks).length, 0)}</b></span></div>
      {!!chapterFeatures.length && <div className="ref-rhythm-chart" role="img" aria-label="章节信息密度图谱">{chapterFeatures.map((c, i) => <button key={i} title={`第 ${stringValue(c.chapter)} 章 · 信息密度 ${Number(c.info_density || 0).toFixed(2)}`} aria-label={`查看第 ${stringValue(c.chapter)} 章特征`} onClick={() => setExpanded(prev => new Set([...prev, i]))}><i style={{ height: `${Math.max(4, Number(c.info_density || 0) * 100)}%` }} /><small>{chapterFeatures.length < 35 ? stringValue(c.chapter) : i % 10 === 0 ? stringValue(c.chapter) : ""}</small></button>)}</div>}
      <div className="ref-chapter-features">{chapterFeatures.map((c, i) => <article key={i}><header><button className="ref-chapter-feature-title" onClick={() => setExpanded(prev => { const n = new Set(prev); n.has(i) ? n.delete(i) : n.add(i); return n; })}>{expanded.has(i) ? <ChevronDown size={14} /> : <ChevronRight size={14} />}<strong>第 {stringValue(c.chapter)} 章</strong></button><div className="ref-tags">{stringList(c.types || c.chapter_types).map(t => <span className="ref-tag" key={t}>{t}</span>)}</div><span className="ref-density">{Number(c.info_density || 0).toFixed(2)}</span></header>{editing ? <div className="ref-rhythm-edit"><div className="ref-type-picker">{chapterTypes.map(type => <button key={type} className={stringList(c.types).includes(type) ? "active" : ""} onClick={() => update(i, { types: stringList(c.types).includes(type) ? stringList(c.types).filter(v => v !== type) : [...stringList(c.types), type] })}>{type}</button>)}</div><label className="studio-field"><span>信息密度</span><input aria-label={`第 ${stringValue(c.chapter)} 章信息密度`} type="range" min="0" max="1" step="0.01" value={Number(c.info_density || 0)} onChange={e => update(i, { info_density: Number(e.target.value) })} /></label><textarea aria-label={`第 ${stringValue(c.chapter)} 章摘要`} value={stringValue(c.summary)} onChange={e => update(i, { summary: e.target.value })} rows={3} /><FactsEditor label="钩子" value={c.hooks} onChange={hooks => update(i, { hooks })} fields={[{ key: "position", label: "位置", options: ["章首", "段中", "章末"] }, { key: "content", label: "内容", area: true }]} /><FactsEditor label="爽点" value={c.payoffs} onChange={payoffs => update(i, { payoffs })} fields={[{ key: "type", label: "类型" }, { key: "plot", label: "情节", area: true }]} /></div> : <><p>{stringValue(c.summary)}</p>{expanded.has(i) && <div className="ref-chapter-signals">{records(c.hooks).map((h, j) => <p key={j}><span className="ref-tag">钩子 · {stringValue(h.position)}</span>{stringValue(h.content)}</p>)}{records(c.payoffs).map((p, j) => <p key={`p${j}`}><span className="ref-tag">爽点 · {stringValue(p.type)}</span>{stringValue(p.plot)}</p>)}</div>}</>}</article>)}</div>
      {!!records(d.shuangdian).length && <div className="ref-fact-view"><h4>爽点分布</h4>{records(d.shuangdian).map((s, i) => <div key={i}><span className="ref-chapter-tag">第 {stringValue(s.chapter)} 章</span><p>{stringValue(s.type)} {stringValue(s.plot)}</p></div>)}</div>}
      {editing && <FactsEditor label="节奏分段" value={d.pacing_segments} onChange={pacing_segments => setDraft(v => ({ ...v, pacing_segments }))} fields={[{ key: "start", label: "起始章节", number: true }, { key: "end", label: "结束章节", number: true }, { key: "pacing", label: "节奏", options: [["fast", "快"], ["medium", "中"], ["slow", "慢"]] }, { key: "avg_info_density", label: "平均信息密度", number: true }]} />}
      {!editing && !!records(d.pacing_segments).length && <div className="ref-pacing-segments">{records(d.pacing_segments).map((p, i) => <div key={i}><span>第 {stringValue(p.start)}–{stringValue(p.end)} 章</span><strong>{({ fast: "快节奏", medium: "中节奏", slow: "慢节奏" } as Record<string, string>)[stringValue(p.pacing)] || stringValue(p.pacing)}</strong><small>信息密度 {Number(p.avg_info_density || 0).toFixed(2)}</small></div>)}</div>}
      {!chapterFeatures.length && !editing && <div className="ref-empty">尚无章节节奏数据</div>}
      {editing && <><button className="text-button" onClick={() => setDraft(v => ({ ...v, chapter_features: [...chapterFeatures, { chapter: chapterFeatures.length + 1, types: [], summary: "", info_density: 0, hooks: [], payoffs: [] }] }))}><Plus size={14} />新增章节特征</button><div className="ref-editor-actions"><button className="button secondary" disabled={busy} onClick={() => setEditing(false)}>取消</button><button className="button primary" disabled={busy} onClick={async () => { try { await onSave(draft); setEditing(false); } catch (e) { setError(messageOf(e)); } }}><Save size={14} />保存节奏</button></div></>}
    </section></>;
}

export function PromptPanel({ prompt, busy, onCopy, children }: { prompt: string; busy?: boolean; onCopy?: () => void; children?: ReactNode }) {
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState("");
  const copy = async () => { try { await navigator.clipboard.writeText(prompt); setCopied(true); onCopy?.(); window.setTimeout(() => setCopied(false), 1800); } catch { setError("无法复制，请在下方选中提示词手动复制。"); } };
  return <div className="ref-prompt-panel"><div className="ref-mini-heading"><strong>提示词</strong><button className="button secondary" onClick={() => void copy()} disabled={!prompt || busy}>{copied ? <Check size={14} /> : <Copy size={14} />}{copied ? "已复制" : "复制提示词"}</button></div>{busy ? <Busy label="正在准备提示词" compact /> : <details><summary>展开完整提示词</summary><textarea aria-label="完整提示词" readOnly value={prompt} rows={8} /></details>}<Notice error>{error}</Notice>{children}</div>;
}
