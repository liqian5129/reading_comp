"""
动态上下文构建器
根据意图选择性注入 system prompt 各节，节省 token 并减少干扰
"""
import logging
from typing import TYPE_CHECKING

from .intent_classifier import Intent, IntentConfig

if TYPE_CHECKING:
    from .memory import Memory

logger = logging.getLogger(__name__)


class DynamicContextBuilder:
    """
    根据 IntentConfig 按需组装 system prompt。
    GENERAL_CHAT fallback 行为与原始 build_system_prompt 完全相同。
    """

    def build(self, memory: "Memory", intent: Intent, config: IntentConfig) -> str:
        parts = []

        # 1. 角色定义（始终注入）+ 意图附加提示
        role_base = """你是用户的私人陪伴助手，平时陪他读书，也陪他聊天、解答问题、倾听心情。

核心能力：
1. 解释、总结和讨论当前书页内容
2. 回答用户关于书中知识点的问题
3. 记录读书笔记和想法
4. 查询阅读历史和笔记
5. 闲聊、情感支持、推荐书单、回答其他日常问题

重要：当用户聊情绪、生活、八卦等与阅读无关的话题时，自然回应即可，不要硬往读书上引导；只有用户明显想聊书或者提到书的时候才切换到阅读模式。

回答风格：友好自然，简洁明了，适合语音播报（避免过长列举，少用 markdown 格式）。

回答长度原则（严格遵守）：
- 闲聊、简单问答、确认类：1句话，不超过40字
- 一般询问、解释概念、查进度/书签：2-3句话，不超过100字
- 深入讨论、用户追问"为什么/展开说/详细讲"、分析书中观点：可展开，不超过200字
- 工具执行结果（书签/计时器/进度更新等）：只说结果，一句话确认即可
- 记笔记后（reading_note）：先确认记录，再判断用户意图——若用户只是指令式录入（"记下XX"），一句确认结束；若用户在分享感受、联想或观点（有"我觉得/感觉/有点像/其实"等表达），在确认后用2-3句自然呼应，像朋友一起聊书一样
- 生成阅读卡片后（generate_reading_card）：告知用户已发送，口头简述时必须基于工具返回的 card_content 字段，不要自己重新生成摘要（避免两个版本不一致）
- 禁止：不必要的铺垫、重复用户说过的话、总结式结尾（"总的来说…"）"""

        if config.role_hint:
            role_base += f"\n\n{config.role_hint}"
        parts.append(role_base)

        # 2. 长期记忆摘要（按需）
        if config.needs_memory:
            lt_digest = memory.long_term.get_digest_for_prompt()
            if lt_digest:
                parts.append(f"【你对这位用户的了解】\n{lt_digest}")

            # 用户偏好
            if memory.persona.reading_preferences:
                parts.append(f"用户的阅读偏好: {', '.join(memory.persona.reading_preferences)}")
            if memory.persona.favorite_genres:
                parts.append(f"用户喜欢的书籍类型: {', '.join(memory.persona.favorite_genres)}")

        # 3. 相关历史记忆 prefetch cache（按需）
        if config.needs_prefetch and memory._prefetch_cache:
            parts.append(f"【相关历史记忆】\n{memory._prefetch_cache}")

        # 4. 当前书籍视觉上下文（始终注入，有识别结果才加）
        ctx = memory.current_book_context
        if ctx.get("book_title") and ctx.get("confidence", 0) >= 0.7:
            page_info = f"第 {ctx['current_page_num']} 页" if ctx.get("current_page_num") else ""
            parts.append(
                f"【当前正在阅读】《{ctx['book_title']}》{page_info}"
                + (f"（{ctx['content_type']}）" if ctx.get("content_type") else "")
            )

        # 5. 本次阅读内容积累（PAGE_CONTENT / GENERAL_CHAT / SHARING 时注入）
        if intent in (Intent.PAGE_CONTENT, Intent.GENERAL_CHAT, Intent.SHARING):
            digest = getattr(memory, "session_reading_digest", "")
            buffer = getattr(memory, "_new_pages_buffer", [])
            if digest:
                parts.append(f"【本次阅读内容积累】\n{digest}")
            elif buffer:
                # 摘要尚未生成（不足5页），直接注入原始缓冲作为兜底
                raw = "\n".join(buffer[-8:])  # 最近8页，避免超长
                parts.append(f"【本次已读书页记录】\n{raw}")

        # 6. 当前页面 OCR 文本（按需）
        if config.needs_page_context and memory.current_page_ocr:
            page_text = memory.current_page_ocr[:2000]
            truncated = "...(内容已截断)" if len(memory.current_page_ocr) > 2000 else ""
            parts.append(
                f"【当前书页内容（摄像头已自动识别）】\n"
                f"以下是摄像头刚刚拍摄并 OCR 识别的书页文字，你已经看到了这些内容，"
                f"请直接基于它回答用户问题，无需再次拍照：\n\n{page_text}{truncated}"
            )

        # 7. 工具调用策略（始终注入）
        parts.append("""## 工具调用策略

### 基本原则
- 收到复杂请求时，先规划需要哪些工具、按什么顺序调用，连续执行，最后统一回复。
- 不要在工具调用中途询问"要不要继续下一步"。

### 数据准确性（最重要）
- 内容只能来自工具返回值，绝不用你对书籍的训练知识填充用户的笔记/划线。
- 做金句卡、整理笔记时：先调 weread_get_notes 获取用户真实数据，再生成卡片。
### 书名推断
- 未指定书名 → 从对话上下文推断，推断不了再调 weread_notebook/reading_progress_query。
- 候选书目超过2本且意图不明确 → 列出选项让用户选，否则自行推断。

### 微信读书 vs 本 App 区分
- 用户问"微信读书的书签/划线/笔记/想法" → weread_get_notes（含书签、划线、想法、点评四类）
- 用户在本 App 手动记录的书签 → bookmark_list / bookmark_create
- 用户说"记一下/我觉得..." → reading_note，书名从当前阅读上下文自动获取

### 常用流程
- 书架/推荐 → weread_shelf(refresh=true)，书架为空时主动刷新，不要让用户说"刷新书架"
- 微信读书笔记 → weread_notebook 看书单 → weread_get_notes 看详情，禁止询问"要不要同步"
- 阅读进度 → weread_progress（需书名），书名未知先查 reading_progress_query
- 用户问"我读到哪了" → weread_get_notes 查微信书签 + reading_progress_query 查本地进度，合并回复
- 金句卡/图片卡 → weread_get_notes 取真实划线 → generate_quote_image(text=<划线内容>)
- 整理/总结阅读内容发飞书 → 读取【本次阅读内容积累】→ 调 reading_notes 取笔记 → 整合后调 generate_summary_image(summary_text=<综合摘要>)
- 用户要一周/近期阅读总结 → 调 reading_history(days=7) 获取包含历史摘要的记录，再整合生成""")

        prompt = "\n\n".join(parts)

        # 日志：记录注入了哪些节（帮助调试 token 节省效果）
        injected = []
        if config.needs_memory:
            injected.append("长期记忆")
        if config.needs_prefetch and memory._prefetch_cache:
            injected.append("prefetch")
        if config.needs_page_context and memory.current_page_ocr:
            injected.append(f"OCR({len(memory.current_page_ocr)}字)")
        logger.debug(
            f"[DynamicContext] intent={intent.value}, 注入节: {', '.join(injected) or '仅角色+工具策略'}, "
            f"prompt总长={len(prompt)}字"
        )

        return prompt
