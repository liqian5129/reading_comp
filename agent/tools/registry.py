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
        "用户明确要求记录（摘抄、记下来、做笔记）时调用；"
        "用户表达值得记录的个人感悟、联想或新发现时也可主动调用。"
        "【user_comment 必须提取】用户在请求记笔记时，往往同时表达了自己的看法或感受（如「这句话真的很深刻」「感觉和XX一样」「说的太对了」），"
        "必须将这部分提取到 user_comment 字段，不能遗漏。"
        "【多句合并】若用户要求把多个句子/段落「存在一起」「一起记下来」，"
        "必须从 OCR 文本中依次找到这些句子并拼接为一条 content，只调用一次工具，不要拆分成多次调用。"
        "「这句话的后面这句话」「后面那段」等指 OCR 文本中紧跟当前句子之后的内容，需从 OCR 原文中提取。"
        "仅当用户明确要求同时保存当前书页截图（如「把这页图片一起记下来」「附上截图」）时，"
        "save_image 才传 true，否则默认 false。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "笔记内容。可包含多个句子或段落，当用户要求合并保存时在此字段中拼接全部内容"},
            "book_name": {"type": "string", "description": "书名，从对话上下文中识别，用户未提及则留空"},
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "标签列表，由用户指定或从内容中提取关键词，可为空",
            },
            "user_comment": {
                "type": "string",
                "description": "用户对笔记内容的批注或个人想法。从用户话语中提取，不能遗漏。例：用户说「记下这句话，说的真的很深刻」→ user_comment='说的真的很深刻'；「把这段记下来，感觉跟打工人一样」→ user_comment='感觉跟打工人一样'。用户没有表达任何看法时才留空。",
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
    "description": "查询阅读历史记录（时长、翻页数、笔记数、阅读内容摘要）。用户询问某段时间的读书情况时调用。",
    "input_schema": {
        "type": "object",
        "properties": {
            "days": {"type": "integer", "description": "查询最近几天的记录。今天=1，近3天=3，这周=7，这月=30，全部历史=0。默认1"},
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
        "查询阅读统计摘要（翻页数、时长、笔记数、书签数）。"
        "用户询问阅读量或阅读习惯时调用。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "days": {
                "type": "integer",
                "description": "统计最近几天。今天=1，近3天=3，这周=7，这月=30，全部历史=0。默认1",
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

FEISHU_SEND_MESSAGE_TOOL = {
    "name": "feishu_send_message",
    "description": (
        "发送任意文本消息到飞书。用户想把内容（问候、提醒、总结等）推送到飞书时调用。"
        "若要发送图片卡片，请用 generate_quote_image 或 generate_summary_image。"
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
        "仅当用户没有指定具体书名、或想查看「哪些书有笔记/划线」时调用。"
        "若用户已指定书名，直接调用 weread_get_notes，不需要先调用此工具。"
    ),
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

WEREAD_GET_NOTES_TOOL = {
    "name": "weread_get_notes",
    "description": (
        "获取某本书的全部微信读书笔记，分四类：书签（位置标记）、划线、想法（附在划线上的评论）、点评（独立书评）。"
        "用户问微信读书的书签、划线、笔记、想法时，只要提到了书名就直接调用此工具，无需先调用 weread_notebook。"
        "调用前自动同步最新数据，无需用户手动触发同步。"
        "若用户未指定书名，改调 weread_notebook 而非本工具（本工具 book_title 必填）。"
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

GENERATE_QUOTE_IMAGE_TOOL = {
    "name": "generate_quote_image",
    "description": (
        "将金句或笔记渲染为精美的图片卡片（日历风格），可根据内容情景自动选配色。"
        "生成的图片可推送飞书。用户说「做成卡片图」「生成金句图片」「做张好看的图」时调用。"
        "生成真实的图片文件并推送飞书，效果比纯文字卡片更精美。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "金句或笔记内容。留空则使用当前书页 OCR 由 AI 提炼"},
            "book_title": {"type": "string", "description": "书名"},
            "author": {"type": "string", "description": "作者"},
            "mood": {
                "type": "string",
                "description": "情景标签: warm(历史/哲学)/cool(科技/科学)/literary(文学/自然)/art(心理/艺术)/neutral(通用)，留空自动判断",
            },
            "template": {
                "type": "string",
                "description": "强制指定模板: classic/warm/cool/literary/purple，留空按 mood 自动选择",
            },
        },
        "required": [],
    },
}

GENERATE_SUMMARY_IMAGE_TOOL = {
    "name": "generate_summary_image",
    "description": (
        "生成带 AI 插图的阅读摘要卡片。先用 AI 文生图生成与内容意境相关的插图，"
        "再与摘要文字合成为图文卡片。用户说「配张图」「生成摘要卡」「插画风的总结」时调用。"
        "需要即梦 API 配置（config.json jimeng 节）。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "summary_text": {"type": "string", "description": "摘要内容。留空则自动使用最近阅读摘要"},
            "title": {"type": "string", "description": "卡片标题，默认用书名"},
            "days": {"type": "integer", "description": "summary 为空时拉取最近 N 天的摘要，默认 1"},
            "illustration_prompt": {
                "type": "string",
                "description": "自定义插图 prompt（留空由 AI 根据内容自动生成）",
            },
            "mood": {"type": "string", "description": "情景标签"},
        },
        "required": [],
    },
}

STYLIZE_PAGE_TOOL = {
    "name": "stylize_page",
    "description": (
        "将当前书页照片转换为插画风格（水彩/线描/手绘等）。"
        "使用即梦图生图 API 将书页重新渲染为艺术风格图片。"
        "用户说「把这页变成插画」「书页转艺术风」「来个手绘版」时调用。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "style": {
                "type": "string",
                "description": "风格: watercolor(水彩)/sketch(素描)/comic(漫画)/ghibli(吉卜力)/ink(水墨)，默认 watercolor",
            },
            "strength": {
                "type": "number",
                "description": "变化强度 0.3-0.9，越大越偏离原图，默认 0.6",
            },
        },
        "required": [],
    },
}

EXPORT_PPT_TOOL = {
    "name": "export_ppt",
    "description": (
        "将笔记/摘要/金句导出为 PPT 演示文稿。"
        "用户说「生成 PPT」「做个幻灯片」「导出演示文稿」时调用。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "book_title": {"type": "string", "description": "书名（按书名过滤笔记），留空导出全部"},
            "days": {"type": "integer", "description": "导出最近 N 天的笔记，默认 7"},
            "theme": {
                "type": "string",
                "description": "PPT 主题: light(浅色)/warm(暖色)/dark(深色)，默认 light",
            },
            "include_summary": {"type": "boolean", "description": "是否包含 AI 摘要页，默认 true"},
        },
        "required": [],
    },
}

EXPORT_MARKDOWN_TOOL = {
    "name": "export_markdown",
    "description": (
        "将笔记/摘要/金句导出为格式化的 Markdown 文件。"
        "用户说「导出 Markdown」「生成 MD」「导出笔记文件」时调用。"
        "支持三种模板：notes(读书笔记)/daily(每日摘要)/quotes(金句集锦)。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "template": {
                "type": "string",
                "description": "模板类型: notes(读书笔记)/daily(每日摘要)/quotes(金句集锦)，默认 notes",
            },
            "book_title": {"type": "string", "description": "书名过滤，留空导出全部"},
            "days": {"type": "integer", "description": "时间范围（天），默认 7"},
        },
        "required": [],
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
    GENERATE_QUOTE_IMAGE_TOOL,
    GENERATE_SUMMARY_IMAGE_TOOL,
    STYLIZE_PAGE_TOOL,
    EXPORT_PPT_TOOL,
    EXPORT_MARKDOWN_TOOL,
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

    def get_tools_for_names(self, names: Optional[List[str]]) -> List[Dict]:
        """按名称列表返回工具定义；names=None 返回全量"""
        if names is None:
            return list(self.tools.values())
        return [self.tools[n] for n in names if n in self.tools]

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
            "generate_quote_image": self._share.exec_generate_quote_image,
            "generate_summary_image": self._share.exec_generate_summary_image,
            "stylize_page": self._share.exec_stylize_page,
            "export_ppt": self._share.exec_export_ppt,
            "export_markdown": self._share.exec_export_markdown,
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
