import { useEffect, useRef, useState } from "react";
import { AlignLeft, ArrowRight, Check, ChevronDown, CircleAlert, Plus, RotateCcw, Trash2 } from "lucide-react";
import { send, studio, type TextVersion } from "./studio";
import { Busy, Modal, Notice, messageOf } from "./ui";
import type { ChapterIndex, CustomFormat } from "./chapterTypes";
import "./chapter.css";

type Props = { documentId: string; version: TextVersion; onVersion: (id: string, summary?: TextVersion) => Promise<void>; onDirty?: (dirty: boolean) => void; onActivity?: (label: string) => void; blocked?: boolean };
type DirectorySummary = { chapter_count: number; empty_chapter_count: number; duplicate_chapter_count: number };
type RecognitionPreview = { preview_token: string; conflict: boolean; old: DirectorySummary; new: DirectorySummary; changes: { kind: string; before: unknown; after: unknown }[]; change_count?: number; duplicate_repair_count?: number; duplicate_repairs: { number: number; title: string; body_characters: number }[] };
const changeLabels: Record<string, string> = { chapter_count: "章节", empty_chapter_count: "空章", duplicate_chapter_count: "重复章节", heading_formats: "标题格式", heading: "章节标题", removed: "移除", added: "新增", changed: "调整", duplicate_repair: "重复章节" };
const changeValue = (value: unknown): string => {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "string" || typeof value === "number") return String(value);
  if (Array.isArray(value)) return value.map(changeValue).join(" · ");
  const item = value as { number?: number; title?: string; body_characters?: number; kind?: string };
  return `${item.kind === "chapter" && item.number !== undefined ? `第${item.number}章 ` : ""}${item.title || ""}${item.body_characters !== undefined ? ` · ${item.body_characters.toLocaleString()} 字` : ""}`.trim() || "—";
};
export default function ChapterRecognitionSettings({ documentId, version, onVersion, onDirty, onActivity, blocked }: Props) {
  const [index, setIndex] = useState<ChapterIndex | null>(null);
  const [formats, setFormats] = useState<string[]>([]);
  const [customFormats, setCustomFormats] = useState<CustomFormat[]>([]);
  const [keywords, setKeywords] = useState("");
  const [garbleRules, setGarbleRules] = useState<{ name: string; pattern: string; enabled: boolean }[]>([]);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [initial, setInitial] = useState("");
  const [expanded, setExpanded] = useState({ headings: false, markers: false });
  const [preview, setPreview] = useState<RecognitionPreview | null>(null);
  const dirtyRef = useRef(onDirty); dirtyRef.current = onDirty;
  const activityRef = useRef(onActivity); activityRef.current = onActivity;
  const requestValues = useRef<Record<string, unknown>>({});
  const carriedDraft = useRef<{ versionId: string; fields: Record<string, unknown> } | null>(null);
  const signature = JSON.stringify({ formats, customFormats, keywords, garbleRules });
  useEffect(() => { dirtyRef.current?.(!!initial && signature !== initial); }, [signature, initial]);
  useEffect(() => { activityRef.current?.(busy); }, [busy]);
  useEffect(() => () => { dirtyRef.current?.(false); activityRef.current?.(""); }, []);
  useEffect(() => {
    let alive = true;
    setBusy("正在读取识别设置"); setError(""); setPreview(null);
    void studio<ChapterIndex>(`/documents/${documentId}/versions/${version.id}/chapters`).then((next) => {
      if (!alive) return;
      const state = { formats: next.heading_formats || [], customFormats: next.options?.custom_formats || [], keywords: (next.options?.author_keywords ?? next.author_keyword_defaults ?? []).join("\n"), garbleRules: next.options?.garble_rules || [] };
      const retained = carriedDraft.current?.versionId === version.id ? carriedDraft.current.fields : {};
      carriedDraft.current = null;
      const draft = { ...state, ...retained } as typeof state;
      setIndex(next); setFormats(draft.formats); setCustomFormats(draft.customFormats); setKeywords(draft.keywords); setGarbleRules(draft.garbleRules); setInitial(JSON.stringify(state));
    }).catch((e) => { if (alive) setError(messageOf(e)); }).finally(() => { if (alive) setBusy(""); });
    return () => { alive = false; };
  }, [documentId, version.id]);
  async function save(path: string, values: Record<string, unknown>, label: string) {
    if (blocked) { setError("请先保存分卷修改"); return; }
    setBusy(label); setError("");
    try {
      const result = await send<TextVersion>(`/documents/${documentId}/chapters/${path}?summary=true`, { base_version_id: version.id, ...values });
      const previous = initial ? JSON.parse(initial) : { formats, customFormats, keywords, garbleRules };
      const saved = path === "settings" ? { ...previous, keywords, garbleRules } : { ...previous, formats, customFormats };
      const fields = path === "settings" ? { formats, customFormats } : { keywords, garbleRules };
      if (result.id !== version.id) carriedDraft.current = { versionId: result.id, fields };
      await onVersion(result.id, result); setInitial(JSON.stringify(saved)); setPreview(null);
    } catch (e) { setError(messageOf(e)); } finally { setBusy(""); }
  }
  async function recognize() {
    if (blocked) { setError("请先保存分卷修改"); return; }
    setBusy("正在预览章节变化"); setError("");
    const values = { base_version_id: version.id, heading_formats: formats, custom_formats: customFormats, repair_duplicates: true };
    try {
      const next = await send<RecognitionPreview>(`/documents/${documentId}/chapters/resegment/preview`, values);
      requestValues.current = { heading_formats: formats, custom_formats: customFormats, repair_duplicates: true };
      if (next.conflict) setPreview(next);
      else await save("resegment", { ...requestValues.current, preview_token: next.preview_token, conflict_policy: "use_new" }, "正在识别章节");
    } catch (e) { setError(messageOf(e)); } finally { setBusy(""); }
  }
  async function keepDirectory() {
    if (!preview) return;
    setBusy("正在保留当前目录"); setError("");
    try {
      await send<TextVersion>(`/documents/${documentId}/chapters/resegment?summary=true`, { base_version_id: version.id, ...requestValues.current, preview_token: preview.preview_token, conflict_policy: "keep_old" });
      setFormats(index?.heading_formats || []); setCustomFormats(index?.options?.custom_formats || []); setPreview(null);
    } catch (e) { setError(messageOf(e)); } finally { setBusy(""); }
  }
  const candidates = index?.candidates?.filter((candidate) => !candidate.custom) || [];
  return <div className="chapter-settings chapter-recognition-settings" aria-busy={!!busy}>
    <Notice error>{error}</Notice>{blocked && <span className="chapter-settings-blocked">请先保存分卷修改</span>}
    <section className={`recognition-card ${expanded.headings ? "expanded" : ""}`}>
      <div className="recognition-card-header"><button className="recognition-disclosure" aria-expanded={expanded.headings} aria-controls="chapter-heading-options" onClick={() => setExpanded((current) => ({ ...current, headings: !current.headings }))}><span className="recognition-card-icon"><AlignLeft size={18} /></span><span><strong>章节标题格式</strong><small>{formats.length ? `${formats.length} 项已选` : "自动识别"}</small></span><ChevronDown size={16} /></button><button className="button primary compact-button" disabled={!!busy || !index || blocked} onClick={() => void recognize()}><RotateCcw size={14} />重新识别</button></div>
      {expanded.headings && <div id="chapter-heading-options" className="recognition-card-body">
        <div className="chapter-format-grid">{candidates.map((candidate) => <label key={candidate.name} className={`chapter-format-choice ${formats.includes(candidate.name) ? "checked" : ""}`}><input type="checkbox" checked={formats.includes(candidate.name)} disabled={!!busy} onChange={(e) => setFormats((current) => e.target.checked ? [...current, candidate.name] : current.filter((f) => f !== candidate.name))} /><span><strong>{candidate.name}</strong>{candidate.format && candidate.format !== candidate.name && <code>{candidate.format}</code>}{candidate.count !== undefined && <small>{candidate.count} 个匹配</small>}</span></label>)}</div>
        <div className="chapter-custom-format-list">{customFormats.map((rule, i) => <div className="chapter-custom-format-row" key={i}><input type="checkbox" aria-label={`启用自定义章节格式 ${i + 1}`} checked={rule.enabled} disabled={!!busy} onChange={(e) => { setCustomFormats((all) => all.map((r, j) => j === i ? { ...r, enabled: e.target.checked } : r)); setFormats((all) => e.target.checked ? [...new Set([...all, rule.name])] : all.filter((n) => n !== rule.name)); }} /><input aria-label={`自定义章节格式 ${i + 1} 名称`} value={rule.name} disabled={!!busy} placeholder="格式名称" onChange={(e) => { const name = e.target.value; setCustomFormats((all) => all.map((r, j) => j === i ? { ...r, name } : r)); setFormats((all) => all.map((n) => n === rule.name ? name : n)); }} /><select aria-label={`自定义章节格式 ${i + 1} 类型`} disabled={!!busy} value={rule.regex !== undefined && !rule.format ? "regex" : "format"} onChange={(e) => setCustomFormats((all) => all.map((r, j) => j === i ? { name: r.name, enabled: r.enabled, ...(e.target.value === "regex" ? { regex: r.format || r.regex || "" } : { format: r.regex || r.format || "" }) } : r))}><option value="format">N 格式</option><option value="regex">正则</option></select><input aria-label={`自定义章节格式 ${i + 1} 匹配规则`} disabled={!!busy} value={rule.format || rule.regex || ""} placeholder={rule.regex !== undefined && !rule.format ? "正则表达式" : "第N节"} onChange={(e) => setCustomFormats((all) => all.map((r, j) => j === i ? { ...r, ...(r.regex !== undefined && !r.format ? { regex: e.target.value } : { format: e.target.value }) } : r))} /><button className="icon-button" aria-label={`删除自定义章节格式 ${i + 1}`} disabled={!!busy} onClick={() => { setCustomFormats((all) => all.filter((_, j) => j !== i)); setFormats((all) => all.filter((n) => n !== rule.name)); }}><Trash2 size={13} /></button></div>)}</div>
        <button className="button secondary compact-button" disabled={!!busy} onClick={() => { const name = `自定义格式 ${customFormats.length + 1}`; setCustomFormats((all) => [...all, { name, format: "第N节", enabled: true }]); setFormats((all) => [...all, name]); }}><Plus size={13} />添加标题格式</button>
      </div>}
    </section>
    <section className={`recognition-card ${expanded.markers ? "expanded" : ""}`}>
      <div className="recognition-card-header"><button className="recognition-disclosure" aria-expanded={expanded.markers} aria-controls="chapter-marker-options" onClick={() => setExpanded((current) => ({ ...current, markers: !current.markers }))}><span className="recognition-card-icon"><CircleAlert size={18} /></span><span><strong>正文标记规则</strong><small>{keywords.split("\n").filter((item) => item.trim()).length} 个关键词 · {garbleRules.filter((rule) => rule.enabled).length} 条杂字规则</small></span><ChevronDown size={16} /></button>{expanded.markers && <button className="button secondary compact-button" disabled={!!busy || !index || blocked} onClick={() => void save("settings", { author_keywords: keywords.split("\n").map((k) => k.trim()).filter(Boolean), garble_rules: garbleRules.filter((r) => r.name.trim() || r.pattern.trim()) }, "正在更新标记")}><Check size={14} />保存规则</button>}</div>
      {expanded.markers && <div id="chapter-marker-options" className="recognition-card-body"><div className="recognition-markers-grid"><div className="studio-field"><span className="chapter-field-heading">非正文关键词<button className="text-button" disabled={!!busy} onClick={() => setKeywords((index?.author_keyword_defaults || []).join("\n"))}>恢复默认</button></span><textarea aria-label="非正文关键词" disabled={!!busy} rows={5} value={keywords} onChange={(e) => setKeywords(e.target.value)} placeholder={"求月票\n作者说\n请假说明"} /></div><div className="studio-field"><span>杂字规则</span><div className="chapter-rule-list">{garbleRules.map((rule, i) => <div className="chapter-rule-row" key={i}><input type="checkbox" aria-label={`启用杂字规则 ${i + 1}`} disabled={!!busy} checked={rule.enabled} onChange={(e) => setGarbleRules((rows) => rows.map((r, j) => j === i ? { ...r, enabled: e.target.checked } : r))} /><input aria-label={`杂字规则 ${i + 1} 名称`} disabled={!!busy} placeholder="规则名称" value={rule.name} onChange={(e) => setGarbleRules((rows) => rows.map((r, j) => j === i ? { ...r, name: e.target.value } : r))} /><input aria-label={`杂字规则 ${i + 1} 正则`} disabled={!!busy} placeholder="正则表达式" value={rule.pattern} onChange={(e) => setGarbleRules((rows) => rows.map((r, j) => j === i ? { ...r, pattern: e.target.value } : r))} /><button className="icon-button" disabled={!!busy} aria-label={`删除杂字规则 ${i + 1}`} onClick={() => setGarbleRules((rows) => rows.filter((_, j) => j !== i))}><Trash2 size={13} /></button></div>)}</div><button className="button secondary compact-button" disabled={!!busy} onClick={() => setGarbleRules((rows) => [...rows, { name: "", pattern: "", enabled: true }])}><Plus size={13} />添加杂字规则</button></div></div></div>}
    </section>
    {preview && <Modal title="章节识别有变化" busy={!!busy} wide onClose={() => setPreview(null)}><div className="modal-body recognition-preview"><div className="recognition-comparison"><div><strong>当前目录</strong><b>{preview.old.chapter_count}<small>章</small></b><span>{preview.old.empty_chapter_count} 空章 · {preview.old.duplicate_chapter_count} 重复</span></div><ArrowRight size={20} /><div><strong>新目录</strong><b>{preview.new.chapter_count}<small>章</small></b><span>{preview.new.empty_chapter_count} 空章 · {preview.new.duplicate_chapter_count} 重复</span></div></div>{preview.changes?.length > 0 && <div className="recognition-changes"><div className="recognition-change-heading"><span>变化 {preview.changes.length}{preview.change_count && preview.change_count > preview.changes.length ? ` / ${preview.change_count}` : ""}</span><span>当前</span><span>新目录</span></div>{preview.changes.map((change, i) => <div key={i}><strong>{changeLabels[change.kind] || change.kind}</strong><span>{changeValue(change.before)}</span><span>{changeValue(change.after)}</span></div>)}</div>}{preview.duplicate_repairs?.length > 0 && <div className="recognition-duplicate-repairs"><h3>重复章节修复</h3>{preview.duplicate_repairs.map((repair, i) => <article key={i}><strong>第{repair.number}章 {repair.title}</strong><span>{repair.body_characters.toLocaleString()} 字</span></article>)}</div>}<Notice error>{error}</Notice>{busy && <Busy label={busy} inline delay={0} />}</div><div className="modal-footer"><button className="button secondary" disabled={!!busy} onClick={() => void keepDirectory()}>保留旧版</button><button className="button primary" disabled={!!busy} onClick={() => void save("resegment", { ...requestValues.current, preview_token: preview.preview_token, conflict_policy: "use_new" }, "正在应用新目录")}>以新版为准<ArrowRight size={15} /></button></div></Modal>}
  </div>;
}
