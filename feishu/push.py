"""
阅读总结推送
构建飞书交互卡片
"""
import logging
from typing import Optional, List
from datetime import datetime

from session.models import ReadingSession, Note, DailySummary

logger = logging.getLogger(__name__)


class SummaryPusher:
    """
    推送阅读总结卡片到飞书
    """
    
    def __init__(self, bot):
        self.bot = bot
    
    def build_summary_card(self, summary: DailySummary, 
                          notes: List[Note],
                          user_id: Optional[str] = None) -> dict:
        """
        构建阅读总结卡片
        
        Args:
            summary: 每日摘要
            notes: 笔记列表
            user_id: 用户 ID（可选，用于私聊推送）
            
        Returns:
            飞书卡片 JSON
        """
        # 标题
        header = {
            "title": {
                "tag": "plain_text",
                "content": f"📚 今日阅读总结 · {summary.date}"
            },
            "template": "blue"
        }
        
        elements = []
        
        # 统计信息
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**⏱ 阅读时长：** {summary.duration_str}"
            }
        })
        
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**📖 拍摄页数：** {summary.total_pages} 页（{summary.total_sessions} 个会话）"
            }
        })
        
        if summary.book_names:
            books_str = ", ".join(summary.book_names[:5])
            elements.append({
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**📚 阅读书目：** {books_str}"
                }
            })
        
        # 分隔线
        elements.append({"tag": "hr"})
        
        # 笔记部分
        if notes:
            elements.append({
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**📝 笔记精选（共 {len(notes)} 条）**"
                }
            })
            
            # 显示前 3 条笔记
            for i, note in enumerate(notes[:3], 1):
                note_text = note.content[:100]
                if len(note.content) > 100:
                    note_text += "..."
                
                elements.append({
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"{i}. {note_text}"
                    }
                })
        else:
            elements.append({
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": "今天还没有记录笔记"
                }
            })
        
        # 底部提示
        elements.append({"tag": "hr"})
        elements.append({
            "tag": "note",
            "elements": [
                {
                    "tag": "plain_text",
                    "content": "💡 回复'今天读了什么'可查看详细信息"
                }
            ]
        })
        
        card = {
            "config": {"wide_screen_mode": True},
            "header": header,
            "elements": elements
        }
        
        return card
    
    async def push_daily_summary(self, chat_id: str, 
                                  summary: DailySummary,
                                  notes: List[Note]):
        """
        推送每日总结到飞书
        
        Args:
            chat_id: 会话 ID
            summary: 每日摘要
            notes: 笔记列表
        """
        card = self.build_summary_card(summary, notes)
        await self.bot.send_interactive_card(chat_id, card)
        logger.info(f"每日总结已推送到飞书: {chat_id}")
    
    async def push_session_end_summary(self, chat_id: str,
                                        session: ReadingSession,
                                        notes: List[Note]):
        """
        推送会话结束总结
        
        Args:
            chat_id: 会话 ID
            session: 阅读会话
            notes: 本次会话的笔记
        """
        header = {
            "title": {
                "tag": "plain_text",
                "content": f"✅ 阅读完成 · {session.book_name or '未命名书籍'}"
            },
            "template": "green"
        }
        
        elements = [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**⏱ 阅读时长：** {session.duration_str}"
                }
            },
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**📖 阅读页数：** {session.total_pages} 页"
                }
            },
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**📸 拍摄快照：** {session.total_snapshots} 张"
                }
            }
        ]
        
        if notes:
            elements.append({"tag": "hr"})
            elements.append({
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**📝 本次笔记（{len(notes)} 条）**"
                }
            })
            for i, note in enumerate(notes[:3], 1):
                note_text = note.content[:80]
                if len(note.content) > 80:
                    note_text += "..."
                elements.append({
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"{i}. {note_text}"
                    }
                })
        
        card = {
            "config": {"wide_screen_mode": True},
            "header": header,
            "elements": elements
        }
        
        await self.bot.send_interactive_card(chat_id, card)
        logger.info(f"会话总结已推送到飞书: {chat_id}")
