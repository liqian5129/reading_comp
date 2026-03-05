"""
书签相关工具：bookmark_create, bookmark_list
"""
import logging
from typing import Dict

logger = logging.getLogger(__name__)


class BookmarkTools:
    def __init__(self, deps):
        self.deps = deps

    async def exec_bookmark_create(self, params: Dict) -> Dict:
        """创建书签"""
        book_title = params.get("book_title", "").strip()
        if not book_title:
            book_title = self.deps.memory.current_book_context.get("book_title", "")
        if not book_title:
            return {"success": False, "error": "无法识别书名，请指定书名"}

        page_num = params.get("page_num", 0)
        if not page_num:
            page_num = self.deps.memory.current_book_context.get("current_page_num", 0) or 0

        note = params.get("note", "")
        page_ocr_excerpt = self.deps.memory.current_page_ocr[:200] if self.deps.memory.current_page_ocr else ""

        bookmark = await self.deps.session_manager.create_bookmark(
            book_title=book_title,
            page_num=page_num,
            page_ocr_excerpt=page_ocr_excerpt,
            note=note,
        )

        page_hint = f"第 {page_num} 页" if page_num else ""
        return {
            "success": True,
            "message": f"书签已创建：《{book_title}》{page_hint}",
            "bookmark_id": bookmark.id,
            "book_title": book_title,
            "page_num": page_num,
            "ts": bookmark.created_at_str,
        }

    async def exec_bookmark_list(self, params: Dict) -> Dict:
        """查询书签列表"""
        book_title = params.get("book_title", "").strip()
        bookmarks = await self.deps.session_manager.list_bookmarks(book_title=book_title, limit=10)

        if not bookmarks:
            scope = f"《{book_title}》" if book_title else "所有书籍"
            return {"success": True, "message": f"{scope}暂无书签", "bookmarks": [], "total": 0}

        bm_list = [
            {
                "id": bm.id,
                "book_title": bm.book_title,
                "page_num": bm.page_num,
                "note": bm.note,
                "excerpt": bm.page_ocr_excerpt[:80] + "..." if len(bm.page_ocr_excerpt) > 80 else bm.page_ocr_excerpt,
                "created_at": bm.created_at_str,
            }
            for bm in bookmarks
        ]

        scope = f"《{book_title}》" if book_title else "全部"
        return {
            "success": True,
            "message": f"{scope}共有 {len(bm_list)} 个书签",
            "bookmarks": bm_list,
            "total": len(bm_list),
        }
