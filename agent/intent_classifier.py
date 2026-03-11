"""
意图分类器 - 纯规则匹配，零延迟
根据用户输入文本判断意图，用于动态选择注入哪些上下文节
"""
import re
from enum import Enum
from dataclasses import dataclass
from typing import List, Tuple


class Intent(str, Enum):
    PAGE_CONTENT = "page_content"   # 解释/什么意思/这段/翻译
    NOTE_TAKING  = "note_taking"    # 记下/摘抄/有感想
    NOTE_RECALL  = "note_recall"    # 找笔记/之前记过
    PROGRESS     = "progress"       # 读到哪/更新进度/第X页
    WEREAD       = "weread"         # 微信读书/书架/划线
    TIMER        = "timer"          # 提醒/分钟后/休息
    SHARING      = "sharing"        # 卡片/金句/飞书/推送
    GENERAL_CHAT = "general_chat"   # 其他（全量上下文）


@dataclass
class IntentConfig:
    needs_page_context: bool   # 是否注入 OCR 书页内容
    needs_memory: bool         # 是否注入长期记忆摘要
    needs_prefetch: bool       # 是否注入 prefetch 向量记忆
    role_hint: str             # 注入 role 附加提示


# 每个意图的上下文需求配置
INTENT_CONFIGS = {
    Intent.PAGE_CONTENT: IntentConfig(
        needs_page_context=True, needs_memory=False, needs_prefetch=False,
        role_hint="请直接基于当前书页内容回答，无需调用工具。"
    ),
    Intent.NOTE_TAKING: IntentConfig(
        needs_page_context=True, needs_memory=True, needs_prefetch=False,
        role_hint="请调用 saving_note 工具记录笔记。记录后判断：用户说「帮我记/记下来/把XX记下来」= 指令式，只一句确认（如「记下了」），不展开讨论不追问；用户同时表达了感受或观点（含「我觉得/感觉/其实/真的很」）= 分享感受，确认后2句自然回应。"
    ),
    Intent.NOTE_RECALL: IntentConfig(
        needs_page_context=False, needs_memory=True, needs_prefetch=True,
        role_hint="请优先使用 note_search 工具进行语义检索，再回答。"
    ),
    Intent.PROGRESS: IntentConfig(
        needs_page_context=False, needs_memory=False, needs_prefetch=False,
        role_hint="请调用 reading_progress_update 或 reading_progress_query 工具。"
    ),
    Intent.WEREAD: IntentConfig(
        needs_page_context=False, needs_memory=False, needs_prefetch=False,
        role_hint="请使用 weread_* 系列工具。工具返回结果后只回答用户问的那一类数据（问书签只说书签，问划线只说划线），不把其他类型一并报出。数据直接列出，不加评论、不引发讨论、不追问。"
    ),
    Intent.TIMER: IntentConfig(
        needs_page_context=False, needs_memory=False, needs_prefetch=False,
        role_hint="请直接调用 set_timer 工具，不要询问确认。"
    ),
    Intent.SHARING: IntentConfig(
        needs_page_context=True, needs_memory=True, needs_prefetch=False,
        role_hint="请使用 generate_quote_image、generate_summary_image 或 feishu_send_message 工具。"
    ),
    Intent.GENERAL_CHAT: IntentConfig(
        needs_page_context=True, needs_memory=True, needs_prefetch=True,
        role_hint="若用户表达了值得记录的个人感悟、联想或新发现，可主动调用 saving_note 保存，无需等用户明确要求。"
    ),
}


# 意图匹配规则：(意图, 关键词列表)，按顺序优先匹配
_INTENT_RULES: List[Tuple[Intent, List[str]]] = [
    (Intent.TIMER, [
        r"提醒", r"分钟后", r"休息", r"定时", r"\d+分钟",
    ]),
    (Intent.WEREAD, [
        r"微信读书", r"书架", r"划线", r"笔记.*同步", r"同步.*笔记",
        r"weread", r"热门划线", r"合并笔记",
    ]),
    (Intent.SHARING, [
        r"卡片", r"金句", r"飞书", r"推送", r"发.*给", r"分享",
    ]),
    (Intent.NOTE_RECALL, [
        r"找.*笔记", r"之前.*记", r"记过", r"记录过", r"搜索笔记",
        r"有没有.*笔记", r"查.*笔记", r"笔记搜索",
    ]),
    (Intent.NOTE_TAKING, [
        r"记下", r"摘抄", r"记一下", r"保存.*笔记",
        r"记笔记", r"记录.*想法", r"做笔记",
    ]),
    (Intent.PROGRESS, [
        r"读到哪", r"进度", r"第.{1,4}页", r"更新.*页", r"读完了",
        r"读了多少", r"页码", r"书单", r"书签",
    ]),
    (Intent.PAGE_CONTENT, [
        r"什么意思", r"解释", r"翻译", r"这段", r"这里", r"这句",
        r"讲什么", r"说什么", r"总结", r"概括", r"分析", r"帮我理解",
    ]),
]


class IntentClassifier:
    """
    规则匹配意图分类器，零延迟。
    未命中任何规则时 fallback 到 GENERAL_CHAT（注入全量上下文，行为与改造前完全相同）。
    """

    @staticmethod
    def classify(text: str) -> Intent:
        if not text:
            return Intent.GENERAL_CHAT

        text_lower = text.lower()
        for intent, patterns in _INTENT_RULES:
            for pattern in patterns:
                if re.search(pattern, text_lower):
                    return intent

        return Intent.GENERAL_CHAT

    @staticmethod
    def get_config(intent: Intent) -> IntentConfig:
        return INTENT_CONFIGS[intent]
