"""
记忆巩固器
在会话结束/定时/token 接近上限时，将对话历史提炼为结构化摘要，持久化到 DB 和日志文件。
"""
import asyncio
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any

logger = logging.getLogger(__name__)

# 巩固摘要的 LLM prompt
_CONSOLIDATION_PROMPT = """\
请将以下对话历史提炼为结构化阅读摘要（300字以内），格式如下：
书籍：《书名》（若无明确书名则写「未知」）
讨论要点：1. ... 2. ... 3. ...
用户关注话题：话题1、话题2、...（逗号分隔）
待续：下次对话可以继续的话题或问题

对话历史：
{history}
"""

# 提取 topic_interests 的 prompt
_TOPICS_EXTRACT_PROMPT = """\
从以下摘要中提取用户关注的话题列表，用 JSON 数组格式返回，如 ["认知心理学", "决策", "读书方法"]。
只返回 JSON 数组，不要其他说明。

摘要：
{summary}
"""


class MemoryConsolidator:
    """
    记忆巩固器。
    触发方式：shutdown、定时、token 上限、用户手动。
    防重复：同一次触发间隔 < debounce_min 分钟则跳过。
    """

    def __init__(
        self,
        llm,                          # AIClient
        embedder,                     # Embedder（可为 None）
        storage,                      # Storage
        memory,                       # Memory
        memory_dir: Path,             # 日志文件目录（memory/）
        debounce_min: int = 5,
        daily_file_enabled: bool = True,
        session_recall_count: int = 2,
    ):
        self.llm = llm
        self.embedder = embedder
        self.storage = storage
        self.memory = memory
        self.memory_dir = memory_dir
        self.debounce_sec = debounce_min * 60
        self.daily_file_enabled = daily_file_enabled
        self.session_recall_count = session_recall_count

        self._last_consolidation_ts: float = 0.0
        self._consolidation_lock = asyncio.Lock()

    def trigger_consolidate(self, reason: str = "manual") -> None:
        """非阻塞触发巩固（fire-and-forget），供 main.py 调用。"""
        asyncio.create_task(self.consolidate(reason=reason))

    async def consolidate(self, reason: str = "manual") -> None:
        """
        执行一次会话巩固：
        1. 节流检查（debounce）
        2. LLM 生成摘要
        3. Embed 摘要
        4. 保存到 DB
        5. 写入 memory/YYYY-MM-DD.md
        6. 更新 long_term_memory.json
        7. 更新 memory.long_term.session_recall
        """
        # 防并发
        if self._consolidation_lock.locked():
            logger.info(f"巩固跳过（已有任务在运行）: reason={reason}")
            return

        async with self._consolidation_lock:
            now = time.time()
            if now - self._last_consolidation_ts < self.debounce_sec:
                elapsed = int(now - self._last_consolidation_ts)
                logger.info(f"巩固跳过（距上次 {elapsed}s < {self.debounce_sec}s）: reason={reason}")
                return

            history = self.memory.get_history()
            if not history:
                logger.info("巩固跳过：对话历史为空")
                return

            logger.info(f"开始记忆巩固: reason={reason}, 历史消息={len(history)}")

            # ── 1. 生成摘要 ─────────────────────────────────────────
            summary_text = await self._generate_summary(history)
            if not summary_text:
                logger.warning("巩固失败：摘要生成为空")
                return

            # ── 2. 提取话题 ─────────────────────────────────────────
            key_topics = await self._extract_topics(summary_text)

            # ── 3. Embed 摘要 ────────────────────────────────────────
            embedding: Optional[List[float]] = None
            if self.embedder:
                embedding = await self.embedder.embed(summary_text)

            # ── 4. 保存到 DB ─────────────────────────────────────────
            summary_id = 0
            try:
                summary_id = await self.storage.save_session_summary(
                    summary_text, key_topics, embedding
                )
                logger.info(f"会话摘要已存入 DB: id={summary_id}")
            except Exception as e:
                logger.error(f"保存摘要到 DB 失败: {e}")

            # ── 5. 写入 daily 日志文件 ───────────────────────────────
            if self.daily_file_enabled:
                await self._append_daily_file(summary_text, reason)

            # ── 6. 更新 long_term_memory.json ───────────────────────
            await self._update_long_term(summary_text, key_topics)

            # ── 7. 刷新 session_recall ──────────────────────────────
            await self._refresh_session_recall()

            self._last_consolidation_ts = time.time()
            logger.info(f"记忆巩固完成: id={summary_id}, 话题={key_topics}")

    async def _generate_summary(self, history: List[Dict]) -> str:
        """调用 LLM 生成结构化摘要，失败返回空字符串"""
        # 将历史拼成文本（只保留 user/assistant 文字消息，跳过 tool）
        history_text_parts = []
        for msg in history[-20:]:  # 最近 20 条
            role = msg.get("role", "")
            if role == "user":
                content = msg.get("content", "")
                if isinstance(content, str):
                    history_text_parts.append(f"用户: {content[:200]}")
            elif role == "assistant":
                content = msg.get("content", "")
                if isinstance(content, str) and content.strip():
                    history_text_parts.append(f"助手: {content[:200]}")

        if not history_text_parts:
            return ""

        history_text = "\n".join(history_text_parts)
        prompt = _CONSOLIDATION_PROMPT.format(history=history_text)

        try:
            resp = await asyncio.wait_for(
                self.llm.chat(user_message=prompt, max_tokens=400),
                timeout=12.0,
            )
            return resp.text.strip() if resp and resp.text else ""
        except asyncio.TimeoutError:
            logger.warning("巩固摘要生成超时（12s）")
            return ""
        except Exception as e:
            logger.warning(f"巩固摘要生成失败: {e}")
            return ""

    async def _extract_topics(self, summary_text: str) -> List[str]:
        """从摘要提取话题列表，失败返回空列表"""
        prompt = _TOPICS_EXTRACT_PROMPT.format(summary=summary_text[:500])
        try:
            resp = await asyncio.wait_for(
                self.llm.chat(user_message=prompt, max_tokens=100),
                timeout=5.0,
            )
            if not resp or not resp.text:
                return []
            text = resp.text.strip()
            # 提取 JSON 数组
            start = text.find("[")
            end = text.rfind("]")
            if start != -1 and end != -1:
                return json.loads(text[start:end + 1])
        except Exception as e:
            logger.debug(f"话题提取失败（已降级）: {e}")
        return []

    async def _append_daily_file(self, summary_text: str, reason: str) -> None:
        """追加写入 memory/YYYY-MM-DD.md"""
        try:
            self.memory_dir.mkdir(parents=True, exist_ok=True)
            date_str = datetime.now().strftime("%Y-%m-%d")
            daily_file = self.memory_dir / f"{date_str}.md"
            now_str = datetime.now().strftime("%H:%M:%S")
            entry = f"\n## {now_str}（{reason}）\n\n{summary_text}\n"
            with open(daily_file, "a", encoding="utf-8") as f:
                f.write(entry)
            logger.info(f"巩固摘要已追加到: {daily_file}")
        except Exception as e:
            logger.error(f"写入 daily 文件失败: {e}")

    async def _update_long_term(self, summary_text: str, key_topics: List[str]) -> None:
        """更新 long_term_memory.json 中的 topic_interests 和 user_insights"""
        try:
            lt = self.memory.long_term

            # 更新话题权重
            for topic in key_topics:
                if topic:
                    lt.topic_interests[topic] = lt.topic_interests.get(topic, 0) + 1

            # 限制话题数量，保留权重最高的 20 个
            if len(lt.topic_interests) > 20:
                sorted_topics = sorted(lt.topic_interests.items(), key=lambda x: x[1], reverse=True)
                lt.topic_interests = dict(sorted_topics[:20])

            await self.memory.save_long_term()
        except Exception as e:
            logger.error(f"更新 long_term_memory 失败: {e}")

    async def _refresh_session_recall(self) -> None:
        """从 DB 加载最近 session_recall_count 次摘要，写入 memory.long_term.session_recall"""
        try:
            summaries = await self.storage.load_recent_summaries(n=self.session_recall_count)
            if summaries:
                self.memory.long_term.session_recall = "\n---\n".join(summaries)
                await self.memory.save_long_term()
        except Exception as e:
            logger.warning(f"刷新 session_recall 失败: {e}")

    async def start_periodic_loop(self, interval_minutes: int) -> None:
        """
        定时巩固循环（asyncio task，interval_minutes=0 禁用）。
        异常不退出循环，仅 log。
        """
        if interval_minutes <= 0:
            logger.info("定时巩固已禁用（interval_minutes=0）")
            return

        logger.info(f"定时巩固已启动，间隔 {interval_minutes} 分钟")
        while True:
            try:
                await asyncio.sleep(interval_minutes * 60)
                await self.consolidate(reason="periodic")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"定时巩固异常（已继续）: {e}")
