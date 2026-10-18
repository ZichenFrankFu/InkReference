import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { AlignLeft, Check, ChevronDown, ChevronLeft, ChevronRight, CircleAlert, FilePlus2, ListChecks, ListFilter, Pencil, Plus, RotateCcw, Search, Settings2, Sparkles, Trash2 } from "lucide-react";
import { send, studio, type TextVersion } from "./studio";
import { Busy, Empty, Modal, Notice, messageOf } from "./ui";
import ChapterTextEditor, { type ChapterEditorHandle, type TextHighlight } from "./ChapterTextEditor";
import "./chapter.css";

type Issue = { kind: string; label: string; count?: number; severity?: string };
type Chapter = { id: string; index: number; number?: number; title: string; raw_marker?: string; kind?: string; start: number; end: number; body_start?: number; char_count: number; issues: Issue[]; proposal_count: number; proposal_ids?: string[]; enabled_proposal_ids?: string[] };
type Format = { name: string; format?: string; count?: number; score?: number; enabled?: boolean; custom?: boolean };
type CustomFormat = { name: string; format?: string; regex?: string; enabled: boolean };
type ChapterIndex = { version_id: string; pattern?: string; heading_formats?: string[]; candidates?: Format[]; gaps?: { missing_numbers?: number[]; after_number?: number; before_number?: number; missing_count?: number }[]; chapters: Chapter[]; options?: { author_keywords?: string[]; garble_rules?: { name: string; pattern: string; enabled: boolean }[]; custom_formats?: { name: string; format?: string; regex?: string; enabled: boolean }[] }; author_keyword_defaults?: string[]; };
type Proposal = { id: string; start: number; end: number; text: string; kind: string; label: string; reason: string; enabled: boolean; removable: boolean };
type Finding = Proposal & { chapter_id: string; chapter_index: number; chapter_title: string };
type ChapterPage = Chapter & { chapter?: Chapter; text: string; start: number; end: number; total: number; body_offset: number; body_text?: string | null; editable_start?: number; editable_end?: number; editable_text?: string; proposals: Proposal[]; diagnostics?: { start: number; end: number; kind?: string; code?: string; message?: string; label?: string }[] };
type Props = { documentId: string; version: TextVersion; onVersion: (id: string) => Promise<void>; onDirty: (dirty: boolean) => void };
const PAGE = 20000;
const issueClass = (kind: string) => /glyph|garbl|mojibake|replacement/.test(kind) ? "glyph" : /author|aside|nonbody|non_body/.test(kind) ? "aside" : /length|gap|duplicate|outlier/.test(kind) ? "length" : "noise";
const formatLabel = (f: Format | string) => typeof f === "string" ? f : f.name;

export default function ChapterWorkbench({ documentId, version, onVersion, onDirty }: Props) {
  const [index, setIndex] = useState<ChapterIndex | null>(null);
  const [active, setActive] = useState(0);
  const [page, setPage] = useState<ChapterPage | null>(null);
  const [pageStart, setPageStart] = useState(0);
  const [loading, setLoading] = useState(true);
  const [reading, setReading] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [selections, setSelections] = useState<Record<string, string[]>>({});
  const [edit, setEdit] = useState<{ body: string; original: string; start: number; end: number; partial: boolean; chapterId: string; versionId: string } | null>(null);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [findingScope, setFindingScope] = useState("all");
  const [findingKind, setFindingKind] = useState("all");
  const [findingQuery, setFindingQuery] = useState("");
  const [findingLimit, setFindingLimit] = useState(100);
  useEffect(() => { setFindingLimit(100); }, [findingScope, findingKind, findingQuery]);
  const [dialog, setDialog] = useState("");
  const [formats, setFormats] = useState<string[]>([]);
  const [customFormat, setCustomFormat] = useState("");
  const [customName, setCustomName] = useState("");
  const [customRegex, setCustomRegex] = useState("");
  const [customFormats, setCustomFormats] = useState<CustomFormat[]>([]);
  const [chosenChapters, setChosenChapters] = useState<string[]>([]);
  const [deleteIds, setDeleteIds] = useState<string[]>([]);
  const [newTitle, setNewTitle] = useState("");
  const [newBody, setNewBody] = useState("");
  const [keywords, setKeywords] = useState("");
  const [garbleRules, setGarbleRules] = useState<{ name: string; pattern: string; enabled: boolean }[]>([]);
  const [discardAction, setDiscardAction] = useState<(() => void) | null>(null);
  const cache = useRef(new Map<string, ChapterPage>());
  const retainedProposals = useRef(new Set<string>());
  const request = useRef(0);
  const versionRef = useRef(version.id);
  const documentRef = useRef(documentId);
  const activeRef = useRef(active);
  const pageRef = useRef<HTMLElement>(null);
  const workspaceRef = useRef<HTMLDivElement>(null);
  const editorRef = useRef<ChapterEditorHandle>(null);
  const pendingFocus = useRef<{ chapterId: string; start: number; end: number } | null>(null);
  const onDirtyRef = useRef(onDirty);
  onDirtyRef.current = onDirty;
  activeRef.current = active;
  const chapters = index?.chapters || [];
  const chapter = chapters[active];
  const selectedIds = chapter ? selections[chapter.id] || [] : [];
  const markedCount = Object.values(selections).reduce((sum, ids) => sum + ids.length, 0);
  const editDirty = !!edit && edit.body !== edit.original;
  useEffect(() => { onDirtyRef.current(editDirty); }, [editDirty]);
  useEffect(() => () => onDirtyRef.current(false), []);

  useLayoutEffect(() => {
    const workspace = workspaceRef.current;
    if (!workspace) return;
    const workbench = workspace.parentElement!;
    const container = workbench.closest<HTMLElement>(".document-content, .reference-preprocess-panel") || workbench;
    let frame = 0;
    const fit = () => {
      // Document coordinates keep the reader height stable while the page scrolls.
      if (!workspace.getClientRects().length) return;
      const top = workspace.getBoundingClientRect().top + window.scrollY;
      const footer = workbench.querySelector<HTMLElement>(".chapter-workbench-footer")?.offsetHeight || 43;
      const documentFooter = container.querySelector<HTMLElement>(".document-footer")?.offsetHeight || 0;
      const available = window.innerHeight - top - footer - documentFooter - 24;
      const minimum = window.innerWidth <= 760 ? 420 : 340;
      workspace.style.setProperty("--chapter-workspace-height", `${Math.max(minimum, Math.min(760, available))}px`);
    };
    const schedule = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(fit); };
    fit();
    const observer = new ResizeObserver(schedule);
    observer.observe(container);
    const preceding = container.parentElement;
    if (preceding && preceding !== container) observer.observe(preceding);
    window.addEventListener("resize", schedule);
    return () => { observer.disconnect(); window.removeEventListener("resize", schedule); cancelAnimationFrame(frame); };
  }, []);

  const fetchPage = useCallback(async (part: Chapter, start = 0, prefetch = false) => {
    const key = `${version.id}:${part.id}:${start}`;
    const stored = cache.current.get(key);
    if (stored) return stored;
    const result = await studio<ChapterPage>(`/documents/${documentId}/versions/${version.id}/chapters/${encodeURIComponent(part.id)}?start=${start}&limit=${PAGE}`);
    if (versionRef.current === version.id) cache.current.set(key, result);
    if (!prefetch && versionRef.current !== version.id) return null;
    return result;
  }, [documentId, version.id]);

  useEffect(() => {
    const ticket = ++request.current;
    const newDocument = documentRef.current !== documentId;
    versionRef.current = version.id;
    documentRef.current = documentId;
    cache.current.clear();
    setLoading(true); setError(""); setEdit(null); setDialog(""); setCustomName(""); setCustomFormat(""); setCustomRegex("");
    if (newDocument) { retainedProposals.current.clear(); setPage(null); setActive(0); setQuery(""); setFilter("all"); }
    void Promise.all([studio<ChapterIndex>(`/documents/${documentId}/versions/${version.id}/chapters`), studio<{ items: Finding[] }>(`/documents/${documentId}/versions/${version.id}/findings`)]).then(async ([next, checks]) => {
      if (ticket !== request.current) return;
      setIndex(next); setFindings([...checks.items, ...next.chapters.flatMap((c) => c.issues.filter((issue) => !checks.items.some((finding) => finding.chapter_id === c.id && issue.kind === finding.kind)).map((issue) => ({ id: `${c.id}-${issue.kind}`, chapter_id: c.id, chapter_index: c.index, chapter_title: c.title, start: (c.body_start || c.start) - c.start, end: (c.body_start || c.start) - c.start + 1, text: c.title, kind: issue.kind, label: issue.label, reason: issue.label, enabled: false, removable: false }))) ]);
      setFormats(next.heading_formats || []);
      setCustomFormats(next.options?.custom_formats || []);
      setChosenChapters([]);
      setKeywords((next.options?.author_keywords ?? next.author_keyword_defaults ?? []).join("\n"));
      setGarbleRules(next.options?.garble_rules || []);
      setSelections(Object.fromEntries(next.chapters.map((c) => [c.id, (c.enabled_proposal_ids || c.proposal_ids || []).filter((id) => !retainedProposals.current.has(id))])));
      const a = newDocument ? 0 : Math.min(activeRef.current, next.chapters.length - 1);
      setActive(Math.max(0, a)); setPageStart(0);
      if (next.chapters[a]) {
        const result = await fetchPage(next.chapters[a]);
        if (ticket !== request.current || !result) return;
        setPage(result);
        if (!next.chapters[a].enabled_proposal_ids) setSelections((s) => ({ ...s, [next.chapters[a].id]: result.proposals.filter((p) => p.enabled && p.removable).map((p) => p.id) }));
        if (next.chapters[a + 1]) void fetchPage(next.chapters[a + 1], 0, true).catch(() => {});
      } else setPage(null);
    }).catch((e) => { if (ticket === request.current) setError(messageOf(e)); }).finally(() => { if (ticket === request.current) setLoading(false); });
  }, [documentId, version.id, fetchPage]);

  useLayoutEffect(() => {
    if (!page || page.id !== chapter?.id) return;
    const partial = typeof page.body_text !== "string";
    const start = partial ? page.editable_start ?? Math.max(page.start, page.body_offset) : page.body_offset;
    const body = partial ? page.editable_text ?? Array.from(page.text).slice(Math.max(0, page.body_offset - page.start)).join("") : page.body_text!;
    setEdit({ body, original: body, start, end: partial ? page.editable_end ?? page.end : page.total, partial, chapterId: page.id, versionId: version.id });
  }, [page, chapter?.id, version.id]);
  useEffect(() => {
    const focus = pendingFocus.current;
    if (!focus || !edit || !page || focus.chapterId !== page.id || edit.chapterId !== page.id || edit.versionId !== version.id || reading) return;
    pendingFocus.current = null;
    const ticket = request.current;
    requestAnimationFrame(() => { if (ticket === request.current) editorRef.current?.reveal(focus.start - edit.start, focus.end - edit.start); });
  }, [edit?.original, edit?.start, edit?.chapterId, edit?.versionId, page?.id, reading, version.id]);

  const changePage = async (next: number, start = 0, force = false) => {
    const part = chapters[next];
    if (!part || (next === active && start === pageStart && page?.id === part.id && page.start === start)) return;
    if (editDirty && !force) { setDiscardAction(() => () => { void changePage(next, start, true); }); return; }
    setActive(next); setPageStart(start); setError("");
    const key = `${version.id}:${part.id}:${start}`;
    const saved = cache.current.get(key);
    const ticket = ++request.current;
    if (saved) { setPage(saved); setReading(false); pageRef.current?.scrollTo({ top: 0 }); return; }
    setReading(true);
    try {
      const result = await fetchPage(part, start);
      if (ticket !== request.current || !result) return;
      setPage(result);
      if (!part.enabled_proposal_ids) setSelections((s) => ({ ...s, [part.id]: s[part.id] || result.proposals.filter((p) => p.enabled && p.removable).map((p) => p.id) }));
      pageRef.current?.scrollTo({ top: 0 });
      if (chapters[next + 1]) void fetchPage(chapters[next + 1], 0, true).catch(() => {});
    } catch (e) { if (ticket === request.current) { setError(messageOf(e)); if (page) { setActive(page.index); setPageStart(page.start); } } }
    finally { if (ticket === request.current) setReading(false); }
  };
  const safeAction = (action: () => void) => editDirty ? setDiscardAction(() => action) : action();
  const mutate = async (path: string, data: Record<string, unknown>, label: string) => {
    setBusy(label); setError("");
    try {
      const result = await send<TextVersion>(`/documents/${documentId}/chapters/${path}`, { base_version_id: version.id, ...data });
      setEdit(null); onDirtyRef.current(false);
      await onVersion(result.id);
      setDialog("");
    } catch (e) { setError(messageOf(e)); }
    finally { setBusy(""); }
  };
  const toggleFinding = (chapterId: string, id: string) => {
    setSelections((all) => {
      const current = all[chapterId] || [];
      if (current.includes(id)) retainedProposals.current.add(id); else retainedProposals.current.delete(id);
      return { ...all, [chapterId]: current.includes(id) ? current.filter((p) => p !== id) : [...current, id] };
    });
  };
  const apply = (all = false) => {
    if (!chapter || editDirty) return;
    void mutate("clean", { selections: (all ? chapters : [chapter]).map((c) => ({ chapter_id: c.id, proposal_ids: selections[c.id] || [] })).filter((c) => c.proposal_ids.length) }, "正在保存清洗版本");
  };
  const saveEdit = () => {
    if (!chapter || !edit || !editDirty || edit.chapterId !== chapter.id || edit.versionId !== version.id) return;
    void mutate(edit.partial ? "edit-slice" : "edit", edit.partial ? { chapter_id: chapter.id, start: edit.start, end: edit.end, text: edit.body, expected_text: edit.original } : { chapter_id: chapter.id, body_text: edit.body }, "正在保存章节修订");
  };
  const filtered = chapters.filter((c) => (!query || c.title.toLocaleLowerCase().includes(query.toLocaleLowerCase()) || String(c.number ?? c.index + 1).includes(query)) && (filter === "all" || (filter === "marked" ? (selections[c.id]?.length || 0) > 0 : c.issues.some((issue) => issueClass(issue.kind) === filter))));
  const visibleFindings = findings.filter((item) => (findingScope === "all" || item.chapter_id === chapter?.id) && (findingKind === "all" || issueClass(item.kind) === findingKind) && (!findingQuery || `${item.chapter_title} ${item.text} ${item.reason}`.toLocaleLowerCase().includes(findingQuery.toLocaleLowerCase())));
  const removableFindings = visibleFindings.filter((item) => item.removable);
  const allFindingsSelected = removableFindings.length > 0 && removableFindings.every((item) => selections[item.chapter_id]?.includes(item.id));
  const selectFindings = (checked: boolean) => {
    setSelections((all) => {
      const next = { ...all };
      for (const item of removableFindings) {
        const ids = next[item.chapter_id] || [];
        next[item.chapter_id] = checked ? [...new Set([...ids, item.id])] : ids.filter((id) => id !== item.id);
        if (checked) retainedProposals.current.delete(item.id); else retainedProposals.current.add(item.id);
      }
      return next;
    });
  };
  const jumpToFinding = (finding: Finding) => {
    safeAction(() => {
      setDialog("");
      pendingFocus.current = { chapterId: finding.chapter_id, start: finding.start, end: finding.end };
      if (page?.id === finding.chapter_id && edit && finding.start >= edit.start && finding.end <= edit.end) {
        pendingFocus.current = null;
        requestAnimationFrame(() => editorRef.current?.reveal(finding.start - edit.start, finding.end - edit.start));
      } else void changePage(finding.chapter_index, Math.floor(finding.start / PAGE) * PAGE, true);
    });
  };
  const annotations = useMemo<TextHighlight[]>(() => {
    if (!page || !edit) return [];
    const before = Array.from(edit.original), after = Array.from(edit.body);
    let prefix = 0, oldEnd = before.length, newEnd = after.length;
    if (editDirty) {
      while (prefix < oldEnd && prefix < newEnd && before[prefix] === after[prefix]) prefix++;
      while (oldEnd > prefix && newEnd > prefix && before[oldEnd - 1] === after[newEnd - 1]) { oldEnd--; newEnd--; }
    }
    return page.proposals.filter((p) => p.end > edit.start && p.start < edit.end).flatMap((p) => {
      let start = Math.max(0, p.start - edit.start), end = Math.min(before.length, p.end - edit.start);
      if (editDirty) {
        if (end <= prefix) { /* Original prefix remains exact. */ }
        else if (start >= oldEnd) { const delta = newEnd - oldEnd; start += delta; end += delta; }
        else return [];
      }
      return [{ id: p.id, start, end, label: p.reason || p.label, kind: issueClass(p.kind), selected: selectedIds.includes(p.id), removable: p.removable }];
    });
  }, [page, edit?.body, edit?.original, edit?.start, selectedIds, editDirty]);
  const openSettings = () => { setDialog("settings"); setError(""); };
  return <div className="chapter-workbench" aria-busy={loading || reading || !!busy}>
    <div className="chapter-workbench-toolbar">
      <div className="chapter-format-summary"><AlignLeft size={15} /><span>{index?.pattern || "章节识别"}</span><span className="chapter-small-tag">{chapters.length} 章</span></div>
      <div className="inline-actions">
        <button className="text-button" disabled={loading || !!busy} onClick={() => safeAction(openSettings)}><Settings2 size={14} />识别设置</button>
        <button className="button secondary compact-button" disabled={loading || !!busy} onClick={() => { setFindingScope("all"); setFindingKind("all"); setDialog("findings"); }}><ListChecks size={14} />问题清单{findings.length > 0 && <span className="chapter-counter">{findings.length}</span>}</button>
      </div>
    </div>
    <div ref={workspaceRef} className="chapter-workspace">
      <aside className="chapter-navigation" aria-label="章节目录">
        <div className="chapter-navigation-top"><strong>章节目录</strong><button className="icon-button" title="添加章节" aria-label="添加章节" disabled={loading || !!busy} onClick={() => safeAction(() => { setNewTitle(`第${chapters.length + 1}章 `); setNewBody(""); setDialog("insert"); })}><Plus size={15} /></button></div>
        <label className="chapter-search"><Search size={14} /><input aria-label="搜索章节" placeholder="搜索章节" value={query} onChange={(e) => setQuery(e.target.value)} /></label>
        <label className="chapter-filter"><ListFilter size={13} /><select aria-label="筛选章节问题" value={filter} onChange={(e) => setFilter(e.target.value)}><option value="all">全部章节</option><option value="marked">待清理</option><option value="aside">非正文</option><option value="glyph">字符缺失 / 乱码</option><option value="length">长度 / 章节异常</option><option value="noise">采集噪声</option></select><ChevronDown size={12} /></label>
        <div className="chapter-navigation-list">
          {loading && !index ? <Busy label="正在识别章节" compact /> : !filtered.length ? <div className="chapter-small-empty">没有匹配的章节</div> : filtered.map((part) => <div key={part.id} className="chapter-navigation-row"><label className="chapter-selection-check"><input type="checkbox" aria-label={`选择章节 ${part.title || part.index + 1}`} disabled={loading || !!busy} checked={chosenChapters.includes(part.id)} onChange={(e) => setChosenChapters((current) => e.target.checked ? [...current,part.id] : current.filter((id) => id!==part.id))}/></label><button className={`chapter-navigation-item ${chapter?.id === part.id ? "active" : ""}`} disabled={loading || !!busy} onClick={() => void changePage(part.index)} aria-current={chapter?.id === part.id ? "location" : undefined}>
            <span className="chapter-order">{String(part.number ?? part.index + 1).padStart(2, "0")}</span><span className="chapter-navigation-copy"><strong>{part.title || `第 ${part.index + 1} 章`}</strong><small>{part.char_count.toLocaleString()} 字{(selections[part.id]?.length || 0) > 0 && <span className="chapter-pending-count"> · {selections[part.id].length} 处待清理</span>}</small>{!!part.issues.length && <span className="chapter-issue-tags">{part.issues.map((issue, i) => <span key={`${issue.kind}-${i}`} className={`chapter-issue ${issueClass(issue.kind)}`} title={`${issue.label}${issue.count ? ` · ${issue.count} 处` : ""}`}>{issue.label}</span>)}</span>}</span>
          </button></div>)}
        </div>
        <div className="chapter-navigation-bottom">{chosenChapters.length ? <><button className="text-button" onClick={() => setChosenChapters([])}>取消选择</button><button className="text-button chapter-selected-delete" disabled={!!busy || loading} onClick={() => safeAction(() => { setDeleteIds(chosenChapters); setDialog("delete"); })}><Trash2 size={12}/>删除所选 {chosenChapters.length} 章</button></> : <><span><i className="issue-dot aside" />非正文</span><span><i className="issue-dot glyph" />字符问题</span></>}</div>
      </aside>
      <section className="chapter-reading-pane">
        <div className={`chapter-loading-track ${loading || reading || busy ? "is-loading" : ""}`}><div className="indeterminate" role={loading || reading || busy ? "progressbar" : undefined} aria-label={busy || (reading ? "正在读取章节" : "正在读取章节目录")}><i /></div></div>
        <div className="chapter-reader-header"><div><span className="chapter-reader-overline">{chapter ? `章节 ${active + 1} / ${chapters.length}` : "章节预览"}</span><h3>{chapter?.title || "选择章节"}</h3></div><div className="chapter-reader-actions"><button className="icon-button" title="上一章" aria-label="上一章" disabled={loading || active <= 0 || !!busy} onClick={() => void changePage(active - 1)}><ChevronLeft size={16} /></button><button className="icon-button" title="下一章" aria-label="下一章" disabled={loading || active >= chapters.length - 1 || !!busy} onClick={() => void changePage(active + 1)}><ChevronRight size={16} /></button><span className="chapter-action-separator" /><button className="icon-button" aria-label="重命名章节" title="重命名章节" disabled={!chapter || !!busy || reading} onClick={() => safeAction(() => { setNewTitle(chapter.title); setDialog("rename"); })}><AlignLeft size={15} /></button><button className="icon-button chapter-delete-button" aria-label="删除章节" title="删除章节" disabled={!chapter || !!busy || reading} onClick={() => safeAction(() => { setDeleteIds([chapter.id]); setDialog("delete"); })}><Trash2 size={14} /></button></div></div>
        <div className="chapter-notice"><Notice error>{error}</Notice></div>
        {!!chapter?.issues.length && <div className="chapter-current-issues">{chapter.issues.map((issue, i) => <button key={`${issue.kind}-${i}`} className={`chapter-issue ${issueClass(issue.kind)}`} onClick={() => { setFindingScope("chapter"); setFindingKind(issueClass(issue.kind)); setDialog("findings"); }}>{issue.label}{issue.count ? ` · ${issue.count}` : ""}</button>)}</div>}
        <article ref={pageRef} className={`chapter-reader-scroll ${reading ? "pending" : ""}`} aria-label={chapter?.title || "章节正文"}>
          {edit && page ? <ChapterTextEditor key={`${version.id}:${page.id}:${edit.start}`} ref={editorRef} value={edit.body} highlights={annotations} readOnly={!!busy || reading || page.id !== chapter?.id} onChange={(body) => setEdit((draft) => draft ? { ...draft, body } : null)} /> : !loading ? <Empty icon={FilePlus2} title="尚无章节" description="上传全文或添加第一章。" /> : <div className="chapter-reader-skeleton"><i /><i /><i /><i /><i /></div>}
        </article>
        <div className="chapter-reader-bottom">
          <div className="chapter-clean-legend">{editDirty ? <span className="chapter-unsaved-label"><Pencil size={13} />正文已修改 · 尚未保存</span> : <span><i className="chapter-removal-swatch" />红色待清理 · 紫色待修复</span>}<small>{edit ? `${Array.from(edit.body).length.toLocaleString()} 字${edit.partial ? " · 当前段" : ""}` : ""}</small></div>
          <div className="inline-actions">{editDirty ? <><button className="text-button" disabled={!!busy} onClick={() => setDiscardAction(() => () => setEdit((draft) => draft ? { ...draft, body: draft.original } : null))}>撤销修订</button><button className="button primary compact-button" disabled={!!busy || reading} onClick={saveEdit}><Check size={14} />保存正文</button></> : <><button className="text-button" disabled={!chapter || !chapter.proposal_count || reading || !!busy} onClick={() => { const ids = chapter.proposal_ids || chapter.enabled_proposal_ids || page?.proposals.filter((p) => p.removable).map((p) => p.id) || []; for (const id of ids) { if (selectedIds.length) retainedProposals.current.add(id); else retainedProposals.current.delete(id); } setSelections((s) => ({ ...s, [chapter.id]: selectedIds.length ? [] : ids })); }}>{selectedIds.length ? "全部保留" : "全选本章标记"}</button><button className="button primary compact-button" disabled={!selectedIds.length || !!busy || reading} onClick={() => apply()}><Sparkles size={14} />清理本章{selectedIds.length ? ` · ${selectedIds.length}` : ""}</button></>}</div>
        </div>
        {!!page && edit?.partial && page.total > PAGE && <div className="chapter-page-controls"><button className="text-button" disabled={pageStart <= 0 || reading || !!busy} onClick={() => void changePage(active, Math.max(0, pageStart - PAGE))}><ChevronLeft size={13} />上一段</button><span>{(pageStart + 1).toLocaleString()}–{Math.min(pageStart + Array.from(page.text).length, page.total).toLocaleString()} / {page.total.toLocaleString()} 字</span><button className="text-button" disabled={pageStart + Array.from(page.text).length >= page.total || reading || !!busy} onClick={() => void changePage(active, pageStart + Array.from(page.text).length)}>下一段<ChevronRight size={13} /></button></div>}
      </section>
    </div>
    <div className="chapter-workbench-footer"><span>{chapters.filter((c) => c.issues.length).length} 章需要检查{markedCount ? ` · ${markedCount} 处待清理` : ""}{index?.gaps?.length ? ` · ${index.gaps.length} 处章节不连续` : ""}</span><button className="button primary compact-button" disabled={!markedCount || !!busy || reading || editDirty} onClick={() => setDialog("batch")}><Sparkles size={13} />清理已选{markedCount > 0 ? ` · ${markedCount} 处` : "标记"}</button></div>

    {dialog === "findings" && <Modal title="正文问题清单" wide busy={!!busy} onClose={() => setDialog("")}><div className="modal-body chapter-findings-modal"><div className="chapter-findings-filters"><label className="chapter-search"><Search size={14}/><input aria-label="搜索问题标记" placeholder="搜索章节、标记内容" value={findingQuery} onChange={(e) => setFindingQuery(e.target.value)}/></label><select aria-label="问题章节范围" value={findingScope} onChange={(e) => setFindingScope(e.target.value)}><option value="all">全部章节</option><option value="chapter">当前章节</option></select><select aria-label="问题类型" value={findingKind} onChange={(e) => setFindingKind(e.target.value)}><option value="all">全部问题</option><option value="aside">非正文</option><option value="glyph">字符缺失 / 乱码</option><option value="noise">采集噪声</option><option value="length">章节异常</option></select></div><div className="chapter-findings-summary"><label><input type="checkbox" aria-label="全选问题标记" checked={allFindingsSelected} disabled={!removableFindings.length || editDirty} onChange={(e) => selectFindings(e.target.checked)}/>全选可清理项</label><span>{visibleFindings.length} 处问题 · 已选 {markedCount} 处</span></div>{editDirty && <Notice>请先保存正文，重新检查后再清理标记。</Notice>}<div className="chapter-findings-list">{visibleFindings.length ? visibleFindings.slice(0, findingLimit).map((finding) => <div className={`chapter-finding-row ${issueClass(finding.kind)}`} key={`${finding.chapter_id}-${finding.id}`}><input type="checkbox" aria-label={`清理 ${finding.chapter_title} ${finding.label} ${finding.id}`} checked={!!finding.removable && !!selections[finding.chapter_id]?.includes(finding.id)} disabled={!finding.removable || editDirty || !!busy} onChange={() => toggleFinding(finding.chapter_id, finding.id)}/><button className="chapter-finding-jump" onClick={() => jumpToFinding(finding)}><span><strong>{finding.chapter_title}</strong><small className={`chapter-issue ${issueClass(finding.kind)}`}>{finding.label}</small>{!finding.removable && <small className="chapter-manual-fix">需手动修复</small>}</span><p>{finding.text || "隐藏字符"}</p><small>{finding.reason}</small></button></div>) : <div className="chapter-small-empty">当前范围没有此类问题</div>}{visibleFindings.length > findingLimit && <button className="chapter-findings-more text-button" onClick={() => setFindingLimit((limit) => limit + 100)}>显示更多标记 · 还剩 {visibleFindings.length - findingLimit} 处</button>}</div></div><div className="modal-footer"><button className="button secondary" onClick={() => setDialog("")}>返回正文</button><button className="button primary" disabled={!markedCount || editDirty || !!busy} onClick={() => setDialog("batch")}><Sparkles size={14}/>清理已选 {markedCount} 处</button></div></Modal>}
    {dialog === "settings" && <Modal title="章节识别与清洗规则" wide busy={!!busy} onClose={() => setDialog("")}><div className="modal-body chapter-settings"><section><h3><AlignLeft size={16} />章节标题格式</h3><div className="chapter-format-grid">{(index?.candidates?.length ? index.candidates.filter((candidate) => !candidate.custom) : [{ name: "第N章/回/节" }, { name: "Chapter N" }, { name: "N、/." }]).map((candidate) => <label key={formatLabel(candidate)} className={`chapter-format-choice ${formats.includes(formatLabel(candidate)) ? "checked" : ""}`}><input type="checkbox" checked={formats.includes(formatLabel(candidate))} onChange={(e) => setFormats((current) => e.target.checked ? [...current, formatLabel(candidate)] : current.filter((f) => f !== formatLabel(candidate)))} /><span><strong>{formatLabel(candidate)}</strong>{candidate.count !== undefined && <small>匹配 {candidate.count} 处</small>}</span></label>)}</div><div className="chapter-custom-format-list">{customFormats.map((rule,i) => <div className="chapter-custom-format-row" key={i}><input type="checkbox" aria-label={`启用自定义章节格式 ${i+1}`} checked={rule.enabled} onChange={(e) => { setCustomFormats((all) => all.map((r,j) => j===i ? {...r,enabled:e.target.checked} : r)); setFormats((all) => e.target.checked ? [...new Set([...all,rule.name])] : all.filter((n) => n!==rule.name)); }}/><input aria-label={`自定义章节格式 ${i+1} 名称`} value={rule.name} placeholder="格式名称" onChange={(e) => { const name=e.target.value; setCustomFormats((all) => all.map((r,j) => j===i ? {...r,name} : r)); setFormats((all) => all.map((n) => n===rule.name ? name : n)); }}/><select aria-label={`自定义章节格式 ${i+1} 类型`} value={rule.regex!==undefined&&!rule.format ? "regex" : "format"} onChange={(e) => setCustomFormats((all) => all.map((r,j) => j===i ? {name:r.name,enabled:r.enabled,...(e.target.value==="regex" ? {regex:r.format||r.regex||""} : {format:r.regex||r.format||""})} : r))}><option value="format">N 格式</option><option value="regex">正则</option></select><input aria-label={`自定义章节格式 ${i+1} 匹配规则`} value={rule.format||rule.regex||""} placeholder={rule.regex!==undefined&&!rule.format ? "正则表达式" : "例如 第N节"} onChange={(e) => setCustomFormats((all) => all.map((r,j) => j===i ? {...r,...(r.regex!==undefined&&!r.format ? {regex:e.target.value} : {format:e.target.value})} : r))}/><button className="icon-button" aria-label={`删除自定义章节格式 ${i+1}`} onClick={() => { setCustomFormats((all) => all.filter((_,j) => j!==i)); setFormats((all) => all.filter((n) => n!==rule.name)); }}><Trash2 size={13}/></button></div>)}</div><div className="form-grid"><label className="studio-field"><span>自定义格式名称</span><input value={customName} onChange={(e) => setCustomName(e.target.value)} placeholder="例如 节标题" /></label><label className="studio-field"><span>标题格式（N 为章节编号）</span><input value={customFormat} onChange={(e) => setCustomFormat(e.target.value)} placeholder="例如 第N节" /></label></div><details className="chapter-advanced-format"><summary>高级：使用正则表达式</summary><label className="studio-field"><span>章节标题正则（数字捕获组在前，标题在后）</span><input value={customRegex} onChange={(e) => setCustomRegex(e.target.value)} placeholder="^第([0-9]+)幕(.*)$" disabled={!!customFormat.trim()} /></label></details><button className="button secondary" disabled={!!busy} onClick={() => void mutate("resegment", { heading_formats: customFormat.trim() || customRegex.trim() ? [...formats, customName.trim() || customFormat.trim() || "自定义正则"] : formats, custom_formats: [...customFormats, ...(customFormat.trim() || customRegex.trim() ? [{ name: customName.trim() || customFormat.trim() || "自定义正则", ...(customFormat.trim() ? { format: customFormat.trim() } : { regex: customRegex.trim() }), enabled: true }] : [])] }, "正在重新识别章节")}><RotateCcw size={14} />{formats.length || customFormat.trim() || customRegex.trim() ? "按所选格式识别" : "自动识别章节"}</button></section><section><h3><CircleAlert size={16} />非正文与乱码标记<button className="text-button" disabled={!!busy} onClick={() => setKeywords((index?.author_keyword_defaults || []).join("\n"))}>恢复默认关键词</button></h3><div className="form-grid"><label className="studio-field"><span>非正文关键词（每行一项）</span><textarea rows={5} value={keywords} onChange={(e) => setKeywords(e.target.value)} placeholder={"求月票\n作者说\n请假说明"} /></label><div className="studio-field"><span>自定义杂字规则</span><div className="chapter-rule-list">{garbleRules.map((rule, i) => <div className="chapter-rule-row" key={i}><input type="checkbox" aria-label={`启用杂字规则 ${i+1}`} checked={rule.enabled} onChange={(e) => setGarbleRules((rows) => rows.map((r,j) => j===i ? {...r,enabled:e.target.checked} : r))}/><input aria-label={`杂字规则 ${i+1} 名称`} placeholder="规则名称" value={rule.name} onChange={(e) => setGarbleRules((rows) => rows.map((r,j) => j===i ? {...r,name:e.target.value} : r))}/><input aria-label={`杂字规则 ${i+1} 正则`} placeholder="正则表达式" value={rule.pattern} onChange={(e) => setGarbleRules((rows) => rows.map((r,j) => j===i ? {...r,pattern:e.target.value} : r))}/><button className="icon-button" aria-label={`删除杂字规则 ${i+1}`} onClick={() => setGarbleRules((rows) => rows.filter((_,j) => j!==i))}><Trash2 size={13}/></button></div>)}</div><button className="text-button" onClick={() => setGarbleRules((rows) => [...rows,{name:"",pattern:"",enabled:true}])}><Plus size={13}/>添加规则</button></div></div><button className="button secondary" disabled={!!busy} onClick={() => void mutate("settings", { author_keywords: keywords.split("\n").map((k) => k.trim()).filter(Boolean), garble_rules: garbleRules.filter((r) => r.name.trim() || r.pattern.trim()) }, "正在更新标记规则")}><Check size={14} />保存标记规则</button></section><Notice error>{error}</Notice>{busy && <Busy label={busy} />}</div><div className="modal-footer"><button className="button secondary" disabled={!!busy} onClick={() => setDialog("")}>完成</button></div></Modal>}
    {(dialog === "rename" || dialog === "insert") && <Modal title={dialog === "rename" ? "重命名章节" : "添加章节"} busy={!!busy} wide={dialog === "insert"} onClose={() => setDialog("")}><div className="modal-body"><label className="studio-field"><span>{dialog === "rename" ? "章节名称" : "完整章节标题"}</span><input autoFocus value={newTitle} onChange={(e) => setNewTitle(e.target.value)} /></label>{dialog === "insert" && <label className="studio-field"><span>正文 · 插入当前章节之后</span><textarea rows={12} value={newBody} onChange={(e) => setNewBody(e.target.value)} /></label>}<Notice error>{error}</Notice>{busy && <Busy label={busy} />}</div><div className="modal-footer"><button className="button secondary" disabled={!!busy} onClick={() => setDialog("")}>取消</button><button className="button primary" disabled={!newTitle.trim() || !!busy} onClick={() => dialog === "rename" ? void mutate("rename", { chapter_id: chapter?.id, title: newTitle }, "正在保存章节标题") : void mutate("insert", { after_chapter_id: chapter?.id || null, heading: newTitle, body_text: newBody }, "正在添加章节")}><Check size={14} />保存</button></div></Modal>}
    {dialog === "delete" && <Modal title="删除章节" busy={!!busy} onClose={() => setDialog("")}><div className="modal-body"><p>{deleteIds.length===1 ? `删除「${chapters.find((c) => c.id===deleteIds[0])?.title || "所选章节"}」？` : `删除选中的 ${deleteIds.length} 章？`}已有版本保留，可从版本历史恢复。</p>{deleteIds.length>1 && <div className="chapter-batch-list">{chapters.filter((c) => deleteIds.includes(c.id)).map((c) => <div key={c.id}><span>{c.title || `章节 ${c.index+1}`}</span></div>)}</div>}<Notice error>{error}</Notice>{busy && <Busy label={busy} />}</div><div className="modal-footer"><button className="button secondary" disabled={!!busy} onClick={() => setDialog("")}>取消</button><button className="button danger" disabled={!!busy} onClick={() => void mutate("delete", { chapter_ids: deleteIds }, "正在保存删除版本")}><Trash2 size={14} />删除章节</button></div></Modal>}
    {dialog === "batch" && <Modal title="清理全部标记" busy={!!busy} onClose={() => setDialog("")}><div className="modal-body"><p>将清理 {chapters.filter((c) => selections[c.id]?.length).length} 章中的 {markedCount} 处标记。</p><div className="chapter-batch-list">{chapters.filter((c) => selections[c.id]?.length).map((c) => <div key={c.id}><span>{c.title}</span><strong>{selections[c.id].length} 处</strong></div>)}</div><Notice error>{error}</Notice>{busy && <Busy label={busy} />}</div><div className="modal-footer"><button className="button secondary" disabled={!!busy} onClick={() => setDialog("")}>返回检查</button><button className="button primary" disabled={!!busy} onClick={() => apply(true)}><Sparkles size={14} />保存清洗版本</button></div></Modal>}
    {discardAction && <Modal title="章节修订尚未保存" onClose={() => setDiscardAction(null)}><div className="modal-body"><p>离开编辑前，请保存本章修订。</p></div><div className="modal-footer"><button className="button secondary" onClick={() => setDiscardAction(null)}>继续编辑</button><button className="button danger" onClick={() => { const action = discardAction; setDiscardAction(null); setEdit((draft) => draft ? { ...draft, body: draft.original } : null); onDirtyRef.current(false); action(); }}>放弃修订</button></div></Modal>}
  </div>;
}
