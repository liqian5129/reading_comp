"""
微信读书数据模型
"""
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class WeReadBook:
    """书架书籍"""
    book_id: str
    title: str
    author: str = ""
    translator: str = ""
    cover: str = ""
    category: str = ""
    finish_reading: bool = False
    reading_time_s: int = 0
    progress: int = 0          # 0-100 百分比
    synced_at: int = 0

    @property
    def reading_time_str(self) -> str:
        m = self.reading_time_s // 60
        if m < 60:
            return f"{m} 分钟"
        return f"{m // 60} 小时 {m % 60} 分钟"

    @property
    def progress_str(self) -> str:
        return f"{self.progress}%"


@dataclass
class WeReadHighlight:
    """划线 — 用户在正文中标注的高亮文字"""
    bookmark_id: str
    book_id: str
    book_title: str
    content: str
    chapter_uid: int = 0
    chapter_title: str = ""
    chapter_idx: int = 0
    created_at: int = 0
    synced_at: int = 0

    @property
    def created_at_str(self) -> str:
        if not self.created_at:
            return ""
        return datetime.fromtimestamp(self.created_at).strftime("%Y-%m-%d %H:%M")


@dataclass
class WeReadNote:
    """
    想法 / 点评
    - 想法 (note_type="想法"): 附在划线上的文字评论，abstract=划线原文，content=用户评论
    - 点评 (note_type="点评"): 独立书评/感想，abstract="", content=用户评论
    """
    review_id: str
    book_id: str
    book_title: str
    content: str
    abstract: str = ""         # 划线原文（仅想法类型有值）
    note_type: str = "想法"    # "想法" 或 "点评"
    chapter_uid: int = 0
    chapter_title: str = ""
    chapter_idx: int = 0
    created_at: int = 0
    synced_at: int = 0

    @property
    def created_at_str(self) -> str:
        if not self.created_at:
            return ""
        return datetime.fromtimestamp(self.created_at).strftime("%Y-%m-%d %H:%M")


@dataclass
class WeReadProgress:
    """阅读进度"""
    book_id: str
    book_title: str
    progress: int = 0          # 0-100 百分比
    reading_time_s: int = 0
    chapter_uid: int = 0
    chapter_offset: int = 0
    synced_at: int = 0

    @property
    def progress_str(self) -> str:
        return f"{self.progress}%"

    @property
    def reading_time_str(self) -> str:
        m = self.reading_time_s // 60
        if m < 60:
            return f"{m} 分钟"
        return f"{m // 60} 小时 {m % 60} 分钟"
