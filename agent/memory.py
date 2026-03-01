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
    """

    def __init__(self, persona_file: Path, long_term_file: Optional[Path] = None, max_history: int = 20):
        self.persona_file = persona_file
        self.long_term_file = long_term_file
        self.max_history = max_history

        # 对话历史
        self.history: List[Dict[str, Any]] = []

        # 用户画像
        self.persona = Persona()

        # 当前页面上下文
        self.current_page_ocr: str = ""
        self.current_page_image: Optional[str] = None

        # 当前书籍视觉上下文（由 VisionAnalyzer 更新）
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

    def update_book_context(self, vision_result: dict):
        """更新当前书籍视觉上下文（由 VisionAnalyzer 回调）"""
        self.current_book_context = {
            "book_title": vision_result.get("book_title", ""),
            "current_page_num": vision_result.get("current_page_num", 0),
            "content_type": vision_result.get("content_type", ""),
            "confidence": vision_result.get("confidence", 0.0),
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
        """
        添加消息到历史
        
        Args:
            role: 'user' 或 'assistant'
            content: 消息内容
        """
        self.history.append({
            "role": role,
            "content": content
        })
        
        # 滑动窗口，保留最近 N 条
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history:]
    
    def get_history(self) -> List[Dict[str, str]]:
        """获取历史消息（用于 LLM 请求）"""
        return self.history
    
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
    
    def build_system_prompt(self) -> str:
        """
        构建系统提示词

        注入顺序：
        1. 角色定义
        2. 长期记忆摘要
        3. 用户偏好
        4. 当前书籍视觉上下文
        5. 当前页 OCR 文本
        """
        parts = []

        # 1. 角色定义
        parts.append("""你是一个陪伴用户阅读的智能助手，以读书场景为核心，同时也能回答用户的一般性问题。

核心能力：
1. 解释、总结和讨论当前书页内容
2. 回答用户关于书中知识点的问题
3. 记录读书笔记和想法
4. 查询阅读历史和笔记
5. 闲聊、推荐书单、回答其他日常问题

回答风格：友好自然，简洁明了，适合语音播报（避免过长列举，少用 markdown 格式）。

回答长度：除非用户明确要求详细说明，每次回复请控制在 350 个字以内。""")

        # 2. 长期记忆摘要
        lt_digest = self.long_term.get_digest_for_prompt()
        if lt_digest:
            parts.append(f"【你对这位用户的了解】\n{lt_digest}")

        # 3. 用户偏好
        if self.persona.reading_preferences:
            parts.append(f"用户的阅读偏好: {', '.join(self.persona.reading_preferences)}")
        if self.persona.favorite_genres:
            parts.append(f"用户喜欢的书籍类型: {', '.join(self.persona.favorite_genres)}")

        # 4. 当前书籍视觉上下文
        ctx = self.current_book_context
        if ctx.get("book_title") and ctx.get("confidence", 0) >= 0.7:
            page_info = f"第 {ctx['current_page_num']} 页" if ctx.get("current_page_num") else ""
            parts.append(
                f"【当前正在阅读】《{ctx['book_title']}》{page_info}"
                + (f"（{ctx['content_type']}）" if ctx.get("content_type") else "")
            )

        # 5. 当前页面 OCR 文本
        if self.current_page_ocr:
            page_text = self.current_page_ocr[:2000]
            truncated = "...(内容已截断)" if len(self.current_page_ocr) > 2000 else ""
            parts.append(f"""【当前书页内容（摄像头已自动识别）】
以下是摄像头刚刚拍摄并 OCR 识别的书页文字，你已经看到了这些内容，请直接基于它回答用户问题，无需再次拍照：

{page_text}{truncated}""")

        return "\n\n".join(parts)
    
    def update_from_session_summary(self, summary: str):
        """
        从会话总结中提取并更新用户偏好
        
        Args:
            summary: AI 生成的会话总结
        """
        # 这里可以调用 LLM 分析总结，提取新的偏好信息
        # 简化处理：先不做自动提取，让用户手动更新
        pass
