"""
阅读数据管理器
管理笔记、书签、阅读进度、书单、阅读统计
"""
import logging
import time
from typing import Optional, List

from .models import Note, Book, BookProgress, Bookmark, ReadingListItem
from .storage import Storage

logger = logging.getLogger(__name__)


class SessionManager:
    """
    阅读数据管理器（历史命名保留，避免大面积重命名）

    负责：
    - 管理笔记
    - 管理书签、阅读进度、书单
    - 记录 OCR 阅读活动并统计
    """

    def __init__(self, storage: Storage):
        self.storage = storage

    # ==================== 阅读活动记录 ====================

    async def record_reading_activity(
        self, ts: int, page_num: int = None, book_title: str = "",
        ocr_chars: int = 0, is_page_turn: int = 0,
    ) -> int:
        """记录一次 OCR 识别事件。is_page_turn 为本次翻过的页数（0/1/2）"""
        return await self.storage.record_reading_activity(
            ts=ts, page_num=page_num, book_title=book_title,
            ocr_chars=ocr_chars, is_page_turn=is_page_turn,
        )

    async def record_reading_page(
        self, ts: int, book_title: str, page_num: int,
        chapter: str, visible_pages: int, ocr_text: str,
    ) -> int:
        """记录翻页时的完整书页内容（永久保存）"""
        return await self.storage.record_reading_page(
            ts=ts, book_title=book_title, page_num=page_num,
            chapter=chapter, visible_pages=visible_pages, ocr_text=ocr_text,
        )

    # ==================== 笔记 ====================

    async def add_note(
        self,
        content: str,
        page_context: str = "",
        book_name: str = "",
        tags: list = None,
        image_path: str = "",
        user_comment: str = "",
    ) -> Note:
        """添加笔记"""
        note = Note(
            id=0,
            ts=int(time.time() * 1000),
            content=content,
            book_name=book_name,
            tags=tags or [],
            page_ocr_context=page_context,
            image_path=image_path,
            user_comment=user_comment,
        )

        note_id = await self.storage.add_note(note)
        note.id = note_id

        logger.info(f"笔记已添加: {note_id}, 书名: {book_name or '(无)'}, 标签: {note.tags}")
        return note

    async def count_notes_by_book(self, book_name: str) -> int:
        """统计指定书名的笔记数量"""
        return await self.storage.count_notes_by_book(book_name)

    async def get_recent_notes(self, days: int = 7, limit: int = 200) -> List[Note]:
        """获取最近 N 天的笔记"""
        return await self.storage.get_recent_notes(days=days, limit=limit)

    async def get_today_notes(self, limit: int = 100) -> List[Note]:
        """获取今日笔记"""
        return await self.storage.get_today_notes(limit=limit)

    # ==================== 书签 ====================

    async def create_bookmark(
        self,
        book_title: str,
        page_num: int = 0,
        page_ocr_excerpt: str = "",
        note: str = "",
        bookmark_type: str = "manual",
    ) -> Bookmark:
        """创建书签（自动关联或创建书籍）"""
        book = await self.storage.get_or_create_book(book_title)
        return await self.storage.create_bookmark(
            book_id=book.id,
            book_title=book_title,
            session_id="",
            page_num=page_num,
            page_ocr_excerpt=page_ocr_excerpt,
            note=note,
            bookmark_type=bookmark_type,
        )

    async def list_bookmarks(self, book_title: str = "", limit: int = 20) -> List[Bookmark]:
        return await self.storage.list_bookmarks(book_title=book_title, limit=limit)

    # ==================== 阅读进度 ====================

    async def upsert_book_progress(
        self,
        book_title: str,
        page_num: int = 0,
        page_ocr: str = "",
        add_read_time_ms: int = 0,
        status: str = "",
    ) -> BookProgress:
        """更新阅读进度"""
        book = await self.storage.get_or_create_book(book_title)
        return await self.storage.upsert_book_progress(
            book_id=book.id,
            book_title=book_title,
            page_num=page_num,
            page_ocr=page_ocr,
            add_read_time_ms=add_read_time_ms,
            status=status,
        )

    async def get_book_progress(self, book_title: str) -> Optional[BookProgress]:
        return await self.storage.get_book_progress(book_title)

    async def list_book_progress(self, status: str = "") -> List[BookProgress]:
        return await self.storage.list_book_progress(status=status)

    # ==================== 阅读统计 ====================

    async def get_reading_stats(self, days: int = 1, book_title: str = "") -> dict:
        """获取阅读统计（基于 OCR 事件流）。days=0 表示全部历史。"""
        return await self.storage.get_reading_stats(days=days, book_title=book_title)

    # ==================== 书单 ====================

    async def manage_reading_list(
        self,
        action: str,
        title: str = "",
        author: str = "",
        notes: str = "",
        status: str = "",
        priority: int = 0,
    ) -> dict:
        """
        管理书单
        action: add / list / mark_done / mark_reading / remove
        """
        if action == "add":
            if not title:
                return {"success": False, "error": "书名不能为空"}
            item = await self.storage.reading_list_add(title, author, notes, priority)
            return {"success": True, "action": "add", "item": item.to_dict()}

        elif action == "list":
            items = await self.storage.reading_list_get_all(status=status)
            return {
                "success": True,
                "action": "list",
                "items": [i.to_dict() for i in items],
                "total": len(items),
            }

        elif action in ("mark_done", "mark_reading", "mark_want"):
            new_status = {"mark_done": "done", "mark_reading": "reading", "mark_want": "want"}[action]
            if not title:
                return {"success": False, "error": "书名不能为空"}
            await self.storage.reading_list_update_status(title, new_status)
            return {"success": True, "action": action, "title": title, "status": new_status}

        elif action == "remove":
            if not title:
                return {"success": False, "error": "书名不能为空"}
            await self.storage.reading_list_remove(title)
            return {"success": True, "action": "remove", "title": title}

        else:
            return {"success": False, "error": f"未知操作: {action}"}
