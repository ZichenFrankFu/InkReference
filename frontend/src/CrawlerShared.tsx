import { useEffect, useRef, useState } from "react";
import { studio, send, type DocumentMeta } from "./studio";
import { Busy, Notice, messageOf } from "./ui";

type Source = { data_dir: string; available: boolean; ranking_index_pending?: boolean | null; capabilities?: Record<string, boolean> };
type Book = { id: string; title: string; author: string; chapters: number; characters: number; is_completed: boolean | number };
type Snapshot = { id: string; platform: string; rank_family: string; rank_sub_cat: string; captured_at: number; works: number };
type Model = { id: string; source: string; state: string; filename: string; path: string | null; available: boolean; bytes?: number };
type Ranking = { snapshot: Snapshot; items: { work_id: string; platform_novel_id: string; chapter_count: number; metadata: Record<string, unknown>; observation: Record<string, unknown> }[] };
type Tab = "books" | "rankings" | "models";

export default function CrawlerShared({ onOpen }: { onOpen: (document: DocumentMeta) => void }) {
  const [source, setSource] = useState<Source | null>(null);
  const [path, setPath] = useState("");
  const [tab, setTab] = useState<Tab>("books");
  const [offset, setOffset] = useState(0);
  const [books, setBooks] = useState<Book[]>([]);
  const [rankings, setRankings] = useState<Snapshot[]>([]);
  const [models, setModels] = useState<Model[]>([]);
  const [ranking, setRanking] = useState<Ranking | null>(null);
  const [rankingOffset, setRankingOffset] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const epoch = useRef(0);

  const status = async () => {
    const result = await studio<Source>("/crawler/status");
    setSource(result); setPath(result.data_dir);
  };
  useEffect(() => { void status().catch((e) => setError(messageOf(e))); }, []);
  useEffect(() => {
    if (!source?.available || source.capabilities?.[tab] === false) return;
    const ticket = ++epoch.current;
    setBusy(true); setError(""); setRanking(null);
    void studio<{ items: Book[] | Snapshot[] | Model[] }>(`/crawler/${tab}?limit=50&offset=${offset}`)
      .then((result) => {
        if (ticket !== epoch.current) return;
        if (tab === "books") setBooks(result.items as Book[]);
        else if (tab === "rankings") setRankings(result.items as Snapshot[]);
        else setModels(result.items as Model[]);
      }).catch((e) => { if (ticket === epoch.current) setError(messageOf(e)); })
      .finally(() => { if (ticket === epoch.current) setBusy(false); });
    return () => { epoch.current++; };
  }, [source, tab, offset]);
  const connect = async () => {
    setBusy(true); setError("");
    try { const result = await send<Source>("/crawler/source", { data_dir: path }, "PUT"); setOffset(0); setSource(result); }
    catch (e) { setError(messageOf(e)); } finally { setBusy(false); }
  };
  const open = async (book: Book) => {
    const ticket = ++epoch.current;
    setBusy(true); setError("");
    try {
      const doc = await send<DocumentMeta>(`/crawler/books/${encodeURIComponent(book.id)}/open`, {});
      if (ticket === epoch.current) onOpen(doc);
    } catch (e) { if (ticket === epoch.current) setError(messageOf(e)); }
    finally { if (ticket === epoch.current) setBusy(false); }
  };
  const showRanking = async (item: Snapshot, nextOffset = 0) => {
    const ticket = ++epoch.current;
    setBusy(true); setError("");
    try {
      const result = await studio<Ranking>(`/crawler/rankings/${encodeURIComponent(item.id)}?limit=50&offset=${nextOffset}`);
      if (ticket === epoch.current) { setRanking(result); setRankingOffset(nextOffset); }
    } catch (e) { if (ticket === epoch.current) setError(messageOf(e)); }
    finally { if (ticket === epoch.current) setBusy(false); }
  };
  const openRankingWork = async (workId: string) => {
    if (!ranking) return;
    const ticket = ++epoch.current;
    setBusy(true); setError("");
    try {
      const doc = await send<DocumentMeta>(`/crawler/rankings/${encodeURIComponent(ranking.snapshot.id)}/works/${encodeURIComponent(workId)}/open`, {});
      if (ticket === epoch.current) onOpen(doc);
    } catch (e) { if (ticket === epoch.current) setError(messageOf(e)); }
    finally { if (ticket === epoch.current) setBusy(false); }
  };
  const currentLength = tab === "books" ? books.length : tab === "rankings" ? rankings.length : models.length;
  return <section className="surface crawler-shared">
    <div className="surface-heading"><h2>InkCrawler 共享数据</h2></div>
    <div className="crawler-source-row"><label>InkCrawler 数据目录<input aria-label="InkCrawler 数据目录" value={path} onChange={(e) => setPath(e.target.value)} /></label><button className="button secondary" onClick={() => void connect()} disabled={busy}>连接</button></div>
    <p>{source?.available ? "已连接，列表读取 InkCrawler 的当前数据。打开分析会保存独立快照；源作品更新后可再次打开。" : "选择 InkCrawler 的数据目录以读取作品、榜单和模型。"}</p>
    {source?.ranking_index_pending && tab === 'rankings' && <p>部分旧榜单尚未进入索引，可能暂时无法显示。</p>}
    <Notice error>{error}</Notice>
    {source?.available && source.capabilities?.[tab] === false && <p>该数据类型需要更新 InkCrawler 后使用。</p>}
    <div className="page-tabs" role="tablist" aria-label="共享数据类型">
      {([['books', '作品'], ['rankings', '榜单'], ['models', '模型']] as const).map(([id, label]) => <button key={id} role="tab" aria-selected={tab === id} disabled={source?.available && source.capabilities?.[id] === false} className={tab === id ? 'active' : ''} onClick={() => { epoch.current++; setBusy(false); setRanking(null); setTab(id); setOffset(0); }}>{label}</button>)}
    </div>
    {busy && <Busy label="正在读取共享数据" inline />}
    {source?.available && source.capabilities?.books !== false && !busy && tab === 'books' && <div className="crawler-shared-list">{books.map((book) => <article key={book.id}><div><strong>{book.title}</strong><small>{book.author || '未知作者'} · {book.chapters} 章 · {book.characters.toLocaleString()} 字{book.is_completed ? ' · 完结' : ''}</small></div><button className="button secondary" disabled={!book.chapters} onClick={() => void open(book)}>打开分析</button></article>)}{!books.length && <p>暂无作品</p>}</div>}
    {source?.available && source.capabilities?.rankings !== false && !busy && tab === 'rankings' && <div className="crawler-shared-list">{ranking ? <><button className="button secondary" onClick={() => setRanking(null)}>返回榜单</button><h3>{ranking.snapshot.rank_family} {ranking.snapshot.rank_sub_cat}</h3>{ranking.items.map((item) => <article key={`${item.platform_novel_id}-${String(item.observation.rank)}`}><div><strong>{String(item.observation.rank ?? '')} {String(item.metadata.title || '未知作品')}</strong><small>{String(item.metadata.author || '未知作者')} · {String(item.metadata.status || '状态未知')} · {item.chapter_count} 章</small></div><button className="button secondary" disabled={!item.chapter_count} onClick={() => void openRankingWork(item.work_id)}>打开分析</button></article>)}</> : <>{rankings.map((item) => <article key={item.id}><div><strong>{item.rank_family} {item.rank_sub_cat}</strong><small>{item.platform} · {new Date(item.captured_at * 1000).toLocaleString()} · {item.works} 部作品</small></div><button className="button secondary" onClick={() => void showRanking(item)}>查看</button></article>)}{!rankings.length && <p>暂无榜单</p>}</>}</div>}
    {source?.available && source.capabilities?.models !== false && !busy && tab === 'models' && <div className="crawler-shared-list">{models.map((model) => <article key={`${model.id}-${model.filename}`}><div><strong>{model.filename}</strong><small>{model.source === 'local' ? '本机登记' : model.source} · {model.available ? '可用' : model.state}</small><code>{model.path}</code></div></article>)}{!models.length && <p>暂无模型</p>}<p>使用模型时仍需可运行该模型的本地服务。</p></div>}
    {source?.available && !ranking && <div className="crawler-shared-pages"><button className="button secondary" disabled={busy || offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>上一页</button><span>第 {Math.floor(offset / 50) + 1} 页</span><button className="button secondary" disabled={busy || currentLength < 50} onClick={() => setOffset(offset + 50)}>下一页</button></div>}
    {source?.available && ranking && tab === 'rankings' && <div className="crawler-shared-pages"><button className="button secondary" disabled={busy || rankingOffset === 0} onClick={() => void showRanking(ranking.snapshot, Math.max(0, rankingOffset - 50))}>上一页</button><span>第 {Math.floor(rankingOffset / 50) + 1} 页</span><button className="button secondary" disabled={busy || ranking.items.length < 50} onClick={() => void showRanking(ranking.snapshot, rankingOffset + 50)}>下一页</button></div>}
  </section>;
}
