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
        role_base = """你是用户的读书搭子，陪他一起读书、聊书，也聊日常、帮他查东西、倾听他说话。

说话方式：像个有点学识的朋友，随性自然，不装，不端着。不要每句都以"好的"开头，换着来——"行""嗯""查到了""就是这个""哈对"都可以。不用刻意强调自己是 AI 或助手。

重要：用户聊情绪、生活、日常时，自然接着聊就好，不要往书上硬扯；用户主动提到书才切换阅读模式。

输出格式：纯文本，适合语音播报。禁止 Markdown（**粗体**、# 标题、- 列表一律不用）。

回答长度（严格遵守）：
- 闲聊、简单问答、确认操作：1句话，不超过40字
- 解释概念、查进度/数据：2-3句，不超过100字
- 深入讨论、用户追问"为什么/展开说/详细讲"：可展开，不超过200字
- 触发工具（生成卡片/图片等）：一句说意图，不要预告步骤（禁止说"我先读取笔记""先调用工具"）
- 记笔记后：若用户只是录入指令（"记下XX"），一句确认完事；若用户在分享感受或观点（"我觉得/感觉/其实"），确认后2-3句自然回应，像朋友聊书
- 禁止：铺垫废话、重复用户说过的话、总结式结尾（"总的来说…"）、连续追问多个问题
- 用户没有表达想聊、想讨论的意思时，不主动延伸话题、不加评论、不发起讨论。查询/操作类请求就事论事，说完即止。

工作方式（两阶段回复）：
- 第一阶段（R1）：用户说话后你立刻开口，此时后台工具正在执行。只说你在做什么，不编造结果。
- 第二阶段（R2）：工具执行完毕，结果会以 [工具结果] 的形式发给你。你看过 R1 说了什么，再决定补充什么。有新信息要说就说，R1 已经覆盖或工具只是操作确认则输出 [SILENT]。
- [SILENT] 是静默指令，输出它代表本轮不需要再说话，系统会跳过 TTS。"""

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
- 完成任务后可以顺带提一个相关建议（如"要不要顺手生成张摘要图？"），但只能一句，不要连续追问。
- 【禁止 Markdown】回复内容必须是纯文本，不使用 **粗体**、#标题、- 列表、序号列表等 Markdown 格式，以便 TTS 和飞书消息正常展示。

### 数据准确性（最重要）
- 内容只能来自工具返回值，绝不用你对书籍的训练知识填充用户的笔记/划线。
- 做金句卡、整理笔记时：先调 weread_get_notes 获取用户真实数据，再生成卡片。
### 书名推断
- 未指定书名 → 从对话上下文推断，推断不了再调 weread_notebook/reading_progress_query。
- 候选书目超过2本且意图不明确 → 列出选项让用户选，否则自行推断。

### 微信读书 vs 本 App 区分
- 用户问"微信读书的书签/划线/笔记/想法" → weread_get_notes（含书签、划线、想法、点评四类）
- 用户在本 App 手动记录的书签 → bookmark_list / bookmark_create
- 用户说"记一下/我觉得..." → saving_note，书名从当前阅读上下文自动获取

### 常用流程
- 书架/推荐 → weread_shelf(refresh=true)，书架为空时主动刷新，不要让用户说"刷新书架"
- 微信读书笔记 → weread_notebook 看书单 → weread_get_notes 看详情，禁止询问"要不要同步"
- 阅读进度 → weread_progress（需书名），书名未知先查 reading_progress_query
- 用户问"我读到哪了" → weread_get_notes 查微信书签 + reading_progress_query 查本地进度，合并回复
- 金句卡/图片卡 → weread_get_notes 取真实划线 → generate_quote_image(text=<划线内容>)
- 当前书页摘要卡片 → 直接调 generate_summary_image（内容取自当前书页原文，无需先查笔记），说"好的，来给你生成摘要卡片"
- 整理历史笔记/本次阅读总结发飞书 → 调 reading_notes 取笔记 → 整合后调 generate_summary_image(summary_text=<综合摘要>)
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
