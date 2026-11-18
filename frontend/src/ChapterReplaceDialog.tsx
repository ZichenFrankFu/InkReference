import { useEffect, useState } from "react";
import { ArrowRight, Replace } from "lucide-react";
import { send, type TextVersion } from "./studio";
import { Busy, Modal, Notice, messageOf } from "./ui";

type Preview = { preview_id: string; match_count: number; affected_chapter_count: number; chapters: { chapter_id: string; title: string; match_count: number }[] };
type Props = { documentId: string; versionId: string; chapterId?: string; onClose: () => void; onVersion: (id: string, version: TextVersion) => Promise<void>; onActivity?: (label: string) => void };
export default function ChapterReplaceDialog({ documentId, versionId, chapterId, onClose, onVersion, onActivity }: Props) {
  const [search, setSearch] = useState(""), [replacement, setReplacement] = useState("");
  const [scope, setScope] = useState<"chapter" | "all">(chapterId ? "chapter" : "all");
  const [preview, setPreview] = useState<Preview | null>(null), [busy, setBusy] = useState(""), [error, setError] = useState("");
  useEffect(() => { onActivity?.(busy); return () => onActivity?.(""); }, [busy, onActivity]);
  const change = (action: () => void) => { action(); setPreview(null); setError(""); };
  const payload = { base_version_id: versionId, search, replacement, scope, ...(scope === "chapter" ? { chapter_id: chapterId } : {}), body_only: true };
  const inspect = async () => {
    setBusy("正在预览替换"); setError(""); setPreview(null);
    try { setPreview(await send<Preview>(`/documents/${documentId}/replace/preview`, payload)); }
    catch (e) { setError(messageOf(e)); } finally { setBusy(""); }
  };
  const apply = async () => {
    if (!preview || !preview.match_count) return;
    setBusy("正在替换正文"); setError("");
    try { const result = await send<TextVersion>(`/documents/${documentId}/replace/apply`, { ...payload, preview_id: preview.preview_id }); await onVersion(result.id, result); onClose(); }
    catch (e) { setError(messageOf(e)); } finally { setBusy(""); }
  };
  return <Modal title="批量替换正文" wide busy={!!busy} onClose={onClose}>
    <div className="modal-body chapter-replace-dialog">
      <div className="chapter-replace-scope" role="group" aria-label="替换范围"><button className={`button ${scope === "chapter" ? "primary" : "secondary"}`} disabled={!!busy || !chapterId} onClick={() => change(() => setScope("chapter"))}>本章</button><button className={`button ${scope === "all" ? "primary" : "secondary"}`} disabled={!!busy} onClick={() => change(() => setScope("all"))}>全文</button><span>仅正文</span></div>
      <div className="chapter-replace-fields"><label className="studio-field"><span>查找文本</span><textarea autoFocus aria-label="查找文本" rows={4} value={search} disabled={!!busy} onChange={event => change(() => setSearch(event.target.value))} /></label><ArrowRight size={18} /><label className="studio-field"><span>替换为</span><textarea aria-label="替换为" rows={4} value={replacement} disabled={!!busy} onChange={event => change(() => setReplacement(event.target.value))} /></label></div>
      {preview && <section className="chapter-replace-preview" aria-label="替换预览" aria-live="polite"><strong>{preview.match_count.toLocaleString()} 处 · {preview.affected_chapter_count} 章</strong><div>{preview.chapters.map(item => <div key={item.chapter_id}><span>{item.title}</span><b>{item.match_count} 处</b></div>)}</div></section>}
      <Notice error>{error}</Notice>{busy && <Busy label={busy} inline delay={0} />}
    </div>
    <div className="modal-footer"><button className="button secondary" disabled={!!busy} onClick={onClose}>取消</button><button className="button secondary" disabled={!!busy || !search.length} onClick={() => void inspect()}>预览替换</button><button className="button primary" disabled={!!busy || !preview?.match_count} onClick={() => void apply()}><Replace size={15} />确认替换</button></div>
  </Modal>;
}
