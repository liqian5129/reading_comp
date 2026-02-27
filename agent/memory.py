"""
记忆系统
管理对话历史和用户偏好
"""
import json
import logging
from typing import List, Dict, Any, Optional
from pathlib import Path
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)


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
    """
    
    def __init__(self, persona_file: Path, max_history: int = 20):
        self.persona_file = persona_file
        self.max_history = max_history
        
        # 对话历史
        self.history: List[Dict[str, Any]] = []
        
        # 用户画像
        self.persona = Persona()
        
        # 当前页面上下文
        self.current_page_ocr: str = ""
        self.current_page_image: Optional[str] = None
        
        # 加载 persona
        self._load_persona()
    
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
        
        包含：
        - 角色定义
        - 用户偏好
        - 当前页面上下文
        """
        parts = []
        
        # 角色定义
        parts.append("""你是一个陪伴用户阅读的智能助手，以读书场景为核心，同时也能回答用户的一般性问题。

核心能力：
1. 解释、总结和讨论当前书页内容
2. 回答用户关于书中知识点的问题
3. 记录读书笔记和想法
4. 管理阅读会话（开始、结束、查看历史）
5. 闲聊、推荐书单、回答其他日常问题

回答风格：友好自然，简洁明了，适合语音播报（避免过长列举，少用 markdown 格式）。

回答长度：除非用户明确要求详细说明，每次回复请控制在 350 个字以内。""")
        
        # 用户偏好
        if self.persona.reading_preferences:
            parts.append(f"用户的阅读偏好: {', '.join(self.persona.reading_preferences)}")
        
        if self.persona.favorite_genres:
            parts.append(f"用户喜欢的书籍类型: {', '.join(self.persona.favorite_genres)}")
        
        # 当前页面上下文
        if self.current_page_ocr:
            # 截取前 2000 字符，避免超出 token 限制
            page_text = self.current_page_ocr[:2000]
            parts.append(f"""
【当前书页内容】
{page_text}
{"...(内容已截断)" if len(self.current_page_ocr) > 2000 else ""}
用户可能会询问关于这页内容的问题。
""")
        
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
