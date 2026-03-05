"""
书单管理工具：reading_list_manage
"""
import logging
from typing import Dict

logger = logging.getLogger(__name__)


class ListTools:
    def __init__(self, deps):
        self.deps = deps

    async def exec_reading_list_manage(self, params: Dict) -> Dict:
        """管理书单"""
        action = params.get("action", "").strip()
        title = params.get("book_title", "").strip()
        author = params.get("author", "")
        notes = params.get("notes", "")
        filter_status = params.get("filter_status", "")

        if action == "list":
            result = await self.deps.session_manager.manage_reading_list(
                action="list", status=filter_status
            )
            if not result["success"]:
                return result
            items = result["items"]
            if not items:
                return {"success": True, "message": "书单为空", "items": [], "total": 0}
            item_list = [
                {
                    "title": i["title"],
                    "author": i["author"],
                    "status": {"want": "想读", "reading": "在读", "done": "已读"}.get(i["status"], i["status"]),
                }
                for i in items
            ]
            return {"success": True, "message": f"书单共 {len(item_list)} 本", "items": item_list, "total": len(item_list)}

        return await self.deps.session_manager.manage_reading_list(
            action=action, title=title, author=author, notes=notes
        )
