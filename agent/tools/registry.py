"""
工具注册表和调度器
包含所有工具 JSON Schema 定义、ToolRegistry 和 ToolDispatcher
"""
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


# ==================== 工具定义 (JSON Schema) ====================

READING_NOTE_TOOL = {
    "name": "reading_note",
    "description": (
        "保存一条读书笔记到本地。书名优先从当前阅读上下文自动获取，无需用户指定。"
        "用户表达记录意图（摘抄、记下来、我觉得、有感想）时调用。"
        "仅当用户明确要求同时保存当前书页截图（如「把这页图片一起记下来」「附上截图」）时，"
        "save_image 才传 true，否则默认 false。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "笔记内容"},
            "book_name": {"type": "string", "description": "书名，从对话上下文中识别，用户未提及则留空"},
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "标签列表，由用户指定或从内容中提取关键词，可为空",
            },
            "save_image": {
                "type": "boolean",
                "description": "是否关联当前书页截图。仅当用户明确要求保存截图时为 true，默认 false。",
            },
        },
        "required": ["content"],
    },
}

READING_HISTORY_TOOL = {
    "name": "reading_history",
    "description": "查询本次及近期的阅读会话记录（时长、翻页数、笔记数）。用户询问今天或近期读书情况时调用。",
    "input_schema": {
        "type": "object",
        "properties": {
            "days": {"type": "integer", "description": "查询最近几天的记录，默认 7 天"},
        },
        "required": [],
    },
}

READING_NOTES_TOOL = {
    "name": "reading_notes",
    "description": (
        "查询本地保存的读书笔记列表（含时间、书名、标签、内容）。"
        "用户想回顾、整理或查看之前记录的笔记时调用；可按书名或时间范围过滤。"
        "仅当用户明确要求发送笔记对应的书页截图（如「把截图发过来」「发图片」）时，"
        "send_images 才传 true，否则默认 false。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "days": {"type": "integer", "description": "查询最近几天的笔记，默认 7 天"},
            "book_name": {"type": "string", "description": "按书名过滤，留空则返回所有书的笔记"},
            "send_images": {
                "type": "boolean",
                "description": "是否同时通过飞书发送笔记关联的书页截图。仅当用户明确要求发截图时为 true，默认 false。",
            },
        },
        "required": [],
    },
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
    },
}

BOOKMARK_LIST_TOOL = {
    "name": "bookmark_list",
    "description": "查询在本 App 内手动保存的本地书签（非微信读书书签）。用户说「记录一下读到这里」或查看本 App 历史书签时调用。若用户问的是微信读书的书签/划线/笔记，应使用 weread_get_notes。",
    "input_schema": {
        "type": "object",
        "properties": {
            "book_title": {"type": "string", "description": "按书名过滤，留空返回所有书的书签"},
        },
        "required": [],
    },
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
    },
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
    },
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
    },
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
    },
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
            "message": {"type": "string", "description": "触发时 TTS 播报的内容（留空则使用默认文案）"},
            "feishu_push": {"type": "boolean", "description": "是否发送飞书提醒卡片（适合纯提醒场景），默认 false"},
            "send_current_page": {
                "type": "boolean",
                "description": (
                    "触发时是否将当前书页 OCR 内容以文本消息发到飞书，默认 false。"
                    "用于'X分钟后发送我现在看书的内容'这类需求。"
                ),
            },
        },
        "required": ["minutes"],
    },
}

GENERATE_READING_CARD_TOOL = {
    "name": "generate_reading_card",
    "description": (
        "生成阅读卡片（金句/知识点/摘要）并推送到飞书。"
        "用户想将微信读书笔记/划线做成卡片时：只传 book_title，不要自己填写 content，"
        "工具会自动从微信读书数据库取用户真实的划线和笔记作为内容。"
        "用户想将当前书页做成卡片时：只传 card_type，内容自动使用摄像头 OCR。"
        "content 参数仅在用户明确口述了具体文字时才填写。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "card_type": {
                "type": "string",
                "description": "卡片类型: quote（金句）/ knowledge（知识点）/ summary（摘要）",
            },
            "content": {"type": "string", "description": "卡片内容。仅在用户口述了具体文字时填写；其余情况留空，工具自动从微信读书或 OCR 取内容，禁止填入你推测或从训练知识生成的文字。"},
            "book_title": {"type": "string", "description": "来源书名，提供后工具自动从微信读书数据库取该书的真实划线和笔记"},
        },
        "required": ["card_type"],
    },
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
    },
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
            "refresh": {"type": "boolean", "description": "是否实时刷新（默认 false 用本地缓存）"},
        },
        "required": [],
    },
}

WEREAD_NOTEBOOK_TOOL = {
    "name": "weread_notebook",
    "description": (
        "列出微信读书中有笔记的书单（含划线数和笔记数）。"
        "用户问及任何微信读书笔记/划线/想法时，无论是否提到书名，都应首先调用此工具获取全局视图。"
        "返回后可进一步调用 weread_get_notes 查看某本书详情。"
    ),
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

WEREAD_GET_NOTES_TOOL = {
    "name": "weread_get_notes",
    "description": (
        "获取某本书的全部微信读书笔记，分四类：书签（位置标记）、划线、想法（附在划线上的评论）、点评（独立书评）。"
        "用户问微信读书的书签、划线、笔记、想法时都应调用此工具。"
        "调用前自动同步最新数据，无需用户手动触发同步。"
        "若用户未明确书名，先调用 weread_notebook 获取书单后自行推断，不要反复询问用户。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "book_title": {"type": "string", "description": "书名（模糊匹配），必填"},
            "limit": {"type": "integer", "description": "每类笔记的最大返回条数，默认 20"},
        },
        "required": ["book_title"],
    },
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
            "book_title": {"type": "string", "description": "书名（模糊匹配），必填"},
        },
        "required": ["book_title"],
    },
}

WEREAD_BEST_HIGHLIGHTS_TOOL = {
    "name": "weread_best_highlights",
    "description": (
        "获取某本书的热门划线（所有读者都标注过的精华句子）。"
        "用于快速了解一本书的核心观点和金句，或在没有个人划线时浏览大家的共识摘录。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "book_title": {"type": "string", "description": "书名（模糊匹配），必填"},
        },
        "required": ["book_title"],
    },
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
            "book_title": {"type": "string", "description": "书名（模糊匹配），必填"},
            "push_to_feishu": {"type": "boolean", "description": "是否推送摘要到飞书（默认 false）"},
        },
        "required": ["book_title"],
    },
}

NOTE_SEARCH_TOOL = {
    "name": "note_search",
    "description": (
        "用语义向量搜索笔记和微信读书划线/想法，找到与指定关键词最相关的历史记录。"
        "用户说「找我写过/划过...的内容」「我之前记录过...」「有没有关于...的笔记」时调用。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词或句子（会用语义相似度检索）"},
            "source": {
                "type": "string",
                "description": "搜索范围: all（全部）/ notes（本地笔记）/ weread（微信读书划线和想法），默认 all",
            },
        },
        "required": ["query"],
    },
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
    WEREAD_BEST_HIGHLIGHTS_TOOL,
    WEREAD_MERGE_NOTES_TOOL,
    NOTE_SEARCH_TOOL,
]


class ToolRegistry:
    """工具注册表"""

    def __init__(self):
        self.tools = {tool["name"]: tool for tool in ALL_TOOLS}

    def get_tools(self) -> List[Dict]:
        """获取所有工具定义"""
        return list(self.tools.values())

    def get_tool(self, name: str) -> Optional[Dict]:
        """获取单个工具定义"""
        return self.tools.get(name)


class ToolDispatcher:
    """
    工具调度器（替代 ToolExecutor）

    接收 deps 命名空间，串行执行工具调用。
    """

    def __init__(self, deps):
        self._deps = deps

        from .note_tools import NoteTools
        from .bookmark_tools import BookmarkTools
        from .progress_tools import ProgressTools
        from .list_tools import ListTools
        from .timer_tools import TimerTools
        from .share_tools import ShareTools
        from .weread_tools import WeReadTools

        self._note = NoteTools(deps)
        self._bookmark = BookmarkTools(deps)
        self._progress = ProgressTools(deps)
        self._list = ListTools(deps)
        self._timer = TimerTools(deps)
        self._share = ShareTools(deps)
        self._weread = WeReadTools(deps)

        self._dispatch = {
            "reading_note": self._note.exec_reading_note,
            "reading_history": self._note.exec_reading_history,
            "reading_notes": self._note.exec_reading_notes,
            "note_search": self._note.exec_note_search,
            "bookmark_create": self._bookmark.exec_bookmark_create,
            "bookmark_list": self._bookmark.exec_bookmark_list,
            "reading_progress_update": self._progress.exec_reading_progress_update,
            "reading_progress_query": self._progress.exec_reading_progress_query,
            "reading_stats": self._progress.exec_reading_stats,
            "reading_list_manage": self._list.exec_reading_list_manage,
            "set_timer": self._timer.exec_set_timer,
            "generate_reading_card": self._share.exec_generate_reading_card,
            "feishu_send_message": self._share.exec_feishu_send_message,
            "weread_shelf": self._weread.exec_weread_shelf,
            "weread_notebook": self._weread.exec_weread_notebook,
            "weread_get_notes": self._weread.exec_weread_get_notes,
            "weread_progress": self._weread.exec_weread_progress,
            "weread_best_highlights": self._weread.exec_weread_best_highlights,
            "weread_merge_notes": self._weread.exec_weread_merge_notes,
        }

    @property
    def feishu_pusher(self):
        return self._deps.feishu_pusher

    @feishu_pusher.setter
    def feishu_pusher(self, value):
        self._deps.feishu_pusher = value

    @property
    def feishu_chat_id(self):
        return self._deps.feishu_chat_id

    @feishu_chat_id.setter
    def feishu_chat_id(self, value):
        self._deps.feishu_chat_id = value

    async def execute(self, tool_name: str, tool_input: Dict) -> Dict[str, Any]:
        """执行工具（串行）"""
        logger.info(f"执行工具: {tool_name}, 参数: {tool_input}")
        try:
            handler = self._dispatch.get(tool_name)
            if handler:
                return await handler(tool_input)
            return {"success": False, "error": f"未知工具: {tool_name}"}
        except Exception as e:
            logger.error(f"工具执行失败: {e}")
            return {"success": False, "error": str(e)}
