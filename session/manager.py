"""
会话管理器
管理阅读会话的生命周期
"""
import logging
import time
import uuid
from datetime import datetime
from typing import Optional, List

from .models import ReadingSession, PageSnapshot, Note, Book, BookProgress, Bookmark, ReadingListItem
from .storage import Storage

logger = logging.getLogger(__name__)


class SessionManager:
    """
    会话管理器
    
    负责：
    - 创建/结束阅读会话
    - 管理页面快照
    - 管理笔记
    - 查询历史记录
    """
    
    def __init__(self, storage: Storage):
        self.storage = storage
        self._current_session: Optional[ReadingSession] = None
        self._last_snapshot_id: Optional[int] = None
        self._last_snapshot_ts: int = 0
        
    @property
    def current_session(self) -> Optional[ReadingSession]:
        """当前活动会话"""
        return self._current_session
    
    def is_active(self) -> bool:
        """是否有活动会话"""
        return self._current_session is not None
    
    async def start_session(self, book_name: str = "", camera_device: int = 0) -> ReadingSession:
        """
        开始新的阅读会话
        
        Args:
            book_name: 书名（可选）
            camera_device: 摄像头设备号
            
        Returns:
            新创建的会话
        """
        session_id = str(uuid.uuid4())[:8]  # 短 ID 便于使用
        
        session = ReadingSession(
            id=session_id,
            book_name=book_name,
            start_at=int(datetime.now().timestamp() * 1000),
            camera_device=camera_device
        )
        
        await self.storage.create_session(session)
        self._current_session = session
        self._last_snapshot_id = None
        self._last_snapshot_ts = 0
        
        logger.info(f"会话已创建: {session_id}, 书名: {book_name or '未命名'}")
        return session
    
    async def end_session(self) -> Optional[ReadingSession]:
        """
        结束当前会话
        
        Returns:
            结束的会话，如果没有活动会话返回 None
        """
        if not self._current_session:
            logger.warning("没有活动会话可结束")
            return None
        
        # 更新最后一张快照的停留时间
        if self._last_snapshot_id:
            dwell = int(datetime.now().timestamp() * 1000) - self._last_snapshot_ts
            await self.storage.update_snapshot_dwell(self._last_snapshot_id, dwell)
        
        end_at = int(datetime.now().timestamp() * 1000)
        await self.storage.end_session(self._current_session.id, end_at)
        
        # 重新加载获取统计信息
        session = await self.storage.get_session(self._current_session.id)
        
        self._current_session = None
        self._last_snapshot_id = None
        
        logger.info(f"会话已结束: {session.id if session else 'unknown'}")
        return session
    
    async def add_snapshot(self, image_path: str, ocr_text: str, 
                          fingerprint: str) -> PageSnapshot:
        """
        添加页面快照
        
        Args:
            image_path: 图片路径
            ocr_text: OCR 文本
            fingerprint: 页面指纹
            
        Returns:
            创建的快照
        """
        if not self._current_session:
            raise RuntimeError("没有活动会话")
        
        ts = int(datetime.now().timestamp() * 1000)
        
        # 更新上一张快照的停留时间
        if self._last_snapshot_id:
            dwell = ts - self._last_snapshot_ts
            await self.storage.update_snapshot_dwell(self._last_snapshot_id, dwell)
        
        snapshot = PageSnapshot(
            id=0,  # 数据库自增
            session_id=self._current_session.id,
            ts=ts,
            image_path=image_path,
            ocr_text=ocr_text,
            fingerprint=fingerprint,
            dwell_ms=0
        )
        
        snapshot_id = await self.storage.add_snapshot(snapshot)
        snapshot.id = snapshot_id
        
        self._last_snapshot_id = snapshot_id
        self._last_snapshot_ts = ts
        
        logger.debug(f"快照已添加: {snapshot_id}")
        return snapshot
    
    async def add_note(
        self,
        content: str,
        page_context: str = "",
        book_name: str = "",
        tags: list = None,
    ) -> Note:
        """
        添加笔记（无需活跃会话）

        Args:
            content: 笔记内容
            page_context: 当前页面 OCR 上下文
            book_name: 书名（优先用传入值，否则取当前会话书名）
            tags: 用户自定义标签列表

        Returns:
            创建的笔记
        """
        # 书名：优先用调用方传入，否则取当前会话书名
        if not book_name and self._current_session:
            book_name = self._current_session.book_name

        note = Note(
            id=0,
            session_id=self._current_session.id if self._current_session else "",
            ts=int(time.time() * 1000),
            content=content,
            book_name=book_name,
            tags=tags or [],
            page_ocr_context=page_context,
        )

        note_id = await self.storage.add_note(note)
        note.id = note_id

        logger.info(f"笔记已添加: {note_id}, 书名: {book_name or '(无)'}, 标签: {note.tags}")
        return note
    
    async def get_current_page_context(self) -> str:
        """获取当前页面的 OCR 上下文"""
        if not self._current_session:
            return ""
        
        snapshot = await self.storage.get_last_snapshot(self._current_session.id)
        return snapshot.ocr_text if snapshot else ""
    
    # ==================== 查询方法 ====================
    
    async def get_session(self, session_id: str) -> Optional[ReadingSession]:
        """获取会话详情"""
        return await self.storage.get_session(session_id)
    
    async def list_sessions(self, limit: int = 10) -> List[ReadingSession]:
        """列出历史会话"""
        return await self.storage.list_sessions(limit=limit)
    
    async def get_today_sessions(self) -> List[ReadingSession]:
        """获取今日会话"""
        return await self.storage.get_today_sessions()
    
    async def get_session_notes(self, session_id: Optional[str] = None) -> List[Note]:
        """获取会话笔记"""
        sid = session_id or (self._current_session.id if self._current_session else None)
        if not sid:
            return []
        return await self.storage.get_session_notes(sid)
    
    async def count_notes_by_book(self, book_name: str) -> int:
        """统计指定书名的笔记数量"""
        return await self.storage.count_notes_by_book(book_name)

    async def get_recent_notes(self, days: int = 7, limit: int = 200) -> List[Note]:
        """获取最近 N 天的笔记（不依赖 session）"""
        return await self.storage.get_recent_notes(days=days, limit=limit)

    async def get_today_notes(self, limit: int = 100) -> List[Note]:
        """获取今日笔记"""
        return await self.storage.get_today_notes(limit=limit)

    async def get_today_summary(self):
        """获取今日摘要"""
        return await self.storage.get_daily_summary()
    
    async def get_session_snapshots(self, session_id: Optional[str] = None) -> List[PageSnapshot]:
        """获取会话快照"""
        sid = session_id or (self._current_session.id if self._current_session else None)
        if not sid:
            return []
        return await self.storage.get_session_snapshots(sid)

    # ==================== 书签 ====================

    async def create_bookmark(
        self,
        book_title: str,
        page_num: int = 0,
        page_ocr_excerpt: str = "",
        note: str = "",
        bookmark_type: str = "manual",
    ) -> Bookmark:
        """创建书签（自动关联或创建书籍）"""
        book = await self.storage.get_or_create_book(book_title)
        session_id = self._current_session.id if self._current_session else ""
        return await self.storage.create_bookmark(
            book_id=book.id,
            book_title=book_title,
            session_id=session_id,
            page_num=page_num,
            page_ocr_excerpt=page_ocr_excerpt,
            note=note,
            bookmark_type=bookmark_type,
        )

    async def list_bookmarks(self, book_title: str = "", limit: int = 20) -> List[Bookmark]:
        return await self.storage.list_bookmarks(book_title=book_title, limit=limit)

    # ==================== 阅读进度 ====================

    async def upsert_book_progress(
        self,
        book_title: str,
        page_num: int = 0,
        page_ocr: str = "",
        add_read_time_ms: int = 0,
        status: str = "",
    ) -> BookProgress:
        """更新阅读进度"""
        book = await self.storage.get_or_create_book(book_title)
        return await self.storage.upsert_book_progress(
            book_id=book.id,
            book_title=book_title,
            page_num=page_num,
            page_ocr=page_ocr,
            add_read_time_ms=add_read_time_ms,
            status=status,
        )

    async def get_book_progress(self, book_title: str) -> Optional[BookProgress]:
        return await self.storage.get_book_progress(book_title)

    async def list_book_progress(self, status: str = "") -> List[BookProgress]:
        return await self.storage.list_book_progress(status=status)

    # ==================== 阅读统计 ====================

    async def get_reading_stats(self, period: str = "today", book_title: str = "") -> dict:
        """获取阅读统计（today/week/month/all）"""
        return await self.storage.get_reading_stats(period=period, book_title=book_title)

    # ==================== 书单 ====================

    async def manage_reading_list(
        self,
        action: str,
        title: str = "",
        author: str = "",
        notes: str = "",
        status: str = "",
        priority: int = 0,
    ) -> dict:
        """
        管理书单
        action: add / list / mark_done / mark_reading / remove
        """
        if action == "add":
            if not title:
                return {"success": False, "error": "书名不能为空"}
            item = await self.storage.reading_list_add(title, author, notes, priority)
            return {"success": True, "action": "add", "item": item.to_dict()}

        elif action == "list":
            items = await self.storage.reading_list_get_all(status=status)
            return {
                "success": True,
                "action": "list",
                "items": [i.to_dict() for i in items],
                "total": len(items),
            }

        elif action in ("mark_done", "mark_reading", "mark_want"):
            new_status = {"mark_done": "done", "mark_reading": "reading", "mark_want": "want"}[action]
            if not title:
                return {"success": False, "error": "书名不能为空"}
            await self.storage.reading_list_update_status(title, new_status)
            return {"success": True, "action": action, "title": title, "status": new_status}

        elif action == "remove":
            if not title:
                return {"success": False, "error": "书名不能为空"}
            await self.storage.reading_list_remove(title)
            return {"success": True, "action": "remove", "title": title}

        else:
            return {"success": False, "error": f"未知操作: {action}"}
