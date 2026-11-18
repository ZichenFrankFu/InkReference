import { useMemo, useState } from "react";
import { ChevronDown, Plus, Trash2 } from "lucide-react";
import { ProgressInput } from "./NarrativePosition";
import { record, stringValue, type ReferenceRecord } from "./reference-types";
import "./manual-stage-form.css";

const labels: Record<string, string> = {
  plot: "剧情大纲", logline: "剧情梗概", events: "事件", plotlines: "情节线", id: "ID", name: "名称", title: "标题", theme: "主题", description: "描述", narrative_progress: "叙事进度", story_time: "故事中时间", participants: "关联角色", setting_ids: "关联设定", causes: "前因事件", results: "结果事件", plotline_ids: "情节线", volume: "分卷", evidence: "原文片段", time_nodes: "时间节点", status: "状态", event_ids: "关联事件", characters: "角色", aliases: "别名", role_tag: "角色定位", temperament: "性格基调", visual_design: "视觉设计", initial_state: "初始状态", gender: "性别", age: "年龄", identity: "身份", ability: "能力", personality: "性格", appearance: "外貌", relationships: "人际关系", character_id: "关联角色", relationship: "关系", snapshots: "阶段状态", narrative_range: "叙事进度范围", start: "开始进度", end: "结束进度", settings: "设定", category: "类型", content: "具体内容", earlier_setting_id: "追加至设定", style: "语言风格", focalization: "叙述视角", naming_style: "取名风格与规律", rhetoric: "修辞习惯", examples: "示例", dialogue_style: "对白风格", narrative_composition: "叙述构成", kind: "类型", percentage: "占比 (%)", assessment: "判断", rhythm: "剧情分析", chapter_analysis: "章节分析", climax: "章节高潮", payoffs: "爽点", foreshadowing: "伏笔", pacing: "节奏", emotion: "情绪", opening: "开篇", pattern: "开篇方式", observations: "开篇记录", finding: "发现", era_background: "时代背景事件", items: "事件条目", approximate_time: "大致时间", appearances: "叙事中出现点", character_ids: "关联角色", context: "语境", usage: "使用方式",
};
const enumLabels: Record<string, string> = { open: "开启", progressing: "推进", resolved: "解决", abandoned: "放弃", power_system: "力量体系", factions: "势力组织", geography: "地理", social_rules: "社会规则", history: "历史背景", worldview: "世界观", other: "其他", action: "动作叙述", description: "外部描写", psychology: "心理呈现", exposition: "背景说明", meme: "网络热梗", real_world_event: "现实事件", dialogue: "对白", thought: "心理", narration: "叙述", reference: "引用" };
const fixedFields = new Set(["schema_version", "source_version_id", "revealed_at", "origin"]);
const typeOf = (schema: ReferenceRecord) => Array.isArray(schema.type) ? schema.type.find(item => item !== "null") : schema.type;
const allowsNull = (schema: ReferenceRecord) => Array.isArray(schema.type) && schema.type.includes("null");
const fieldLabel = (key: string) => labels[key] || key.replaceAll("_", " ");

function initialValue(schema: ReferenceRecord, sourceVersionId = "", field = ""): unknown {
  if (field === "source_version_id") return sourceVersionId;
  if (field === "revealed_at") return { chapter: null, offset: null };
  if (field === "origin") return "manual";
  if (field === "id") return `manual_${crypto.randomUUID()}`;
  if (Array.isArray(schema.enum) && schema.enum.length === 1) return schema.enum[0];
  if (allowsNull(schema)) return null;
  const type = typeOf(schema);
  if (type === "object") return Object.fromEntries(Object.entries(record(schema.properties)).map(([key, child]) => [key, initialValue(record(child), sourceVersionId, key)]));
  if (type === "array") {
    if (field === "narrative_composition") return ["action", "description", "psychology", "exposition"].map(kind => ({ ...record(initialValue(record(schema.items), sourceVersionId)), kind }));
    return [];
  }
  if (type === "number" || type === "integer") return typeof schema.minimum === "number" ? schema.minimum : 0;
  if (type === "boolean") return false;
  return "";
}

/** Generate one template or array item only when a user adds it; IDs never reroll during render. */
export function schemaValue(schema: ReferenceRecord, sourceVersionId = ""): unknown {
  return initialValue(schema, sourceVersionId);
}

type Choice = { id: string; label: string };
type Context = { sourceVersionId: string; disabled: boolean; choices: Record<string, Choice[]>; initialRelationships?: unknown[] };
const relationCollection: Record<string, string> = { participants: "characters", character_id: "characters", character_ids: "characters", event_ids: "events", causes: "events", results: "events", setting_ids: "settings", earlier_setting_id: "settings", plotline_ids: "plotlines" };
function relationChoices(value: unknown): Record<string, Choice[]> {
  const result: Record<string, Choice[]> = {};
  const walk = (node: unknown, collection = "") => {
    if (Array.isArray(node)) { node.forEach(item => walk(item, collection)); return; }
    const obj = record(node);
    if (typeof obj.id === "string" && collection) {
      const list = result[collection] ||= [];
      if (!list.some(item => item.id === obj.id)) list.push({ id: obj.id, label: stringValue(obj.name || obj.title || obj.description) || obj.id });
    }
    Object.entries(obj).forEach(([key, item]) => { if (Array.isArray(item) || item && typeof item === "object") walk(item, key); });
  };
  walk(value);
  return result;
}

function ArrayField({ schema, value, onChange, field, label, path, depth, context }: { label: string; schema: ReferenceRecord; value: unknown; onChange: (value: unknown) => void; field: string; path: string; depth: number; context: Context }) {
  const rows = Array.isArray(value) ? value : [], itemSchema = record(schema.items), itemType = typeOf(itemSchema);
  const [open, setOpen] = useState(depth < 3), [page, setPage] = useState(0);
  const pageSize = 20, currentPage = Math.min(page, Math.max(0, Math.ceil(rows.length / pageSize) - 1)), start = currentPage * pageSize;
  const add = () => {
    let next = schemaValue(itemSchema, context.sourceVersionId);
    if (field === "snapshots") {
      const previous = record(rows.at(-1)), previousRange = record(previous.narrative_range);
      next = { ...record(next), relationships: structuredClone(Array.isArray(previous.relationships) ? previous.relationships : context.initialRelationships || []), narrative_range: { ...record(record(next).narrative_range), start: typeof previousRange.end === "number" ? previousRange.end : Number(previousRange.start || 0) } };
    }
    onChange([...rows, next]); setPage(Math.floor(rows.length / pageSize)); setOpen(true);
  };
  return <section className={`manual-array manual-array-${field}`}>
    <div className="manual-array-heading"><button type="button" className="manual-group-toggle" aria-expanded={open} onClick={() => setOpen(!open)}><ChevronDown size={14} /><strong>{label}</strong><span>{rows.length}</span></button><button type="button" className="icon-button" aria-label={`添加${label}`} title={`添加${label}`} disabled={context.disabled || rows.length >= Number(schema.maxItems || 10000)} onClick={add}><Plus size={15} /></button></div>
    {open && <div className="manual-array-items">{rows.slice(start, start + pageSize).map((item, visibleIndex) => {
      const index = start + visibleIndex, obj = record(item), key = typeof obj.id === "string" ? obj.id : `${path}.${index}`;
      return <section className="manual-array-item" key={key}><div className="manual-item-heading"><span>{stringValue(obj.name || obj.title) || `${fieldLabel(field)} ${index + 1}`}</span><button type="button" className="icon-button danger" aria-label={`删除${fieldLabel(field)} ${index + 1}`} title="删除" disabled={context.disabled} onClick={() => onChange(rows.filter((_, at) => at !== index))}><Trash2 size={14} /></button></div><SchemaField schema={itemSchema} value={item} onChange={next => onChange(rows.map((current, at) => at === index ? next : current))} field={itemType === "object" ? "" : field} path={`${path}.${index}`} depth={depth + 1} context={context} /></section>;
    })}{rows.length > pageSize && <div className="manual-array-pagination"><button type="button" className="button secondary" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>上一页</button><span>{currentPage + 1} / {Math.ceil(rows.length / pageSize)}</span><button type="button" className="button secondary" disabled={start + pageSize >= rows.length} onClick={() => setPage(currentPage + 1)}>下一页</button></div>}</div>}
  </section>;
}

function SchemaField({ schema, value, onChange, field, path, depth, context }: { schema: ReferenceRecord; value: unknown; onChange: (value: unknown) => void; field: string; path: string; depth: number; context: Context }) {
  if (fixedFields.has(field)) return null;
  const phaseLabels: Record<string, string> = { name: "阶段名称", identity: "该阶段身份变化", ability: "该阶段能力变化", personality: "该阶段性格变化", appearance: "该阶段外貌变化", visual_design: "该阶段视觉变化", relationships: "该阶段人际关系变化" };
  const type = typeOf(schema), label = path.includes(".snapshots.") && phaseLabels[field] ? phaseLabels[field] : path.includes(".initial_state.") && field === "relationships" ? "初始人际关系" : fieldLabel(field), options = Array.isArray(schema.enum) ? schema.enum : null;
  if (type === "array") return <ArrayField schema={schema} value={value} onChange={onChange} field={field} label={label} path={path} depth={depth} context={context} />;
  if (type === "object") {
    const obj = record(value), properties = Object.entries(record(schema.properties)).filter(([key]) => !fixedFields.has(key));
    const childContext = obj.initial_state ? { ...context, initialRelationships: Array.isArray(record(obj.initial_state).relationships) ? record(obj.initial_state).relationships as unknown[] : [] } : context;
    const content = <div className="manual-object-grid">{properties.map(([key, child]) => <SchemaField key={key} schema={record(child)} value={obj[key]} onChange={next => onChange({ ...obj, [key]: next })} field={key} path={`${path}.${key}`} depth={depth + 1} context={childContext} />)}</div>;
    return field ? <fieldset className={`manual-object manual-object-${field}`}><legend>{label}</legend>{content}</fieldset> : content;
  }
  if (field === "narrative_progress" || ["start", "end"].includes(field) && path.includes("narrative_range")) return <ProgressInput label={label} value={value} nullable={allowsNull(schema)} disabled={context.disabled} onChange={onChange} />;
  if (options) {
    if (options.length === 1) return null;
    return <label className="studio-field"><span>{label}</span><select aria-label={label} disabled={context.disabled} value={value == null ? "" : stringValue(value)} onChange={event => { const selected = options.find(item => item !== null && String(item) === event.target.value); onChange(selected === undefined ? allowsNull(schema) ? null : "" : selected); }}><option value="" disabled={!allowsNull(schema) && !options.includes("")}>选择{label}</option>{options.filter(item => item !== null && item !== "").map(item => <option value={String(item)} key={String(item)}>{enumLabels[String(item)] || String(item)}</option>)}</select></label>;
  }
  if (type === "number" || type === "integer") return <label className="studio-field"><span>{label}</span><input type="number" aria-label={label} value={value == null ? "" : Number(value)} min={typeof schema.minimum === "number" ? schema.minimum : undefined} max={typeof schema.maximum === "number" ? schema.maximum : undefined} step={type === "integer" ? 1 : "any"} disabled={context.disabled} onChange={event => onChange(event.target.value === "" ? allowsNull(schema) ? null : 0 : Number(event.target.value))} /></label>;
  if (type === "boolean") return <label className="manual-checkbox"><input type="checkbox" aria-label={label} checked={value === true} disabled={context.disabled} onChange={event => onChange(event.target.checked)} /><span>{label}</span></label>;
  const choices = context.choices[relationCollection[field]] || [], listId = `manual-options-${path.replace(/[^a-zA-Z0-9-]/g, "-")}`;
  const multiline = field === "evidence" || Number(schema.maxLength || 0) >= 10000 && !["name", "title"].includes(field);
  return <label className={`studio-field ${multiline ? "manual-field-wide" : ""} ${field === "id" ? "manual-stable-id" : ""}`}><span>{label}</span>{multiline ? <textarea aria-label={label} value={stringValue(value)} disabled={context.disabled} onChange={event => onChange(event.target.value === "" && allowsNull(schema) ? null : event.target.value)} maxLength={typeof schema.maxLength === "number" ? schema.maxLength : undefined} rows={field === "evidence" ? 3 : 2} /> : <><input aria-label={label} value={stringValue(value)} readOnly={field === "id"} disabled={context.disabled} list={choices.length ? listId : undefined} onChange={event => onChange(event.target.value === "" && allowsNull(schema) ? null : event.target.value)} maxLength={typeof schema.maxLength === "number" ? schema.maxLength : undefined} />{choices.length > 0 && <datalist id={listId}>{choices.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</datalist>}</>}</label>;
}

export default function ManualStageForm({ schema, value, onChange, disabled = false, sourceVersionId = "" }: { schema: ReferenceRecord; value: ReferenceRecord; onChange: (value: ReferenceRecord) => void; disabled?: boolean; sourceVersionId?: string }) {
  const choices = useMemo(() => relationChoices(value), [value]);
  return <div className="manual-stage-form"><SchemaField schema={schema} value={value} onChange={next => onChange(record(next))} field="" path="stage" depth={0} context={{ sourceVersionId, disabled, choices }} /></div>;
}
