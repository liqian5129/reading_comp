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
        dt = datetime.fromtimestamp(self.ts / 1000)  # 本地时间
        return {
            "id": self.id,
            "ts": self.ts,
            "created_at": dt.strftime("%Y-%m-%d %H:%M:%S"),
            "book_name": self.book_name,
            "tags": self.tags,
            "content": self.content,
            "session_id": self.session_id,
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
        """用于 JSON 文件命名的本地时间字符串"""
        dt = datetime.fromtimestamp(self.ts / 1000)
        return dt.strftime("%Y%m%dT%H%M%S")


@dataclass
class Book:
    """书籍实体"""
    id: int                          # 自增 ID
    title: str                       # 书名（唯一）
    author: str = ""                 # 作者
    genre: str = ""                  # 类型/分类
    total_pages: int = 0             # 总页数
    cover_image_path: str = ""       # 封面图片路径
    created_at: int = 0              # 创建时间戳 (ms)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BookProgress:
    """每本书的阅读进度"""
    id: int                          # 自增 ID
    book_id: int                     # 关联书籍 ID
    book_title: str                  # 书名（冗余，方便查询）
    last_page_num: int = 0           # 最后阅读页码
    last_page_ocr: str = ""          # 最后页面 OCR 文本
    last_read_at: int = 0            # 最后阅读时间戳 (ms)
    total_read_time_ms: int = 0      # 累计阅读时长 (ms)
    total_pages_read: int = 0        # 累计翻页数
    status: str = "reading"          # reading / finished / paused

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def status_str(self) -> str:
        return {"reading": "阅读中", "finished": "已完成", "paused": "已暂停"}.get(self.status, self.status)


@dataclass
class Bookmark:
    """书签"""
    id: int                          # 自增 ID
    book_id: int                     # 关联书籍 ID
    book_title: str                  # 书名
    session_id: str                  # 所属会话 ID
    page_num: int = 0                # 页码（视觉识别或用户指定）
    page_ocr_excerpt: str = ""       # 页面前 200 字
    note: str = ""                   # 用户备注
    bookmark_type: str = "manual"    # manual / auto
    ts: int = 0                      # 创建时间戳 (ms)

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def created_at_str(self) -> str:
        dt = datetime.fromtimestamp(self.ts / 1000)
        return dt.strftime("%Y-%m-%d %H:%M")


@dataclass
class ReadingListItem:
    """书单条目"""
    id: int                          # 自增 ID
    title: str                       # 书名
    author: str = ""                 # 作者
    status: str = "want"             # want / reading / done
    priority: int = 0                # 优先级（0=普通，1=高）
    notes: str = ""                  # 备注
    added_at: int = 0                # 加入时间戳 (ms)
    started_at: Optional[int] = None # 开始阅读时间戳
    finished_at: Optional[int] = None  # 完成时间戳

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def status_str(self) -> str:
        return {"want": "想读", "reading": "在读", "done": "已读"}.get(self.status, self.status)


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
