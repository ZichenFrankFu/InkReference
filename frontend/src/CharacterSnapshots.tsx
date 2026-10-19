import { useEffect, useId, useState } from "react";
import { ChevronDown, Plus, Trash2, UsersRound } from "lucide-react";
import { record, records, stableId, stringList, stringValue, type ReferenceRecord } from "./reference-types";
import "./character-snapshots.css";
import { Busy } from "./ui";
import { RangeBadge, RangeFields } from "./NarrativePosition";

export const characterTemperaments = ["强大", "智谋", "利他", "共情", "利己", "执念", "反差", "宿命"];
type Props = {
  value: ReferenceRecord;
  characters?: ReferenceRecord[];
  events?: ReferenceRecord[];
  onChange: (value: ReferenceRecord) => void;
  readOnly?: boolean;
  identityOnly?: boolean;
  onCharacterSearch?: (query: string) => Promise<ReferenceRecord[]>;
};
const text = stringValue;

function Field({ label, value, onChange, readOnly, area = false, options, optional = false }: {
  label: string; value: unknown; onChange: (value: string) => void; readOnly?: boolean; area?: boolean; options?: string[]; optional?: boolean;
}) {
  const id = useId();
  return <label className={`character-field ${area ? "character-field-wide" : ""}`} htmlFor={id}><span>{label}{optional && <small>可选</small>}</span>{readOnly ? <div className="character-field-value">{text(value) || "—"}</div> : options ? <select id={id} aria-label={label} value={text(value)} onChange={event => onChange(event.target.value)}><option value="">未记录</option>{text(value) && !options.includes(text(value)) && <option value={text(value)}>{text(value)}</option>}{options.map(option => <option key={option}>{option}</option>)}</select> : area ? <textarea id={id} aria-label={label} rows={2} value={text(value)} onChange={event => onChange(event.target.value)} /> : <input id={id} aria-label={label} value={text(value)} onChange={event => onChange(event.target.value)} />}</label>;
}
function Relationships({ value, characters, currentId, onChange, readOnly, onCharacterSearch, title = "人际关系" }: {
  value: unknown; characters: ReferenceRecord[]; currentId: string; onChange: (value: ReferenceRecord[]) => void; readOnly?: boolean; onCharacterSearch?: (query: string) => Promise<ReferenceRecord[]>; title?: string;
}) {
  const rows = records(value);
  const [query, setQuery] = useState("");
  const [remote, setRemote] = useState<ReferenceRecord[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState("");
  useEffect(() => {
    if (!onCharacterSearch || !query.trim()) { setRemote([]); setSearching(false); setSearchError(""); return; }
    let active = true;
    const timer = window.setTimeout(() => { setSearching(true); setSearchError(""); void onCharacterSearch(query.trim()).then(next => { if (active) setRemote(next); }).catch(cause => { if (active) setSearchError(cause instanceof Error ? cause.message : "查找失败"); }).finally(() => { if (active) setSearching(false); }); }, 250);
    return () => { active = false; window.clearTimeout(timer); };
  }, [query, onCharacterSearch]);
  const pool = query.trim() && onCharacterSearch ? remote : characters;
  const selected = rows.map(row => text(row.character_id));
  const choices = [...pool.filter(character => !query.trim() || onCharacterSearch || `${text(character.name)} ${text(character.id)}`.includes(query)), ...characters.filter(character => selected.includes(text(character.id)) && !pool.some(candidate => candidate.id === character.id))].filter(character => text(character.id) && text(character.id) !== currentId);
  const update = (index: number, next: ReferenceRecord) => onChange(rows.map((row, i) => i === index ? next : row));
  return <section className="character-relationships"><div className="character-section-heading"><h4>{title}</h4>{!readOnly && <button className="text-button" type="button" onClick={() => onChange([...rows, { character_id: "", relationship: "" }])}><Plus size={13} />添加</button>}</div>{!readOnly && (characters.length > 8 || onCharacterSearch) && <input className="character-event-search" aria-label="搜索关联角色" placeholder="搜索角色" value={query} onChange={event => setQuery(event.target.value)} />}{searching && <Busy label="正在查找角色" compact />}{searchError && <div className="notice error" role="alert">{searchError}</div>}{rows.length ? <div className="character-relationship-list">{rows.map((row, index) => <div className="character-relationship-row" key={index}>{readOnly ? <><span className="character-related-name"><UsersRound size={13} />{text(choices.find(character => character.id === row.character_id)?.name) || text(row.character_id) || "未关联角色"}</span><span>{text(row.relationship) || "—"}</span></> : <><select aria-label={`人际关系 ${index + 1} 角色`} value={text(row.character_id)} onChange={event => update(index, { ...row, character_id: event.target.value })}><option value="">选择角色</option>{text(row.character_id) && !choices.some(character => character.id === row.character_id) && <option value={text(row.character_id)}>{text(row.character_id)}</option>}{choices.map(character => <option key={text(character.id)} value={text(character.id)}>{text(character.name) || text(character.id)}</option>)}</select><input aria-label={`人际关系 ${index + 1} 关系`} value={text(row.relationship)} onChange={event => update(index, { ...row, relationship: event.target.value })} placeholder="关系" /><button className="icon-button danger" type="button" aria-label={`删除人际关系 ${index + 1}`} onClick={() => onChange(rows.filter((_, i) => i !== index))}><Trash2 size={14} /></button></>}</div>)}</div> : <div className="character-empty">暂无人际关系</div>}</section>;
}
function EventLinks({ value, events, onChange, readOnly, minProgress = 0 }: { value: unknown; events: ReferenceRecord[]; onChange: (value: string[]) => void; readOnly?: boolean; minProgress?: number }) {
  const selected = stringList(value);
  const [query, setQuery] = useState("");
  const choices: ReferenceRecord[] = [...events.filter(event => readOnly || Number(event.narrative_progress ?? 0) >= minProgress || selected.includes(text(event.id))), ...selected.filter(id => !events.some(event => event.id === id)).map(id => ({ id, name: id }))];
  return <section className="character-event-links"><div className="character-section-heading"><h4>事件经历</h4>{!!selected.length && <span>{selected.length}</span>}</div>{readOnly ? (selected.length ? <div className="character-event-tags">{selected.map(id => <span key={id}>{text(choices.find(event => event.id === id)?.name) || id}</span>)}</div> : <div className="character-empty">暂无关联事件</div>) : <><input className="character-event-search" aria-label="搜索关联事件" placeholder="搜索事件" value={query} onChange={event => setQuery(event.target.value)} /><div className="character-event-choices">{choices.filter(event => !query || `${text(event.name)} ${text(event.id)}`.toLocaleLowerCase().includes(query.toLocaleLowerCase())).map(event => <label key={text(event.id)} className={Number(event.narrative_progress ?? minProgress) < minProgress ? "invalid-event" : ""}><input type="checkbox" disabled={Number(event.narrative_progress ?? minProgress) < minProgress && !selected.includes(text(event.id))} checked={selected.includes(text(event.id))} onChange={change => onChange(change.target.checked ? [...selected, text(event.id)] : selected.filter(id => id !== event.id))} /><span>{text(event.name) || text(event.id)}{Number(event.narrative_progress ?? minProgress) < minProgress && <small>早于上一阶段</small>}</span></label>)}{!choices.length && <div className="character-empty">暂无事件</div>}</div></>}</section>;
}
function Snapshot({ value, characters, events, currentId, onChange, onDelete, readOnly, onCharacterSearch, minEventProgress = 0 }: {
  value: ReferenceRecord; characters: ReferenceRecord[]; events: ReferenceRecord[]; currentId: string; onChange: (value: ReferenceRecord) => void; onDelete: () => void; readOnly?: boolean; onCharacterSearch?: (query: string) => Promise<ReferenceRecord[]>; minEventProgress?: number;
}) {
  const [expanded, setExpanded] = useState(true);
  const update = (key: string, next: unknown) => onChange({ ...value, [key]: next });
  return <article className="character-snapshot"><header><button type="button" className="character-snapshot-toggle" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}><ChevronDown size={15} /><span><strong>{text(value.name) || "阶段状态"}</strong><RangeBadge value={value} /></span></button>{!readOnly && <button type="button" className="icon-button danger" aria-label={`删除 ${text(value.name) || "阶段状态"}`} onClick={onDelete}><Trash2 size={15} /></button>}</header>{expanded && <div className="character-snapshot-content">{!readOnly && <><Field label="阶段名称" value={value.name} onChange={next => update("name", next)} /><RangeFields value={value} onChange={onChange} /></>}<div className="character-field-grid">{[["identity", "身份"], ["ability", "能力"], ["personality", "性格"], ["appearance", "外貌"], ["visual_design", "视觉元素设计"]].map(([key, label]) => <Field key={key} label={`该阶段${label}变化`} area value={value[key]} onChange={next => update(key, next)} readOnly={readOnly} />)}</div><Relationships title="该阶段人际关系变化" value={value.relationships} characters={characters} currentId={currentId} onChange={next => update("relationships", next)} readOnly={readOnly} onCharacterSearch={onCharacterSearch} /><EventLinks value={value.event_ids} events={events} minProgress={minEventProgress} onChange={next => update("event_ids", next)} readOnly={readOnly} /></div>}</article>;
}

export function CharacterSnapshots({ value, characters = [], events = [], onChange, readOnly = false, onCharacterSearch, identityOnly = false }: Props) {
  const initial = record(value.initial_state), snapshots = records(value.snapshots);
  const ordered = snapshots.map((snapshot, index) => ({ snapshot, index })).sort((a, b) => Number(record(a.snapshot.narrative_range).start ?? 0) - Number(record(b.snapshot.narrative_range).start ?? 0));
  let inheritedRelationships = records(initial.relationships);
  const shownSnapshots: { snapshot: ReferenceRecord; index: number; minEventProgress: number }[] = ordered.map(({ snapshot, index }, order) => { const relationships = Array.isArray(snapshot.relationships) ? records(snapshot.relationships) : inheritedRelationships; inheritedRelationships = relationships; return { snapshot: { ...snapshot, relationships }, index, minEventProgress: order ? Number(record(ordered[order - 1].snapshot.narrative_range).start ?? 0) : Number(initial.narrative_progress ?? 0) }; });
  const update = (key: string, next: unknown) => onChange({ ...value, [key]: next });
  const updateInitial = (key: string, next: unknown) => update("initial_state", { ...initial, [key]: next });
  const aliasesId = useId();
  const addSnapshot = () => {
    const last = shownSnapshots[shownSnapshots.length - 1]?.snapshot, previous = last || initial;
    update("snapshots", [...snapshots, {
      id: stableId("snapshot"), name: `阶段 ${snapshots.length + 1}`, narrative_range: { start: Number(record(last?.narrative_range).end ?? record(last?.narrative_range).start ?? initial.narrative_progress ?? 0), end: null }, story_time: null,
      event_ids: [], identity: "", ability: "", personality: "", appearance: "", visual_design: "", relationships: structuredClone(records(previous.relationships)),
    }]);
  };
  return <div className={`character-profile ${readOnly ? "read-only" : ""}`}><section className="character-initial-state"><div className="character-section-heading"><h3>{identityOnly ? "人物资料" : "初始状态"}</h3></div><div className="character-field-grid"><Field label="姓名" value={value.name} onChange={next => update("name", next)} readOnly={readOnly} /><Field label="角色定位" value={value.role_tag} onChange={next => update("role_tag", next)} readOnly={readOnly} />{readOnly ? <Field label="别名" value={stringList(value.aliases).join("、")} onChange={() => undefined} readOnly /> : <label className="character-field" htmlFor={aliasesId}><span>别名</span><input id={aliasesId} value={(Array.isArray(value.aliases) ? value.aliases.map(text) : stringList(value.aliases)).join("、")} onChange={event => update("aliases", event.target.value ? event.target.value.split(/[、,，]/).map(alias => alias.trim()) : [])} onBlur={() => update("aliases", stringList(value.aliases).map(alias => alias.trim()).filter(Boolean))} /></label>}{!identityOnly && <><Field label="性别" value={initial.gender} options={["男", "女", "特殊"]} onChange={next => updateInitial("gender", next || null)} readOnly={readOnly} /><Field label="年龄" value={initial.age} onChange={next => updateInitial("age", next)} readOnly={readOnly} /></>}<Field label="性格基调" value={value.temperament} options={characterTemperaments} onChange={next => update("temperament", next)} readOnly={readOnly} /><Field label="视觉元素设计" value={value.visual_design} area onChange={next => update("visual_design", next)} readOnly={readOnly} />{!identityOnly && [["identity", "身份"], ["ability", "能力"], ["personality", "性格"], ["appearance", "外貌"]].map(([key, label]) => <Field key={key} label={label} value={initial[key]} area onChange={next => updateInitial(key, next)} readOnly={readOnly} />)}</div>{!identityOnly && <Relationships title="初始人际关系" value={initial.relationships} characters={characters} currentId={text(value.id)} onChange={next => updateInitial("relationships", next)} readOnly={readOnly} onCharacterSearch={onCharacterSearch} />}</section>{!identityOnly && <section className="character-snapshots"><div className="character-section-heading"><h3>阶段状态</h3>{!readOnly && <button className="button secondary" type="button" onClick={addSnapshot}><Plus size={14} />添加状态</button>}</div>{snapshots.length ? shownSnapshots.map(({ snapshot, index, minEventProgress }) => <Snapshot key={text(snapshot.id) || index} value={snapshot} minEventProgress={minEventProgress} characters={characters} events={events} currentId={text(value.id)} onChange={next => update("snapshots", snapshots.map((current, i) => i === index ? next : current))} onDelete={() => update("snapshots", snapshots.filter((_, i) => index !== i))} readOnly={readOnly} onCharacterSearch={onCharacterSearch} />) : <div className="character-empty">暂无阶段状态</div>}</section>}</div>;
}
export default CharacterSnapshots;
