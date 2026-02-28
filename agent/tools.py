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

ALL_TOOLS = [
    READING_NOTE_TOOL,
    READING_HISTORY_TOOL,
    READING_NOTES_TOOL,
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
    
    def __init__(self, session_manager, scanner, memory):
        self.session_manager = session_manager
        self.scanner = scanner
        self.memory = memory
        
    async def execute(self, tool_name: str, tool_input: Dict) -> Dict[str, Any]:
        """
        执行工具
        
        Args:
            tool_name: 工具名称
            tool_input: 工具参数
            
        Returns:
            执行结果
        """
        logger.info(f"执行工具: {tool_name}, 参数: {tool_input}")
        
        try:
            if tool_name == "reading_note":
                return await self._exec_reading_note(tool_input)
            elif tool_name == "reading_history":
                return await self._exec_reading_history(tool_input)
            elif tool_name == "reading_notes":
                return await self._exec_reading_notes(tool_input)
            else:
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
