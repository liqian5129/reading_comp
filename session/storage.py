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
    Note,
    Book, BookProgress, Bookmark, ReadingListItem,
    SearchResult, SessionSummary,
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
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT DEFAULT '',
                ts INTEGER NOT NULL,
                content TEXT NOT NULL,
                book_name TEXT DEFAULT '',
                tags TEXT DEFAULT '[]',
                page_ocr_context TEXT DEFAULT '',
                user_comment TEXT DEFAULT ''
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

            CREATE TABLE IF NOT EXISTS reading_activity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER NOT NULL,
                page_num INTEGER,
                book_title TEXT DEFAULT '',
                ocr_chars INTEGER DEFAULT 0,
                is_page_turn INTEGER DEFAULT 0,
                page_text TEXT DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_bookmarks_book ON bookmarks(book_id);
            CREATE INDEX IF NOT EXISTS idx_progress_book ON reading_progress(book_id);
            CREATE INDEX IF NOT EXISTS idx_reading_activity_ts ON reading_activity(ts);

            CREATE TABLE IF NOT EXISTS reading_pages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER NOT NULL,
                book_title TEXT DEFAULT '',
                page_num INTEGER DEFAULT 0,
                chapter TEXT DEFAULT '',
                visible_pages INTEGER DEFAULT 1,
                ocr_text TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_reading_pages_ts ON reading_pages(ts);

            CREATE TABLE IF NOT EXISTS weread_books (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id TEXT UNIQUE NOT NULL, title TEXT NOT NULL,
                author TEXT DEFAULT '', translator TEXT DEFAULT '',
                cover TEXT DEFAULT '', category TEXT DEFAULT '',
                finish_reading INTEGER DEFAULT 0,
                reading_time_s INTEGER DEFAULT 0,
                progress INTEGER DEFAULT 0,
                synced_at INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS weread_highlights (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bookmark_id TEXT UNIQUE NOT NULL, book_id TEXT NOT NULL,
                book_title TEXT NOT NULL, content TEXT NOT NULL,
                chapter_uid INTEGER DEFAULT 0,
                chapter_title TEXT DEFAULT '', chapter_idx INTEGER DEFAULT 0,
                created_at INTEGER DEFAULT 0, synced_at INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS weread_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                review_id TEXT UNIQUE NOT NULL, book_id TEXT NOT NULL,
                book_title TEXT NOT NULL, abstract TEXT DEFAULT '',
                content TEXT NOT NULL, chapter_uid INTEGER DEFAULT 0,
                chapter_title TEXT DEFAULT '', chapter_idx INTEGER DEFAULT 0,
                created_at INTEGER DEFAULT 0, synced_at INTEGER DEFAULT 0,
                note_type TEXT DEFAULT '想法'
            );
            CREATE TABLE IF NOT EXISTS weread_progress (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id TEXT UNIQUE NOT NULL, book_title TEXT NOT NULL,
                progress INTEGER DEFAULT 0,
                reading_time_s INTEGER DEFAULT 0,
                chapter_uid INTEGER DEFAULT 0,
                chapter_offset INTEGER DEFAULT 0,
                synced_at INTEGER DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_wr_highlights_book ON weread_highlights(book_id);
            CREATE INDEX IF NOT EXISTS idx_wr_notes_book ON weread_notes(book_id);

            CREATE TABLE IF NOT EXISTS session_summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                summary_text TEXT NOT NULL,
                key_topics TEXT DEFAULT '[]',
                embedding BLOB,
                created_at INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS note_links (
                note_id    INTEGER NOT NULL,
                related_id INTEGER NOT NULL,
                score      REAL NOT NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY (note_id, related_id)
            );

            CREATE INDEX IF NOT EXISTS idx_note_links_note ON note_links(note_id);
            CREATE INDEX IF NOT EXISTS idx_note_links_related ON note_links(related_id);

            CREATE TABLE IF NOT EXISTS reading_digests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_title TEXT DEFAULT '',
                digest_text TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );
        """)
        await self._conn.commit()

        # 迁移：为旧版本数据库补充新列
        migrations = [
            ("notes", "book_name", "TEXT DEFAULT ''"),
            ("notes", "tags", "TEXT DEFAULT '[]'"),
            ("weread_notes", "note_type", "TEXT DEFAULT '想法'"),
            ("notes", "image_path", "TEXT DEFAULT ''"),
            ("notes", "embedding", "BLOB"),
            ("weread_highlights", "embedding", "BLOB"),
            ("weread_notes", "embedding", "BLOB"),
            ("reading_activity", "is_page_turn", "INTEGER DEFAULT 0"),
            ("reading_activity", "page_text", "TEXT DEFAULT ''"),
            ("notes", "user_comment", "TEXT DEFAULT ''"),
        ]
        for table, col, definition in migrations:
            try:
                await self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")
                await self._conn.commit()
                logger.info(f"{table} 表已迁移：添加列 {col}")
            except Exception:
                pass  # 列已存在
    
    # ==================== Reading Activity ====================

    async def record_reading_activity(
        self, ts: int, page_num: int = None, book_title: str = "",
        ocr_chars: int = 0, is_page_turn: int = 0,
    ) -> int:
        """记录一次 OCR 识别事件。is_page_turn 为本次翻过的页数（0=未翻页，1=单页，2=双页）"""
        cursor = await self._conn.execute(
            "INSERT INTO reading_activity (ts, page_num, book_title, ocr_chars, is_page_turn) VALUES (?, ?, ?, ?, ?)",
            (ts, page_num, book_title, ocr_chars, int(is_page_turn)),
        )
        await self._conn.commit()
        return cursor.lastrowid

    async def record_reading_page(
        self,
        ts: int,
        book_title: str,
        page_num: int,
        chapter: str,
        visible_pages: int,
        ocr_text: str,
    ) -> int:
        """记录一次翻页时的完整书页内容（永久保存）"""
        cursor = await self._conn.execute(
            """INSERT INTO reading_pages
               (ts, book_title, page_num, chapter, visible_pages, ocr_text)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (ts, book_title, page_num, chapter, visible_pages, ocr_text),
        )
        await self._conn.commit()
        return cursor.lastrowid

    async def get_reading_pages(self, days: int = 1) -> List[dict]:
        """查询指定天数内的书页记录，按时间正序返回"""
        import time as _t
        since_ts = int((_t.time() - days * 86400) * 1000) if days > 0 else 0
        results = []
        async with self._conn.execute(
            """SELECT ts, book_title, page_num, chapter, visible_pages, ocr_text
               FROM reading_pages WHERE ts >= ? ORDER BY ts""",
            (since_ts,),
        ) as cursor:
            async for row in cursor:
                results.append({
                    "ts": row["ts"],
                    "book_title": row["book_title"],
                    "page_num": row["page_num"],
                    "chapter": row["chapter"],
                    "visible_pages": row["visible_pages"],
                    "ocr_text": row["ocr_text"],
                })
        return results

    # ==================== Notes ====================
    
    async def add_note(self, note: Note) -> int:
        """添加笔记，返回 ID，并同步写 JSON 文件"""
        cursor = await self._conn.execute(
            """INSERT INTO notes (session_id, ts, content, book_name, tags, page_ocr_context, image_path, user_comment)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                note.session_id,
                note.ts,
                note.content,
                note.book_name,
                json.dumps(note.tags, ensure_ascii=False),
                note.page_ocr_context,
                note.image_path,
                note.user_comment,
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
            image_path=row['image_path'] if row['image_path'] else "",
        )
    
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

    _ACTIVE_GAP_MS = 5 * 60 * 1000  # 两次 OCR 间隔 < 5 分钟视为连续阅读

    async def get_reading_stats(
        self,
        days: int = 1,
        book_title: str = "",
    ) -> dict:
        """
        基于 reading_activity（OCR 事件流）统计阅读数据。
        - 页数：SUM(is_page_turn)
        - 时长：相邻 OCR 事件间隔 < 5 分钟则计为阅读时间
        days=0 表示全部历史。
        """
        import time as _t
        if days <= 0:
            since_ts = 0
        else:
            since_ts = int((_t.time() - days * 86400) * 1000)

        book_filter = " AND book_title = ?" if book_title else ""
        params = [since_ts] + ([book_title] if book_title else [])

        # 1. 页数：基于内容变化的翻页检测计数
        async with self._conn.execute(
            f"""SELECT SUM(is_page_turn) as page_count
                FROM reading_activity
                WHERE ts >= ?{book_filter}""",
            params,
        ) as cursor:
            row = await cursor.fetchone()
            total_pages = row['page_count'] or 0

        # 1b. 阅读过的书籍列表
        async with self._conn.execute(
            f"""SELECT DISTINCT book_title
                FROM reading_activity
                WHERE ts >= ? AND book_title != ''{book_filter}""",
            params,
        ) as cursor:
            books_read = [row['book_title'] async for row in cursor]

        # 2. 阅读时长：相邻事件间隔 < 5 分钟则累加
        async with self._conn.execute(
            f"SELECT ts FROM reading_activity WHERE ts >= ?{book_filter} ORDER BY ts",
            params,
        ) as cursor:
            timestamps = [row['ts'] async for row in cursor]

        active_ms = 0
        for i in range(1, len(timestamps)):
            gap = timestamps[i] - timestamps[i - 1]
            if gap <= self._ACTIVE_GAP_MS:
                active_ms += gap

        # 3. 笔记数
        note_filter = " AND book_name = ?" if book_title else ""
        async with self._conn.execute(
            f"SELECT COUNT(*) as cnt FROM notes WHERE ts >= ?{note_filter}",
            [since_ts] + ([book_title] if book_title else []),
        ) as cursor:
            row = await cursor.fetchone()
            note_count = row['cnt'] or 0

        # 4. 书签数
        bm_filter = " AND book_title = ?" if book_title else ""
        async with self._conn.execute(
            f"SELECT COUNT(*) as cnt FROM bookmarks WHERE ts >= ?{bm_filter}",
            [since_ts] + ([book_title] if book_title else []),
        ) as cursor:
            row = await cursor.fetchone()
            bookmark_count = row['cnt'] or 0

        minutes = active_ms // 60000
        duration_str = f"{minutes} 分钟" if minutes < 60 else f"{minutes // 60} 小时 {minutes % 60} 分钟"

        return {
            "period": days,
            "book_title": book_title,
            "total_pages": total_pages,
            "total_duration_ms": active_ms,
            "duration_str": duration_str,
            "note_count": note_count,
            "bookmark_count": bookmark_count,
            "books_read": books_read,
            "ocr_events": len(timestamps),
        }

    # ==================== Embeddings ====================

    async def save_embedding(self, table: str, row_id: int, embedding: list) -> bool:
        """保存 embedding 向量到指定表"""
        allowed = {"notes", "weread_highlights", "weread_notes"}
        if table not in allowed:
            logger.warning(f"save_embedding: 不支持的表 {table}")
            return False
        try:
            blob = json.dumps(embedding).encode("utf-8")
            await self._conn.execute(
                f"UPDATE {table} SET embedding = ? WHERE id = ?", (blob, row_id)
            )
            await self._conn.commit()
            return True
        except Exception as e:
            logger.warning(f"save_embedding 失败 ({table}#{row_id}): {e}")
            return False

    async def get_rows_missing_embedding(
        self, table: str, limit: int = 50, book_id: str = None
    ) -> list:
        """查询 embedding 为空的行，用于启动时或同步后补全"""
        allowed = {"notes", "weread_highlights", "weread_notes"}
        if table not in allowed:
            return []
        if table == "notes":
            sql = "SELECT id, content, book_name FROM notes WHERE embedding IS NULL"
            params: list = []
            if book_id:
                sql += " AND book_name = ?"
                params.append(book_id)
        else:
            sql = f"SELECT id, content, book_title FROM {table} WHERE embedding IS NULL"
            params = []
            if book_id:
                sql += " AND book_id = ?"
                params.append(book_id)
        sql += f" LIMIT {limit}"
        rows = []
        async with self._conn.execute(sql, params) as cursor:
            async for row in cursor:
                rows.append(dict(row))
        return rows

    async def search_by_embedding(
        self,
        query_vec: list,
        tables: Optional[List[str]] = None,
        top_k: int = 5,
    ) -> List[SearchResult]:
        """
        在指定表中用余弦相似度检索最相关的记忆条目。
        tables 默认为 ["notes", "weread_highlights", "weread_notes", "session_summaries"]
        """
        try:
            import numpy as np
        except ImportError:
            logger.warning("search_by_embedding: numpy 未安装，跳过向量检索")
            return []

        if tables is None:
            tables = ["notes", "weread_highlights", "weread_notes", "session_summaries"]

        q = np.array(query_vec, dtype=np.float32)
        q_norm = np.linalg.norm(q)
        if q_norm == 0:
            return []
        q = q / q_norm

        candidates: List[SearchResult] = []

        for table in tables:
            try:
                rows = await self._fetch_embedding_rows(table)
                for row in rows:
                    emb_blob = row.get("embedding")
                    if not emb_blob:
                        continue
                    try:
                        emb = np.array(json.loads(emb_blob), dtype=np.float32)
                        emb_norm = np.linalg.norm(emb)
                        if emb_norm == 0:
                            continue
                        score = float(np.dot(q, emb / emb_norm))
                    except Exception:
                        continue

                    content = row.get("content", "") or row.get("summary_text", "")
                    book_name = (
                        row.get("book_name", "")
                        or row.get("book_title", "")
                        or ""
                    )
                    candidates.append(SearchResult(
                        source=table,
                        content=content,
                        book_name=book_name,
                        score=score,
                        created_at=row.get("created_at", 0) or row.get("ts", 0) or 0,
                    ))
            except Exception as e:
                logger.warning(f"search_by_embedding: 表 {table} 检索失败: {e}")
                continue

        candidates.sort(key=lambda x: x.score, reverse=True)
        return candidates[:top_k]

    async def _fetch_embedding_rows(self, table: str) -> list:
        """从指定表加载所有带 embedding 的行"""
        rows = []
        if table == "notes":
            sql = "SELECT id, content, book_name, ts, embedding FROM notes WHERE embedding IS NOT NULL"
        elif table == "weread_highlights":
            sql = "SELECT id, content, book_title, created_at, embedding FROM weread_highlights WHERE embedding IS NOT NULL"
        elif table == "weread_notes":
            sql = "SELECT id, content, book_title, created_at, embedding FROM weread_notes WHERE embedding IS NOT NULL"
        elif table == "session_summaries":
            sql = "SELECT id, summary_text, created_at, embedding FROM session_summaries WHERE embedding IS NOT NULL"
        else:
            return []

        async with self._conn.execute(sql) as cursor:
            async for row in cursor:
                rows.append(dict(row))
        return rows

    # ==================== Session Summaries ====================

    async def save_session_summary(
        self,
        summary_text: str,
        key_topics: List[str],
        embedding: Optional[list] = None,
    ) -> int:
        """保存会话巩固摘要，返回 ID"""
        try:
            ts = int(datetime.now().timestamp() * 1000)
            blob = json.dumps(embedding).encode("utf-8") if embedding else None
            cursor = await self._conn.execute(
                "INSERT INTO session_summaries (summary_text, key_topics, embedding, created_at) VALUES (?, ?, ?, ?)",
                (summary_text, json.dumps(key_topics, ensure_ascii=False), blob, ts),
            )
            await self._conn.commit()
            return cursor.lastrowid
        except Exception as e:
            logger.error(f"save_session_summary 失败: {e}")
            return 0

    async def load_recent_summaries(self, n: int = 2) -> List[str]:
        """加载最近 n 次会话摘要文本"""
        results = []
        try:
            async with self._conn.execute(
                "SELECT summary_text FROM session_summaries ORDER BY created_at DESC LIMIT ?",
                (n,),
            ) as cursor:
                async for row in cursor:
                    results.append(row["summary_text"])
        except Exception as e:
            logger.warning(f"load_recent_summaries 失败: {e}")
        return list(reversed(results))  # 按时间正序返回

    # ==================== Note Links ====================

    async def save_note_links(self, note_id: int, related: List[tuple]) -> bool:
        """
        批量保存笔记关联（双向）。
        related: [(related_id, score), ...]
        """
        if not related:
            return True
        try:
            ts = int(datetime.now().timestamp() * 1000)
            rows = []
            for related_id, score in related:
                rows.append((note_id, related_id, score, ts))
                rows.append((related_id, note_id, score, ts))  # 双向
            await self._conn.executemany(
                "INSERT OR REPLACE INTO note_links (note_id, related_id, score, created_at) VALUES (?, ?, ?, ?)",
                rows,
            )
            await self._conn.commit()
            return True
        except Exception as e:
            logger.warning(f"save_note_links 失败: {e}")
            return False

    # ==================== Reading Digests ====================

    async def save_reading_digest(self, digest_text: str, book_title: str = "") -> int:
        """保存本次阅读内容压缩摘要，返回 ID"""
        import time as _time
        ts = int(_time.time() * 1000)
        cursor = await self._conn.execute(
            "INSERT INTO reading_digests (book_title, digest_text, created_at) VALUES (?, ?, ?)",
            (book_title, digest_text, ts),
        )
        await self._conn.commit()
        return cursor.lastrowid

    async def get_recent_digests(self, days: int = 7) -> List[dict]:
        """查询最近 N 天的阅读摘要"""
        import time as _time
        cutoff = int((_time.time() - days * 86400) * 1000)
        result = []
        async with self._conn.execute(
            "SELECT book_title, digest_text, created_at FROM reading_digests WHERE created_at >= ? ORDER BY created_at DESC LIMIT 20",
            (cutoff,),
        ) as cursor:
            async for row in cursor:
                result.append({"book_title": row[0], "digest_text": row[1], "created_at": row[2]})
        return result

    async def get_related_notes(self, note_id: int, limit: int = 5) -> List[dict]:
        """按笔记 ID 查询关联笔记（按相似度降序）"""
        results = []
        try:
            async with self._conn.execute(
                """SELECT nl.related_id, nl.score, n.content, n.book_name, n.ts
                   FROM note_links nl
                   LEFT JOIN notes n ON nl.related_id = n.id
                   WHERE nl.note_id = ?
                   ORDER BY nl.score DESC LIMIT ?""",
                (note_id, limit),
            ) as cursor:
                async for row in cursor:
                    results.append({
                        "id": row["related_id"],
                        "score": row["score"],
                        "content": row["content"] or "",
                        "book_name": row["book_name"] or "",
                        "ts": row["ts"] or 0,
                    })
        except Exception as e:
            logger.warning(f"get_related_notes 失败: {e}")
        return results
