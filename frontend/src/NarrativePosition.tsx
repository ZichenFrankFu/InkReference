import { createContext, useContext, useState, type ReactNode } from "react";
import { record, stringValue, type ReferenceRecord } from "./reference-types";
import "./narrative-position.css";

export type NarrativeContextValue = { chapterCount: number; chapterNumbers?: number[] };
const NarrativeContext = createContext<NarrativeContextValue>({ chapterCount: 0 });
export function NarrativeProvider({ chapterCount = 0, chapterNumbers, children }: NarrativeContextValue & { children: ReactNode }) {
  return <NarrativeContext.Provider value={{ chapterCount: Math.max(0, Math.trunc(chapterCount)), chapterNumbers }}>{children}</NarrativeContext.Provider>;
}
export const useNarrativeContext = () => useContext(NarrativeContext);
export function progressLabel(value: unknown) {
  const number = typeof value === "number" ? value : typeof value === "string" && value.trim() ? Number(value) : NaN;
  return Number.isFinite(number) ? `${Number(number.toFixed(2))}%` : "—";
}
export function narrativeLabel(value: unknown, chapterCount: number) {
  return typeof value === "number" && Number.isFinite(value) && chapterCount > 0 ? `第 ${Number((value * chapterCount / 100).toFixed(2))} / ${chapterCount} 章` : progressLabel(value);
}
export function PositionBadge({ value }: { value: ReferenceRecord }) {
  const { chapterCount } = useNarrativeContext();
  const time = typeof value.story_time === "string" ? value.story_time : stringValue(record(value.story_time).label);
  return <div className="narrative-position"><span className="narrative-progress">{narrativeLabel(value.narrative_progress, chapterCount)}</span>{time && <span>{time}</span>}</div>;
}
export function RangeBadge({ value }: { value: ReferenceRecord }) {
  const { chapterCount } = useNarrativeContext(), range = record(value.narrative_range), time = stringValue(value.story_time);
  return <div className="narrative-position"><span className="narrative-progress">{narrativeLabel(range.start, chapterCount)} — {range.end == null ? "最新章节" : narrativeLabel(range.end, chapterCount)}</span>{time && <span>{time}</span>}</div>;
}
export function ProgressInput({ value, onChange, label = "叙事进度", nullable = false, min = 0, max = 100, disabled = false }: { value: unknown; onChange: (value: number | null) => void; label?: string; nullable?: boolean; min?: number; max?: number; disabled?: boolean }) {
  const { chapterCount } = useNarrativeContext();
  const [preferredMode, setMode] = useState<"chapter" | "percent">("chapter");
  const mode = chapterCount > 0 ? preferredMode : "percent", numeric = typeof value === "number" ? value : null;
  const chapter = numeric == null ? "" : Number((numeric * chapterCount / 100).toFixed(4));
  return <div className="narrative-input-field"><div className="narrative-input-label"><span>{label}</span>{chapterCount > 0 && <div className="narrative-unit-switch" role="group" aria-label={`${label}单位`}><button type="button" disabled={disabled} aria-pressed={mode === "chapter"} onClick={() => setMode("chapter")}>章</button><button type="button" disabled={disabled} aria-pressed={mode === "percent"} onClick={() => setMode("percent")}>%</button></div>}</div><div className="narrative-input-control">{mode === "chapter" && <span>第</span>}<input aria-label={`${label}${mode === "chapter" ? "章节" : " (%)"}`} disabled={disabled} type="number" min={mode === "chapter" ? min * chapterCount / 100 : min} max={mode === "chapter" ? max * chapterCount / 100 : max} step="any" value={mode === "chapter" ? chapter : numeric ?? ""} placeholder={nullable ? "最新章节" : ""} onChange={event => { const number = event.target.value === "" ? null : Number(event.target.value); onChange(number == null ? nullable ? null : 0 : mode === "chapter" ? number / chapterCount * 100 : number); }} /><span>{mode === "chapter" ? `/ ${chapterCount} 章` : "%"}</span></div></div>;
}
export function ProgressFields({ value, onChange }: { value: ReferenceRecord; onChange: (value: ReferenceRecord) => void }) {
  return <div className="ref-form-grid narrative-position-fields"><ProgressInput value={value.narrative_progress} onChange={narrative_progress => onChange({ ...value, narrative_progress })} /><label className="studio-field"><span>故事中时间</span><input aria-label="故事中时间" value={stringValue(value.story_time)} onChange={event => onChange({ ...value, story_time: event.target.value || null })} /></label></div>;
}
export function RangeFields({ value, onChange }: { value: ReferenceRecord; onChange: (value: ReferenceRecord) => void }) {
  const range = record(value.narrative_range);
  return <div className="narrative-range-fields"><div className="ref-form-grid"><ProgressInput label="开始进度" value={range.start} max={typeof range.end === "number" ? range.end : 100} onChange={start => onChange({ ...value, narrative_range: { ...range, start } })} /><ProgressInput label="结束进度" nullable value={range.end} min={typeof range.start === "number" ? range.start : 0} onChange={end => onChange({ ...value, narrative_range: { ...range, end } })} /></div><label className="studio-field"><span>故事中时间</span><input aria-label="故事中时间" value={stringValue(value.story_time)} onChange={event => onChange({ ...value, story_time: event.target.value || null })} /></label></div>;
}
