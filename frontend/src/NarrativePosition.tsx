import { record, stringValue, type ReferenceRecord } from "./reference-types";

export function progressLabel(value: unknown) {
  const number = typeof value === "number" ? value : typeof value === "string" && value.trim() ? Number(value) : NaN;
  return Number.isFinite(number) ? `${Number(number.toFixed(2))}%` : "—";
}

export function PositionBadge({ value }: { value: ReferenceRecord }) {
  const time = typeof value.story_time === "string" ? value.story_time : stringValue(record(value.story_time).label);
  return <div className="narrative-position"><span className="narrative-progress">{progressLabel(value.narrative_progress)}</span>{time && <span>{time}</span>}</div>;
}

export function RangeBadge({ value }: { value: ReferenceRecord }) {
  const range = record(value.narrative_range), time = stringValue(value.story_time);
  return <div className="narrative-position"><span className="narrative-progress">{progressLabel(range.start)} — {range.end == null ? "持续中" : progressLabel(range.end)}</span>{time && <span>{time}</span>}</div>;
}

export function ProgressFields({ value, onChange }: { value: ReferenceRecord; onChange: (value: ReferenceRecord) => void }) {
  return <div className="ref-form-grid narrative-position-fields"><label className="studio-field"><span>叙事进度 (%)</span><input aria-label="叙事进度 (%)" type="number" min="0" max="100" step="0.01" value={typeof value.narrative_progress === "number" ? value.narrative_progress : ""} onChange={event => onChange({ ...value, narrative_progress: event.target.value === "" ? null : Number(event.target.value) })} /></label><label className="studio-field"><span>故事中时间</span><input aria-label="故事中时间" value={stringValue(value.story_time)} onChange={event => onChange({ ...value, story_time: event.target.value || null })} /></label></div>;
}

export function RangeFields({ value, onChange }: { value: ReferenceRecord; onChange: (value: ReferenceRecord) => void }) {
  const range = record(value.narrative_range);
  return <div className="narrative-range-fields"><div className="ref-form-grid"><label className="studio-field"><span>开始进度 (%)</span><input aria-label="开始进度 (%)" type="number" min="0" max="100" step="0.01" value={typeof range.start === "number" ? range.start : ""} onChange={event => onChange({ ...value, narrative_range: { ...range, start: event.target.value === "" ? null : Number(event.target.value) } })} /></label><label className="studio-field"><span>结束进度 (%)</span><input aria-label="结束进度 (%)" type="number" min={typeof range.start === "number" ? range.start : 0} max="100" step="0.01" placeholder="持续中" value={typeof range.end === "number" ? range.end : ""} onChange={event => onChange({ ...value, narrative_range: { ...range, end: event.target.value === "" ? null : Number(event.target.value) } })} /></label></div><label className="studio-field"><span>故事中时间</span><input aria-label="故事中时间" value={stringValue(value.story_time)} onChange={event => onChange({ ...value, story_time: event.target.value || null })} /></label></div>;
}
