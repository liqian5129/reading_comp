"""
工具定义和执行
定义 AI 可调用的工具，以及执行逻辑
"""
import logging
from typing import Dict, Any, List, Callable, Optional
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)


# ==================== 工具定义 (JSON Schema) ====================

READING_NOTE_TOOL = {
    "name": "reading_note",
    "description": (
        "保存一条读书笔记到本地。书名优先从当前阅读上下文自动获取，无需用户指定。"
        "用户表达记录意图（摘抄、记下来、我觉得、有感想）时调用。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "笔记内容"
            },
            "book_name": {
                "type": "string",
                "description": "书名，从对话上下文中识别，用户未提及则留空"
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "标签列表，由用户指定或从内容中提取关键词，可为空"
            }
        },
        "required": ["content"]
    }
}

READING_HISTORY_TOOL = {
    "name": "reading_history",
    "description": "查询本次及近期的阅读会话记录（时长、翻页数、笔记数）。用户询问今天或近期读书情况时调用。",
    "input_schema": {
        "type": "object",
        "properties": {
            "days": {
                "type": "integer",
                "description": "查询最近几天的记录，默认 7 天"
            }
        },
        "required": []
    }
}

READING_NOTES_TOOL = {
    "name": "reading_notes",
    "description": (
        "查询本地保存的读书笔记列表（含时间、书名、标签、内容）。"
        "用户想回顾、整理或查看之前记录的笔记时调用；可按书名或时间范围过滤。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "days": {
                "type": "integer",
                "description": "查询最近几天的笔记，默认 7 天"
            },
            "book_name": {
                "type": "string",
                "description": "按书名过滤，留空则返回所有书的笔记"
            }
        },
        "required": []
    }
}

BOOKMARK_CREATE_TOOL = {
    "name": "bookmark_create",
    "description": (
        "创建书签，标记当前阅读位置，自动摘录当前书页内容。"
        "用户想记录当前读到哪里（以便下次继续）时调用。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "book_title": {"type": "string", "description": "书名，从上下文识别"},
            "page_num": {"type": "integer", "description": "页码，用户提及或视觉识别，未知则 0"},
            "note": {"type": "string", "description": "用户对书签的备注"},
        },
        "required": ["book_title"],
    }
}

BOOKMARK_LIST_TOOL = {
    "name": "bookmark_list",
    "description": "查询已保存的书签列表，显示每个书签的页码和摘录。用户想了解上次读到哪里或查看历史书签时调用。",
    "input_schema": {
        "type": "object",
        "properties": {
            "book_title": {"type": "string", "description": "按书名过滤，留空返回所有书的书签"},
        },
        "required": [],
    }
}

READING_PROGRESS_UPDATE_TOOL = {
    "name": "reading_progress_update",
    "description": (
        "更新本地记录的阅读进度（页码或状态）。"
        "用户告知当前页码、标记某书已读完或暂停时调用。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "book_title": {"type": "string", "description": "书名"},
            "page_num": {"type": "integer", "description": "当前页码，未提及则 0"},
            "status": {
                "type": "string",
                "description": "reading（阅读中）/ finished（已完成）/ paused（已暂停），不变则留空",
            },
        },
        "required": ["book_title"],
    }
}

READING_PROGRESS_QUERY_TOOL = {
    "name": "reading_progress_query",
    "description": (
        "查询本地记录的阅读进度（页码、完成度、阅读时长）。"
        "用户问进度但未指定书名时调用（留空 book_title），可作为推断当前在读书名的依据。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "book_title": {"type": "string", "description": "书名，留空则返回所有在读书籍"},
        },
        "required": [],
    }
}

READING_LIST_MANAGE_TOOL = {
    "name": "reading_list_manage",
    "description": (
        "管理书单（想读/在读/已读列表）：添加、查看、更新状态或移除书目。"
        "用户操作个人书单时调用，支持按状态过滤查看。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "add（加入）/ list（查看）/ mark_done（标记完成）/ mark_reading（标记在读）/ remove（移除）",
            },
            "book_title": {"type": "string", "description": "书名，list 操作可留空"},
            "author": {"type": "string", "description": "作者（add 时可选）"},
            "notes": {"type": "string", "description": "备注（add 时可选）"},
            "filter_status": {
                "type": "string",
                "description": "list 时的状态过滤: want / reading / done，留空返回全部",
            },
        },
        "required": ["action"],
    }
}

READING_STATS_TOOL = {
    "name": "reading_stats",
    "description": (
        "查询阅读统计摘要（翻页数、时长、笔记数、书签数），可按天/周/月/全部统计。"
        "用户询问阅读量或阅读习惯时调用。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "period": {
                "type": "string",
                "description": "统计周期: today（今天）/ week（近7天）/ month（近30天）/ all（全部），默认 today",
            },
            "book_title": {"type": "string", "description": "按书名过滤，留空则统计全部书"},
        },
        "required": [],
    }
}

SET_TIMER_TOOL = {
    "name": "set_timer",
    "description": (
        "设定倒计时定时器，可在触发时播报提醒、发飞书卡片，或将当前书页 OCR 内容推送飞书。"
        "用户需要延时提醒或延时发送书页内容时调用；send_current_page=true 时内容在触发时刻读取。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "minutes": {"type": "integer", "description": "多少分钟后触发"},
            "message": {
                "type": "string",
                "description": "触发时 TTS 播报的内容（留空则使用默认文案）"
            },
            "feishu_push": {
                "type": "boolean",
                "description": "是否发送飞书提醒卡片（适合纯提醒场景），默认 false"
            },
            "send_current_page": {
                "type": "boolean",
                "description": (
                    "触发时是否将当前书页 OCR 内容以文本消息发到飞书，默认 false。"
                    "用于'X分钟后发送我现在看书的内容'这类需求。"
                )
            },
        },
        "required": ["minutes"],
    }
}

GENERATE_READING_CARD_TOOL = {
    "name": "generate_reading_card",
    "description": (
        "从当前书页内容生成阅读卡片（金句/知识点/摘要）并推送到飞书。"
        "用户想将书页精华整理成卡片或分享到飞书时调用；内容留空则自动使用当前书页 OCR。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "card_type": {
                "type": "string",
                "description": "卡片类型: quote（金句）/ knowledge（知识点）/ summary（摘要）",
            },
            "content": {"type": "string", "description": "卡片内容（留空则使用当前书页 OCR 内容生成）"},
            "book_title": {"type": "string", "description": "来源书名"},
        },
        "required": ["card_type"],
    }
}

FEISHU_SEND_MESSAGE_TOOL = {
    "name": "feishu_send_message",
    "description": (
        "发送任意文本消息到飞书。用户想把内容（问候、提醒、总结等）推送到飞书时调用。"
        "若要发送书页卡片，请用 generate_reading_card；若要发送阅读卡片，也优先用该工具。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "要发送的消息内容"},
        },
        "required": ["message"],
    }
}

WEREAD_SHELF_TOOL = {
    "name": "weread_shelf",
    "description": (
        "查看或刷新微信读书书架（书目列表和阅读进度概览）。"
        "若书架缓存为空，主动传 refresh=true 实时拉取，不要要求用户手动说刷新。"
        "查笔记请用 weread_notebook；查某书进度请用 weread_progress。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "refresh": {
                "type": "boolean",
                "description": "是否实时刷新（默认 false 用本地缓存）"
            }
        },
        "required": [],
    }
}

WEREAD_NOTEBOOK_TOOL = {
    "name": "weread_notebook",
    "description": (
        "列出微信读书中有笔记的书单（含划线数和笔记数）。"
        "用户问及任何微信读书笔记/划线/想法时，无论是否提到书名，都应首先调用此工具获取全局视图。"
        "返回后可进一步调用 weread_get_notes 查看某本书详情。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {},
        "required": [],
    }
}

WEREAD_GET_NOTES_TOOL = {
    "name": "weread_get_notes",
    "description": (
        "获取某本书的全部微信读书笔记，分三类：划线、想法（附在划线上的评论）、点评（独立书评）。"
        "调用前自动同步最新数据，无需用户手动触发同步。"
        "若用户未明确书名，先调用 weread_notebook 获取书单后自行推断，不要反复询问用户。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "book_title": {
                "type": "string",
                "description": "书名（模糊匹配），必填"
            },
            "limit": {
                "type": "integer",
                "description": "每类笔记的最大返回条数，默认 20"
            }
        },
        "required": ["book_title"],
    }
}

WEREAD_PROGRESS_TOOL = {
    "name": "weread_progress",
    "description": (
        "实时查询微信读书某本书的阅读进度（百分比和阅读时长）。"
        "若书名不明确，先调用 reading_progress_query 或 weread_shelf 推断后再调用本工具。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "book_title": {
                "type": "string",
                "description": "书名（模糊匹配），必填"
            }
        },
        "required": ["book_title"],
    }
}

WEREAD_MERGE_NOTES_TOOL = {
    "name": "weread_merge_notes",
    "description": (
        "将某本书的微信读书划线/想法与本地笔记合并，生成综合摘要，可选推送飞书。"
        "用户想对一本书做跨平台笔记整合时调用。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "book_title": {
                "type": "string",
                "description": "书名（模糊匹配），必填"
            },
            "push_to_feishu": {
                "type": "boolean",
                "description": "是否推送摘要到飞书（默认 false）"
            }
        },
        "required": ["book_title"],
    }
}

ALL_TOOLS = [
    READING_NOTE_TOOL,
    READING_HISTORY_TOOL,
    READING_NOTES_TOOL,
    BOOKMARK_CREATE_TOOL,
    BOOKMARK_LIST_TOOL,
    READING_PROGRESS_UPDATE_TOOL,
    READING_PROGRESS_QUERY_TOOL,
    READING_LIST_MANAGE_TOOL,
    READING_STATS_TOOL,
    SET_TIMER_TOOL,
    GENERATE_READING_CARD_TOOL,
    FEISHU_SEND_MESSAGE_TOOL,
    WEREAD_SHELF_TOOL,
    WEREAD_NOTEBOOK_TOOL,
    WEREAD_GET_NOTES_TOOL,
    WEREAD_PROGRESS_TOOL,
    WEREAD_MERGE_NOTES_TOOL,
]


class ToolRegistry:
    """
    工具注册表
    """
    
    def __init__(self):
        self.tools = {tool["name"]: tool for tool in ALL_TOOLS}
    
    def get_tools(self) -> List[Dict]:
        """获取所有工具定义"""
        return list(self.tools.values())
    
    def get_tool(self, name: str) -> Optional[Dict]:
        """获取单个工具定义"""
        return self.tools.get(name)


class ToolExecutor:
    """
    工具执行器

    执行 AI 调用的工具，并与系统各模块交互
    """

    def __init__(self, session_manager, scanner, memory, llm=None, timer_manager=None,
                 feishu_pusher=None, feishu_chat_id: str = "",
                 weread_client=None, weread_storage=None):
        self.session_manager = session_manager
        self.scanner = scanner
        self.memory = memory
        self.llm = llm                      # 用于 generate_reading_card
        self.timer_manager = timer_manager
        self.feishu_pusher = feishu_pusher
        self.feishu_chat_id = feishu_chat_id
        self.weread_client = weread_client
        self.weread_storage = weread_storage

    async def execute(self, tool_name: str, tool_input: Dict) -> Dict[str, Any]:
        """执行工具"""
        logger.info(f"执行工具: {tool_name}, 参数: {tool_input}")

        try:
            dispatch = {
                "reading_note": self._exec_reading_note,
                "reading_history": self._exec_reading_history,
                "reading_notes": self._exec_reading_notes,
                "bookmark_create": self._exec_bookmark_create,
                "bookmark_list": self._exec_bookmark_list,
                "reading_progress_update": self._exec_reading_progress_update,
                "reading_progress_query": self._exec_reading_progress_query,
                "reading_list_manage": self._exec_reading_list_manage,
                "reading_stats": self._exec_reading_stats,
                "set_timer": self._exec_set_timer,
                "generate_reading_card": self._exec_generate_reading_card,
                "feishu_send_message": self._exec_feishu_send_message,
                "weread_shelf": self._exec_weread_shelf,
                "weread_notebook": self._exec_weread_notebook,
                "weread_get_notes": self._exec_weread_get_notes,
                "weread_progress": self._exec_weread_progress,
                "weread_merge_notes": self._exec_weread_merge_notes,
            }
            handler = dispatch.get(tool_name)
            if handler:
                return await handler(tool_input)
            return {"success": False, "error": f"未知工具: {tool_name}"}

        except Exception as e:
            logger.error(f"工具执行失败: {e}")
            return {"success": False, "error": str(e)}
    
    async def _exec_reading_note(self, params: Dict) -> Dict:
        """记录笔记"""
        content = params.get("content", "")
        if not content:
            return {
                "success": False,
                "error": "笔记内容不能为空"
            }

        book_name = params.get("book_name", "")
        tags = params.get("tags") or []

        # 获取当前页面上下文（有摄像头时才有值）
        page_context = self.memory.current_page_ocr

        note = await self.session_manager.add_note(
            content=content,
            page_context=page_context,
            book_name=book_name,
            tags=tags,
        )

        book_hint = f"《{note.book_name}》" if note.book_name else ""
        tag_hint = f"，标签：{', '.join(note.tags)}" if note.tags else ""
        # 统计该书的笔记数（而非全局自增 ID）
        book_note_count = await self.session_manager.count_notes_by_book(note.book_name)
        count_scope = f"{book_hint}第 {book_note_count} 条" if note.book_name else f"第 {book_note_count} 条"
        return {
            "success": True,
            "message": f"笔记已记录（{count_scope}）{tag_hint}",
            "note_id": note.id,
            "book_note_count": book_note_count,
            "utc_datetime": note.to_json_dict()["utc_datetime"],
        }
    
    async def _exec_reading_history(self, params: Dict) -> Dict:
        """查询阅读历史"""
        days = params.get("days", 7)
        
        # 获取今日会话
        sessions = await self.session_manager.get_today_sessions()
        summary = await self.session_manager.get_today_summary()
        
        if not sessions:
            return {
                "success": True,
                "message": "今天还没有阅读记录",
                "sessions": [],
                "total_duration": "0 分钟",
                "total_pages": 0
            }
        
        # 构建历史记录
        session_infos = []
        for s in sessions:
            notes = await self.session_manager.get_session_notes(s.id)
            session_infos.append({
                "book": s.book_name or "未命名书籍",
                "duration": s.duration_str,
                "pages": s.total_pages,
                "notes_count": len(notes)
            })
        
        return {
            "success": True,
            "message": f"今天共阅读 {len(sessions)} 个会话，总计 {summary.duration_str}",
            "sessions": session_infos,
            "total_duration": summary.duration_str,
            "total_pages": summary.total_pages,
            "total_notes": summary.total_notes
        }

    async def _exec_reading_notes(self, params: Dict) -> Dict:
        """查询笔记内容列表"""
        days = params.get("days", 7)
        book_filter = params.get("book_name", "").strip()

        notes = await self.session_manager.get_recent_notes(days=days)

        if book_filter:
            notes = [n for n in notes if book_filter in (n.book_name or "")]

        if not notes:
            scope = f"《{book_filter}》" if book_filter else f"最近 {days} 天"
            return {
                "success": True,
                "message": f"{scope}暂无读书笔记",
                "notes": [],
                "total": 0
            }

        note_list = []
        for n in notes:
            note_list.append({
                "id": n.id,
                "datetime": n.created_at_str,
                "book_name": n.book_name or "",
                "tags": n.tags,
                "content": n.content,
            })

        scope = f"《{book_filter}》" if book_filter else f"最近 {days} 天"
        return {
            "success": True,
            "message": f"{scope}共有 {len(note_list)} 条读书笔记",
            "notes": note_list,
            "total": len(note_list)
        }

    async def _exec_bookmark_create(self, params: Dict) -> Dict:
        """创建书签"""
        book_title = params.get("book_title", "").strip()
        if not book_title:
            # 从视觉上下文中尝试获取书名
            book_title = self.memory.current_book_context.get("book_title", "")
        if not book_title:
            return {"success": False, "error": "无法识别书名，请指定书名"}

        page_num = params.get("page_num", 0)
        # 若没有传 page_num，尝试从视觉上下文获取
        if not page_num:
            page_num = self.memory.current_book_context.get("current_page_num", 0) or 0

        note = params.get("note", "")
        page_ocr_excerpt = self.memory.current_page_ocr[:200] if self.memory.current_page_ocr else ""

        bookmark = await self.session_manager.create_bookmark(
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

    async def _exec_bookmark_list(self, params: Dict) -> Dict:
        """查询书签列表"""
        book_title = params.get("book_title", "").strip()
        bookmarks = await self.session_manager.list_bookmarks(book_title=book_title, limit=10)

        if not bookmarks:
            scope = f"《{book_title}》" if book_title else "所有书籍"
            return {"success": True, "message": f"{scope}暂无书签", "bookmarks": [], "total": 0}

        bm_list = []
        for bm in bookmarks:
            bm_list.append({
                "id": bm.id,
                "book_title": bm.book_title,
                "page_num": bm.page_num,
                "note": bm.note,
                "excerpt": bm.page_ocr_excerpt[:80] + "..." if len(bm.page_ocr_excerpt) > 80 else bm.page_ocr_excerpt,
                "created_at": bm.created_at_str,
            })

        scope = f"《{book_title}》" if book_title else "全部"
        return {
            "success": True,
            "message": f"{scope}共有 {len(bm_list)} 个书签",
            "bookmarks": bm_list,
            "total": len(bm_list),
        }

    async def _exec_reading_progress_update(self, params: Dict) -> Dict:
        """更新阅读进度"""
        book_title = params.get("book_title", "").strip()
        if not book_title:
            return {"success": False, "error": "书名不能为空"}

        page_num = params.get("page_num", 0)
        status = params.get("status", "")

        progress = await self.session_manager.upsert_book_progress(
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

    async def _exec_reading_progress_query(self, params: Dict) -> Dict:
        """查询阅读进度"""
        book_title = params.get("book_title", "").strip()

        if book_title:
            progress = await self.session_manager.get_book_progress(book_title)
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
            all_progress = await self.session_manager.list_book_progress(status="reading")
            if not all_progress:
                return {"success": True, "message": "目前没有在读书籍", "books": []}
            books = [
                {"book_title": p.book_title, "last_page_num": p.last_page_num,
                 "total_pages_read": p.total_pages_read}
                for p in all_progress
            ]
            return {"success": True, "message": f"正在阅读 {len(books)} 本书", "books": books}

    async def _exec_reading_list_manage(self, params: Dict) -> Dict:
        """管理书单"""
        action = params.get("action", "").strip()
        title = params.get("book_title", "").strip()
        author = params.get("author", "")
        notes = params.get("notes", "")
        filter_status = params.get("filter_status", "")

        # 将 list 的 filter_status 传给 status
        if action == "list":
            result = await self.session_manager.manage_reading_list(
                action="list", status=filter_status
            )
            if not result["success"]:
                return result
            items = result["items"]
            if not items:
                return {"success": True, "message": "书单为空", "items": [], "total": 0}
            item_list = [
                {"title": i["title"], "author": i["author"],
                 "status": {"want": "想读", "reading": "在读", "done": "已读"}.get(i["status"], i["status"])}
                for i in items
            ]
            return {"success": True, "message": f"书单共 {len(item_list)} 本", "items": item_list, "total": len(item_list)}

        return await self.session_manager.manage_reading_list(
            action=action, title=title, author=author, notes=notes
        )

    async def _exec_reading_stats(self, params: Dict) -> Dict:
        """阅读统计"""
        period = params.get("period", "today")
        book_title = params.get("book_title", "")
        stats = await self.session_manager.get_reading_stats(period=period, book_title=book_title)

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

    async def _exec_set_timer(self, params: Dict) -> Dict:
        """设定定时提醒"""
        minutes = params.get("minutes", 0)
        if not minutes or minutes <= 0:
            return {"success": False, "error": "请指定正确的分钟数"}

        message = params.get("message", "")
        feishu_push = params.get("feishu_push", False)

        if not self.timer_manager:
            return {"success": False, "error": "定时器模块未初始化"}

        # 构建"触发时发送书页内容"的闭包，捕获触发时刻最新 OCR 内容
        on_fire = None
        if params.get("send_current_page"):
            _pusher = self.feishu_pusher
            _chat_id = self.feishu_chat_id
            _memory = self.memory
            if not _pusher or not _chat_id:
                return {"success": False, "error": "send_current_page 需要飞书配置，请先确认飞书已启用且 chat_id 有效"}

            async def _send_page_content():
                content = _memory.current_page_ocr
                if not content:
                    logger.warning("⏰ 定时器触发：书页 OCR 内容为空，跳过发送")
                    return
                await _pusher.push_text(_chat_id, content)
                logger.info("⏰ 书页内容已发送到飞书")

            on_fire = _send_page_content
            if not message:
                message = "书页内容已推送到飞书"

        timer_id = await self.timer_manager.set_timer(
            minutes=minutes, message=message, feishu_push=feishu_push, on_fire=on_fire
        )

        desc = []
        if feishu_push:
            desc.append("飞书提醒卡")
        if on_fire:
            desc.append("发送当前书页内容到飞书")
        action_hint = f"（{', '.join(desc)}）" if desc else ""

        return {
            "success": True,
            "message": f"已设定 {minutes} 分钟后触发{action_hint}",
            "timer_id": timer_id,
            "minutes": minutes,
        }

    async def _exec_generate_reading_card(self, params: Dict) -> Dict:
        """生成阅读卡片并推送飞书"""
        card_type = params.get("card_type", "quote")
        content = params.get("content", "").strip()
        book_title = params.get("book_title", "").strip()

        # 没有指定内容时，使用当前书页 OCR
        if not content:
            content = self.memory.current_page_ocr[:1000]
        if not content:
            return {"success": False, "error": "没有可用的内容生成卡片，请先拍摄书页或指定内容"}
        if not book_title:
            book_title = self.memory.current_book_context.get("book_title", "")

        # 用 AI 生成卡片内容
        type_map = {"quote": "金句", "knowledge": "知识点", "summary": "摘要"}
        type_label = type_map.get(card_type, card_type)

        card_content = content
        if self.llm:
            prompt = (
                f"请从以下书页内容中提炼一张「{type_label}卡片」，"
                f"用简洁、有力的语言表达核心内容，100字以内。"
                f"直接输出卡片内容，不要额外说明。\n\n书页内容：\n{content[:800]}"
            )
            try:
                resp = await self.llm.chat(user_message=prompt, max_tokens=200)
                if resp.text:
                    card_content = resp.text.strip()
            except Exception as e:
                logger.error(f"生成卡片内容失败: {e}")

        # 推送飞书
        pushed = False
        if self.feishu_pusher and self.feishu_chat_id:
            try:
                await self.feishu_pusher.push_reading_card(
                    self.feishu_chat_id, card_type, card_content, book_title
                )
                pushed = True
            except Exception as e:
                logger.error(f"飞书推送失败: {e}")

        return {
            "success": True,
            "message": f"已生成{type_label}卡片" + ("并推送到飞书" if pushed else ""),
            "card_type": card_type,
            "card_content": card_content,
            "book_title": book_title,
            "feishu_pushed": pushed,
        }

    async def _exec_feishu_send_message(self, params: Dict) -> Dict:
        """发送文本消息到飞书"""
        message = params.get("message", "").strip()
        if not message:
            return {"success": False, "error": "消息内容不能为空"}

        if not self.feishu_pusher or not self.feishu_chat_id:
            return {"success": False, "error": "飞书未配置或 chat_id 为空"}

        try:
            await self.feishu_pusher.push_text(self.feishu_chat_id, message)
            return {"success": True, "message": "消息已发送到飞书"}
        except Exception as e:
            logger.error(f"飞书发送消息失败: {e}")
            return {"success": False, "error": str(e)}

    # ==================== 微信读书工具 ====================

    def _check_weread(self) -> Optional[Dict]:
        """检查微信读书是否可用，不可用时返回友好提示 dict"""
        if not self.weread_client or not self.weread_storage:
            return {
                "success": False,
                "error": "微信读书未配置，请在 config.json 中设置 weread.enabled=true 和 weread.cookie_string"
            }
        return None

    async def _exec_weread_shelf(self, params: Dict) -> Dict:
        """查看/刷新书架"""
        err = self._check_weread()
        if err:
            return err

        refresh = params.get("refresh", False)

        if refresh:
            # 实时拉取
            data = await self.weread_client.get_shelf()
            books = data.get("books", [])
            progress_list = data.get("progress", [])

            if books is None:
                return {"success": False, "error": "微信读书 Cookie 可能已过期，请更新 cookie_string"}

            # 写入本地缓存
            await self.weread_storage.upsert_books(books)
            for prog in progress_list:
                await self.weread_storage.upsert_progress(prog)
        else:
            # 读本地缓存
            books = await self.weread_storage.list_books()

        if not books:
            return {
                "success": True,
                "message": "书架为空，请先说'刷新微信书架'同步数据",
                "books": [],
                "total": 0,
            }

        book_list = [
            {
                "title": b.title,
                "author": b.author,
                "progress": b.progress_str,
                "reading_time": b.reading_time_str,
                "finished": b.finish_reading,
            }
            for b in books
        ]
        return {
            "success": True,
            "message": f"书架共 {len(book_list)} 本书",
            "books": book_list,
            "total": len(book_list),
            "refreshed": refresh,
        }

    async def _exec_weread_notebook(self, params: Dict) -> Dict:
        """列出有笔记的书单"""
        err = self._check_weread()
        if err:
            return err

        raw_books = await self.weread_client.get_notebook_books()
        if raw_books is None:
            return {"success": False, "error": "获取笔记书单失败，Cookie 可能已过期"}

        if not raw_books:
            return {
                "success": True,
                "message": "暂无有笔记的书籍",
                "books": [],
                "total": 0,
            }

        book_list = []
        for item in raw_books:
            # 兼容 {"book": {...}} 和 {"bookInfo": {...}} 两种结构
            book_info = item.get("book") or item.get("bookInfo") or item
            title = book_info.get("title", "") if isinstance(book_info, dict) else ""
            if not title:
                continue
            book_list.append({
                "title": title,
                "highlights": item.get("noteCount", 0),    # 划线数
                "notes": item.get("reviewCount", 0),        # 想法+点评数
            })

        return {
            "success": True,
            "message": f"共 {len(book_list)} 本书有笔记，可用 weread_get_notes 查看某本书的详细内容",
            "books": book_list,
            "total": len(book_list),
        }

    async def _exec_weread_get_notes(self, params: Dict) -> Dict:
        """自动同步并分类展示某本书的全部笔记（划线/想法/点评）"""
        err = self._check_weread()
        if err:
            return err

        book_title = params.get("book_title", "").strip()
        if not book_title:
            return {"success": False, "error": "请指定书名"}

        limit = params.get("limit", 20)

        # 先找本地缓存，没有则尝试刷新书架
        book = await self.weread_storage.find_book_by_title(book_title)
        if not book:
            shelf_data = await self.weread_client.get_shelf()
            if shelf_data:
                await self.weread_storage.upsert_books(shelf_data.get("books", []))
                for prog in shelf_data.get("progress", []):
                    await self.weread_storage.upsert_progress(prog)
            book = await self.weread_storage.find_book_by_title(book_title)
            if not book:
                return {
                    "success": False,
                    "error": f"未找到《{book_title}》，请确认书名是否正确或书是否在书架中"
                }

        # 自动从微信读书拉取最新划线和笔记
        highlights_api = await self.weread_client.get_highlights(book.book_id, book_title=book.title)
        if highlights_api is not None:
            await self.weread_storage.upsert_highlights(highlights_api)

        notes_api = await self.weread_client.get_notes(book.book_id)
        if notes_api:
            for n in notes_api:
                if not n.book_title:
                    n.book_title = book.title
            await self.weread_storage.upsert_notes(notes_api)

        # 从本地读取，分类返回
        highlights = await self.weread_storage.list_highlights(book.book_id, limit=limit)
        thoughts = await self.weread_storage.list_notes(book.book_id, limit=limit, note_type="想法")
        reviews = await self.weread_storage.list_notes(book.book_id, limit=limit, note_type="点评")

        return {
            "success": True,
            "book_title": book.title,
            "message": (
                f"《{book.title}》：划线 {len(highlights)} 条、"
                f"想法 {len(thoughts)} 条、点评 {len(reviews)} 条"
            ),
            "highlights": [
                {"chapter": h.chapter_title, "content": h.content, "time": h.created_at_str}
                for h in highlights
            ],
            "thoughts": [
                {
                    "chapter": n.chapter_title,
                    "abstract": n.abstract,   # 被标注的原文
                    "content": n.content,      # 用户写的想法
                    "time": n.created_at_str,
                }
                for n in thoughts
            ],
            "reviews": [
                {"chapter": n.chapter_title, "content": n.content, "time": n.created_at_str}
                for n in reviews
            ],
            "highlights_count": len(highlights),
            "thoughts_count": len(thoughts),
            "reviews_count": len(reviews),
        }

    async def _exec_weread_progress(self, params: Dict) -> Dict:
        """查询实时阅读进度"""
        err = self._check_weread()
        if err:
            return err

        book_title = params.get("book_title", "").strip()
        if not book_title:
            return {"success": False, "error": "请指定书名"}

        book = await self.weread_storage.find_book_by_title(book_title)
        if not book:
            return {
                "success": False,
                "error": f"本地未找到《{book_title}》，请先说'刷新微信书架'"
            }

        # 实时拉取（进度时效性要求高）
        progress = await self.weread_client.get_progress(book.book_id)
        if not progress:
            return {"success": False, "error": f"获取《{book_title}》进度失败，Cookie 可能已过期"}

        progress.book_title = book.title
        await self.weread_storage.upsert_progress(progress)

        return {
            "success": True,
            "message": (
                f"《{book.title}》已读 {progress.progress_str}，"
                f"阅读时长 {progress.reading_time_str}"
            ),
            "book_title": book.title,
            "progress": progress.progress,
            "progress_str": progress.progress_str,
            "reading_time_str": progress.reading_time_str,
            "chapter_uid": progress.chapter_uid,
        }

    async def _exec_weread_merge_notes(self, params: Dict) -> Dict:
        """合并微信读书划线+想法与本地笔记"""
        err = self._check_weread()
        if err:
            return err

        book_title = params.get("book_title", "").strip()
        if not book_title:
            return {"success": False, "error": "请指定书名"}

        push_to_feishu = params.get("push_to_feishu", False)

        book = await self.weread_storage.find_book_by_title(book_title)
        if not book:
            return {
                "success": False,
                "error": f"本地未找到《{book_title}》，请先同步书架"
            }

        # 获取微信读书划线
        highlights = await self.weread_storage.list_highlights(book.book_id, limit=30)
        # 获取微信读书想法笔记
        wr_notes = await self.weread_storage.list_notes(book.book_id, limit=20)
        # 获取本地笔记
        local_notes = await self.session_manager.get_recent_notes(days=30)
        local_notes = [n for n in local_notes if book_title in (n.book_name or "")]

        # 拼接摘要供 AI 整理
        sections = []
        if highlights:
            hl_text = "\n".join(f"- {h.content}" for h in highlights[:20])
            sections.append(f"【微信读书划线 {len(highlights)} 条】\n{hl_text}")
        if wr_notes:
            thoughts = [n for n in wr_notes if n.note_type == "想法"]
            reviews = [n for n in wr_notes if n.note_type == "点评"]
            if thoughts:
                thought_text = "\n".join(
                    f"- [原文] {n.abstract}\n  [想法] {n.content}" if n.abstract else f"- {n.content}"
                    for n in thoughts[:10]
                )
                sections.append(f"【微信读书想法 {len(thoughts)} 条】\n{thought_text}")
            if reviews:
                review_text = "\n".join(f"- {n.content}" for n in reviews[:10])
                sections.append(f"【微信读书点评 {len(reviews)} 条】\n{review_text}")
        if local_notes:
            local_text = "\n".join(f"- {n.content}" for n in local_notes[:10])
            sections.append(f"【本地笔记 {len(local_notes)} 条】\n{local_text}")

        if not sections:
            return {
                "success": True,
                "message": f"《{book.title}》暂无划线、笔记数据",
                "summary": "",
            }

        combined = "\n\n".join(sections)
        summary = combined  # 默认直接返回合并内容，AI 自行整理

        # 可选推送飞书
        pushed = False
        if push_to_feishu and self.feishu_pusher and self.feishu_chat_id:
            try:
                msg = f"📚《{book.title}》笔记合集\n\n{summary[:2000]}"
                await self.feishu_pusher.push_text(self.feishu_chat_id, msg)
                pushed = True
            except Exception as e:
                logger.error(f"飞书推送失败: {e}")

        return {
            "success": True,
            "message": f"《{book.title}》笔记合并完成" + ("，已推送飞书" if pushed else ""),
            "book_title": book.title,
            "highlights_count": len(highlights),
            "thoughts_count": len([n for n in wr_notes if n.note_type == "想法"]),
            "reviews_count": len([n for n in wr_notes if n.note_type == "点评"]),
            "local_notes_count": len(local_notes),
            "summary": summary,
            "feishu_pushed": pushed,
        }
