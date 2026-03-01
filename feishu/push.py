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

    # ==================== 新卡片类型 ====================

    async def push_timer_alert(self, chat_id: str, message: str, minutes: int):
        """
        推送定时提醒卡片（黄色 ⏰）

        Args:
            chat_id: 会话 ID
            message: 提醒内容
            minutes: 设定的分钟数
        """
        now_str = datetime.now().strftime("%H:%M")
        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": f"⏰ 阅读提醒"},
                "template": "yellow",
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": f"**{message}**"},
                },
                {
                    "tag": "note",
                    "elements": [
                        {"tag": "plain_text", "content": f"定时 {minutes} 分钟 · {now_str} 触发"}
                    ],
                },
            ],
        }
        await self.bot.send_interactive_card(chat_id, card)
        logger.info(f"定时提醒已推送到飞书: {chat_id}")

    async def push_reading_card(
        self, chat_id: str, card_type: str, content: str, book_title: str = ""
    ):
        """
        推送阅读卡片（金句/知识点/摘要）

        Args:
            chat_id: 会话 ID
            card_type: quote / knowledge / summary
            content: 卡片内容
            book_title: 来源书名
        """
        type_cfg = {
            "quote":     ("💬 金句卡", "purple"),
            "knowledge": ("🧠 知识点卡", "green"),
            "summary":   ("📋 摘要卡", "blue"),
        }
        title, color = type_cfg.get(card_type, ("📖 阅读卡片", "blue"))
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

        elements = [
            {"tag": "div", "text": {"tag": "lark_md", "content": content}},
            {"tag": "hr"},
        ]
        if book_title:
            elements.append({
                "tag": "note",
                "elements": [{"tag": "plain_text", "content": f"来源：《{book_title}》· {now_str}"}],
            })
        else:
            elements.append({
                "tag": "note",
                "elements": [{"tag": "plain_text", "content": now_str}],
            })

        card = {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": title}, "template": color},
            "elements": elements,
        }
        await self.bot.send_interactive_card(chat_id, card)
        logger.info(f"阅读卡片（{card_type}）已推送到飞书: {chat_id}")

    async def push_bookmark_created(self, chat_id: str, bookmark, book_title: str = ""):
        """
        推送书签创建通知（橙色 🔖）

        Args:
            chat_id: 会话 ID
            bookmark: Bookmark 对象
            book_title: 书名（冗余，用于显示）
        """
        title = book_title or getattr(bookmark, "book_title", "未知书籍")
        page_hint = f"第 {bookmark.page_num} 页" if bookmark.page_num else ""
        note_hint = f"\n备注：{bookmark.note}" if bookmark.note else ""

        elements = [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**《{title}》{page_hint}**{note_hint}",
                },
            },
        ]
        if bookmark.page_ocr_excerpt:
            excerpt = bookmark.page_ocr_excerpt[:100]
            if len(bookmark.page_ocr_excerpt) > 100:
                excerpt += "..."
            elements.append({
                "tag": "div",
                "text": {"tag": "lark_md", "content": f"> {excerpt}"},
            })
        elements.append({
            "tag": "note",
            "elements": [{"tag": "plain_text", "content": bookmark.created_at_str}],
        })

        card = {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": "🔖 书签已创建"}, "template": "orange"},
            "elements": elements,
        }
        await self.bot.send_interactive_card(chat_id, card)
        logger.info(f"书签通知已推送到飞书: {chat_id}")
