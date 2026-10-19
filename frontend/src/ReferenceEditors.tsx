import { useEffect, useState, type ReactNode } from "react";
import { Check, ChevronDown, ChevronRight, Copy, Edit3, Plus, Save, Search, Trash2, X } from "lucide-react";
import { Busy, messageOf, Notice } from "./ui";
import { characterExperiences, chapterNumber, records, referenceChunkLabel, roleTags, settingCategories, stringList, stringValue, type ReferenceChunkRange, type ReferenceRecord } from "./reference-types";

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

export function PromptPanel({ prompt, busy, onCopy, children }: { prompt: string; busy?: boolean; onCopy?: () => void; children?: ReactNode }) {
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState("");
  const copy = async () => { try { await navigator.clipboard.writeText(prompt); setCopied(true); onCopy?.(); window.setTimeout(() => setCopied(false), 1800); } catch { setError("无法复制，请在下方选中提示词手动复制。"); } };
  return <div className="ref-prompt-panel"><div className="ref-mini-heading"><strong>提示词</strong><button className="button secondary" onClick={() => void copy()} disabled={!prompt || busy}>{copied ? <Check size={14} /> : <Copy size={14} />}{copied ? "已复制" : "复制提示词"}</button></div>{busy ? <Busy label="正在准备提示词" compact /> : <details><summary>展开完整提示词</summary><textarea aria-label="完整提示词" readOnly value={prompt} rows={8} /></details>}<Notice error>{error}</Notice>{children}</div>;
}
