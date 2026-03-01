"""
数据存储层
使用 aiosqlite 实现异步 SQLite 操作
"""
import json
import logging
import aiosqlite
from datetime import datetime, timedelta
from typing import Optional, List
from pathlib import Path

from .models import (
    ReadingSession, PageSnapshot, Note, DailySummary,
    Book, BookProgress, Bookmark, ReadingListItem,
)

logger = logging.getLogger(__name__)


class Storage:
    """
    SQLite 异步存储
    """

    def __init__(self, db_path: Path, notes_dir: Optional[Path] = None):
        self.db_path = db_path
        self.notes_dir = notes_dir
        self._conn: Optional[aiosqlite.Connection] = None
        
    async def initialize(self):
        """初始化数据库连接和表结构"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        self._conn = await aiosqlite.connect(str(self.db_path))
        self._conn.row_factory = aiosqlite.Row
        
        await self._create_tables()
        logger.info(f"数据库已初始化: {self.db_path}")
        
    async def close(self):
        """关闭数据库连接"""
        if self._conn:
            await self._conn.close()
            self._conn = None
            
    async def _create_tables(self):
        """创建表结构"""
        await self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                book_name TEXT DEFAULT '',
                start_at INTEGER NOT NULL,
                end_at INTEGER,
                camera_device INTEGER DEFAULT 0,
                total_pages INTEGER DEFAULT 0,
                total_snapshots INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                ts INTEGER NOT NULL,
                image_path TEXT NOT NULL,
                ocr_text TEXT DEFAULT '',
                fingerprint TEXT DEFAULT '',
                dwell_ms INTEGER DEFAULT 0,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );

            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT DEFAULT '',
                ts INTEGER NOT NULL,
                content TEXT NOT NULL,
                book_name TEXT DEFAULT '',
                tags TEXT DEFAULT '[]',
                page_ocr_context TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS books (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT UNIQUE NOT NULL,
                author TEXT DEFAULT '',
                genre TEXT DEFAULT '',
                total_pages INTEGER DEFAULT 0,
                cover_image_path TEXT DEFAULT '',
                created_at INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS reading_progress (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id INTEGER NOT NULL,
                book_title TEXT NOT NULL,
                last_page_num INTEGER DEFAULT 0,
                last_page_ocr TEXT DEFAULT '',
                last_read_at INTEGER DEFAULT 0,
                total_read_time_ms INTEGER DEFAULT 0,
                total_pages_read INTEGER DEFAULT 0,
                status TEXT DEFAULT 'reading',
                FOREIGN KEY (book_id) REFERENCES books(id)
            );

            CREATE TABLE IF NOT EXISTS bookmarks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id INTEGER NOT NULL,
                book_title TEXT NOT NULL,
                session_id TEXT DEFAULT '',
                page_num INTEGER DEFAULT 0,
                page_ocr_excerpt TEXT DEFAULT '',
                note TEXT DEFAULT '',
                bookmark_type TEXT DEFAULT 'manual',
                ts INTEGER NOT NULL,
                FOREIGN KEY (book_id) REFERENCES books(id)
            );

            CREATE TABLE IF NOT EXISTS reading_list (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                author TEXT DEFAULT '',
                status TEXT DEFAULT 'want',
                priority INTEGER DEFAULT 0,
                notes TEXT DEFAULT '',
                added_at INTEGER DEFAULT 0,
                started_at INTEGER,
                finished_at INTEGER
            );

            CREATE INDEX IF NOT EXISTS idx_snapshots_session ON snapshots(session_id);
            CREATE INDEX IF NOT EXISTS idx_notes_session ON notes(session_id);
            CREATE INDEX IF NOT EXISTS idx_sessions_start ON sessions(start_at);
            CREATE INDEX IF NOT EXISTS idx_bookmarks_book ON bookmarks(book_id);
            CREATE INDEX IF NOT EXISTS idx_progress_book ON reading_progress(book_id);
        """)
        await self._conn.commit()

        # 迁移：为旧版本数据库补充新列
        migrations = [
            ("notes", "book_name", "TEXT DEFAULT ''"),
            ("notes", "tags", "TEXT DEFAULT '[]'"),
        ]
        for table, col, definition in migrations:
            try:
                await self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")
                await self._conn.commit()
                logger.info(f"{table} 表已迁移：添加列 {col}")
            except Exception:
                pass  # 列已存在
    
    # ==================== Sessions ====================
    
    async def create_session(self, session: ReadingSession) -> bool:
        """创建会话"""
        try:
            await self._conn.execute(
                """INSERT INTO sessions (id, book_name, start_at, camera_device)
                   VALUES (?, ?, ?, ?)""",
                (session.id, session.book_name, session.start_at, session.camera_device)
            )
            await self._conn.commit()
            return True
        except Exception as e:
            logger.error(f"创建会话失败: {e}")
            return False
    
    async def end_session(self, session_id: str, end_at: int) -> bool:
        """结束会话"""
        try:
            # 更新统计信息
            await self._conn.execute(
                """UPDATE sessions 
                   SET end_at = ?,
                       total_snapshots = (SELECT COUNT(*) FROM snapshots WHERE session_id = ?),
                       total_pages = (SELECT COUNT(DISTINCT fingerprint) FROM snapshots WHERE session_id = ?)
                   WHERE id = ?""",
                (end_at, session_id, session_id, session_id)
            )
            await self._conn.commit()
            return True
        except Exception as e:
            logger.error(f"结束会话失败: {e}")
            return False
    
    async def get_session(self, session_id: str) -> Optional[ReadingSession]:
        """获取会话"""
        async with self._conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return ReadingSession(
                    id=row['id'],
                    book_name=row['book_name'],
                    start_at=row['start_at'],
                    end_at=row['end_at'],
                    camera_device=row['camera_device'],
                    total_pages=row['total_pages'],
                    total_snapshots=row['total_snapshots']
                )
            return None
    
    async def list_sessions(self, limit: int = 10, offset: int = 0) -> List[ReadingSession]:
        """列出会话"""
        sessions = []
        async with self._conn.execute(
            "SELECT * FROM sessions ORDER BY start_at DESC LIMIT ? OFFSET ?",
            (limit, offset)
        ) as cursor:
            async for row in cursor:
                sessions.append(ReadingSession(
                    id=row['id'],
                    book_name=row['book_name'],
                    start_at=row['start_at'],
                    end_at=row['end_at'],
                    camera_device=row['camera_device'],
                    total_pages=row['total_pages'],
                    total_snapshots=row['total_snapshots']
                ))
        return sessions
    
    async def get_today_sessions(self) -> List[ReadingSession]:
        """获取今日会话"""
        today_start = int(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
        sessions = []
        async with self._conn.execute(
            "SELECT * FROM sessions WHERE start_at >= ? ORDER BY start_at DESC",
            (today_start,)
        ) as cursor:
            async for row in cursor:
                sessions.append(ReadingSession(
                    id=row['id'],
                    book_name=row['book_name'],
                    start_at=row['start_at'],
                    end_at=row['end_at'],
                    camera_device=row['camera_device'],
                    total_pages=row['total_pages'],
                    total_snapshots=row['total_snapshots']
                ))
        return sessions
    
    # ==================== Snapshots ====================
    
    async def add_snapshot(self, snapshot: PageSnapshot) -> int:
        """添加快照，返回 ID"""
        cursor = await self._conn.execute(
            """INSERT INTO snapshots (session_id, ts, image_path, ocr_text, fingerprint)
               VALUES (?, ?, ?, ?, ?)""",
            (snapshot.session_id, snapshot.ts, snapshot.image_path, 
             snapshot.ocr_text, snapshot.fingerprint)
        )
        await self._conn.commit()
        return cursor.lastrowid
    
    async def update_snapshot_dwell(self, snapshot_id: int, dwell_ms: int):
        """更新快照停留时长"""
        await self._conn.execute(
            "UPDATE snapshots SET dwell_ms = ? WHERE id = ?",
            (dwell_ms, snapshot_id)
        )
        await self._conn.commit()
    
    async def get_last_snapshot(self, session_id: str) -> Optional[PageSnapshot]:
        """获取会话的最新快照"""
        async with self._conn.execute(
            "SELECT * FROM snapshots WHERE session_id = ? ORDER BY ts DESC LIMIT 1",
            (session_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return PageSnapshot(
                    id=row['id'],
                    session_id=row['session_id'],
                    ts=row['ts'],
                    image_path=row['image_path'],
                    ocr_text=row['ocr_text'],
                    fingerprint=row['fingerprint'],
                    dwell_ms=row['dwell_ms']
                )
            return None
    
    async def get_session_snapshots(self, session_id: str) -> List[PageSnapshot]:
        """获取会话的所有快照"""
        snapshots = []
        async with self._conn.execute(
            "SELECT * FROM snapshots WHERE session_id = ? ORDER BY ts ASC",
            (session_id,)
        ) as cursor:
            async for row in cursor:
                snapshots.append(PageSnapshot(
                    id=row['id'],
                    session_id=row['session_id'],
                    ts=row['ts'],
                    image_path=row['image_path'],
                    ocr_text=row['ocr_text'],
                    fingerprint=row['fingerprint'],
                    dwell_ms=row['dwell_ms']
                ))
        return snapshots
    
    # ==================== Notes ====================
    
    async def add_note(self, note: Note) -> int:
        """添加笔记，返回 ID，并同步写 JSON 文件"""
        cursor = await self._conn.execute(
            """INSERT INTO notes (session_id, ts, content, book_name, tags, page_ocr_context)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                note.session_id,
                note.ts,
                note.content,
                note.book_name,
                json.dumps(note.tags, ensure_ascii=False),
                note.page_ocr_context,
            )
        )
        await self._conn.commit()
        note_id = cursor.lastrowid
        note.id = note_id

        # 同步写 JSON 文件
        if self.notes_dir:
            self._save_note_json(note)

        return note_id

    def _save_note_json(self, note: Note):
        """将笔记写入 JSON 文件，文件名使用 UTC 时间戳"""
        try:
            self.notes_dir.mkdir(parents=True, exist_ok=True)
            filename = f"{note.utc_filename}.json"
            filepath = self.notes_dir / filename
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(note.to_json_dict(), f, ensure_ascii=False, indent=2)
            logger.info(f"笔记 JSON 已写入: {filepath}")
        except Exception as e:
            logger.error(f"写入笔记 JSON 失败: {e}")
    
    async def get_session_notes(self, session_id: str, limit: int = 100) -> List[Note]:
        """获取会话的笔记"""
        notes = []
        async with self._conn.execute(
            "SELECT * FROM notes WHERE session_id = ? ORDER BY ts ASC LIMIT ?",
            (session_id, limit)
        ) as cursor:
            async for row in cursor:
                notes.append(self._row_to_note(row))
        return notes

    async def get_today_notes(self, limit: int = 100) -> List[Note]:
        """获取今日笔记（按 notes.ts 判断，不依赖 session）"""
        today_start = int(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
        notes = []
        async with self._conn.execute(
            "SELECT * FROM notes WHERE ts >= ? ORDER BY ts ASC LIMIT ?",
            (today_start, limit)
        ) as cursor:
            async for row in cursor:
                notes.append(self._row_to_note(row))
        return notes

    async def get_recent_notes(self, days: int = 7, limit: int = 200) -> List[Note]:
        """获取最近 N 天的笔记（不依赖 session，按 notes.ts 判断）"""
        since_ts = int((datetime.now() - timedelta(days=days)).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).timestamp() * 1000)
        notes = []
        async with self._conn.execute(
            "SELECT * FROM notes WHERE ts >= ? ORDER BY ts DESC LIMIT ?",
            (since_ts, limit)
        ) as cursor:
            async for row in cursor:
                notes.append(self._row_to_note(row))
        return notes

    async def count_notes_by_book(self, book_name: str) -> int:
        """统计指定书名的笔记数量"""
        if book_name:
            async with self._conn.execute(
                "SELECT COUNT(*) as count FROM notes WHERE book_name = ?", (book_name,)
            ) as cursor:
                row = await cursor.fetchone()
                return row['count'] if row else 0
        else:
            async with self._conn.execute(
                "SELECT COUNT(*) as count FROM notes"
            ) as cursor:
                row = await cursor.fetchone()
                return row['count'] if row else 0

    @staticmethod
    def _row_to_note(row) -> Note:
        tags_raw = row['tags'] if row['tags'] else '[]'
        try:
            tags = json.loads(tags_raw)
        except Exception:
            tags = []
        return Note(
            id=row['id'],
            session_id=row['session_id'] or "",
            ts=row['ts'],
            content=row['content'],
            book_name=row['book_name'] if row['book_name'] else "",
            tags=tags,
            page_ocr_context=row['page_ocr_context'] if row['page_ocr_context'] else "",
        )
    
    # ==================== Statistics ====================
    
    async def get_daily_summary(self, date: Optional[datetime] = None) -> DailySummary:
        """获取每日阅读摘要"""
        if date is None:
            date = datetime.now()
        
        date_str = date.strftime("%Y-%m-%d")
        day_start = int(date.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
        day_end = int((date + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
        
        summary = DailySummary(date=date_str)
        
        # 获取今日会话
        sessions = []
        longest_duration = 0
        
        async with self._conn.execute(
            "SELECT * FROM sessions WHERE start_at >= ? AND start_at < ? ORDER BY start_at DESC",
            (day_start, day_end)
        ) as cursor:
            async for row in cursor:
                session = ReadingSession(
                    id=row['id'],
                    book_name=row['book_name'],
                    start_at=row['start_at'],
                    end_at=row['end_at'],
                    camera_device=row['camera_device'],
                    total_pages=row['total_pages'],
                    total_snapshots=row['total_snapshots']
                )
                sessions.append(session)
                
                # 统计
                summary.total_sessions += 1
                summary.total_duration_ms += session.duration_ms
                summary.total_pages += session.total_pages
                
                if session.book_name and session.book_name not in summary.book_names:
                    summary.book_names.append(session.book_name)
                
                # 最长会话
                if session.duration_ms > longest_duration:
                    longest_duration = session.duration_ms
                    summary.longest_session = session
        
        # 统计笔记数
        async with self._conn.execute(
            """SELECT COUNT(*) as count FROM notes n
               JOIN sessions s ON n.session_id = s.id
               WHERE s.start_at >= ? AND s.start_at < ?""",
            (day_start, day_end)
        ) as cursor:
            row = await cursor.fetchone()
            summary.total_notes = row['count'] if row else 0
        
        return summary

    # ==================== Books ====================

    async def get_or_create_book(self, title: str, author: str = "") -> Book:
        """获取或创建书籍（按 title 去重）"""
        async with self._conn.execute(
            "SELECT * FROM books WHERE title = ?", (title,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return self._row_to_book(row)

        ts = int(datetime.now().timestamp() * 1000)
        cursor = await self._conn.execute(
            "INSERT INTO books (title, author, created_at) VALUES (?, ?, ?)",
            (title, author, ts)
        )
        await self._conn.commit()
        book_id = cursor.lastrowid
        return Book(id=book_id, title=title, author=author, created_at=ts)

    @staticmethod
    def _row_to_book(row) -> Book:
        return Book(
            id=row['id'],
            title=row['title'],
            author=row['author'] or "",
            genre=row['genre'] or "",
            total_pages=row['total_pages'] or 0,
            cover_image_path=row['cover_image_path'] or "",
            created_at=row['created_at'] or 0,
        )

    # ==================== BookProgress ====================

    async def upsert_book_progress(
        self,
        book_id: int,
        book_title: str,
        page_num: int = 0,
        page_ocr: str = "",
        add_read_time_ms: int = 0,
        status: str = "",
    ) -> BookProgress:
        """插入或更新阅读进度"""
        ts = int(datetime.now().timestamp() * 1000)

        async with self._conn.execute(
            "SELECT * FROM reading_progress WHERE book_id = ?", (book_id,)
        ) as cursor:
            row = await cursor.fetchone()

        if row is None:
            cursor = await self._conn.execute(
                """INSERT INTO reading_progress
                   (book_id, book_title, last_page_num, last_page_ocr, last_read_at,
                    total_read_time_ms, total_pages_read, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (book_id, book_title, page_num, page_ocr[:500], ts,
                 add_read_time_ms, 1 if page_num else 0, status or "reading")
            )
            await self._conn.commit()
            progress_id = cursor.lastrowid
        else:
            progress_id = row['id']
            new_status = status if status else row['status']
            new_pages_read = row['total_pages_read'] + (1 if page_num and page_num != row['last_page_num'] else 0)
            await self._conn.execute(
                """UPDATE reading_progress
                   SET last_page_num = ?, last_page_ocr = ?, last_read_at = ?,
                       total_read_time_ms = total_read_time_ms + ?,
                       total_pages_read = ?, status = ?
                   WHERE id = ?""",
                (page_num or row['last_page_num'],
                 page_ocr[:500] if page_ocr else row['last_page_ocr'],
                 ts, add_read_time_ms, new_pages_read, new_status, progress_id)
            )
            await self._conn.commit()

        async with self._conn.execute(
            "SELECT * FROM reading_progress WHERE id = ?", (progress_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return self._row_to_progress(row)

    async def get_book_progress(self, book_title: str) -> Optional[BookProgress]:
        """按书名查询阅读进度"""
        async with self._conn.execute(
            "SELECT * FROM reading_progress WHERE book_title = ?", (book_title,)
        ) as cursor:
            row = await cursor.fetchone()
            return self._row_to_progress(row) if row else None

    async def list_book_progress(self, status: str = "") -> List[BookProgress]:
        """列出所有阅读进度（可按状态过滤）"""
        if status:
            sql = "SELECT * FROM reading_progress WHERE status = ? ORDER BY last_read_at DESC"
            params = (status,)
        else:
            sql = "SELECT * FROM reading_progress ORDER BY last_read_at DESC"
            params = ()
        results = []
        async with self._conn.execute(sql, params) as cursor:
            async for row in cursor:
                results.append(self._row_to_progress(row))
        return results

    @staticmethod
    def _row_to_progress(row) -> BookProgress:
        return BookProgress(
            id=row['id'],
            book_id=row['book_id'],
            book_title=row['book_title'],
            last_page_num=row['last_page_num'] or 0,
            last_page_ocr=row['last_page_ocr'] or "",
            last_read_at=row['last_read_at'] or 0,
            total_read_time_ms=row['total_read_time_ms'] or 0,
            total_pages_read=row['total_pages_read'] or 0,
            status=row['status'] or "reading",
        )

    # ==================== Bookmarks ====================

    async def create_bookmark(
        self,
        book_id: int,
        book_title: str,
        session_id: str,
        page_num: int = 0,
        page_ocr_excerpt: str = "",
        note: str = "",
        bookmark_type: str = "manual",
    ) -> Bookmark:
        """创建书签"""
        ts = int(datetime.now().timestamp() * 1000)
        cursor = await self._conn.execute(
            """INSERT INTO bookmarks
               (book_id, book_title, session_id, page_num, page_ocr_excerpt, note, bookmark_type, ts)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (book_id, book_title, session_id, page_num,
             page_ocr_excerpt[:200], note, bookmark_type, ts)
        )
        await self._conn.commit()
        bm_id = cursor.lastrowid
        return Bookmark(
            id=bm_id, book_id=book_id, book_title=book_title,
            session_id=session_id, page_num=page_num,
            page_ocr_excerpt=page_ocr_excerpt[:200],
            note=note, bookmark_type=bookmark_type, ts=ts,
        )

    async def list_bookmarks(self, book_title: str = "", limit: int = 20) -> List[Bookmark]:
        """列出书签"""
        if book_title:
            sql = "SELECT * FROM bookmarks WHERE book_title = ? ORDER BY ts DESC LIMIT ?"
            params = (book_title, limit)
        else:
            sql = "SELECT * FROM bookmarks ORDER BY ts DESC LIMIT ?"
            params = (limit,)
        results = []
        async with self._conn.execute(sql, params) as cursor:
            async for row in cursor:
                results.append(self._row_to_bookmark(row))
        return results

    @staticmethod
    def _row_to_bookmark(row) -> Bookmark:
        return Bookmark(
            id=row['id'],
            book_id=row['book_id'],
            book_title=row['book_title'],
            session_id=row['session_id'] or "",
            page_num=row['page_num'] or 0,
            page_ocr_excerpt=row['page_ocr_excerpt'] or "",
            note=row['note'] or "",
            bookmark_type=row['bookmark_type'] or "manual",
            ts=row['ts'],
        )

    # ==================== Reading List ====================

    async def reading_list_add(self, title: str, author: str = "", notes: str = "", priority: int = 0) -> ReadingListItem:
        """加入书单"""
        # 如已存在则直接返回
        async with self._conn.execute(
            "SELECT * FROM reading_list WHERE title = ?", (title,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return self._row_to_list_item(row)

        ts = int(datetime.now().timestamp() * 1000)
        cursor = await self._conn.execute(
            "INSERT INTO reading_list (title, author, status, priority, notes, added_at) VALUES (?, ?, 'want', ?, ?, ?)",
            (title, author, priority, notes, ts)
        )
        await self._conn.commit()
        item_id = cursor.lastrowid
        return ReadingListItem(id=item_id, title=title, author=author, priority=priority, notes=notes, added_at=ts)

    async def reading_list_update_status(self, title: str, status: str) -> bool:
        """更新书单状态"""
        ts = int(datetime.now().timestamp() * 1000)
        extra = ""
        params: list = [status]
        if status == "reading":
            extra = ", started_at = ?"
            params.append(ts)
        elif status == "done":
            extra = ", finished_at = ?"
            params.append(ts)
        params.append(title)
        await self._conn.execute(
            f"UPDATE reading_list SET status = ?{extra} WHERE title = ?", params
        )
        await self._conn.commit()
        return True

    async def reading_list_remove(self, title: str) -> bool:
        """从书单移除"""
        await self._conn.execute("DELETE FROM reading_list WHERE title = ?", (title,))
        await self._conn.commit()
        return True

    async def reading_list_get_all(self, status: str = "") -> List[ReadingListItem]:
        """获取书单列表"""
        if status:
            sql = "SELECT * FROM reading_list WHERE status = ? ORDER BY priority DESC, added_at DESC"
            params = (status,)
        else:
            sql = "SELECT * FROM reading_list ORDER BY priority DESC, added_at DESC"
            params = ()
        results = []
        async with self._conn.execute(sql, params) as cursor:
            async for row in cursor:
                results.append(self._row_to_list_item(row))
        return results

    @staticmethod
    def _row_to_list_item(row) -> ReadingListItem:
        return ReadingListItem(
            id=row['id'],
            title=row['title'],
            author=row['author'] or "",
            status=row['status'] or "want",
            priority=row['priority'] or 0,
            notes=row['notes'] or "",
            added_at=row['added_at'] or 0,
            started_at=row['started_at'],
            finished_at=row['finished_at'],
        )

    # ==================== Reading Stats ====================

    async def get_reading_stats(
        self,
        period: str = "today",
        book_title: str = "",
    ) -> dict:
        """阅读统计：翻页数 / 时长 / 笔记数"""
        now = datetime.now()
        if period == "today":
            since_ts = int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
        elif period == "week":
            since_ts = int((now - timedelta(days=7)).replace(
                hour=0, minute=0, second=0, microsecond=0
            ).timestamp() * 1000)
        elif period == "month":
            since_ts = int((now - timedelta(days=30)).replace(
                hour=0, minute=0, second=0, microsecond=0
            ).timestamp() * 1000)
        else:  # all
            since_ts = 0

        # 会话筛选
        book_filter = " AND book_name = ?" if book_title else ""
        params_base = [since_ts] + ([book_title] if book_title else [])

        async with self._conn.execute(
            f"SELECT COUNT(*) as cnt, SUM(total_pages) as pages, SUM(end_at - start_at) as duration_ms"
            f" FROM sessions WHERE start_at >= ? AND end_at IS NOT NULL{book_filter}",
            params_base,
        ) as cursor:
            row = await cursor.fetchone()
            total_pages = row['pages'] or 0
            total_duration_ms = row['duration_ms'] or 0
            session_count = row['cnt'] or 0

        # 笔记数
        note_filter = " AND book_name = ?" if book_title else ""
        async with self._conn.execute(
            f"SELECT COUNT(*) as cnt FROM notes WHERE ts >= ?{note_filter}",
            [since_ts] + ([book_title] if book_title else []),
        ) as cursor:
            row = await cursor.fetchone()
            note_count = row['cnt'] or 0

        # 书签数
        bm_filter = " AND book_title = ?" if book_title else ""
        async with self._conn.execute(
            f"SELECT COUNT(*) as cnt FROM bookmarks WHERE ts >= ?{bm_filter}",
            [since_ts] + ([book_title] if book_title else []),
        ) as cursor:
            row = await cursor.fetchone()
            bookmark_count = row['cnt'] or 0

        minutes = total_duration_ms // 60000
        duration_str = f"{minutes} 分钟" if minutes < 60 else f"{minutes // 60} 小时 {minutes % 60} 分钟"

        return {
            "period": period,
            "book_title": book_title,
            "session_count": session_count,
            "total_pages": total_pages,
            "total_duration_ms": total_duration_ms,
            "duration_str": duration_str,
            "note_count": note_count,
            "bookmark_count": bookmark_count,
        }
