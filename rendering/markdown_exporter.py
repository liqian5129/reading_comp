"""
Markdown 导出器
将阅读笔记、金句、摘要导出为格式化的 Markdown 文件
支持多种模板：读书笔记、每日摘要、金句集锦
"""
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


class MarkdownExporter:
    """Markdown 文件导出器"""

    def __init__(self, output_dir: str = "./data/markdown"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def export_reading_notes(
        self,
        notes: List[dict],
        book_title: str = "",
        author: str = "",
        filename: Optional[str] = None,
    ) -> str:
        """
        导出读书笔记 Markdown。

        Args:
            notes: [{"content": "...", "tags": [...], "created_at": "...", "book_name": "..."}]
            book_title: 书名
            author: 作者
            filename: 文件名（不含扩展名）
        """
        lines = []
        title = f"《{book_title}》读书笔记" if book_title else "读书笔记"
        lines.append(f"# {title}")
        lines.append("")

        if author:
            lines.append(f"**作者**: {author}")
            lines.append("")

        lines.append(f"**导出时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        lines.append("")
        lines.append("---")
        lines.append("")

        # 按书名分组
        grouped = {}
        for note in notes:
            book = note.get("book_name", book_title or "未分类")
            grouped.setdefault(book, []).append(note)

        for book, book_notes in grouped.items():
            if len(grouped) > 1:
                lines.append(f"## 《{book}》")
                lines.append("")

            for i, note in enumerate(book_notes, 1):
                content = note.get("content", "")
                tags = note.get("tags", [])
                created = note.get("created_at", "")

                lines.append(f"### {i}. ")
                lines.append("")

                # 引用格式展示内容
                for para in content.split("\n"):
                    if para.strip():
                        lines.append(f"> {para.strip()}")
                lines.append("")

                # 元信息
                meta_parts = []
                if tags:
                    meta_parts.append(" ".join(f"`{t}`" for t in tags))
                if created:
                    meta_parts.append(f"*{created}*")
                if meta_parts:
                    lines.append(" | ".join(meta_parts))
                    lines.append("")

        md_text = "\n".join(lines)
        fname = filename or f"notes_{book_title or 'all'}_{datetime.now().strftime('%Y%m%d')}"
        # 清理文件名中的特殊字符
        fname = "".join(c if c.isalnum() or c in "-_" else "_" for c in fname)
        filepath = self.output_dir / f"{fname}.md"
        filepath.write_text(md_text, encoding="utf-8")
        logger.info(f"笔记 Markdown 已导出: {filepath}")
        return str(filepath)

    def export_daily_summary(
        self,
        summary_text: str,
        highlights: Optional[List[str]] = None,
        notes: Optional[List[dict]] = None,
        stats: Optional[dict] = None,
        date: Optional[datetime] = None,
        filename: Optional[str] = None,
    ) -> str:
        """
        导出每日阅读摘要 Markdown。

        Args:
            summary_text: AI 生成的摘要
            highlights: 金句列表
            notes: 笔记列表
            stats: 统计数据 {"pages": N, "duration": "Xh", "books": [...]}
            date: 日期
            filename: 文件名
        """
        dt = date or datetime.now()
        lines = []
        lines.append(f"# 阅读日记 | {dt.strftime('%Y年%m月%d日')}")
        lines.append("")

        # 统计概览
        if stats:
            lines.append("## 今日数据")
            lines.append("")
            if stats.get("pages"):
                lines.append(f"- 翻页数: **{stats['pages']}** 页")
            if stats.get("duration"):
                lines.append(f"- 阅读时长: **{stats['duration']}**")
            if stats.get("books"):
                books_str = "、".join(f"《{b}》" for b in stats["books"])
                lines.append(f"- 在读书目: {books_str}")
            if stats.get("notes_count"):
                lines.append(f"- 笔记数: **{stats['notes_count']}** 条")
            lines.append("")

        # 摘要
        if summary_text:
            lines.append("## 阅读摘要")
            lines.append("")
            lines.append(summary_text)
            lines.append("")

        # 金句
        if highlights:
            lines.append("## 金句摘录")
            lines.append("")
            for h in highlights:
                lines.append(f"> {h}")
                lines.append("")

        # 笔记
        if notes:
            lines.append("## 读书笔记")
            lines.append("")
            for note in notes:
                content = note.get("content", "")
                book = note.get("book_name", "")
                lines.append(f"- {content}")
                if book:
                    lines.append(f"  *—— 《{book}》*")
                lines.append("")

        lines.append("---")
        lines.append(f"*Generated at {dt.strftime('%H:%M')}*")

        md_text = "\n".join(lines)
        fname = filename or f"daily_{dt.strftime('%Y%m%d')}"
        filepath = self.output_dir / f"{fname}.md"
        filepath.write_text(md_text, encoding="utf-8")
        logger.info(f"每日摘要 Markdown 已导出: {filepath}")
        return str(filepath)

    def export_quote_collection(
        self,
        quotes: List[dict],
        title: str = "金句集锦",
        filename: Optional[str] = None,
    ) -> str:
        """
        导出金句集锦 Markdown。

        Args:
            quotes: [{"text": "...", "book": "...", "author": "...", "tags": [...]}]
            title: 标题
            filename: 文件名
        """
        lines = []
        lines.append(f"# {title}")
        lines.append("")
        lines.append(f"**收录**: {len(quotes)} 条 | **导出时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        lines.append("")
        lines.append("---")
        lines.append("")

        # 按书分组
        by_book = {}
        for q in quotes:
            book = q.get("book", "其他")
            by_book.setdefault(book, []).append(q)

        for book, book_quotes in by_book.items():
            if book:
                lines.append(f"## 《{book}》")
                lines.append("")

            for q in book_quotes:
                text = q.get("text", "")
                author = q.get("author", "")
                tags = q.get("tags", [])

                lines.append(f"> {text}")
                if author:
                    lines.append(f"> —— {author}")
                lines.append("")
                if tags:
                    lines.append(" ".join(f"`{t}`" for t in tags))
                    lines.append("")

        md_text = "\n".join(lines)
        fname = filename or f"quotes_{datetime.now().strftime('%Y%m%d')}"
        filepath = self.output_dir / f"{fname}.md"
        filepath.write_text(md_text, encoding="utf-8")
        logger.info(f"金句集锦 Markdown 已导出: {filepath}")
        return str(filepath)
