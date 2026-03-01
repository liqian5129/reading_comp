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
        "记录用户的读书笔记。当用户说'记录一下'、'摘抄这段'、'记个笔记'时调用。"
        "book_name 从对话中识别书名（如用户未提及则留空）。"
        "tags 由用户指定或从对话中提取关键词作为标签。"
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
    "description": "查询用户的阅读历史记录。当用户问'今天读了什么'、'最近读了什么书'时调用。",
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
        "查询用户过往的读书笔记内容，返回完整笔记列表供 AI 查看和整理。"
        "当用户说'看看我的笔记'、'整理一下笔记'、'我之前记了什么'、'最近的读书笔记'时调用。"
        "返回每条笔记的时间、书名、标签和完整内容。"
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
        "创建书签，标记当前阅读位置。当用户说'记个书签'、'标记这里'、'记下这一页'时调用。"
        "自动获取当前页 OCR 摘录；page_num 由视觉识别或用户指定。"
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
    "description": "查询书签列表。当用户说'我读到哪了'、'看看书签'、'列出我的书签'时调用。",
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
        "更新阅读进度或状态。当用户说'告诉你我读到第X页了'、'这本书读完了'、"
        "'我暂停读这本书'时调用。"
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
        "查询某本书的阅读进度。当用户说'我《XX》读到哪里了'、'进度怎么样'、"
        "'我最近在读什么书'时调用。"
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
        "管理书单（想读/在读/已读列表）。当用户说'加入书单'、'我想读XX'、"
        "'书单里有什么'、'标记XX已读'、'从书单删除XX'时调用。"
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
        "查询阅读统计数据（翻页数、时长、笔记数）。当用户说'我这周读了多少'、"
        "'今天翻了几页'、'我最近的阅读情况'时调用。"
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
        "设定阅读提醒定时器。当用户说'提醒我X分钟后休息'、'设个X分钟提醒'、"
        "'X分钟后提醒我'时调用。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "minutes": {"type": "integer", "description": "多少分钟后提醒"},
            "message": {"type": "string", "description": "提醒内容，留空则使用默认文案"},
            "feishu_push": {"type": "boolean", "description": "是否同步推送飞书，默认 false"},
        },
        "required": ["minutes"],
    }
}

GENERATE_READING_CARD_TOOL = {
    "name": "generate_reading_card",
    "description": (
        "生成阅读卡片（金句/知识点/摘要）并推送到飞书。当用户说'生成一张金句卡'、"
        "'把这段做成卡片'、'发到飞书'时调用。"
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

    def __init__(self, session_manager, scanner, memory, llm=None, timer_manager=None, feishu_pusher=None, feishu_chat_id: str = ""):
        self.session_manager = session_manager
        self.scanner = scanner
        self.memory = memory
        self.llm = llm                      # 用于 generate_reading_card
        self.timer_manager = timer_manager
        self.feishu_pusher = feishu_pusher
        self.feishu_chat_id = feishu_chat_id

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

        timer_id = await self.timer_manager.set_timer(
            minutes=minutes, message=message, feishu_push=feishu_push
        )
        return {
            "success": True,
            "message": f"已设定 {minutes} 分钟后的提醒",
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
