"""
数据存储层
使用 aiosqlite 实现异步 SQLite 操作
"""
import json
import logging
import aiosqlite
from datetime import datetime, timedelta
from typing import Optional, List
from pathlib import Path

from .models import ReadingSession, PageSnapshot, Note, DailySummary

logger = logging.getLogger(__name__)


class Storage:
    """
    SQLite 异步存储
    """
    
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None
        
    async def initialize(self):
        """初始化数据库连接和表结构"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        self._conn = await aiosqlite.connect(str(self.db_path))
        self._conn.row_factory = aiosqlite.Row
        
        await self._create_tables()
        logger.info(f"数据库已初始化: {self.db_path}")
        
    async def close(self):
        """关闭数据库连接"""
        if self._conn:
            await self._conn.close()
            self._conn = None
            
    async def _create_tables(self):
        """创建表结构"""
        await self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                book_name TEXT DEFAULT '',
                start_at INTEGER NOT NULL,
                end_at INTEGER,
                camera_device INTEGER DEFAULT 0,
                total_pages INTEGER DEFAULT 0,
                total_snapshots INTEGER DEFAULT 0
            );
            
            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                ts INTEGER NOT NULL,
                image_path TEXT NOT NULL,
                ocr_text TEXT DEFAULT '',
                fingerprint TEXT DEFAULT '',
                dwell_ms INTEGER DEFAULT 0,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );
            
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                ts INTEGER NOT NULL,
                content TEXT NOT NULL,
                page_ocr_context TEXT DEFAULT '',
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );
            
            CREATE INDEX IF NOT EXISTS idx_snapshots_session ON snapshots(session_id);
            CREATE INDEX IF NOT EXISTS idx_notes_session ON notes(session_id);
            CREATE INDEX IF NOT EXISTS idx_sessions_start ON sessions(start_at);
        """)
        await self._conn.commit()
    
    # ==================== Sessions ====================
    
    async def create_session(self, session: ReadingSession) -> bool:
        """创建会话"""
        try:
            await self._conn.execute(
                """INSERT INTO sessions (id, book_name, start_at, camera_device)
                   VALUES (?, ?, ?, ?)""",
                (session.id, session.book_name, session.start_at, session.camera_device)
            )
            await self._conn.commit()
            return True
        except Exception as e:
            logger.error(f"创建会话失败: {e}")
            return False
    
    async def end_session(self, session_id: str, end_at: int) -> bool:
        """结束会话"""
        try:
            # 更新统计信息
            await self._conn.execute(
                """UPDATE sessions 
                   SET end_at = ?,
                       total_snapshots = (SELECT COUNT(*) FROM snapshots WHERE session_id = ?),
                       total_pages = (SELECT COUNT(DISTINCT fingerprint) FROM snapshots WHERE session_id = ?)
                   WHERE id = ?""",
                (end_at, session_id, session_id, session_id)
            )
            await self._conn.commit()
            return True
        except Exception as e:
            logger.error(f"结束会话失败: {e}")
            return False
    
    async def get_session(self, session_id: str) -> Optional[ReadingSession]:
        """获取会话"""
        async with self._conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return ReadingSession(
                    id=row['id'],
                    book_name=row['book_name'],
                    start_at=row['start_at'],
                    end_at=row['end_at'],
                    camera_device=row['camera_device'],
                    total_pages=row['total_pages'],
                    total_snapshots=row['total_snapshots']
                )
            return None
    
    async def list_sessions(self, limit: int = 10, offset: int = 0) -> List[ReadingSession]:
        """列出会话"""
        sessions = []
        async with self._conn.execute(
            "SELECT * FROM sessions ORDER BY start_at DESC LIMIT ? OFFSET ?",
            (limit, offset)
        ) as cursor:
            async for row in cursor:
                sessions.append(ReadingSession(
                    id=row['id'],
                    book_name=row['book_name'],
                    start_at=row['start_at'],
                    end_at=row['end_at'],
                    camera_device=row['camera_device'],
                    total_pages=row['total_pages'],
                    total_snapshots=row['total_snapshots']
                ))
        return sessions
    
    async def get_today_sessions(self) -> List[ReadingSession]:
        """获取今日会话"""
        today_start = int(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
        sessions = []
        async with self._conn.execute(
            "SELECT * FROM sessions WHERE start_at >= ? ORDER BY start_at DESC",
            (today_start,)
        ) as cursor:
            async for row in cursor:
                sessions.append(ReadingSession(
                    id=row['id'],
                    book_name=row['book_name'],
                    start_at=row['start_at'],
                    end_at=row['end_at'],
                    camera_device=row['camera_device'],
                    total_pages=row['total_pages'],
                    total_snapshots=row['total_snapshots']
                ))
        return sessions
    
    # ==================== Snapshots ====================
    
    async def add_snapshot(self, snapshot: PageSnapshot) -> int:
        """添加快照，返回 ID"""
        cursor = await self._conn.execute(
            """INSERT INTO snapshots (session_id, ts, image_path, ocr_text, fingerprint)
               VALUES (?, ?, ?, ?, ?)""",
            (snapshot.session_id, snapshot.ts, snapshot.image_path, 
             snapshot.ocr_text, snapshot.fingerprint)
        )
        await self._conn.commit()
        return cursor.lastrowid
    
    async def update_snapshot_dwell(self, snapshot_id: int, dwell_ms: int):
        """更新快照停留时长"""
        await self._conn.execute(
            "UPDATE snapshots SET dwell_ms = ? WHERE id = ?",
            (dwell_ms, snapshot_id)
        )
        await self._conn.commit()
    
    async def get_last_snapshot(self, session_id: str) -> Optional[PageSnapshot]:
        """获取会话的最新快照"""
        async with self._conn.execute(
            "SELECT * FROM snapshots WHERE session_id = ? ORDER BY ts DESC LIMIT 1",
            (session_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return PageSnapshot(
                    id=row['id'],
                    session_id=row['session_id'],
                    ts=row['ts'],
                    image_path=row['image_path'],
                    ocr_text=row['ocr_text'],
                    fingerprint=row['fingerprint'],
                    dwell_ms=row['dwell_ms']
                )
            return None
    
    async def get_session_snapshots(self, session_id: str) -> List[PageSnapshot]:
        """获取会话的所有快照"""
        snapshots = []
        async with self._conn.execute(
            "SELECT * FROM snapshots WHERE session_id = ? ORDER BY ts ASC",
            (session_id,)
        ) as cursor:
            async for row in cursor:
                snapshots.append(PageSnapshot(
                    id=row['id'],
                    session_id=row['session_id'],
                    ts=row['ts'],
                    image_path=row['image_path'],
                    ocr_text=row['ocr_text'],
                    fingerprint=row['fingerprint'],
                    dwell_ms=row['dwell_ms']
                ))
        return snapshots
    
    # ==================== Notes ====================
    
    async def add_note(self, note: Note) -> int:
        """添加笔记，返回 ID"""
        cursor = await self._conn.execute(
            """INSERT INTO notes (session_id, ts, content, page_ocr_context)
               VALUES (?, ?, ?, ?)""",
            (note.session_id, note.ts, note.content, note.page_ocr_context)
        )
        await self._conn.commit()
        return cursor.lastrowid
    
    async def get_session_notes(self, session_id: str, limit: int = 100) -> List[Note]:
        """获取会话的笔记"""
        notes = []
        async with self._conn.execute(
            "SELECT * FROM notes WHERE session_id = ? ORDER BY ts ASC LIMIT ?",
            (session_id, limit)
        ) as cursor:
            async for row in cursor:
                notes.append(Note(
                    id=row['id'],
                    session_id=row['session_id'],
                    ts=row['ts'],
                    content=row['content'],
                    page_ocr_context=row['page_ocr_context']
                ))
        return notes
    
    async def get_today_notes(self, limit: int = 100) -> List[Note]:
        """获取今日笔记"""
        today_start = int(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
        notes = []
        async with self._conn.execute(
            """SELECT n.* FROM notes n
               JOIN sessions s ON n.session_id = s.id
               WHERE s.start_at >= ?
               ORDER BY n.ts ASC LIMIT ?""",
            (today_start, limit)
        ) as cursor:
            async for row in cursor:
                notes.append(Note(
                    id=row['id'],
                    session_id=row['session_id'],
                    ts=row['ts'],
                    content=row['content'],
                    page_ocr_context=row['page_ocr_context']
                ))
        return notes
    
    # ==================== Statistics ====================
    
    async def get_daily_summary(self, date: Optional[datetime] = None) -> DailySummary:
        """获取每日阅读摘要"""
        if date is None:
            date = datetime.now()
        
        date_str = date.strftime("%Y-%m-%d")
        day_start = int(date.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
        day_end = int((date + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
        
        summary = DailySummary(date=date_str)
        
        # 获取今日会话
        sessions = []
        longest_duration = 0
        
        async with self._conn.execute(
            "SELECT * FROM sessions WHERE start_at >= ? AND start_at < ? ORDER BY start_at DESC",
            (day_start, day_end)
        ) as cursor:
            async for row in cursor:
                session = ReadingSession(
                    id=row['id'],
                    book_name=row['book_name'],
                    start_at=row['start_at'],
                    end_at=row['end_at'],
                    camera_device=row['camera_device'],
                    total_pages=row['total_pages'],
                    total_snapshots=row['total_snapshots']
                )
                sessions.append(session)
                
                # 统计
                summary.total_sessions += 1
                summary.total_duration_ms += session.duration_ms
                summary.total_pages += session.total_pages
                
                if session.book_name and session.book_name not in summary.book_names:
                    summary.book_names.append(session.book_name)
                
                # 最长会话
                if session.duration_ms > longest_duration:
                    longest_duration = session.duration_ms
                    summary.longest_session = session
        
        # 统计笔记数
        async with self._conn.execute(
            """SELECT COUNT(*) as count FROM notes n
               JOIN sessions s ON n.session_id = s.id
               WHERE s.start_at >= ? AND s.start_at < ?""",
            (day_start, day_end)
        ) as cursor:
            row = await cursor.fetchone()
            summary.total_notes = row['count'] if row else 0
        
        return summary
