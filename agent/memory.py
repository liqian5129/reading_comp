"""
记忆系统
管理对话历史和用户偏好
"""
import asyncio
import json
import logging
from typing import List, Dict, Any, Optional
from pathlib import Path
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)


@dataclass
class LongTermMemory:
    """跨会话长期记忆（持久化到 JSON 文件）"""
    book_summaries: Dict[str, str] = field(default_factory=dict)
    # {book_title: "AI 提炼的阅读历程摘要"}
    user_insights: List[str] = field(default_factory=list)
    # ["用户喜欢在晚上读历史类书籍", ...]
    reading_streaks: Dict[str, Any] = field(default_factory=lambda: {
        "current_streak_days": 0,
        "last_read_date": "",
    })
    topic_interests: Dict[str, int] = field(default_factory=dict)
    # {话题: 出现权重}，如 {"认知心理学": 3, "决策": 2}
    session_recall: str = ""
    # 最近 2 次会话摘要（启动时从 DB 加载）

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "LongTermMemory":
        return cls(
            book_summaries=data.get("book_summaries", {}),
            user_insights=data.get("user_insights", []),
            reading_streaks=data.get("reading_streaks", {
                "current_streak_days": 0,
                "last_read_date": "",
            }),
            topic_interests=data.get("topic_interests", {}),
            session_recall=data.get("session_recall", ""),
        )

    def get_digest_for_prompt(self) -> str:
        """生成适合注入 system_prompt 的摘要（控制 token 数）"""
        parts = []

        if self.book_summaries:
            books = list(self.book_summaries.items())[:5]  # 最多 5 本
            summaries = "\n".join(f"- 《{t}》: {s[:100]}" for t, s in books)
            parts.append(f"历史阅读记录：\n{summaries}")

        if self.user_insights:
            insights = "；".join(self.user_insights[:3])  # 最多 3 条
            parts.append(f"用户阅读习惯：{insights}")

        streak = self.reading_streaks.get("current_streak_days", 0)
        if streak > 0:
            parts.append(f"连续阅读天数：{streak} 天")

        if self.topic_interests:
            top_topics = sorted(self.topic_interests.items(), key=lambda x: x[1], reverse=True)[:5]
            parts.append(f"常关注话题：{'、'.join(t for t, _ in top_topics)}")

        if self.session_recall:
            parts.append(f"上次阅读摘要：\n{self.session_recall}")

        return "\n".join(parts)


@dataclass
class Persona:
    """
    用户画像/偏好
    """
    reading_preferences: List[str] = field(default_factory=list)  # 阅读偏好
    favorite_genres: List[str] = field(default_factory=list)      # 喜欢的书籍类型
    read_books: List[str] = field(default_factory=list)           # 已读书目
    notes: str = ""                                                # 个人备注
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> "Persona":
        return cls(**data)


class Memory:
    """
    记忆管理器

    管理：
    - 滑动窗口对话历史
    - 用户偏好 (persona.json)
    - 当前页面上下文
    - 当前书籍视觉上下文
    - 跨会话长期记忆 (long_term_memory.json)
    - 向量检索主动注入（prefetch 模式）
    """

    def __init__(self, persona_file: Path, long_term_file: Optional[Path] = None,
                 max_history: int = 20, embedder=None, storage=None,
                 proactive_top_k: int = 3):
        self.persona_file = persona_file
        self.long_term_file = long_term_file
        self.max_history = max_history
        self.embedder = embedder    # Optional[Embedder]
        self.storage = storage      # Optional[Storage]，用于向量检索
        self.proactive_top_k = proactive_top_k

        # 对话历史
        self.history: List[Dict[str, Any]] = []

        # 用户画像
        self.persona = Persona()

        # 当前页面上下文
        self.current_page_ocr: str = ""
        self.current_page_image: Optional[str] = None

        # 当前书籍上下文（由 KimiOCR 更新）
        self.current_book_context: Dict[str, Any] = {
            "book_title": "",
            "current_page_num": 0,
            "content_type": "",
            "confidence": 0.0,
        }

        # 本次会话摘要
        self.session_summary: str = ""

        # 长期记忆
        self.long_term = LongTermMemory()
        self._long_term_lock = asyncio.Lock()

        # 主动注入 prefetch 缓存
        self._prefetch_cache: Optional[str] = None
        self._prefetch_task: Optional[asyncio.Task] = None

        # 翻页预分析 hint（由 ProactivePageAnalyzer 写入，翻页时重置）
        self.proactive_page_hint: Optional[str] = None

        # 指尖点读：当前手指指向的文字（行级别）
        self.finger_pointed_text: str = ""

        # 本次会话滚动阅读摘要
        self.session_reading_digest: str = ""
        self._new_pages_buffer: list = []   # 待压缩的新页摘录
        self._valid_page_count: int = 0     # 有效新页计数（用于触发）
        self._last_buffer_hash: int = 0     # 去重用：上次加入 buffer 的 OCR hash

        # 加载
        self._load_persona()
        self._load_long_term()
    
    def _load_persona(self):
        """从文件加载用户画像"""
        if self.persona_file.exists():
            try:
                with open(self.persona_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self.persona = Persona.from_dict(data)
                logger.info(f"已加载用户画像: {self.persona_file}")
            except Exception as e:
                logger.error(f"加载用户画像失败: {e}")
                self.persona = Persona()

    def _load_long_term(self):
        """从文件加载长期记忆"""
        if self.long_term_file and self.long_term_file.exists():
            try:
                with open(self.long_term_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self.long_term = LongTermMemory.from_dict(data)
                logger.info(f"已加载长期记忆: {self.long_term_file}")
            except Exception as e:
                logger.error(f"加载长期记忆失败: {e}")
                self.long_term = LongTermMemory()

    async def save_long_term(self):
        """异步保存长期记忆（带锁保护）"""
        if not self.long_term_file:
            return
        async with self._long_term_lock:
            try:
                self.long_term_file.parent.mkdir(parents=True, exist_ok=True)
                with open(self.long_term_file, 'w', encoding='utf-8') as f:
                    json.dump(self.long_term.to_dict(), f, ensure_ascii=False, indent=2)
                logger.info("长期记忆已保存")
            except Exception as e:
                logger.error(f"保存长期记忆失败: {e}")

    def update_book_context(self, book_info: dict):
        """更新当前书籍上下文（由 KimiOCR 回调）"""
        self.current_book_context = {
            "book_title": book_info.get("book_title", ""),
            # KimiOCR 返回 page_num，兼容旧字段 current_page_num
            "current_page_num": book_info.get("current_page_num") or book_info.get("page_num", 0),
            "content_type": book_info.get("content_type", ""),
            "confidence": book_info.get("confidence", 0.0),
        }
    
    def save_persona(self):
        """保存用户画像"""
        try:
            self.persona_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.persona_file, 'w', encoding='utf-8') as f:
                json.dump(self.persona.to_dict(), f, ensure_ascii=False, indent=2)
            logger.info("用户画像已保存")
        except Exception as e:
            logger.error(f"保存用户画像失败: {e}")
    
    def update_persona(self, new_preferences: Dict[str, Any]):
        """更新用户画像"""
        for key, value in new_preferences.items():
            if hasattr(self.persona, key):
                setattr(self.persona, key, value)
        self.save_persona()
    
    def add_message(self, role: str, content: str):
        """添加纯文字消息到历史"""
        self.history.append({"role": role, "content": content})
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history:]

    def add_raw_message(self, msg: dict):
        """添加原始消息（支持 tool_calls / tool result 格式）"""
        self.history.append(msg)
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history:]

    def add_tool_round(self, raw_assistant_msg: dict, tool_results: list):
        """
        保存一轮工具调用到历史：assistant(tool_calls) + tool results。
        在 _process_user_message_inner 完成每轮工具调用后调用。
        """
        if raw_assistant_msg:
            self.add_raw_message(raw_assistant_msg)
        for result in tool_results:
            self.add_raw_message({
                "role": "tool",
                "tool_call_id": result["tool_use_id"],
                "content": result["content"],
            })
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history:]
    
    def get_history(self) -> List[Dict[str, str]]:
        """
        获取历史消息（用于 LLM 请求）。
        自动清理孤立的 tool 结果消息，防止 history 被 max_history 截断后
        出现 tool_call_id 找不到对应 assistant tool_calls 的 API 400 错误。
        """
        # 收集所有 assistant 消息中声明的 tool_call_id
        valid_ids: set = set()
        for msg in self.history:
            if msg.get("role") == "assistant":
                for tc in (msg.get("tool_calls") or []):
                    cid = tc.get("id") or tc.get("tool_use_id", "")
                    if cid:
                        valid_ids.add(cid)

        # 过滤掉 tool_call_id 不在 valid_ids 中的孤立 tool 结果
        return [
            msg for msg in self.history
            if not (msg.get("role") == "tool" and msg.get("tool_call_id", "") not in valid_ids)
        ]
    
    def clear_history(self):
        """清空历史"""
        self.history = []
    
    def set_page_context(self, ocr_text: str, image_path: Optional[str] = None):
        """
        设置当前页面上下文
        
        Args:
            ocr_text: 页面 OCR 文本
            image_path: 页面图片路径
        """
        self.current_page_ocr = ocr_text
        self.current_page_image = image_path
        logger.debug(f"页面上下文已更新，文本长度: {len(ocr_text)}")
    
    def clear_page_context(self):
        """清空页面上下文"""
        self.current_page_ocr = ""
        self.current_page_image = None
    
    def trigger_prefetch(self, query_text: str) -> None:
        """
        在 AI 回答完毕后调用（fire-and-forget），预取下一轮相关记忆。
        第一轮 cache 为空时 build_system_prompt 跳过注入（降级），不影响功能。
        """
        if not self.embedder or not self.storage:
            return
        # 取消旧的 prefetch task（若有）
        if self._prefetch_task and not self._prefetch_task.done():
            self._prefetch_task.cancel()
        self._prefetch_task = asyncio.create_task(
            self._prefetch_memories(query_text)
        )

    async def _prefetch_memories(self, query_text: str) -> None:
        """后台预取相关记忆，存入 _prefetch_cache。任何异常静默降级。"""
        try:
            embedding = await self.embedder.embed(query_text)
            if not embedding:
                self._prefetch_cache = None
                return

            results = await self.storage.search_by_embedding(
                embedding,
                top_k=self.proactive_top_k,
            )
            if not results:
                self._prefetch_cache = None
                return

            lines = []
            for r in results:
                book_hint = f"《{r.book_name}》" if r.book_name else ""
                source_map = {
                    "notes": "你的笔记",
                    "weread_highlights": "微信读书划线",
                    "weread_notes": "微信读书想法",
                    "session_summaries": "历史会话摘要",
                }
                source_label = source_map.get(r.source, r.source)
                snippet = r.content[:100].replace("\n", " ")
                lines.append(f"- {source_label}{book_hint}：{snippet}…（相似度 {r.score:.2f}）")

            self._prefetch_cache = "\n".join(lines)
            logger.info(f"prefetch 完成: 命中 {len(results)} 条相关记忆")
        except Exception as e:
            logger.warning(f"prefetch_memories 失败（已降级）: {e}")
            self._prefetch_cache = None

    def build_system_prompt(self, user_text: str = "") -> str:
        """
        构建系统提示词（intent-aware 动态版本）

        根据用户输入文本分类意图，再由 DynamicContextBuilder 按意图选择性注入各节。
        user_text 为空时 fallback 到 GENERAL_CHAT（行为与改造前完全相同）。
        """
        from .intent_classifier import IntentClassifier
        from .context_builder import DynamicContextBuilder

        intent = IntentClassifier.classify(user_text)
        config = IntentClassifier.get_config(intent)
        logger.debug(f"[Memory] 意图分类: '{user_text[:30]}' → {intent.value}")

        return DynamicContextBuilder().build(self, intent, config)
    
    def set_finger_text(self, text: str):
        self.finger_pointed_text = text

    def clear_finger_text(self):
        self.finger_pointed_text = ""

    def update_from_session_summary(self, summary: str):
        """
        从会话总结中提取并更新用户偏好
        
        Args:
            summary: AI 生成的会话总结
        """
        # 这里可以调用 LLM 分析总结，提取新的偏好信息
        # 简化处理：先不做自动提取，让用户手动更新
        pass
