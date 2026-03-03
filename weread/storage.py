"""
微信读书 SQLite CRUD 层
复用主 Storage 的同一 aiosqlite connection
"""
import logging
import time
from typing import List, Optional

import aiosqlite

from .models import WeReadBook, WeReadHighlight, WeReadNote, WeReadProgress

logger = logging.getLogger(__name__)


class WeReadStorage:
    """微信读书存储层，复用主数据库连接"""

    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn

    # ==================== Books ====================

    async def upsert_books(self, books: List[WeReadBook]) -> int:
        """批量插入或更新书架书籍，返回写入数量"""
        now = int(time.time())
        count = 0
        for b in books:
            try:
                await self._conn.execute(
                    """INSERT INTO weread_books
                       (book_id, title, author, translator, cover, category,
                        finish_reading, reading_time_s, progress, synced_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(book_id) DO UPDATE SET
                         title=excluded.title, author=excluded.author,
                         translator=excluded.translator, cover=excluded.cover,
                         category=excluded.category,
                         finish_reading=excluded.finish_reading,
                         reading_time_s=excluded.reading_time_s,
                         progress=excluded.progress,
                         synced_at=excluded.synced_at""",
                    (b.book_id, b.title, b.author, b.translator, b.cover,
                     b.category, 1 if b.finish_reading else 0,
                     b.reading_time_s, b.progress, now)
                )
                count += 1
            except Exception as e:
                logger.error(f"upsert_book 失败 {b.book_id}: {e}")
        await self._conn.commit()
        return count

    async def list_books(self) -> List[WeReadBook]:
        """列出所有书架书籍（按阅读时长降序）"""
        books = []
        async with self._conn.execute(
            "SELECT * FROM weread_books ORDER BY reading_time_s DESC"
        ) as cursor:
            async for row in cursor:
                books.append(self._row_to_book(row))
        return books

    async def find_book_by_title(self, title: str) -> Optional[WeReadBook]:
        """按书名模糊查找（返回最佳匹配）"""
        async with self._conn.execute(
            "SELECT * FROM weread_books WHERE title LIKE ? ORDER BY reading_time_s DESC LIMIT 1",
            (f"%{title}%",)
        ) as cursor:
            row = await cursor.fetchone()
            return self._row_to_book(row) if row else None

    @staticmethod
    def _row_to_book(row) -> WeReadBook:
        return WeReadBook(
            book_id=row["book_id"],
            title=row["title"],
            author=row["author"] or "",
            translator=row["translator"] or "",
            cover=row["cover"] or "",
            category=row["category"] or "",
            finish_reading=bool(row["finish_reading"]),
            reading_time_s=row["reading_time_s"] or 0,
            progress=row["progress"] or 0,
            synced_at=row["synced_at"] or 0,
        )

    # ==================== Highlights ====================

    async def upsert_highlights(self, highlights: List[WeReadHighlight]) -> int:
        """批量插入或更新划线"""
        now = int(time.time())
        count = 0
        for h in highlights:
            try:
                await self._conn.execute(
                    """INSERT INTO weread_highlights
                       (bookmark_id, book_id, book_title, content,
                        chapter_uid, chapter_title, chapter_idx,
                        created_at, synced_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(bookmark_id) DO UPDATE SET
                         content=excluded.content,
                         chapter_title=excluded.chapter_title,
                         synced_at=excluded.synced_at""",
                    (h.bookmark_id, h.book_id, h.book_title, h.content,
                     h.chapter_uid, h.chapter_title, h.chapter_idx,
                     h.created_at, now)
                )
                count += 1
            except Exception as e:
                logger.error(f"upsert_highlight 失败 {h.bookmark_id}: {e}")
        await self._conn.commit()
        return count

    async def list_highlights(self, book_id: str, limit: int = 50) -> List[WeReadHighlight]:
        """查询指定书的划线，按章节顺序排列"""
        results = []
        async with self._conn.execute(
            """SELECT * FROM weread_highlights WHERE book_id = ?
               ORDER BY chapter_idx ASC, created_at ASC LIMIT ?""",
            (book_id, limit)
        ) as cursor:
            async for row in cursor:
                results.append(self._row_to_highlight(row))
        return results

    @staticmethod
    def _row_to_highlight(row) -> WeReadHighlight:
        return WeReadHighlight(
            bookmark_id=row["bookmark_id"],
            book_id=row["book_id"],
            book_title=row["book_title"],
            content=row["content"],
            chapter_uid=row["chapter_uid"] or 0,
            chapter_title=row["chapter_title"] or "",
            chapter_idx=row["chapter_idx"] or 0,
            created_at=row["created_at"] or 0,
            synced_at=row["synced_at"] or 0,
        )

    # ==================== Notes ====================

    async def upsert_notes(self, notes: List[WeReadNote]) -> int:
        """批量插入或更新想法/点评（含 note_type 区分）"""
        now = int(time.time())
        count = 0
        for n in notes:
            try:
                await self._conn.execute(
                    """INSERT INTO weread_notes
                       (review_id, book_id, book_title, abstract, content,
                        chapter_uid, chapter_title, chapter_idx,
                        created_at, synced_at, note_type)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(review_id) DO UPDATE SET
                         content=excluded.content,
                         abstract=excluded.abstract,
                         chapter_title=excluded.chapter_title,
                         note_type=excluded.note_type,
                         synced_at=excluded.synced_at""",
                    (n.review_id, n.book_id, n.book_title, n.abstract, n.content,
                     n.chapter_uid, n.chapter_title, n.chapter_idx,
                     n.created_at, now, n.note_type)
                )
                count += 1
            except Exception as e:
                logger.error(f"upsert_note 失败 {n.review_id}: {e}")
        await self._conn.commit()
        return count

    async def list_notes(self, book_id: str, limit: int = 50,
                         note_type: str = "") -> List[WeReadNote]:
        """
        查询指定书的笔记。
        note_type: "想法" / "点评" / "" (空=全部)
        """
        if note_type:
            sql = """SELECT * FROM weread_notes WHERE book_id = ? AND note_type = ?
                     ORDER BY chapter_idx ASC, created_at ASC LIMIT ?"""
            params = (book_id, note_type, limit)
        else:
            sql = """SELECT * FROM weread_notes WHERE book_id = ?
                     ORDER BY chapter_idx ASC, created_at ASC LIMIT ?"""
            params = (book_id, limit)
        results = []
        async with self._conn.execute(sql, params) as cursor:
            async for row in cursor:
                results.append(self._row_to_note(row))
        return results

    @staticmethod
    def _row_to_note(row) -> WeReadNote:
        return WeReadNote(
            review_id=row["review_id"],
            book_id=row["book_id"],
            book_title=row["book_title"],
            abstract=row["abstract"] or "",
            content=row["content"],
            note_type=row["note_type"] if row["note_type"] else "想法",
            chapter_uid=row["chapter_uid"] or 0,
            chapter_title=row["chapter_title"] or "",
            chapter_idx=row["chapter_idx"] or 0,
            created_at=row["created_at"] or 0,
            synced_at=row["synced_at"] or 0,
        )

    # ==================== Progress ====================

    async def upsert_progress(self, progress: WeReadProgress) -> None:
        """插入或更新阅读进度"""
        now = int(time.time())
        await self._conn.execute(
            """INSERT INTO weread_progress
               (book_id, book_title, progress, reading_time_s,
                chapter_uid, chapter_offset, synced_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(book_id) DO UPDATE SET
                 book_title=excluded.book_title,
                 progress=excluded.progress,
                 reading_time_s=excluded.reading_time_s,
                 chapter_uid=excluded.chapter_uid,
                 chapter_offset=excluded.chapter_offset,
                 synced_at=excluded.synced_at""",
            (progress.book_id, progress.book_title, progress.progress,
             progress.reading_time_s, progress.chapter_uid,
             progress.chapter_offset, now)
        )
        await self._conn.commit()

    async def get_progress(self, book_id: str) -> Optional[WeReadProgress]:
        """查询指定书的进度缓存"""
        async with self._conn.execute(
            "SELECT * FROM weread_progress WHERE book_id = ?", (book_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return WeReadProgress(
                book_id=row["book_id"],
                book_title=row["book_title"],
                progress=row["progress"] or 0,
                reading_time_s=row["reading_time_s"] or 0,
                chapter_uid=row["chapter_uid"] or 0,
                chapter_offset=row["chapter_offset"] or 0,
                synced_at=row["synced_at"] or 0,
            )
