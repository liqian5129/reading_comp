"""
主动页面预分析器
翻页后 fire-and-forget，在用户开口前完成 LLM 分析并写入 memory.proactive_page_hint
"""
import asyncio
import logging
from typing import Optional, Dict

logger = logging.getLogger(__name__)

_ANALYZE_PROMPT = """请对以下书页内容做简短预分析（100字以内），包含：
1. 本页核心概念（1-2个关键词）
2. 用户最可能提问的方向
3. 一个延伸思考点

直接输出分析结果，无需标题或序号。

书页内容：
{ocr_text}"""


class ProactivePageAnalyzer:
    """
    翻页预分析器：在 _on_snapshot 回调中 fire-and-forget，不阻塞主流程。
    分析结果写入 memory.proactive_page_hint，供 DynamicContextBuilder 注入。
    """

    def __init__(self, llm, memory):
        self.llm = llm
        self.memory = memory
        self._current_task: Optional[asyncio.Task] = None

    def on_page_changed(self, page_ocr: str, book_context: Dict) -> None:
        """
        翻页时调用（fire-and-forget）。
        取消上一页未完成的分析任务，重置 hint，启动新任务。
        """
        # 重置上一页 hint
        self.memory.proactive_page_hint = None

        # 取消未完成任务
        if self._current_task and not self._current_task.done():
            self._current_task.cancel()

        # 内容太短不值得分析
        if len(page_ocr.strip()) < 50:
            return

        self._current_task = asyncio.create_task(
            self._analyze(page_ocr, book_context)
        )

    async def _analyze(self, page_ocr: str, book_context: Dict) -> None:
        """后台 LLM 分析，8s 超时，任何异常静默降级"""
        try:
            prompt = _ANALYZE_PROMPT.format(ocr_text=page_ocr[:1500])
            resp = await asyncio.wait_for(
                self.llm.chat(user_message=prompt, max_tokens=150),
                timeout=8.0,
            )
            if resp and resp.text:
                self.memory.proactive_page_hint = resp.text.strip()
                book_title = book_context.get("book_title", "")
                logger.info(
                    f"页面预分析完成（{'《' + book_title + '》' if book_title else '未知书籍'}）: "
                    f"{self.memory.proactive_page_hint[:60]}…"
                )
        except asyncio.CancelledError:
            pass  # 翻页时被正常取消
        except asyncio.TimeoutError:
            logger.debug("页面预分析超时（8s），已降级")
        except Exception as e:
            logger.debug(f"页面预分析失败（已降级）: {e}")
