"""Read InkCrawler's live SQLite data without modifying its workspace.

Analysis documents are immutable snapshots owned by InkReference.  The shared
source remains the crawler database; no two applications write its schema.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from contextlib import closing, contextmanager
from urllib.parse import urlsplit

from .database import DataError

_ID = re.compile(r"^[0-9a-f]{32,64}$")
_VISIBLE_BOOK = """NOT EXISTS (SELECT 1 FROM task_trash t WHERE t.item_id=b.id
    AND (t.kind='work' OR (t.kind='book' AND t.include_work=1)))"""
_CHUNK = 256 * 1024


def _completion_value(status):
    value = re.sub(r'\s+', '', status).casefold() if isinstance(status, str) else ''
    if value in {'连载', '连载中', '連載', '連載中', '未完结', '未完結', '未完本', 'ongoing', 'serializing'}:
        return 'ongoing'
    if value in {'完结', '已完结', '完結', '已完結', '完本', '已完本', 'completed', 'complete', 'finished'}:
        return 'completed'
    return ''


def _completion(db, book):
    """Read the source-aware completion contract, including old workspaces."""
    row = dict(book)
    # Recent InkCrawler versions update these two fields atomically whenever
    # the active source or its known completion status changes. Using that
    # shared cache avoids per-work source queries in large libraries.
    if row.get('completion_source_cache') in ('source', 'manual', 'default'):
        return bool(row.get('is_completed', 0)), row['completion_source_cache']
    active, url = row.get('active_channel_id'), row.get('toc_url')
    status = ''
    if active:
        columns = {item[1] for item in db.execute('PRAGMA table_info(channels)')}
        if {'id', 'book_id', 'toc_url', 'source_completion_status'} <= columns:
            channel = db.execute('SELECT toc_url,source_completion_status FROM channels WHERE id=? AND book_id=?',
                                 (active, row['id'])).fetchone()
            if channel and channel['toc_url'] == url:
                status = _completion_value(channel['source_completion_status'])
                if not status:
                    try:
                        parts = urlsplit(url or '')
                        host = parts.hostname
                        pattern = (r'/book/([0-9]+)/?' if host == 'www.qidian.com' else
                                   r'/info/([0-9]+)/?' if host == 'book.qidian.com' else
                                   r'/page/([0-9]+)/?' if host in ('fanqienovel.com', 'www.fanqienovel.com') else '')
                        match = re.fullmatch(pattern, parts.path) if pattern else None
                        if (match and parts.scheme == 'https' and not parts.username and not parts.password
                                and parts.port in (None, 443)
                                and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ranking_works'").fetchone()):
                            platform = 'qidian' if host in ('www.qidian.com', 'book.qidian.com') else 'fanqie'
                            rank = db.execute('SELECT metadata_json FROM ranking_works WHERE platform=? AND platform_novel_id=?',
                                              (platform, match.group(1))).fetchone()
                            if rank:
                                value = json.loads(rank['metadata_json'])
                                status = _completion_value(value.get('status', '')) if isinstance(value, dict) else ''
                    except (ValueError, TypeError, RecursionError):
                        pass
    else:
        status = _completion_value(row.get('source_completion_status', ''))
    if status == 'ongoing':
        return False, 'source'
    if status == 'completed':
        return True, 'source'
    marked = bool(row.get('manual_is_completed', row.get('is_completed', 0)))
    return marked, 'manual' if marked else 'default'


def default_crawler_dir() -> Path:
    configured = os.environ.get("INKCRAWLER_DATA_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "InkTools" / "InkCrawler"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "InkCrawler"
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / "InkCrawler"


class CrawlerBridge:
    def __init__(self, reference_dir: Path):
        self.reference_dir = Path(reference_dir)
        self.settings_db = self.reference_dir / "workbench.sqlite3"

    def source(self) -> Path:
        try:
            if self.settings_db.is_file():
                with closing(sqlite3.connect(self.settings_db.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)) as db:
                    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='crawler_source_settings'").fetchone():
                        row = db.execute("SELECT value FROM crawler_source_settings WHERE key='data_dir'").fetchone()
                        if row and isinstance(row[0], str) and row[0]:
                            return Path(row[0]).expanduser().resolve()
        except (OSError, sqlite3.Error, ValueError):
            pass
        return default_crawler_dir().resolve()

    def configure(self, value: str) -> dict:
        if not isinstance(value, str) or not value.strip() or len(value) > 4096:
            raise DataError(422, "请选择 InkCrawler 数据目录")
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise DataError(422, "数据目录必须使用绝对路径")
        path = path.resolve()
        self._validate(path)
        self.reference_dir.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.settings_db, timeout=15)) as db:
            with db:
                db.execute('CREATE TABLE IF NOT EXISTS crawler_source_settings (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
                db.execute("INSERT INTO crawler_source_settings VALUES('data_dir',?) "
                           "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(path),))
        return self.status()

    @staticmethod
    def _validate(path: Path) -> Path:
        database = path / "inkcrawler.sqlite3"
        if not database.is_file() or database.is_symlink():
            raise DataError(404, "未找到 InkCrawler 数据库，请选择其数据目录")
        try:
            with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
                names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not {"books", "chapters", "source_downloads"} <= names:
                    raise DataError(422, "所选目录不是兼容的 InkCrawler 工作区")
        except sqlite3.Error as exc:
            raise DataError(422, "无法读取所选 InkCrawler 数据库") from exc
        return database

    def status(self) -> dict:
        path = self.source()
        try:
            with self.database(path) as db:
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                book_columns = {row[1] for row in db.execute('PRAGMA table_info(books)')}
                trash_columns = {row[1] for row in db.execute('PRAGMA table_info(task_trash)')} if 'task_trash' in tables else set()
                capabilities = {
                    'books': {'is_completed'} <= book_columns and {'item_id', 'kind', 'include_work'} <= trash_columns,
                    'rankings': {'ranking_snapshots', 'ranking_observations', 'ranking_works', 'ranking_metadata_versions',
                                 'ranking_chapters', 'ranking_observation_chapters'} <= tables,
                    'models': 'source_files' in tables,
                }
                pending = None
                if {'source_downloads', 'source_pages', 'ranking_migration_pages'} <= tables:
                    pending = bool(db.execute('''SELECT 1 FROM source_downloads d
                        JOIN source_pages p ON p.download_id=d.id
                        LEFT JOIN ranking_migration_pages m ON m.download_id=p.download_id AND m.page=p.page
                        WHERE d.source IN ('qidian','fanqie','custom')
                          AND (m.page IS NULL OR m.payload_hash<>p.payload_hash OR m.skipped>0)
                        LIMIT 1''').fetchone())
                elif {'source_downloads', 'source_pages'} <= tables:
                    pending = bool(db.execute('''SELECT 1 FROM source_downloads d
                        JOIN source_pages p ON p.download_id=d.id
                        WHERE d.source IN ('qidian','fanqie','custom') LIMIT 1''').fetchone())
        except DataError:
            return {"data_dir": str(path), "available": False, "capabilities": {}, "ranking_index_pending": None}
        return {"data_dir": str(path), "available": True, "capabilities": capabilities, "ranking_index_pending": pending}

    @contextmanager
    def database(self, source=None):
        path = self._validate(self.source() if source is None else source)
        try:
            db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=15)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            try:
                db.execute("BEGIN")
                yield db
            finally:
                db.close()
        except sqlite3.Error as exc:
            raise DataError(409, "无法读取 InkCrawler 数据，请检查数据目录或升级 InkCrawler 后重试") from exc

    def books(self, *, limit=50, offset=0):
        with self.database() as db:
            rows = db.execute("""SELECT b.*,
                count(c.id) AS chapters,coalesce(sum(c.char_count),0) AS characters
                FROM books b LEFT JOIN chapters c ON c.book_id=b.id AND c.state<>'deleted' AND c.char_count>0
                WHERE """ + _VISIBLE_BOOK + """
                GROUP BY b.id ORDER BY b.created_at DESC,b.id LIMIT ? OFFSET ?""", (limit, offset)).fetchall()
            output = []
            for row in rows:
                completed, origin = _completion(db, row)
                item = {key: row[key] for key in ('id', 'title', 'author', 'created_at', 'chapters', 'characters')}
                output.append({**item, 'is_completed': completed, 'completion_source': origin})
        return output

    def rankings(self, *, limit=50, offset=0):
        with self.database() as db:
            rows = db.execute("""SELECT s.id,s.platform,s.rank_family,s.rank_sub_cat,s.captured_at,
                count(o.id) AS works FROM ranking_snapshots s LEFT JOIN ranking_observations o ON o.snapshot_id=s.id
                GROUP BY s.id ORDER BY s.captured_at DESC,s.id LIMIT ? OFFSET ?""", (limit, offset)).fetchall()
        return [dict(row) for row in rows]

    def ranking(self, ident, *, limit=50, offset=0):
        if not _ID.fullmatch(ident):
            raise DataError(404, "榜单不存在")
        with self.database() as db:
            snapshot = db.execute("SELECT * FROM ranking_snapshots WHERE id=?", (ident,)).fetchone()
            if snapshot is None:
                raise DataError(404, "榜单不存在")
            rows = db.execute("""SELECT w.id AS work_id,w.platform,w.platform_novel_id,
                o.id AS observation_id,m.metadata_json,o.observation_json,
                (SELECT count(*) FROM ranking_observation_chapters oc WHERE oc.observation_id=o.id) AS chapter_count
                FROM ranking_observations o JOIN ranking_works w ON w.id=o.work_id
                LEFT JOIN ranking_metadata_versions m ON m.work_id=o.work_id AND m.digest=o.metadata_digest
                WHERE o.snapshot_id=? ORDER BY CASE WHEN json_valid(o.observation_json) THEN
                    CASE WHEN json_type(o.observation_json,'$.rank') IN ('integer','real')
                        AND json_extract(o.observation_json,'$.rank')>0
                        THEN json_extract(o.observation_json,'$.rank') ELSE 2147483647 END
                    ELSE 2147483647 END, o.page,o.record_index,o.id LIMIT ? OFFSET ?""", (ident, limit, offset)).fetchall()
        items = []
        for row in rows:
            try:
                observation = json.loads(row['observation_json'])
                if not isinstance(observation, dict):
                    continue
                try:
                    metadata = json.loads(row['metadata_json']) if row['metadata_json'] else None
                    if not isinstance(metadata, dict):
                        raise ValueError('invalid metadata')
                    unavailable = False
                except (ValueError, TypeError, RecursionError):
                    metadata, unavailable = {}, True
                if metadata.get('_publication_evidence') not in ('detail', 'first_chapter'):
                    metadata.pop('publish_date', None)
                items.append({'work_id': row['work_id'], 'observation_id': row['observation_id'],
                              'chapter_count': row['chapter_count'], 'platform': row['platform'], 'platform_novel_id': row['platform_novel_id'],
                              'metadata': metadata, 'metadata_unavailable': unavailable,
                              'observation': observation})
            except (ValueError, AttributeError, TypeError, RecursionError):
                continue
        return {"snapshot": dict(snapshot), "items": items}

    def models(self, *, limit=50, offset=0):
        with self.database() as db:
            root = Path(db.execute('PRAGMA database_list').fetchone()[2]).parent
            columns = {row[1] for row in db.execute('PRAGMA table_info(source_downloads)')}
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            # A hidden task may still own intentionally retained model files.
            # Older workspaces without these tables retain the former behavior.
            alternatives = ['d.task_removed=0']
            if 'task_trash' in tables and 'include_files' in {row[1] for row in db.execute('PRAGMA table_info(task_trash)')}:
                alternatives.append("EXISTS(SELECT 1 FROM task_trash t WHERE t.kind='source' AND t.item_id=d.id AND t.include_files=0)")
            if 'source_retained_assets' in tables:
                alternatives.append('EXISTS(SELECT 1 FROM source_retained_assets r WHERE r.download_id=d.id)')
            visible = ' AND (' + ' OR '.join(alternatives) + ')' if 'task_removed' in columns else ''
            local = []
            if 'workspace_options' in tables:
                stored = db.execute("SELECT value FROM workspace_options WHERE key='local_model_assets'").fetchone()
                try:
                    values = json.loads(stored[0]) if stored else []
                except (TypeError, ValueError):
                    values = []
                if isinstance(values, list):
                    for item in values[:1000]:
                        if (not isinstance(item, dict) or not isinstance(item.get('id'), str)
                                or not _ID.fullmatch(item['id']) or not isinstance(item.get('name'), str)
                                or not isinstance(item.get('path'), str) or item.get('kind') not in ('file', 'directory')):
                            continue
                        path = Path(item['path'])
                        if not path.is_absolute():
                            continue
                        try:
                            available = path.is_file() if item['kind'] == 'file' else path.is_dir()
                        except OSError:
                            available = False
                        local.append({'id': item['id'], 'source': 'local', 'state': '已登记' if available else '路径不可用',
                                      'filename': item['name'], 'path': str(path), 'available': available})
            local_page = local[offset:offset + limit]
            source_offset = max(0, offset - len(local))
            source_limit = max(0, limit - len(local_page))
            rows = db.execute("""SELECT d.id,d.source,d.state,d.request,f.filename,f.result
                FROM source_downloads d JOIN source_files f ON f.download_id=d.id
                WHERE d.source IN ('model_huggingface','model_github','model_kaggle')""" + visible + """
                ORDER BY d.created_at DESC,d.id,f.number LIMIT ? OFFSET ?""", (source_limit, source_offset)).fetchall()
        result = local_page
        for row in rows:
            try:
                meta = json.loads(row['result'] or '{}')
                owner = row['id']
                filename = meta.get('relative_path') or row['filename']
                if (not _ID.fullmatch(owner) or not isinstance(filename, str) or len(filename) > 4096
                        or '\\' in filename or filename.startswith('/')):
                    continue
                parts = filename.split('/')
                if not parts or any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,180}', part)
                                    or part in ('.', '..') for part in parts):
                    continue
                parent = root / 'source-downloads' / owner
                path = parent.joinpath(*parts)
                if (parent.parent.is_symlink() or parent.is_symlink()
                        or any(parent.joinpath(*parts[:index]).is_symlink() for index in range(1, len(parts) + 1))):
                    continue
                try:
                    stat = path.stat()
                    available = bool(meta and path.is_file() and stat.st_size == meta.get('bytes')
                                     and stat.st_mtime_ns == meta.get('mtime_ns'))
                except OSError:
                    available = False
                result.append({'id': row['id'], 'source': row['source'], 'state': row['state'],
                               'filename': filename, 'path': str(path) if available else None, 'available': available,
                               'sha256': meta.get('sha256'), 'bytes': meta.get('bytes')})
            except (ValueError, TypeError, AttributeError):
                continue
        return result

    def snapshot_book(self, ident, documents):
        if not _ID.fullmatch(ident):
            raise DataError(404, "作品不存在")
        from .documents import MAX_UPLOAD_BYTES
        with self.database() as db:
            root = Path(db.execute('PRAGMA database_list').fetchone()[2]).parent
            book = db.execute("SELECT b.* FROM books b WHERE b.id=? AND " + _VISIBLE_BOOK, (ident,)).fetchone()
            if book is None:
                raise DataError(404, "作品不存在")
            is_completed, completion_source = _completion(db, book)
            raw_parts = bytearray()
            chapters = db.execute("""SELECT rowid,title FROM chapters WHERE book_id=?
                AND state<>'deleted' AND char_count>0 ORDER BY position,id""", (ident,))
            found = False
            for chapter in chapters:
                found = True
                header = (chapter['title'] + '\n').encode('utf-8')
                if len(raw_parts) + len(header) + 2 > MAX_UPLOAD_BYTES:
                    raise DataError(413, "此作品超过 InkReference 当前 50 MiB 全文分析上限")
                raw_parts.extend(header)
                with db.blobopen('chapters', 'content', chapter['rowid'], readonly=True) as content:
                    while chunk := content.read(_CHUNK):
                        if len(raw_parts) + len(chunk) + 2 > MAX_UPLOAD_BYTES:
                            raise DataError(413, "此作品超过 InkReference 当前 50 MiB 全文分析上限")
                        raw_parts.extend(chunk)
                raw_parts.extend(b'\n\n')
            if not found:
                raise DataError(422, "该作品没有已保存的文本章节")
        metadata = {'title': book['title'], 'author': book['author'], 'created_at': book['created_at'],
                    'is_completed': is_completed, 'completion_source': completion_source}
        return self._upload_snapshot(documents, root, ['book', ident], book['title'], book['author'], raw_parts, metadata)

    @staticmethod
    def _upload_snapshot(documents, root, provenance, title, author, raw_parts, metadata):
        raw = bytes(raw_parts)
        identity = json.dumps([str(root), provenance, title, author, metadata, hashlib.sha256(raw).hexdigest()],
                              ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
        digest = hashlib.sha256(identity).hexdigest()
        result = documents.upload(f"{digest[:32]}.txt", raw, encoding='utf-8', title=title,
                                  idempotency_key=f"inkcrawler:{digest}")
        origin = {'source': 'inkcrawler', 'kind': provenance[0], 'data_dir': str(root),
                  'work_id': provenance[1] if provenance[0] == 'book' else provenance[2],
                  'snapshot_id': provenance[1] if provenance[0] == 'ranking' else None,
                  'observation_id': provenance[3] if provenance[0] == 'ranking' else None,
                  'author': author, 'metadata': metadata, 'content_sha256': hashlib.sha256(raw).hexdigest()}
        encoded = json.dumps(origin, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        try:
            with closing(sqlite3.connect(documents.db_path, timeout=15)) as db:
                db.execute('PRAGMA foreign_keys=ON')
                with db:
                    db.execute('''CREATE TABLE IF NOT EXISTS crawler_document_origins (
                        document_id TEXT PRIMARY KEY REFERENCES doc_documents(id) ON DELETE CASCADE,
                        origin_json TEXT NOT NULL)''')
                    db.execute('INSERT OR IGNORE INTO crawler_document_origins VALUES (?,?)', (result['id'], encoded))
                    existing = db.execute('SELECT origin_json FROM crawler_document_origins WHERE document_id=?', (result['id'],)).fetchone()
                    if not existing or existing[0] != encoded:
                        raise DataError(409, '已有快照的来源信息不一致，请检查作品数据')
        except sqlite3.Error as exc:
            raise DataError(500, '正文快照已保存，但来源信息写入失败；请重试') from exc
        return {**result, 'crawler_origin': origin}

    def snapshot_origin(self, document_id, documents):
        if not _ID.fullmatch(document_id):
            raise DataError(404, '文档不存在')
        with closing(sqlite3.connect(documents.db_path, timeout=15)) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='crawler_document_origins'").fetchone():
                return None
            row = db.execute('SELECT origin_json FROM crawler_document_origins WHERE document_id=?', (document_id,)).fetchone()
            if row is None:
                return None
            try:
                return json.loads(row[0])
            except (TypeError, ValueError):
                raise DataError(409, '文档来源信息损坏') from None

    def snapshot_ranking_work(self, snapshot_id, work_id, documents):
        """Create an analysis snapshot from the chapters saved with this observation."""
        if not _ID.fullmatch(snapshot_id) or not _ID.fullmatch(work_id):
            raise DataError(404, '榜单作品不存在')
        from .documents import MAX_UPLOAD_BYTES
        with self.database() as db:
            root = Path(db.execute('PRAGMA database_list').fetchone()[2]).parent
            observation = db.execute('''SELECT o.id,o.observation_json,m.metadata_json FROM ranking_observations o
                JOIN ranking_works w ON w.id=o.work_id
                LEFT JOIN ranking_metadata_versions m ON m.work_id=o.work_id AND m.digest=o.metadata_digest
                WHERE o.snapshot_id=? AND o.work_id=? ORDER BY o.captured_at DESC,o.id DESC LIMIT 1''',
                (snapshot_id, work_id)).fetchone()
            if observation is None:
                raise DataError(404, '榜单作品不存在')
            try:
                metadata = json.loads(observation['metadata_json'])
                observed = json.loads(observation['observation_json'])
                if not isinstance(metadata, dict) or not isinstance(observed, dict):
                    raise ValueError('invalid metadata')
                title = metadata.get('title') or '未命名作品'
                author = metadata.get('author') or ''
                if not isinstance(title, str) or not isinstance(author, str):
                    raise ValueError('invalid title or author')
            except (TypeError, ValueError):
                raise DataError(409, '榜单作品元数据损坏，请先在 InkCrawler 中检查数据') from None
            links = db.execute('''SELECT oc.chapter_index,c.rowid,c.work_id FROM ranking_observation_chapters oc
                LEFT JOIN ranking_chapters c ON c.id=oc.chapter_id
                WHERE oc.observation_id=? ORDER BY oc.chapter_index''', (observation['id'],))
            raw_parts = bytearray()
            found = 0
            for index, link in enumerate(links):
                found += 1
                if link['rowid'] is None or link['work_id'] != work_id or link['chapter_index'] != index:
                    raise DataError(409, '榜单章节关联损坏，请先在 InkCrawler 中检查数据')
                with db.blobopen('ranking_chapters', 'chapter_json', link['rowid'], readonly=True) as blob:
                    # JSON escaping can double the text size; bound even damaged input before reading it.
                    if len(blob) > MAX_UPLOAD_BYTES * 2 + 1024 * 1024:
                        raise DataError(413, '此作品超过 InkReference 当前 50 MiB 全文分析上限')
                    try:
                        chapter = json.loads(blob.read())
                    except (ValueError, UnicodeError):
                        raise DataError(409, '榜单章节数据损坏，请先在 InkCrawler 中检查数据') from None
                if (not isinstance(chapter, dict) or chapter.get('chapter_num') != index + 1
                        or not isinstance(chapter.get('content'), str) or not chapter['content']):
                    raise DataError(409, '榜单章节正文损坏，请先在 InkCrawler 中检查数据')
                heading = chapter.get('title') or ''
                if not isinstance(heading, str):
                    raise DataError(409, '榜单章节标题损坏，请先在 InkCrawler 中检查数据')
                part = (heading + '\n' + chapter['content'] + '\n\n').encode('utf-8')
                if len(raw_parts) + len(part) > MAX_UPLOAD_BYTES:
                    raise DataError(413, '此作品超过 InkReference 当前 50 MiB 全文分析上限')
                raw_parts.extend(part)
            expected = observed.get('_stored_chapter_count')
            if type(expected) is int and expected != found:
                raise DataError(409, '榜单章节不完整，请先在 InkCrawler 中检查数据')
            if not found:
                raise DataError(422, '该榜单作品没有已保存的章节')
        metadata = dict(metadata)
        if metadata.get('_publication_evidence') not in ('detail', 'first_chapter') and metadata.get('publish_date'):
            metadata['historical_publish_date_unverified'] = metadata.pop('publish_date')
        return self._upload_snapshot(documents, root, ['ranking', snapshot_id, work_id, observation['id']],
                                     title, author, raw_parts, metadata)
