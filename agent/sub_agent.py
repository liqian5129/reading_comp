"""
Sub Agent - 后台工具执行器

职责：
  - 独立 LLM 链路（不阻塞主对话）
  - 两阶段 LLM：Stage A 轻量目录选择 → Stage B 参数生成
  - LLM 超时/异常时返回 {"__timeout__": True}，由 main.py 通知用户
  - 返回工具执行结果 dict 或 None（LLM 判断不需要工具）
"""
import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from agent.tool_selector import TOOL_CATALOG

logger = logging.getLogger(__name__)

# ── Stage A：轻量工具目录选择（不含 schema，只判断要调用哪些工具） ────────────
_CATALOG_TEXT = "\n".join(f"- {k}: {v}" for k, v in TOOL_CATALOG.items())

_STAGE_A_SYSTEM = f"""你是对话助手的后台工具执行器。
对话助手正在跟用户说话，你在后台并行判断是否需要调用工具。

任务：仅当用户【当前这条消息】明确包含数据查询或操作意图时，返回需要调用的工具名。
判断标准：
- 用户明确请求查询/操作（"帮我查"、"看看"、"有哪些"、"记一下"、"读到哪了"等）→ 返回工具名
- 用户纠正上一轮操作后紧接新请求（"不对是X分钟"、"错了帮我改成"、"不对不对是..."）→ 识别为新工具调用，返回对应工具名
- 用户聊天、问好、表达情绪、确认存在（"你在吗"、"好的"、"谢谢"）→ 空列表
- 用户让 AI 解释/分析内容（无需查数据库）→ 空列表
- 不确定时 → 空列表

可用工具：
{_CATALOG_TEXT}

只输出 JSON，不要任何其他文字：{{"tools": ["tool_name1"]}}"""

# ── Stage B：完整 schema 参数生成（只传命中工具的 schema） ────────────────────
_STAGE_B_SYSTEM = """你是对话助手的后台工具执行器。
对话助手正在跟用户说话，你在后台并行执行工具查询，完成后把结果返回给它。
要求：只输出 tool_use，不做任何对话性回复。
- 历史对话可用于理解用户意图，包括提取用户或助手上一轮提到的具体内容（金句文字、书名、时间等）
- 若用户说「顺着刚才」「就是那句」「上面那个」或明显引用上一轮内容，应从历史中提取对应内容填入参数
- 若用户纠正上一轮操作（"不对是X分钟"），从当前消息中提取正确参数重新调用工具
- 当前书页内容已包含在用户消息中，可用于填写摘要/卡片等工具参数；但若历史中已有更明确的对应内容（如刚查出的金句），优先使用历史"""

# Stage A：目录选择（小 payload，快）；Stage B：参数生成（少量 schema，也快）
_STAGE_A_TIMEOUT_S = 12.0
_STAGE_B_TIMEOUT_S = 18.0
# 外层安全兜底（Stage A + Stage B 总时间上限）
_LLM_TIMEOUT_S = 30.0


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
        page_context: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Returns:
            {tool_name: result_dict}，或 None（不需要工具），或 {"__timeout__": True}（超时）。
        """
        if not tools:
            logger.info("[SubAgent] tools 为空，跳过")
            return None

        t0 = time.time()
        logger.info(f"[SubAgent] 启动 | user_text='{user_text[:50]}' | 全量tools={len(tools)}")

        # ── LLM Round 1 ───────────────────────────────────────────────────────
        tool_calls = None
        try:
            tool_calls = await asyncio.wait_for(
                self._llm_select_and_call(user_text, tools, history, llm, t0, page_context),
                timeout=_LLM_TIMEOUT_S,
            )
            logger.info(f"[SubAgent] LLM 完成 +{time.time()-t0:.1f}s | tool_calls={[tc['name'] for tc in tool_calls] if tool_calls else '[]'}")
        except asyncio.TimeoutError:
            logger.warning(f"[SubAgent] LLM 超时（>{_LLM_TIMEOUT_S}s），返回超时标志")
            return {"__timeout__": True}
        except Exception as e:
            logger.warning(f"[SubAgent] LLM 异常: {e}，返回超时标志")
            return {"__timeout__": True}

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
        page_context: Optional[str] = None,
    ) -> List[dict]:
        """
        两阶段 LLM 调用：
          Stage A — 发轻量目录，LLM 返回工具名列表（小 payload，快）
          Stage B — 只发命中工具的完整 schema，LLM 生成参数并返回 tool_use
        """
        # ── Stage A：工具选择 ────────────────────────────────────────────────
        selected_names = await asyncio.wait_for(
            self._stage_a_select(user_text, history, llm, t0),
            timeout=_STAGE_A_TIMEOUT_S,
        )
        if not selected_names:
            return []

        # ── Stage B：参数生成 ────────────────────────────────────────────────
        selected_schemas = [t for t in tools if t["name"] in set(selected_names)]
        if not selected_schemas:
            logger.warning(f"[SubAgent/StageB] 工具 {selected_names} 无对应 schema，跳过")
            return []

        return await asyncio.wait_for(
            self._stage_b_call(user_text, selected_schemas, history, llm, t0, page_context),
            timeout=_STAGE_B_TIMEOUT_S,
        )

    async def _stage_a_select(
        self,
        user_text: str,
        history: List[dict],
        llm,
        t0: float,
    ) -> List[str]:
        """Stage A：发轻量目录，返回工具名列表。"""
        import json, re as _re
        resp = await llm.chat(
            user_message=user_text,
            system_prompt=_STAGE_A_SYSTEM,
            history=history,
            max_tokens=100,
        )
        t_a = time.time() - t0
        text = (resp.text or "").strip()
        try:
            m = _re.search(r'\{[^{}]*\}', text, _re.DOTALL)
            data = json.loads(m.group()) if m else {}
            raw = data.get("tools", [])
            names = [n for n in raw if n in TOOL_CATALOG]
            unknown = set(raw) - set(names)
            if unknown:
                logger.warning(f"[SubAgent/StageA] 过滤未知工具: {unknown}")
            logger.info(f"[SubAgent/StageA] +{t_a:.1f}s → {names}")
            return names
        except Exception as e:
            logger.warning(f"[SubAgent/StageA] 解析失败 ({e}): {text[:80]!r}")
            return []

    async def _stage_b_call(
        self,
        user_text: str,
        schemas: List[dict],
        history: List[dict],
        llm,
        t0: float,
        page_context: Optional[str] = None,
    ) -> List[dict]:
        """Stage B：发选中工具完整 schema，LLM 生成参数并返回 tool_use。"""
        augmented_message = user_text
        if page_context:
            augmented_message = (
                f"【当前书页内容（供参数填写参考）】\n{page_context}\n\n"
                f"【用户消息】{user_text}"
            )

        tool_calls_collected = []
        first_token_time = None
        text_chunks = []

        async for chunk in llm.chat_stream(
            user_message=augmented_message,
            system_prompt=_STAGE_B_SYSTEM,
            history=history,
            tools=schemas,
        ):
            if chunk.type == "text_delta":
                if first_token_time is None:
                    first_token_time = time.time()
                    logger.info(f"[SubAgent/StageB] 首字节 +{first_token_time-t0:.1f}s（text_delta，非 tool_use）")
                text_chunks.append(chunk.content)
            elif chunk.type == "tool_use":
                if first_token_time is None:
                    first_token_time = time.time()
                    logger.info(f"[SubAgent/StageB] 首字节 +{first_token_time-t0:.1f}s（tool_use）")
                if chunk.tool_calls:
                    tool_calls_collected.extend(chunk.tool_calls)
                    logger.info(f"[SubAgent/StageB] 收到 tool_use: {[tc['name'] for tc in chunk.tool_calls]}")

        if text_chunks:
            logger.warning(f"[SubAgent/StageB] LLM 输出文本而非 tool_use: {''.join(text_chunks)[:80]!r}")

        return tool_calls_collected
