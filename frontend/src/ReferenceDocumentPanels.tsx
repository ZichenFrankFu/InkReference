import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowDownToLine, History, RotateCcw } from "lucide-react";
import { download } from "./api";
import ChapterWorkbench from "./ChapterWorkbench";
import { VolumePanel, SourceFiles } from "./VolumePanel";
import { studio, send, type DocumentMeta, type TextVersion } from "./studio";
import { Busy, Modal, Notice, dateLabel, messageOf } from "./ui";

type Props = { document: DocumentMeta; onRefresh: () => Promise<void>; onDirty?: (dirty: boolean) => void };
type Version = TextVersion & { parent_version_id?: string };
export function ReferenceFilesPanel({ document }: Props) {
  return <SourceFiles documentId={document.id} versionId={document.latest_version_id} />;
}
export function ReferencePreprocessPanel({ document, onRefresh, onDirty }: Props) {
  const [meta, setMeta] = useState(document);
  const [version, setVersion] = useState<Version | null>(null);
  const [tab, setTab] = useState("chapters");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [dirty, setDirty] = useState(false);
  const [pendingVersion, setPendingVersion] = useState("");
  const [restoreTarget, setRestoreTarget] = useState("");
  const [reset, setReset] = useState(0);
  const ticket = useRef(0);
  const onDirtyRef = useRef(onDirty); onDirtyRef.current = onDirty;
  const dirtyRef = useRef(dirty); dirtyRef.current = dirty;
  const load = useCallback(async (id?: string) => {
    const current = ++ticket.current;
    setBusy("正在读取章节版本"); setError("");
    try {
      const next = await studio<DocumentMeta>(`/documents/${document.id}`);
      const loaded = await studio<Version>(`/documents/${document.id}/versions/${id || next.latest_version_id}?include_text=false`);
      if (current !== ticket.current) return;
      setMeta(next); setVersion(loaded); setDirty(false); onDirtyRef.current?.(false);
    } catch (e) { if (current === ticket.current) setError(messageOf(e)); }
    finally { if (current === ticket.current) setBusy(""); }
  }, [document.id]);
  useEffect(() => { void load(document.latest_version_id); return () => { ticket.current++; }; }, [load, document.latest_version_id]);
  const changeDirty = (value: boolean) => { setDirty(value); onDirtyRef.current?.(value); };
  const changeVersion = (id: string) => dirtyRef.current ? setPendingVersion(id) : void load(id);
  const saved = async (id: string) => { changeDirty(false); await load(id); await onRefresh(); };
  const restore = async () => {
    if (!restoreTarget) return;
    setBusy("正在恢复全文版本"); setError("");
    try {
      const result = await send<Version>(`/documents/${document.id}/chapters/restore`, { base_version_id: meta.latest_version_id, target_version_id: restoreTarget });
      setRestoreTarget(""); await saved(result.id);
    } catch (e) { setError(messageOf(e)); } finally { setBusy(""); }
  };
  const exportText = async () => {
    if (!version) return;
    setBusy("正在准备 TXT"); setError("");
    try { await download(`/studio/documents/${document.id}/versions/${version.id}/download`, `${document.title}.txt`); }
    catch (e) { setError(messageOf(e)); } finally { setBusy(""); }
  };
  return <section className="reference-preprocess-panel" aria-label="参考作品章节预处理" style={{ position: "relative", minHeight: 650 }}>
    <div className={`document-detail-progress ${busy ? "active" : ""}`}><div className="indeterminate" role={busy ? "progressbar" : undefined} aria-label={busy || "章节版本"}><i /></div></div>
    <div className="document-subbar"><div className="document-review-tabs" role="tablist" aria-label="预处理工作区"><button role="tab" aria-selected={tab === "chapters"} className={tab === "chapters" ? "active" : ""} onClick={() => setTab("chapters")}>章节清洗</button><button role="tab" aria-selected={tab === "volumes"} className={tab === "volumes" ? "active" : ""} onClick={() => setTab("volumes")}>分卷规划</button></div><div className="document-version-history"><div className="version-select"><History size={14} /><select value={version?.id || ""} aria-label="参考作品全文版本" disabled={!!busy} onChange={(e) => changeVersion(e.target.value)}>{(meta.versions || []).map((v) => <option key={v.id} value={v.id}>版本 {v.version_number} · {dateLabel(v.created_at)}</option>)}</select></div>{version && (version.id !== meta.latest_version_id || version.parent_version_id) && <button className="text-button" disabled={!!busy || dirty} onClick={() => setRestoreTarget(version.id === meta.latest_version_id ? version.parent_version_id! : version.id)}><RotateCcw size={13} />{version.id === meta.latest_version_id ? "撤销上次更改" : "恢复此版本"}</button>}<button className="text-button" disabled={!version || !!busy} onClick={() => void exportText()}><ArrowDownToLine size={13} />TXT</button></div></div>
    <Notice error>{error}</Notice>
    {!version && busy ? <Busy label="正在读取章节版本" /> : version && <><div hidden={tab !== "chapters"}><ChapterWorkbench key={`${document.id}:${reset}`} documentId={document.id} version={version} onVersion={saved} onDirty={changeDirty} /></div>{tab === "volumes" && <VolumePanel documentId={document.id} versionId={version.id} />}</>}
    {pendingVersion && <Modal title="章节修订尚未保存" onClose={() => setPendingVersion("")}><div className="modal-body"><p>切换版本前，请保存当前章节修订。</p></div><div className="modal-footer"><button className="button secondary" onClick={() => setPendingVersion("")}>继续修订</button><button className="button danger" onClick={() => { const id = pendingVersion; setPendingVersion(""); changeDirty(false); setReset((n) => n + 1); void load(id); }}>放弃修订</button></div></Modal>}
    {restoreTarget && <Modal title="恢复全文版本" busy={!!busy} onClose={() => setRestoreTarget("")}><div className="modal-body"><p>恢复为新的最新版本，已有版本保留。</p><Notice error>{error}</Notice>{busy && <Busy label={busy} />}</div><div className="modal-footer"><button className="button secondary" disabled={!!busy} onClick={() => setRestoreTarget("")}>取消</button><button className="button primary" disabled={!!busy} onClick={() => void restore()}><RotateCcw size={14} />恢复版本</button></div></Modal>}
  </section>;
}
