"""
用户洞见检测器
检测用户发言中的个人观点/感悟，fire-and-forget 自动保存为笔记候选。
仅在用户表达自己的思考、发现、联想时触发；简单查询/搜索不触发。
"""
import asyncio
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# 触发词：用户表达个人观点/感悟/联想
_INSIGHT_RE = re.compile(
    r"我觉得|我认为|我感觉|我发现|让我想到|让我觉得|我想到|"
    r"有个想法|有个感悟|忽然明白|突然意识到|联想到|这让我|"
    r"我的理解是|我的感受|感同身受|不禁想|我有个|我体会到|"
    r"读到这里|看到这里|这段让我|我悟到|醍醐灌顶"
)

# 排除词：简单查询/工具类请求（不含个人观点）
_QUERY_RE = re.compile(
    r"^(帮我|请|能不能|可以吗|麻烦)|"
    r"(什么意思|怎么理解|如何理解|是什么意思)|"
    r"(查一下|找一下|搜索|搜一下)|"
    r"^(刷新|更新|同步|显示|列出|查询|查看|看看)"
)

_MIN_LENGTH = 15  # 少于15字不算有效洞见


class InsightDetector:
    """
    检测用户发言中有价值的个人洞见，自动 fire-and-forget 保存为笔记。
    只保存含个人观点/思考的内容，过滤掉简单问答和工具类请求。
    """

    def __init__(self, session_manager, embedder, storage, knowledge_linker=None):
        self.session_manager = session_manager
        self.embedder = embedder
        self.storage = storage
        self.knowledge_linker = knowledge_linker

    def is_insight(self, text: str) -> bool:
        """判断文本是否包含用户个人洞见（不含简单查询）"""
        if len(text.strip()) < _MIN_LENGTH:
            return False
        if _QUERY_RE.search(text.strip()):
            return False
        return bool(_INSIGHT_RE.search(text))

    def maybe_save(self, text: str, book_name: str = "") -> None:
        """若 text 包含个人洞见，fire-and-forget 保存为笔记候选"""
        if self.is_insight(text):
            asyncio.create_task(self._save_insight(text, book_name))

    async def _save_insight(self, text: str, book_name: str) -> None:
        try:
            note = await self.session_manager.add_note(
                content=text,
                page_context="",
                book_name=book_name,
                tags=["auto_insight"],
            )
            if not note or not note.id:
                return
            logger.info(f"💡 用户洞见已记录（笔记#{note.id}）: {text[:50]}…")

            # 异步 embedding
            if self.embedder and self.storage:
                try:
                    embed_text = f"{book_name} {text}".strip()
                    embedding = await self.embedder.embed(embed_text)
                    if embedding:
                        await self.storage.save_embedding("notes", note.id, embedding)
                except Exception as e:
                    logger.debug(f"InsightDetector embed 失败: {e}")

            # 知识链接
            if self.knowledge_linker:
                asyncio.create_task(self.knowledge_linker.link_note(note))
        except Exception as e:
            logger.debug(f"InsightDetector 保存失败（已降级）: {e}")
