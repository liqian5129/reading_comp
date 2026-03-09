"""
Sub Agent - 后台工具执行器

职责：
  - 独立 LLM 链路（不阻塞主对话）
  - LLM Round 1 超时时正则兜底直接提参执行
  - 返回工具执行结果 dict 或 None（LLM 判断不需要工具）
"""
import asyncio
import logging
import re
import time
from typing import Any, Dict, List, Optional

from agent.tool_selector import _regex_select

logger = logging.getLogger(__name__)

# Sub Agent 专属 system prompt：聚焦工具执行，不做对话
_SUB_AGENT_SYSTEM_PROMPT = """你是对话助手的工具查询助手。
对话助手正在跟用户说话，你在后台并行执行工具查询，完成后把结果返回给它。
任务：仅当用户【当前这条消息】明确包含数据查询意图时，调用对应工具。
判断标准：
- 用户明确请求查询（"帮我查"、"看看"、"有哪些"、"读到哪了"等）→ 调用工具
- 用户在问好、聊天、表达情绪、确认存在（"你在吗"、"还在吗"、"好的"、"谢谢"）→ 不调用工具
- 不确定时 → 不调用工具
要求：只输出 tool_use，不做任何对话性回复。历史对话仅用于补充书名等上下文，不作为调用依据。"""

# Sub Agent LLM Round 1 超时阈值（秒）
_LLM_TIMEOUT_S = 20.0

# ── 正则兜底参数提取 ──────────────────────────────────────────────────────────
_NUM = r"(\d+|一|二|三|四|五|六|七|八|九|十|十五|二十|三十|四十|五十|六十)"

# reading_note 触发词（正则兜底时必须有明确触发词，避免把查询语句当笔记内容）
_NOTE_TRIGGER = re.compile(
    r"^(帮我记[一下来]?|记[一下来]?|记录[一下]?|保存[一下]?|做个?笔记|写[一下]?下来)"
)


def _cn_to_int(s: str) -> int:
    mapping = {
        "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
        "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
        "十五": 15, "二十": 20, "三十": 30, "四十": 40,
        "五十": 50, "六十": 60,
    }
    if s.isdigit():
        return int(s)
    return mapping.get(s, 0)


def _extract_book_title(user_text: str, memory=None) -> str:
    """从 user_text 或 memory 中提取书名，返回空字符串表示未找到。"""
    # 优先：《书名》格式
    m = re.search(r"[《〈]([^》〉]+)[》〉]", user_text)
    if m:
        return m.group(1)
    # 次选：memory 中的当前书籍
    if memory:
        ctx = getattr(memory, "current_book_context", {})
        title = ctx.get("book_title", "")
        if title:
            return title
    return ""


def _regex_fallback_params(tool_name: str, user_text: str, memory=None) -> Optional[dict]:
    """
    正则兜底：为已知工具从 user_text 中快速提取参数。
    返回参数 dict；返回 None 表示跳过此工具。
    """
    # ── 无参工具 ──────────────────────────────────────────────────────────────
    no_param_tools = {
        "reading_notes", "bookmark_list", "reading_stats",
        "reading_history", "weread_shelf", "weread_notebook",
        "reading_progress_query",
    }
    if tool_name in no_param_tools:
        return {}

    # ── set_timer ─────────────────────────────────────────────────────────────
    if tool_name == "set_timer":
        m = re.search(_NUM + r"\s*分钟?", user_text)
        if m:
            minutes = _cn_to_int(m.group(1))
            if minutes > 0:
                return {"minutes": minutes, "label": "休息提醒"}
        m = re.search(_NUM + r"\s*小时", user_text)
        if m:
            hours = _cn_to_int(m.group(1))
            if hours > 0:
                return {"minutes": hours * 60, "label": "休息提醒"}
        return None  # 无时间信息，跳过

    # ── bookmark_create ───────────────────────────────────────────────────────
    if tool_name == "bookmark_create":
        return {}

    # ── reading_progress_update ───────────────────────────────────────────────
    if tool_name == "reading_progress_update":
        m = re.search(r"第\s*" + _NUM + r"\s*页", user_text)
        if m:
            return {"page_num": _cn_to_int(m.group(1))}
        return {}

    # ── weread_get_notes ──────────────────────────────────────────────────────
    if tool_name == "weread_get_notes":
        title = _extract_book_title(user_text, memory)
        return {"book_title": title} if title else {}

    # ── weread_progress ───────────────────────────────────────────────────────
    if tool_name == "weread_progress":
        title = _extract_book_title(user_text, memory)
        return {"book_title": title} if title else {}

    # ── weread_best_highlights ────────────────────────────────────────────────
    if tool_name == "weread_best_highlights":
        title = _extract_book_title(user_text, memory)
        return {"book_title": title} if title else {}

    # ── reading_stats ─────────────────────────────────────────────────────────
    if tool_name == "reading_stats":
        if re.search(r"今天|今日", user_text):
            return {"period": "today"}
        if re.search(r"本周|这周|这一周|一周", user_text):
            return {"period": "week"}
        if re.search(r"本月|这个月|一个月", user_text):
            return {"period": "month"}
        return {"period": "today"}

    # ── reading_note ──────────────────────────────────────────────────────────
    # 正则兜底必须有明确触发词才执行，避免把查询语句当笔记内容保存
    if tool_name == "reading_note":
        if not _NOTE_TRIGGER.match(user_text):
            logger.debug(f"[SubAgent/fallback] reading_note: 无触发词，跳过 (text={user_text[:30]})")
            return None  # 没有"帮我记/记下来"等触发词，跳过
        content = _NOTE_TRIGGER.sub("", user_text).strip()
        if content:
            return {"content": content}
        return None

    # ── 其他工具 ─────────────────────────────────────────────────────────────
    return {}


class SubAgent:
    """后台工具执行器，与 Main LLM 并行运行。"""

    async def run(
        self,
        user_text: str,
        tools: List[dict],
        history: List[dict],
        memory,
        llm,
        tool_dispatcher,
    ) -> Optional[Dict[str, Any]]:
        """
        Returns:
            {tool_name: result_dict}，或 None（不需要工具）。
        """
        if not tools:
            logger.info("[SubAgent] tools 为空，跳过")
            return None

        t0 = time.time()
        logger.info(f"[SubAgent] 启动 | user_text='{user_text[:50]}' | tools数={len(tools)}")

        # ── LLM Round 1 ───────────────────────────────────────────────────────
        tool_calls = None
        try:
            tool_calls = await asyncio.wait_for(
                self._llm_select_and_call(user_text, tools, history, llm, t0),
                timeout=_LLM_TIMEOUT_S,
            )
            logger.info(f"[SubAgent] LLM 完成 +{time.time()-t0:.1f}s | tool_calls={[tc['name'] for tc in tool_calls] if tool_calls else '[]'}")
        except asyncio.TimeoutError:
            logger.warning(f"[SubAgent] LLM Round 1 超时（>{_LLM_TIMEOUT_S}s），触发正则兜底")
            tool_calls = None
        except Exception as e:
            logger.warning(f"[SubAgent] LLM Round 1 异常: {e}，触发正则兜底")
            tool_calls = None

        # ── 正则兜底 ──────────────────────────────────────────────────────────
        if tool_calls is None:
            tool_calls = self._regex_fallback(user_text, tools, memory)
            if tool_calls:
                logger.info(f"[SubAgent] 正则兜底工具: {[tc['name'] for tc in tool_calls]}")
            else:
                logger.info("[SubAgent] 正则兜底也无结果")

        if not tool_calls:
            logger.info("[SubAgent] 无工具调用，返回 None")
            return None

        # ── 执行工具 ──────────────────────────────────────────────────────────
        logger.info(f"[SubAgent] 执行工具: {[tc['name'] for tc in tool_calls]} | params={[tc.get('input') for tc in tool_calls]}")
        t_exec = time.time()
        tasks = [
            tool_dispatcher.execute(tc["name"], tc.get("input", {}))
            for tc in tool_calls
        ]
        results_list = await asyncio.gather(*tasks, return_exceptions=True)

        results = {}
        for tc, result in zip(tool_calls, results_list):
            name = tc["name"]
            if isinstance(result, Exception):
                logger.error(f"[SubAgent] 工具 {name} 执行异常: {result}")
                results[name] = {"success": False, "error": str(result)}
            else:
                results[name] = result
                logger.info(f"[SubAgent] 工具 {name} 完成 +{time.time()-t_exec:.1f}s | {str(result)[:120]}")

        logger.info(f"[SubAgent] 全流程耗时 {time.time()-t0:.1f}s")
        return results

    async def _llm_select_and_call(
        self,
        user_text: str,
        tools: List[dict],
        history: List[dict],
        llm,
        t0: float,
    ) -> List[dict]:
        """调用 Sub Agent LLM，收集 tool_use chunk。空 list = LLM 认为不需要工具。"""
        tool_calls_collected = []
        first_token_time = None
        text_chunks = []

        async for chunk in llm.chat_stream(
            user_message=user_text,
            system_prompt=_SUB_AGENT_SYSTEM_PROMPT,
            history=history,
            tools=tools,
        ):
            if chunk.type == "text_delta":
                if first_token_time is None:
                    first_token_time = time.time()
                    logger.info(f"[SubAgent/LLM] 首字节 +{first_token_time-t0:.1f}s（text_delta，非 tool_use）")
                text_chunks.append(chunk.content)
            elif chunk.type == "tool_use":
                if first_token_time is None:
                    first_token_time = time.time()
                    logger.info(f"[SubAgent/LLM] 首字节 +{first_token_time-t0:.1f}s（tool_use）")
                if chunk.tool_calls:
                    tool_calls_collected.extend(chunk.tool_calls)
                    logger.info(f"[SubAgent/LLM] 收到 tool_use: {[tc['name'] for tc in chunk.tool_calls]}")

        if text_chunks:
            text_preview = "".join(text_chunks)[:80]
            logger.warning(f"[SubAgent/LLM] LLM 输出了文本而非 tool_use（前80字）: {text_preview!r}")

        return tool_calls_collected

    def _regex_fallback(
        self,
        user_text: str,
        tools: List[dict],
        memory,
    ) -> List[dict]:
        """正则兜底：复用 tool_selector._regex_select 完整规则。"""
        tool_names = {t["name"] for t in tools}
        matched = _regex_select(user_text)
        for name in matched:
            if name not in tool_names:
                continue
            params = _regex_fallback_params(name, user_text, memory)
            if params is not None:
                logger.info(f"[SubAgent/fallback] 命中: {name}, params={params}")
                return [{"name": name, "input": params}]
        return []
