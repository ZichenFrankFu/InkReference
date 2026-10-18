import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Check, ChevronLeft, ChevronRight, FilePlus2, ListChecks, Pencil, Plus, Search, Trash2 } from "lucide-react";
import { send, studio, type TextVersion } from "./studio";
import { Busy, Empty, Modal, Notice, messageOf } from "./ui";
import ChapterTextEditor, { ChapterTitleEditor, type ChapterEditorHandle, type TextHighlight } from "./ChapterTextEditor";
import ChapterCleanupDialog from "./ChapterCleanupDialog";
import { chapterLabel, issueClass, needsReview, type Chapter, type ChapterIndex, type ChapterPage, type Finding } from "./chapterTypes";
import "./chapter.css";

type Props = { documentId: string; version: TextVersion; onVersion: (id: string, summary?: TextVersion) => Promise<void>; onDirty: (dirty: boolean) => void; onComplete?: () => void; onActivity?: (label: string) => void };
type Draft = { body: string; original: string; expected: string; start: number; end: number; offsets?: number[]; chapterId: string; versionId: string; title: string; originalTitle: string };
const PAGE = 20000;
const CACHE_SIZE = 32;
function displayOffset(offsets: number[] | undefined, raw: number, start: number) {
  if (!offsets?.length) return Math.max(0, raw - start);
  let lo = 0, hi = offsets.length - 1;
  while (lo < hi) { const middle = Math.floor((lo + hi) / 2); if (offsets[middle] < raw) lo = middle + 1; else hi = middle; }
  return lo;
}
export default function ChapterWorkbench({ documentId, version, onVersion, onDirty, onComplete, onActivity }: Props) {
  const [index, setIndex] = useState<ChapterIndex | null>(null);
  const [active, setActive] = useState(0);
  const [page, setPage] = useState<ChapterPage | null>(null);
  const [pageStart, setPageStart] = useState(0);
  const pageStartRef = useRef(0); pageStartRef.current = pageStart;
  const [loading, setLoading] = useState(true);
  const [reading, setReading] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [edit, setEdit] = useState<Draft | null>(null);
  const [dialog, setDialog] = useState("");
  const [newTitle, setNewTitle] = useState("");
  const [newBody, setNewBody] = useState("");
  const [discardAction, setDiscardAction] = useState<(() => void) | null>(null);
  const cache = useRef(new Map<string, ChapterPage>());
  const request = useRef(0);
  const versionRef = useRef(version.id);
  const documentRef = useRef(documentId);
  const activeRef = useRef(active);
  const selectedChapterRef = useRef<string | undefined>(undefined);
  const workspaceRef = useRef<HTMLDivElement>(null);
  const navigationRef = useRef<HTMLDivElement>(null);
  const [navigationTop, setNavigationTop] = useState(0);
  const editorRef = useRef<ChapterEditorHandle>(null);
  const titleRef = useRef<HTMLInputElement>(null);
  const pendingFocus = useRef<Finding | null>(null);
  const onDirtyRef = useRef(onDirty); onDirtyRef.current = onDirty;
  const activityRef = useRef(onActivity); activityRef.current = onActivity;
  activeRef.current = active;
  const chapters = index?.chapters || [];
  const chapter = chapters[active];
  selectedChapterRef.current = chapter?.id;
  const dirty = !!edit && (edit.body !== edit.original || edit.title !== edit.originalTitle);
  const affected = useMemo(() => chapters.filter(needsReview), [chapters]);
  const actualCount = chapters.filter((part) => !part.placeholder).length;
  const missingCount = chapters.reduce((sum, part) => sum + (part.placeholder ? part.missing_count || 1 : 0), 0);
  const missing = chapter?.placeholder || chapter?.kind === "missing";
  useEffect(() => { onDirtyRef.current(dirty); }, [dirty]);
  useEffect(() => { activityRef.current?.(busy || (loading ? "正在读取章节" : reading ? "正在读取章节" : "")); }, [busy, loading, reading]);
  useEffect(() => () => { onDirtyRef.current(false); activityRef.current?.(""); }, []);

  useLayoutEffect(() => {
    const workspace = workspaceRef.current;
    if (!workspace) return;
    let frame = 0;
    const fit = () => {
      if (!workspace.getClientRects().length) return;
      const top = workspace.getBoundingClientRect().top;
      workspace.style.setProperty("--chapter-workspace-height", `${Math.max(410, Math.min(850, window.innerHeight - Math.max(0, top) - 26))}px`);
    };
    const schedule = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(fit); };
    const observer = new ResizeObserver(schedule);
    observer.observe(workspace.parentElement!); window.addEventListener("resize", schedule); fit();
    return () => { observer.disconnect(); window.removeEventListener("resize", schedule); cancelAnimationFrame(frame); };
  }, []);

  const fetchPage = useCallback(async (part: Chapter, start = 0) => {
    const key = `${version.id}:${part.id}:${start}`;
    const stored = cache.current.get(key);
    if (stored) { cache.current.delete(key); cache.current.set(key, stored); return stored; }
    const result = await studio<ChapterPage>(`/documents/${documentId}/versions/${version.id}/chapters/${encodeURIComponent(part.id)}?start=${start}&limit=${PAGE}&body_only=true`);
    if (versionRef.current !== version.id) return null;
    cache.current.set(key, result);
    while (cache.current.size > CACHE_SIZE) cache.current.delete(cache.current.keys().next().value!);
    return result;
  }, [documentId, version.id]);

  useEffect(() => {
    const ticket = ++request.current;
    const newDocument = documentRef.current !== documentId;
    const previousChapter = selectedChapterRef.current;
    versionRef.current = version.id; documentRef.current = documentId;
    cache.current.clear(); setLoading(true); setError("");
    if (newDocument) { setIndex(null); setPage(null); setEdit(null); setActive(0); setQuery(""); setFilter("all"); }
    void studio<ChapterIndex>(`/documents/${documentId}/versions/${version.id}/chapters`).then(async (next) => {
      if (ticket !== request.current) return;
      const matched = next.chapters.findIndex((part) => part.id === previousChapter);
      const a = newDocument ? 0 : matched >= 0 ? matched : Math.max(0, Math.min(activeRef.current, next.chapters.length - 1));
      const nextStart = !newDocument && next.chapters[a]?.id === previousChapter ? pageStartRef.current : 0;
      setIndex(next); setActive(a); setPageStart(nextStart);
      if (next.chapters[a]) {
        const result = await fetchPage(next.chapters[a], nextStart);
        if (ticket !== request.current || !result) return;
        setPage(result);
      } else { setPage(null); setEdit(null); }
    }).catch((e) => { if (ticket === request.current) setError(messageOf(e)); }).finally(() => { if (ticket === request.current) setLoading(false); });
  }, [documentId, version.id, fetchPage]);

  useLayoutEffect(() => {
    if (!page || page.id !== chapter?.id || (page.version_id && page.version_id !== version.id)) return;
    const start = page.editable_start ?? Math.max(page.start, page.body_offset || 0);
    const end = page.editable_end ?? page.end;
    const body = page.editable_text ?? page.body_text ?? Array.from(page.text).slice(Math.max(0, (page.body_offset || 0) - page.start)).join("");
    const title = page.placeholder || page.kind === "missing" ? "" : page.title;
    setEdit({ body, original: body, expected: page.expected_text ?? body, start, end, offsets: page.source_offset_map, chapterId: page.id, versionId: version.id, title, originalTitle: title });
  }, [page, chapter?.id, version.id]);

  useEffect(() => {
    const finding = pendingFocus.current;
    if (!finding || !edit || edit.chapterId !== finding.chapter_id || page?.id !== finding.chapter_id || reading || loading || edit.start !== (page.editable_start ?? page.start)) return;
    pendingFocus.current = null;
    if (finding.scope === "heading") { requestAnimationFrame(() => { const input = titleRef.current; if (!input) return; const points = Array.from(edit.title), offset = page?.title_source_start || 0; input.focus(); input.setSelectionRange(points.slice(0, Math.max(0, finding.start - offset)).join("").length, points.slice(0, Math.max(0, finding.end - offset)).join("").length); }); return; }
    const start = displayOffset(edit.offsets, finding.start, edit.start), end = displayOffset(edit.offsets, finding.end, edit.start);
    requestAnimationFrame(() => editorRef.current?.reveal(start, end));
  }, [edit?.chapterId, edit?.versionId, edit?.start, loading, reading]);

  const changePage = async (next: number, start = 0, force = false) => {
    const part = chapters[next];
    if (!part || (next === active && start === pageStart && page?.id === part.id)) return;
    if (dirty && !force) { setDiscardAction(() => () => { void changePage(next, start, true); }); return; }
    setActive(next); setPageStart(start); setError("");
    const saved = cache.current.get(`${version.id}:${part.id}:${start}`);
    const ticket = ++request.current;
    if (saved) { setPage(saved); setReading(false); return; }
    setReading(true);
    try { const result = await fetchPage(part, start); if (ticket === request.current && result) setPage(result); }
    catch (e) { if (ticket === request.current) { setError(messageOf(e)); if (page) { const old = chapters.findIndex((part) => part.id === page.id); setActive(Math.max(0, old)); setPageStart(page.start); } } }
    finally { if (ticket === request.current) setReading(false); }
  };
  const safeAction = (action: () => void) => dirty ? setDiscardAction(() => action) : action();
  const mutate = async (path: string, data: Record<string, unknown>, label: string) => {
    setBusy(label); setError("");
    try {
      const result = await send<TextVersion>(`/documents/${documentId}/chapters/${path}?summary=true`, { base_version_id: version.id, ...data });
      await onVersion(result.id, result);
      setDialog("");
    } catch (e) { setError(messageOf(e)); throw e; }
    finally { setBusy(""); }
  };
  const saveEdit = async () => {
    if (!chapter || chapter.gap_range || !edit || !dirty || edit.chapterId !== chapter.id || edit.versionId !== version.id) return;
    try {
      await mutate(missing ? "edit" : "edit-slice", missing ? { chapter_id: chapter.id, body_text: edit.body, title: edit.title } : { chapter_id: chapter.id, start: edit.start, end: edit.end, text: edit.body, expected_text: edit.expected, ...(edit.title !== edit.originalTitle ? { title: edit.title } : {}) }, "正在保存章节");
    } catch { /* Keep the user's draft and selection available for retry. */ }
  };
  const apply = async (selections: { chapter_id: string; proposal_ids: string[] }[]) => { await mutate("clean", { selections }, `正在清理 ${selections.length} 章`); };
  const jumpToFinding = (finding: Finding) => {
    setDialog(""); pendingFocus.current = finding;
    const next = chapters.findIndex((part) => part.id === finding.chapter_id);
    if (next < 0) return;
    if (finding.scope === "heading") {
      if (page?.id === finding.chapter_id && edit) {
        pendingFocus.current = null;
        const offset = page.title_source_start || 0, points = Array.from(edit.title);
        requestAnimationFrame(() => { titleRef.current?.focus(); titleRef.current?.setSelectionRange(points.slice(0, Math.max(0, finding.start - offset)).join("").length, points.slice(0, Math.max(0, finding.end - offset)).join("").length); });
      } else void changePage(next, 0, true);
      return;
    }
    if (page?.id === finding.chapter_id && edit && finding.start >= edit.start && finding.end <= edit.end) {
      pendingFocus.current = null;
      requestAnimationFrame(() => editorRef.current?.reveal(displayOffset(edit.offsets, finding.start, edit.start), displayOffset(edit.offsets, finding.end, edit.start)));
    } else void changePage(next, Math.max(0, Math.floor((finding.start - (chapters[next].body_start || chapters[next].start) + chapters[next].start) / PAGE) * PAGE), true);
  };
  const annotations = useMemo<TextHighlight[]>(() => {
    if (!page || !edit) return [];
    const before = Array.from(edit.original), after = Array.from(edit.body);
    let prefix = 0, oldEnd = before.length, newEnd = after.length;
    if (dirty) {
      while (prefix < oldEnd && prefix < newEnd && before[prefix] === after[prefix]) prefix++;
      while (oldEnd > prefix && newEnd > prefix && before[oldEnd - 1] === after[newEnd - 1]) { oldEnd--; newEnd--; }
    }
    return page.proposals.filter((proposal) => proposal.scope !== "heading" && proposal.end > edit.start && proposal.start < edit.end).flatMap((proposal) => {
      let start = displayOffset(edit.offsets, proposal.start, edit.start), end = displayOffset(edit.offsets, proposal.end, edit.start);
      if (dirty) {
        if (end <= prefix) { /* Retain marks in unchanged text. */ }
        else if (start >= oldEnd) { const delta = newEnd - oldEnd; start += delta; end += delta; }
        else return [];
      }
      return [{ id: proposal.id, start, end, label: proposal.reason || proposal.label, kind: issueClass(proposal.kind), selected: true, removable: proposal.removable }];
    });
  }, [page, edit?.body, edit?.original, edit?.start, dirty]);
  const titleAnnotations = useMemo<TextHighlight[]>(() => {
    if (!page || !edit || edit.title !== edit.originalTitle) return [];
    return (page.title_proposals || page.proposals.filter((proposal) => proposal.scope === "heading")).map((proposal) => ({ id: proposal.id, start: Math.max(0, proposal.start - (page.title_source_start || 0)), end: Math.max(0, proposal.end - (page.title_source_start || 0)), label: proposal.reason || proposal.label, kind: issueClass(proposal.kind), selected: true, removable: proposal.removable }));
  }, [page, edit?.title, edit?.originalTitle]);
  const manualFindings = useMemo(() => page?.proposals.filter((proposal) => !proposal.removable) || [], [page]);
  const filtered = useMemo(() => chapters.filter((part) => (!query || chapterLabel(part).toLocaleLowerCase().includes(query.toLocaleLowerCase()) || String(part.number ?? part.index + 1).includes(query)) && (filter === "all" || (filter === "review" ? needsReview(part) : part.issues.some((issue) => issueClass(issue.kind) === filter)))), [chapters, query, filter]);
  const virtual = filtered.length > 150;
  const virtualStart = virtual ? Math.max(0, Math.floor(navigationTop / 66) - 6) : 0;
  const directoryItems = virtual ? filtered.slice(virtualStart, virtualStart + 30) : filtered;
  useEffect(() => { if (navigationRef.current) navigationRef.current.scrollTop = 0; setNavigationTop(0); }, [query, filter]);
  useEffect(() => {
    if (!virtual || !navigationRef.current || !chapter) return;
    const at = filtered.findIndex((part) => part.id === chapter.id), target = navigationRef.current;
    if (at >= 0 && (at * 66 < target.scrollTop || (at + 1) * 66 > target.scrollTop + target.clientHeight)) target.scrollTop = Math.max(0, at * 66 - 66);
  }, [active, virtual]);
  const openCleanup = () => safeAction(() => { setError(""); setDialog("cleanup"); });
  const pageBodyLength = edit ? Array.from(edit.body).length : 0;
  const hasPrevious = pageStart > 0;
  const hasNext = !!page && (page.next_start !== undefined ? page.next_start !== null : (page.editable_end ?? page.end) < page.total);
  return <div className="chapter-workbench chapter-workbench-v2" aria-busy={loading || reading || !!busy || (!!page?.version_id && page.version_id !== version.id)}>
    <div className="chapter-workbench-toolbar"><div className="chapter-workbench-title"><strong>正文清理</strong><span>{actualCount} 章{missingCount ? ` · ${missingCount} 章待补全` : ""}</span></div><div className="inline-actions"><button className="button secondary compact-button" disabled={loading || !!busy || reading} onClick={() => openCleanup()}><ListChecks size={14} />批量清理{affected.length > 0 && <span className="chapter-counter">{affected.length}</span>}</button>{onComplete && <button className="button primary compact-button" disabled={loading || !!busy || reading} onClick={() => safeAction(onComplete)}><Check size={14} />完成清理</button>}</div></div>
    <div className="chapter-guide" aria-label="正文标记说明"><span><i className="chapter-guide-swatch removing" />红色：待清理</span><span><i className="chapter-guide-swatch diagnostic" />紫色：待检查</span><span><i className="chapter-guide-swatch missing" />空章：待补充</span></div>
    <div ref={workspaceRef} className="chapter-workspace">
      <aside className="chapter-navigation" aria-label="章节目录">
        <div className="chapter-navigation-top"><strong>章节目录</strong><button className="icon-button" title="添加章节" aria-label="添加章节" disabled={loading || !!busy} onClick={() => safeAction(() => { setNewTitle(""); setNewBody(""); setDialog("insert"); })}><Plus size={15} /></button></div>
        <label className="chapter-search"><Search size={14} /><input aria-label="搜索章节" placeholder="搜索章节" value={query} onChange={(e) => setQuery(e.target.value)} /></label>
        <div className="chapter-directory-filter"><button className={filter === "all" ? "active" : ""} onClick={() => setFilter("all")}>全部</button><button className={filter === "review" ? "active" : ""} onClick={() => setFilter("review")}>待检查 {affected.length || ""}</button></div>
        <div ref={navigationRef} className={`chapter-navigation-list ${virtual ? "virtual" : ""}`} onScroll={(event) => { if (virtual) setNavigationTop(event.currentTarget.scrollTop); }}>
          <div className="chapter-directory-items" style={virtual ? { height: filtered.length * 66, position: "relative" } : undefined}>
          {loading && !index ? <div className="chapter-directory-skeleton" aria-label="正在读取目录"><i /><i /><i /><i /></div> : !filtered.length ? <div className="chapter-small-empty">没有匹配的章节</div> : directoryItems.map((part, row) => <button style={virtual ? { position: "absolute", top: (virtualStart + row) * 66, left: 0, right: 0, height: 66 } : undefined} key={part.id} className={`chapter-navigation-item ${chapter?.id === part.id ? "active" : ""} ${part.placeholder ? "placeholder" : ""} ${needsReview(part) && !part.placeholder ? part.issues.some((issue) => issueClass(issue.kind) === "glyph" || issueClass(issue.kind) === "length") ? "has-diagnostic" : "has-removal" : ""}`} disabled={loading || !!busy} onClick={() => void changePage(chapters.findIndex((item) => item.id === part.id))} aria-current={chapter?.id === part.id ? "location" : undefined}><span className="chapter-navigation-copy"><strong>{chapterLabel(part)}</strong><small>{part.placeholder ? "待补充正文" : `${part.char_count.toLocaleString()} 字`}</small></span></button>)}
          </div>
        </div>
      </aside>
      <section className="chapter-reading-pane">
        <div className="chapter-reader-header"><div className="chapter-heading-fields"><span className="chapter-reader-overline">{chapter ? `${active + 1} / ${chapters.length}` : "章节正文"}</span>{chapter && edit ? <div className="chapter-editable-heading">{chapter.kind !== "preamble" && <strong className="chapter-number-label">第{chapter.number ?? chapter.index + 1}章</strong>}<ChapterTitleEditor ref={titleRef} value={edit.title} highlights={titleAnnotations} readOnly={!!chapter.gap_range || !!busy || reading || loading || page?.id !== chapter.id || edit.versionId !== version.id} placeholder={missing ? "章节标题" : ""} onChange={(title) => setEdit((draft) => draft ? { ...draft, title } : null)} /></div> : <h3>{chapter ? chapterLabel(chapter) : "选择章节"}</h3>}</div><div className="chapter-reader-actions"><button className="icon-button" title="上一章" aria-label="上一章" disabled={loading || active <= 0 || !!busy} onClick={() => void changePage(active - 1)}><ChevronLeft size={16} /></button><button className="icon-button" title="下一章" aria-label="下一章" disabled={loading || active >= chapters.length - 1 || !!busy} onClick={() => void changePage(active + 1)}><ChevronRight size={16} /></button><button className="button secondary compact-button chapter-delete-button" disabled={!chapter || !!missing || !!busy || reading || loading} onClick={() => safeAction(() => setDialog("delete"))}><Trash2 size={14} /><span>删除本章</span></button></div></div>
        {!!page && page.id === chapter?.id && (!!page.structured_issues?.length || manualFindings.length > 0) && <div className="chapter-inline-findings" aria-label="本章问题位置">{page.structured_issues?.map((issue) => <span className="chapter-structural-issue" key={issue.id} title={issue.reason}>{issue.label}{issue.reason && issue.reason !== issue.label ? ` · ${issue.reason}` : ""}</span>)}{manualFindings.slice(0, 4).map((finding) => <button className="chapter-inline-finding" key={finding.id} disabled={dirty || !!busy || reading || loading} onClick={() => jumpToFinding({ ...finding, chapter_id: page.id, chapter_index: page.index, chapter_title: chapterLabel(page) })}><span>{finding.scope === "heading" ? "章节名 · " : ""}{finding.label}</span><code>{/^[\p{Cf}\p{Cc}\s]*$/u.test(finding.text) ? "〔隐藏字符〕" : Array.from(finding.text).slice(0, 16).join("")}</code>{finding.scope !== "heading" && (finding.start < (edit?.start || 0) || finding.end > (edit?.end || 0)) ? <small>第{Math.floor(finding.start / PAGE) + 1}段</small> : null}</button>)}</div>}
        <div className="chapter-notice"><Notice error>{error}</Notice></div>
        <article className={`chapter-reader-scroll ${reading ? "pending" : ""}`} aria-label={chapter ? chapterLabel(chapter) : "章节正文"}>
          {edit && page ? <ChapterTextEditor key={`${page.id}:${edit.start}`} ref={editorRef} value={edit.body} highlights={annotations} readOnly={!!chapter?.gap_range || !!busy || reading || loading || page.id !== chapter?.id || edit.versionId !== version.id} placeholder={chapter?.gap_range ? "缺章范围过大，请先调整章节编号，或手动添加指定章节。" : missing ? "在这里补充本章正文" : ""} onChange={(body) => setEdit((draft) => draft ? { ...draft, body } : null)} /> : !loading ? <Empty icon={FilePlus2} title="尚无章节" description="上传全文或添加第一章。" /> : <div className="chapter-reader-skeleton"><i /><i /><i /><i /><i /></div>}
        </article>
        <div className="chapter-reader-bottom"><div className="chapter-save-state">{dirty && <span className="chapter-unsaved-label"><Pencil size={13} />未保存</span>}<small>{pageBodyLength.toLocaleString()} 字</small></div><div className="inline-actions">{dirty && <><button className="text-button" disabled={!!busy} onClick={() => setDiscardAction(() => () => setEdit((draft) => draft ? { ...draft, body: draft.original, title: draft.originalTitle } : null))}>撤销</button><button className="button primary compact-button" disabled={!!busy || reading || loading} onClick={() => void saveEdit()}><Check size={14} />保存</button></>}</div></div>
        {!!page && (hasPrevious || hasNext) && <div className="chapter-page-controls"><button className="text-button" disabled={!hasPrevious || reading || !!busy} onClick={() => void changePage(active, Math.max(0, pageStart - PAGE))}><ChevronLeft size={13} />上一段</button><span>正文分段 {Math.floor(pageStart / PAGE) + 1}</span><button className="text-button" disabled={!hasNext || reading || !!busy} onClick={() => void changePage(active, page.next_start ?? page.editable_end ?? page.end)}>下一段<ChevronRight size={13} /></button></div>}
      </section>
    </div>
    {dialog === "cleanup" && <ChapterCleanupDialog documentId={documentId} versionId={version.id} chapters={chapters} busy={!!busy} error={error} onClose={() => setDialog("")} onJump={jumpToFinding} onApply={apply} />}
    {dialog === "insert" && <Modal title="添加章节" busy={!!busy} wide onClose={() => setDialog("")}><div className="modal-body"><label className="studio-field"><span>章节标题</span><input aria-label="章节标题" maxLength={80} value={newTitle} onChange={(e) => setNewTitle(e.target.value)} /></label><label className="studio-field"><span>章节正文</span><textarea aria-label="新章节正文" rows={10} value={newBody} onChange={(e) => setNewBody(e.target.value)} /></label><Notice error>{error}</Notice>{busy && <Busy label={busy} inline />}</div><div className="modal-footer"><button className="button secondary" disabled={!!busy} onClick={() => setDialog("")}>取消</button><button className="button primary" disabled={!!busy || !newTitle.trim()} onClick={() => { void mutate("insert", { heading: /^(?:第[0-9零〇一二三四五六七八九十百千万两]+[章回节]|chapter\s+\d+)/iu.test(newTitle.trim()) ? newTitle.trim() : `第${(chapter?.number ?? active + 1) + (missing ? 0 : 1)}章 ${newTitle.trim()}`, body_text: newBody, after_chapter_id: missing ? chapters.slice(0, active).reverse().find((part) => !part.placeholder)?.id : chapter?.id }, "正在添加章节").catch(() => {}); }}>保存</button></div></Modal>}
    {dialog === "delete" && chapter && <Modal title="删除章节" busy={!!busy} onClose={() => setDialog("")}><div className="modal-body"><p>删除《{chapterLabel(chapter)}》？</p><Notice error>{error}</Notice>{busy && <Busy label={busy} inline />}</div><div className="modal-footer"><button className="button secondary" disabled={!!busy} onClick={() => setDialog("")}>取消</button><button className="button danger" disabled={!!busy} onClick={() => void mutate("delete", { chapter_ids: [chapter.id] }, "正在删除章节").catch(() => {})}>删除</button></div></Modal>}
    {discardAction && <Modal title="章节尚未保存" onClose={() => setDiscardAction(null)}><div className="modal-body">保留当前修改，或放弃后继续。</div><div className="modal-footer"><button className="button secondary" onClick={() => setDiscardAction(null)}>继续编辑</button><button className="button danger" onClick={() => { const action = discardAction; setDiscardAction(null); setError(""); setEdit((draft) => draft ? { ...draft, body: draft.original, title: draft.originalTitle } : null); onDirtyRef.current(false); action(); }}>放弃修改并继续</button></div></Modal>}
  </div>;
}
