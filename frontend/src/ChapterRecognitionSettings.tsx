import { useEffect, useRef, useState } from "react";
import { AlignLeft, Check, CircleAlert, Plus, RotateCcw, Trash2 } from "lucide-react";
import { send, studio, type TextVersion } from "./studio";
import { Busy, Notice, messageOf } from "./ui";
import type { ChapterIndex, CustomFormat } from "./chapterTypes";
import "./chapter.css";

type Props = { documentId: string; version: TextVersion; onVersion: (id: string, summary?: TextVersion) => Promise<void>; onDirty?: (dirty: boolean) => void; blocked?: boolean };
export default function ChapterRecognitionSettings({ documentId, version, onVersion, onDirty, blocked }: Props) {
  const [index, setIndex] = useState<ChapterIndex | null>(null);
  const [formats, setFormats] = useState<string[]>([]);
  const [customFormats, setCustomFormats] = useState<CustomFormat[]>([]);
  const [keywords, setKeywords] = useState("");
  const [garbleRules, setGarbleRules] = useState<{ name: string; pattern: string; enabled: boolean }[]>([]);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [initial, setInitial] = useState("");
  const dirtyRef = useRef(onDirty); dirtyRef.current = onDirty;
  const signature = JSON.stringify({ formats, customFormats, keywords, garbleRules });
  useEffect(() => { dirtyRef.current?.(!!initial && signature !== initial); }, [signature, initial]);
  useEffect(() => () => dirtyRef.current?.(false), []);
  useEffect(() => {
    let alive = true;
    setBusy("正在读取识别设置"); setError("");
    void studio<ChapterIndex>(`/documents/${documentId}/versions/${version.id}/chapters`).then((next) => {
      if (!alive) return;
      const state = { formats: next.heading_formats || [], customFormats: next.options?.custom_formats || [], keywords: (next.options?.author_keywords ?? next.author_keyword_defaults ?? []).join("\n"), garbleRules: next.options?.garble_rules || [] };
      setIndex(next); setFormats(state.formats); setCustomFormats(state.customFormats); setKeywords(state.keywords); setGarbleRules(state.garbleRules); setInitial(JSON.stringify(state));
    }).catch((e) => { if (alive) setError(messageOf(e)); }).finally(() => { if (alive) setBusy(""); });
    return () => { alive = false; };
  }, [documentId, version.id]);
  async function save(path: string, values: Record<string, unknown>, label: string) {
    if (blocked) { setError("请先保存分卷修改"); return; }
    setBusy(label); setError("");
    try {
      const result = await send<TextVersion>(`/documents/${documentId}/chapters/${path}?summary=true`, { base_version_id: version.id, ...values });
      await onVersion(result.id, result); dirtyRef.current?.(false); setInitial(signature);
    } catch (e) { setError(messageOf(e)); } finally { setBusy(""); }
  }
  return <div className="chapter-settings chapter-recognition-settings" aria-busy={!!busy}>
    <Notice error>{error}</Notice>{blocked && <span className="chapter-settings-blocked">请先保存分卷修改</span>}{busy && <Busy label={busy} inline />}
    <section><div className="chapter-settings-heading"><h3><AlignLeft size={16} />章节标题格式</h3><button className="button secondary compact-button" disabled={!!busy || !index || blocked} onClick={() => void save("resegment", { heading_formats: formats, custom_formats: customFormats }, "正在识别章节")}><RotateCcw size={14} />{formats.length ? "按所选格式识别" : "自动识别章节"}</button></div>
      <div className="chapter-format-grid">{(index?.candidates?.filter((candidate) => !candidate.custom) || []).map((candidate) => <label key={candidate.name} className={`chapter-format-choice ${formats.includes(candidate.name) ? "checked" : ""}`}><input type="checkbox" checked={formats.includes(candidate.name)} disabled={!!busy} onChange={(e) => setFormats((current) => e.target.checked ? [...current, candidate.name] : current.filter((f) => f !== candidate.name))} /><span><strong>{candidate.name}</strong>{candidate.count !== undefined && <small>{candidate.count} 个匹配</small>}</span></label>)}</div>
      <div className="chapter-custom-format-list">{customFormats.map((rule, i) => <div className="chapter-custom-format-row" key={i}><input type="checkbox" aria-label={`启用自定义章节格式 ${i + 1}`} checked={rule.enabled} disabled={!!busy} onChange={(e) => { setCustomFormats((all) => all.map((r, j) => j === i ? { ...r, enabled: e.target.checked } : r)); setFormats((all) => e.target.checked ? [...new Set([...all, rule.name])] : all.filter((n) => n !== rule.name)); }} /><input aria-label={`自定义章节格式 ${i + 1} 名称`} value={rule.name} placeholder="格式名称" onChange={(e) => { const name = e.target.value; setCustomFormats((all) => all.map((r, j) => j === i ? { ...r, name } : r)); setFormats((all) => all.map((n) => n === rule.name ? name : n)); }} /><select aria-label={`自定义章节格式 ${i + 1} 类型`} value={rule.regex !== undefined && !rule.format ? "regex" : "format"} onChange={(e) => setCustomFormats((all) => all.map((r, j) => j === i ? { name: r.name, enabled: r.enabled, ...(e.target.value === "regex" ? { regex: r.format || r.regex || "" } : { format: r.regex || r.format || "" }) } : r))}><option value="format">N 格式</option><option value="regex">正则</option></select><input aria-label={`自定义章节格式 ${i + 1} 匹配规则`} value={rule.format || rule.regex || ""} placeholder={rule.regex !== undefined && !rule.format ? "正则表达式" : "例如 第N节"} onChange={(e) => setCustomFormats((all) => all.map((r, j) => j === i ? { ...r, ...(r.regex !== undefined && !r.format ? { regex: e.target.value } : { format: e.target.value }) } : r))} /><button className="icon-button" aria-label={`删除自定义章节格式 ${i + 1}`} disabled={!!busy} onClick={() => { setCustomFormats((all) => all.filter((_, j) => j !== i)); setFormats((all) => all.filter((n) => n !== rule.name)); }}><Trash2 size={13} /></button></div>)}</div>
      <button className="text-button" disabled={!!busy} onClick={() => { const name = `自定义格式 ${customFormats.length + 1}`; setCustomFormats((all) => [...all, { name, format: "第N节", enabled: true }]); setFormats((all) => [...all, name]); }}><Plus size={13} />添加标题格式</button>
    </section>
    <section><div className="chapter-settings-heading"><h3><CircleAlert size={16} />正文标记规则</h3><button className="button secondary compact-button" disabled={!!busy || !index || blocked} onClick={() => void save("settings", { author_keywords: keywords.split("\n").map((k) => k.trim()).filter(Boolean), garble_rules: garbleRules.filter((r) => r.name.trim() || r.pattern.trim()) }, "正在更新标记")}><Check size={14} />保存标记规则</button></div>
      <div className="form-grid"><div className="studio-field"><span className="chapter-field-heading">非正文关键词<button className="text-button" disabled={!!busy} onClick={() => setKeywords((index?.author_keyword_defaults || []).join("\n"))}>恢复默认</button></span><textarea aria-label="非正文关键词" rows={5} value={keywords} onChange={(e) => setKeywords(e.target.value)} placeholder={"求月票\n作者说\n请假说明"} /></div><div className="studio-field"><span>杂字规则</span><div className="chapter-rule-list">{garbleRules.map((rule, i) => <div className="chapter-rule-row" key={i}><input type="checkbox" aria-label={`启用杂字规则 ${i + 1}`} checked={rule.enabled} onChange={(e) => setGarbleRules((rows) => rows.map((r, j) => j === i ? { ...r, enabled: e.target.checked } : r))} /><input aria-label={`杂字规则 ${i + 1} 名称`} placeholder="规则名称" value={rule.name} onChange={(e) => setGarbleRules((rows) => rows.map((r, j) => j === i ? { ...r, name: e.target.value } : r))} /><input aria-label={`杂字规则 ${i + 1} 正则`} placeholder="正则表达式" value={rule.pattern} onChange={(e) => setGarbleRules((rows) => rows.map((r, j) => j === i ? { ...r, pattern: e.target.value } : r))} /><button className="icon-button" aria-label={`删除杂字规则 ${i + 1}`} onClick={() => setGarbleRules((rows) => rows.filter((_, j) => j !== i))}><Trash2 size={13} /></button></div>)}</div><button className="text-button" onClick={() => setGarbleRules((rows) => [...rows, { name: "", pattern: "", enabled: true }])}><Plus size={13} />添加杂字规则</button></div></div>
    </section>
  </div>;
}
