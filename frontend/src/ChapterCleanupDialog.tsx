import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, Search, Sparkles } from "lucide-react";
import { studio } from "./studio";
import { Busy, Modal, Notice, messageOf } from "./ui";
import { chapterLabel, issueClass, needsReview, type Chapter, type Finding } from "./chapterTypes";

type Props = { documentId: string; versionId: string; chapters: Chapter[]; initialChapterId?: string; busy: boolean; error: string; onClose: () => void; onJump: (finding: Finding) => void; onApply: (selections: { chapter_id: string; proposal_ids: string[] }[]) => Promise<void> };
const SIZE = 16;
export default function ChapterCleanupDialog({ documentId, versionId, chapters, initialChapterId, busy, error, onClose, onJump, onApply }: Props) {
  const affected = useMemo(() => chapters.filter(needsReview).sort((a, b) => Number(!!a.placeholder) - Number(!!b.placeholder)), [chapters]);
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("all");
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState<Record<string, string[]>>({});
  const [details, setDetails] = useState<Record<string, Finding[]>>({});
  const [snippetLimits, setSnippetLimits] = useState<Record<string, number>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [collecting, setCollecting] = useState(false);
  const [collected, setCollected] = useState(0);
  const [localError, setLocalError] = useState("");
  const fetched = useRef(new Map<string, Promise<Finding[]>>());
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const filtered = affected.filter((chapter) => (!query || chapterLabel(chapter).toLocaleLowerCase().includes(query.toLocaleLowerCase()) || String(chapter.number ?? "").includes(query)) && (kind === "all" || chapter.issues.some((issue) => issueClass(issue.kind) === kind)));
  const visible = filtered.slice(page * SIZE, (page + 1) * SIZE);
  const eligible = filtered.filter((chapter) => chapter.proposal_count > 0);
  const selectedChapterCount = Object.values(selected).filter((ids) => ids.length > 0).length;
  const availableIds = (chapter: Chapter): string[] | undefined => chapter.proposal_ids || chapter.enabled_proposal_ids || details[chapter.id]?.filter((finding) => finding.removable).map((finding) => finding.id);
  const chapterSelected = (chapter: Chapter) => {
    const ids = availableIds(chapter), chosen = selected[chapter.id] || [];
    return ids ? ids.length > 0 && ids.every((id) => chosen.includes(id)) : chapter.proposal_count > 0 && chosen.length === chapter.proposal_count;
  };
  const allSelected = eligible.length > 0 && eligible.every(chapterSelected);
  const locked = busy || collecting;
  const read = (chapter: Chapter) => {
    let task = fetched.current.get(chapter.id);
    if (!task) {
      task = studio<{ items: Finding[] }>(`/documents/${documentId}/versions/${versionId}/findings?chapter_id=${encodeURIComponent(chapter.id)}&limit=10000`).then((result) => result.items);
      fetched.current.set(chapter.id, task);
    }
    return task;
  };
  useEffect(() => {
    if (!initialChapterId) return;
    const target = affected.findIndex((chapter) => chapter.id === initialChapterId);
    if (target >= 0) setPage(Math.floor(target / SIZE));
  }, []);
  useEffect(() => {
    let active = true;
    const missing = visible.filter((chapter) => !details[chapter.id] && !errors[chapter.id]);
    if (!missing.length) { setLoading(false); return; }
    setLoading(true);
    let cursor = 0;
    const workers = Array.from({ length: Math.min(4, missing.length) }, async () => {
      while (cursor < missing.length) {
        const chapter = missing[cursor++];
        try { const result = await read(chapter); if (active) setDetails((all) => ({ ...all, [chapter.id]: result })); }
        catch (e) { if (active) setErrors((all) => ({ ...all, [chapter.id]: messageOf(e) })); }
      }
    });
    void Promise.all(workers).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [page, query, kind]);
  const toggleChapter = async (chapter: Chapter, checked: boolean) => {
    setLocalError("");
    if (!checked) { setSelected((all) => ({ ...all, [chapter.id]: [] })); return; }
    try {
      let ids = availableIds(chapter);
      if (!ids) {
        const findings = await read(chapter); ids = findings.filter((finding) => finding.removable).map((finding) => finding.id);
        setDetails((all) => ({ ...all, [chapter.id]: findings }));
      }
      setSelected((all) => ({ ...all, [chapter.id]: ids! }));
    } catch (e) { setLocalError(messageOf(e)); }
  };
  const toggleAll = async (checked: boolean) => {
    if (!checked) { setSelected((all) => ({ ...all, ...Object.fromEntries(eligible.map((chapter) => [chapter.id, []])) })); return; }
    setLocalError(""); setCollected(0);
    const unresolved = eligible.some((chapter) => !availableIds(chapter));
    if (unresolved) setCollecting(true);
    try {
      const next: Record<string, string[]> = {};
      for (const chapter of eligible) {
        let ids = availableIds(chapter);
        if (!ids) {
          const findings = await read(chapter); ids = findings.filter((finding) => finding.removable).map((finding) => finding.id);
          if (alive.current) setDetails((all) => ({ ...all, [chapter.id]: findings }));
        }
        next[chapter.id] = ids;
        if (alive.current) setCollected((count) => count + 1);
      }
      if (alive.current) setSelected((all) => ({ ...all, ...next }));
    } catch (e) { setLocalError(messageOf(e)); } finally { if (alive.current) setCollecting(false); }
  };
  const toggleFinding = (chapterId: string, id: string, checked: boolean) => {
    setSelected((all) => ({ ...all, [chapterId]: checked ? [...new Set([...(all[chapterId] || []), id])] : (all[chapterId] || []).filter((candidate) => candidate !== id) }));
  };
  const apply = async () => {
    setLocalError("");
    const selections = Object.entries(selected).filter(([, ids]) => ids.length > 0).map(([chapter_id, proposal_ids]) => ({ chapter_id, proposal_ids }));
    if (!selections.length) return;
    try { await onApply(selections); } catch (e) { setLocalError(messageOf(e)); }
  };
  return <Modal title="批量清理" wide busy={locked} onClose={onClose}>
    <div className="modal-body chapter-cleanup-dialog">
      <div className="chapter-cleanup-intro"><strong>{affected.length} 章需要检查</strong></div>
      <div className="chapter-findings-filters"><label className="chapter-search"><Search size={14} /><input aria-label="搜索待检查章节" placeholder="搜索章节" value={query} onChange={(e) => { setQuery(e.target.value); setPage(0); }} /></label><select aria-label="筛选待检查问题" value={kind} onChange={(e) => { setKind(e.target.value); setPage(0); }}><option value="all">全部问题</option><option value="aside">非正文</option><option value="glyph">字符问题</option><option value="noise">采集噪声</option><option value="length">待补充 / 章节异常</option></select></div>
      <div className="chapter-findings-summary"><label><input type="checkbox" aria-label="全选可清理章节" ref={(node) => { if (node) node.indeterminate = selectedChapterCount > 0 && !allSelected; }} checked={allSelected} disabled={!eligible.length || locked} onChange={(e) => void toggleAll(e.target.checked)} />全选可清理章节</label><span>已选 {selectedChapterCount} 章</span></div>
      <Notice error>{error || localError}</Notice>
      <div className="chapter-cleanup-list">
        {!visible.length && <div className="chapter-small-empty">没有需要检查的章节</div>}
        {visible.map((chapter) => <div key={chapter.id} className={`chapter-cleanup-card ${selected[chapter.id]?.length ? "checked" : ""}`}>
          <div className="chapter-cleanup-card-heading">{chapter.placeholder ? <span className="chapter-missing-symbol" aria-label="待补充" /> : <input type="checkbox" aria-label={`清理 ${chapterLabel(chapter)}`} title="全选本章标记" ref={(node) => { if (node) node.indeterminate = (selected[chapter.id]?.length || 0) > 0 && !chapterSelected(chapter); }} checked={chapterSelected(chapter)} disabled={!chapter.proposal_count || locked} onChange={(e) => void toggleChapter(chapter, e.target.checked)} />}<strong>{chapterLabel(chapter)}</strong><span>{chapter.proposal_count ? "可清理" : "需手动处理"}</span></div>
          <div className="chapter-cleanup-snippets">
            {errors[chapter.id] ? <Notice error>{errors[chapter.id]}</Notice> : !details[chapter.id] ? <div className="chapter-snippet-skeleton"><i /><i /></div> : details[chapter.id].length ? details[chapter.id].slice(0, snippetLimits[chapter.id] || 4).map((finding) => <div className="chapter-cleanup-fragment" key={finding.id}>{finding.removable ? <input type="checkbox" aria-label={`清理片段 ${chapterLabel(chapter)} ${finding.text.slice(0, 40) || finding.label}`} checked={!!selected[chapter.id]?.includes(finding.id)} disabled={locked} onChange={(e) => toggleFinding(chapter.id, finding.id, e.target.checked)} /> : <span className="chapter-fragment-manual" />}<button className="chapter-cleanup-snippet" onClick={() => onJump(finding)} disabled={locked}><span className={`chapter-issue ${issueClass(finding.kind)}`}>{finding.scope === "heading" ? "章节名 · " : ""}{finding.label}{!finding.removable ? " · 手动修复" : ""}</span><p>{finding.context_before}<mark className={finding.removable ? "removing" : "diagnostic"}>{/^[\p{Cf}\p{Cc}\s]*$/u.test(finding.text) ? "〔隐藏字符〕" : finding.text}</mark>{finding.context_after}</p></button></div>) : <button className="chapter-cleanup-snippet" disabled={locked} onClick={() => onJump({ id: chapter.id, chapter_id: chapter.id, chapter_index: chapter.index, chapter_title: chapterLabel(chapter), start: 0, end: 0, text: "", kind: chapter.issues[0]?.kind || "gap", label: chapter.issues[0]?.label || "待补充", reason: "", enabled: false, removable: false })}><span className="chapter-manual-fix">{chapter.placeholder ? "正文为空，点击补充本章" : chapter.issues.map((issue) => issue.label).join(" · ") || "打开正文检查"}</span></button>}
          {(details[chapter.id]?.length || 0) > (snippetLimits[chapter.id] || 4) && <button className="text-button chapter-more-snippets" onClick={() => setSnippetLimits((all) => ({ ...all, [chapter.id]: (all[chapter.id] || 4) + 12 }))}>展开更多标记 · 还有 {details[chapter.id].length - (snippetLimits[chapter.id] || 4)} 处</button>}
          </div>
        </div>)}
      </div>
      {(loading || collecting) && <Busy label={collecting ? `正在选择 ${collected} / ${eligible.length} 章` : "正在读取标记内容"} inline />}
      {filtered.length > SIZE && <div className="chapter-cleanup-pagination"><button className="button secondary compact-button" disabled={page <= 0 || locked} onClick={() => setPage((current) => current - 1)}><ChevronLeft size={14} />上一页</button><span>{page + 1} / {Math.ceil(filtered.length / SIZE)} 页 · {filtered.length} 章</span><button className="button secondary compact-button" disabled={(page + 1) * SIZE >= filtered.length || locked} onClick={() => setPage((current) => current + 1)}>下一页<ChevronRight size={14} /></button></div>}
    </div>
    <div className="modal-footer"><button className="button secondary" disabled={locked} onClick={onClose}>返回正文</button><button className="button primary" disabled={!selectedChapterCount || locked} onClick={() => void apply()}><Sparkles size={14} />清理所选 {selectedChapterCount} 章</button></div>
  </Modal>;
}
