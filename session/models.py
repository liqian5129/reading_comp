"""
数据模型定义
"""
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional, List
import json


@dataclass
class ReadingSession:
    """
    阅读会话
    """
    id: str                          # 会话 ID (UUID)
    book_name: str = ""              # 书名（可选）
    start_at: int = 0                # 开始时间戳 (ms)
    end_at: Optional[int] = None     # 结束时间戳 (ms)
    camera_device: int = 0           # 摄像头设备号
    
    # 统计信息
    total_pages: int = 0             # 总页数
    total_snapshots: int = 0         # 总快照数
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> "ReadingSession":
        return cls(**data)
    
    @property
    def duration_ms(self) -> int:
        """阅读时长（毫秒）"""
        end = self.end_at or int(datetime.now().timestamp() * 1000)
        return end - self.start_at
    
    @property
    def duration_str(self) -> str:
        """格式化的阅读时长"""
        minutes = self.duration_ms // 60000
        if minutes < 60:
            return f"{minutes} 分钟"
        hours = minutes // 60
        mins = minutes % 60
        return f"{hours} 小时 {mins} 分钟"


@dataclass
class PageSnapshot:
    """
    页面快照
    """
    id: int                          # 自增 ID
    session_id: str                  # 所属会话 ID
    ts: int                          # 时间戳 (ms)
    image_path: str                  # 图片保存路径
    ocr_text: str = ""               # OCR 识别文本
    fingerprint: str = ""            # 页面指纹
    dwell_ms: int = 0                # 停留时长（毫秒）
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> "PageSnapshot":
        return cls(**data)


@dataclass
class Note:
    """
    阅读笔记
    """
    id: int                          # 自增 ID
    ts: int                          # UTC 时间戳 (ms)
    content: str = ""                # 笔记内容
    session_id: str = ""             # 所属会话 ID（可为空）
    book_name: str = ""              # 书名（可为空）
    tags: List[str] = field(default_factory=list)  # 用户自定义标签
    page_ocr_context: str = ""       # 记录时的页面 OCR 上下文

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json_dict(self) -> dict:
        """用于写入 JSON 文件的完整格式"""
        dt = datetime.utcfromtimestamp(self.ts / 1000)
        return {
            "id": self.id,
            "utc_ts": self.ts,
            "utc_datetime": dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "book_name": self.book_name,
            "tags": self.tags,
            "content": self.content,
            "session_id": self.session_id,
            "page_ocr_context": self.page_ocr_context,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Note":
        return cls(**data)

    @property
    def created_at_str(self) -> str:
        """格式化创建时间（本地时间）"""
        dt = datetime.fromtimestamp(self.ts / 1000)
        return dt.strftime("%Y-%m-%d %H:%M")

    @property
    def utc_filename(self) -> str:
        """用于 JSON 文件命名的 UTC 时间字符串"""
        dt = datetime.utcfromtimestamp(self.ts / 1000)
        return dt.strftime("%Y%m%dT%H%M%SZ")


@dataclass
class DailySummary:
    """
    每日阅读摘要（用于飞书推送）
    """
    date: str                        # 日期 YYYY-MM-DD
    total_sessions: int = 0          # 会话数
    total_duration_ms: int = 0       # 总时长
    total_pages: int = 0             # 总页数
    total_notes: int = 0             # 笔记数
    book_names: List[str] = field(default_factory=list)  # 阅读书目
    longest_session: Optional[ReadingSession] = None  # 最长会话
    
    @property
    def duration_str(self) -> str:
        """格式化的总时长"""
        minutes = self.total_duration_ms // 60000
        if minutes < 60:
            return f"{minutes} 分钟"
        hours = minutes // 60
        mins = minutes % 60
        return f"{hours} 小时 {mins} 分钟"
