"""
阅读进度相关工具：reading_progress_update, reading_progress_query, reading_stats
"""
import logging
from typing import Dict

logger = logging.getLogger(__name__)


class ProgressTools:
    def __init__(self, deps):
        self.deps = deps

    async def exec_reading_progress_update(self, params: Dict) -> Dict:
        """更新阅读进度"""
        book_title = params.get("book_title", "").strip()
        if not book_title:
            return {"success": False, "error": "书名不能为空"}

        page_num = params.get("page_num", 0)
        status = params.get("status", "")

        progress = await self.deps.session_manager.upsert_book_progress(
            book_title=book_title,
            page_num=page_num,
            status=status,
        )

        page_hint = f"第 {progress.last_page_num} 页" if progress.last_page_num else ""
        return {
            "success": True,
            "message": f"《{book_title}》进度已更新：{page_hint} 状态={progress.status_str}",
            "book_title": book_title,
            "last_page_num": progress.last_page_num,
            "status": progress.status,
            "total_pages_read": progress.total_pages_read,
        }

    async def exec_reading_progress_query(self, params: Dict) -> Dict:
        """查询阅读进度"""
        book_title = params.get("book_title", "").strip()

        if book_title:
            progress = await self.deps.session_manager.get_book_progress(book_title)
            if not progress:
                return {"success": True, "message": f"暂无《{book_title}》的阅读记录", "progress": None}
            return {
                "success": True,
                "progress": {
                    "book_title": progress.book_title,
                    "last_page_num": progress.last_page_num,
                    "status": progress.status_str,
                    "total_pages_read": progress.total_pages_read,
                },
            }
        else:
            all_progress = await self.deps.session_manager.list_book_progress(status="reading")
            if not all_progress:
                return {"success": True, "message": "目前没有在读书籍", "books": []}
            books = [
                {"book_title": p.book_title, "last_page_num": p.last_page_num,
                 "total_pages_read": p.total_pages_read}
                for p in all_progress
            ]
            return {"success": True, "message": f"正在阅读 {len(books)} 本书", "books": books}

    async def exec_reading_stats(self, params: Dict) -> Dict:
        """阅读统计"""
        period = params.get("period", "today")
        book_title = params.get("book_title", "")
        stats = await self.deps.session_manager.get_reading_stats(period=period, book_title=book_title)

        period_map = {"today": "今天", "week": "本周", "month": "本月", "all": "全部"}
        period_label = period_map.get(period, period)
        book_hint = f"《{book_title}》" if book_title else ""

        return {
            "success": True,
            "message": f"{period_label}{book_hint}：翻页 {stats['total_pages']} 页，"
                       f"阅读 {stats['duration_str']}，笔记 {stats['note_count']} 条，"
                       f"书签 {stats['bookmark_count']} 个",
            **stats,
        }
