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

READING_START_TOOL = {
    "name": "reading_start",
    "description": "开始一个新的阅读会话。当用户说'开始读书'、'打开书'等时调用。",
    "input_schema": {
        "type": "object",
        "properties": {
            "book_name": {
                "type": "string",
                "description": "书名，如果用户没有提供可以留空"
            }
        },
        "required": []
    }
}

READING_SNAP_TOOL = {
    "name": "reading_snap",
    "description": "拍摄当前书页，进行 OCR 识别。当用户说'看看这页'、'拍一下'、'这页讲了什么'时调用。",
    "input_schema": {
        "type": "object",
        "properties": {},
        "required": []
    }
}

READING_NOTE_TOOL = {
    "name": "reading_note",
    "description": "记录用户的读书笔记。当用户说'记录一下'、'摘抄这段'、'记个笔记'时调用。",
    "input_schema": {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "笔记内容"
            }
        },
        "required": ["content"]
    }
}

READING_STOP_TOOL = {
    "name": "reading_stop",
    "description": "结束当前阅读会话。当用户说'读完了'、'结束阅读'、'关闭书'时调用。",
    "input_schema": {
        "type": "object",
        "properties": {},
        "required": []
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

ALL_TOOLS = [
    READING_START_TOOL,
    READING_SNAP_TOOL,
    READING_NOTE_TOOL,
    READING_STOP_TOOL,
    READING_HISTORY_TOOL,
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
            if tool_name == "reading_start":
                return await self._exec_reading_start(tool_input)
            elif tool_name == "reading_snap":
                return await self._exec_reading_snap(tool_input)
            elif tool_name == "reading_note":
                return await self._exec_reading_note(tool_input)
            elif tool_name == "reading_stop":
                return await self._exec_reading_stop(tool_input)
            elif tool_name == "reading_history":
                return await self._exec_reading_history(tool_input)
            else:
                return {"success": False, "error": f"未知工具: {tool_name}"}
                
        except Exception as e:
            logger.error(f"工具执行失败: {e}")
            return {"success": False, "error": str(e)}
    
    async def _exec_reading_start(self, params: Dict) -> Dict:
        """开始阅读会话"""
        book_name = params.get("book_name", "")
        
        if self.session_manager.is_active():
            return {
                "success": False,
                "error": "已经有进行中的阅读会话，请先结束当前会话"
            }
        
        session = await self.session_manager.start_session(book_name=book_name)
        
        # 启动自动扫描
        await self.scanner.start(session.id)
        
        return {
            "success": True,
            "message": f"已开始阅读{f'《{book_name}》' if book_name else ''}，我会自动记录你的阅读进度",
            "session_id": session.id
        }
    
    async def _exec_reading_snap(self, params: Dict) -> Dict:
        """拍摄书页"""
        if not self.session_manager.is_active():
            return {
                "success": False,
                "error": "没有进行中的阅读会话，请先说'开始读书'"
            }
        
        # 触发一次手动扫描
        result = await self.scanner.manual_scan()
        
        if result:
            image_path, ocr_text, fingerprint = result
            
            # 更新记忆
            self.memory.set_page_context(ocr_text, image_path)
            
            # 截断 OCR 文本用于返回
            preview = ocr_text[:200] + "..." if len(ocr_text) > 200 else ocr_text
            
            return {
                "success": True,
                "message": f"已拍摄当前页面，识别到 {len(ocr_text)} 字",
                "ocr_preview": preview,
                "image_path": image_path
            }
        else:
            return {
                "success": False,
                "error": "拍摄失败，请检查摄像头"
            }
    
    async def _exec_reading_note(self, params: Dict) -> Dict:
        """记录笔记"""
        if not self.session_manager.is_active():
            return {
                "success": False,
                "error": "没有进行中的阅读会话，无法记录笔记"
            }
        
        content = params.get("content", "")
        if not content:
            return {
                "success": False,
                "error": "笔记内容不能为空"
            }
        
        # 获取当前页面上下文
        page_context = self.memory.current_page_ocr
        
        note = await self.session_manager.add_note(content, page_context)
        
        return {
            "success": True,
            "message": f"笔记已记录（第 {note.id} 条）",
            "note_id": note.id
        }
    
    async def _exec_reading_stop(self, params: Dict) -> Dict:
        """结束阅读会话"""
        if not self.session_manager.is_active():
            return {
                "success": False,
                "error": "没有进行中的阅读会话"
            }
        
        # 停止扫描
        await self.scanner.stop()
        
        # 结束会话
        session = await self.session_manager.end_session()
        
        # 清空页面上下文
        self.memory.clear_page_context()
        
        if session:
            return {
                "success": True,
                "message": f"阅读会话已结束，共阅读 {session.duration_str}，{session.total_pages} 页",
                "session_id": session.id,
                "duration": session.duration_str,
                "pages": session.total_pages,
                "snapshots": session.total_snapshots
            }
        else:
            return {
                "success": False,
                "error": "结束会话失败"
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
