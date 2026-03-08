"""
笔记相关工具：reading_note, reading_history, reading_notes, note_search
"""
import asyncio
import logging
from typing import Dict

logger = logging.getLogger(__name__)


class NoteTools:
    def __init__(self, deps):
        self.deps = deps

    async def exec_reading_note(self, params: Dict) -> Dict:
        """记录笔记"""
        content = params.get("content", "")
        if not content:
            return {"success": False, "error": "笔记内容不能为空"}

        book_name = params.get("book_name", "")
        tags = params.get("tags") or []
        user_comment = params.get("user_comment", "").strip()
        page_context = self.deps.memory.current_page_ocr
        image_path = ""
        if params.get("save_image"):
            image_path = getattr(self.deps.memory, "current_page_image", "") or ""

        note = await self.deps.session_manager.add_note(
            content=content,
            page_context=page_context,
            book_name=book_name,
            tags=tags,
            image_path=image_path,
            user_comment=user_comment,
        )

        book_hint = f"《{note.book_name}》" if note.book_name else ""
        tag_hint = f"，标签：{', '.join(note.tags)}" if note.tags else ""
        book_note_count = await self.deps.session_manager.count_notes_by_book(note.book_name)
        count_scope = f"{book_hint}第 {book_note_count} 条" if note.book_name else f"第 {book_note_count} 条"

        # 异步 embedding（fire-and-forget）
        if self.deps.embedder and self.deps.storage and note.id:
            embed_text = f"{note.book_name} {' '.join(note.tags)} {note.content}"
            asyncio.create_task(self._embed_note(note.id, embed_text))

        # 知识链接（fire-and-forget，step 4）
        knowledge_linker = getattr(self.deps, "knowledge_linker", None)
        if knowledge_linker and note.id:
            asyncio.create_task(knowledge_linker.link_note(note))

        return {
            "success": True,
            "message": f"笔记已记录（{count_scope}）{tag_hint}",
            "note_id": note.id,
            "book_note_count": book_note_count,
        }

    async def _embed_note(self, note_id: int, text: str) -> None:
        """Fire-and-forget：为 notes 表的一条记录写入 embedding"""
        try:
            embedding = await self.deps.embedder.embed(text)
            if embedding:
                await self.deps.storage.save_embedding("notes", note_id, embedding)
                logger.info(f"笔记 #{note_id} embedding 已写入（维度 {len(embedding)}）")
        except Exception as e:
            logger.warning(f"_embed_note 失败（已降级）: {e}")

    async def exec_reading_history(self, params: Dict) -> Dict:
        """查询阅读历史（基于 OCR 事件流统计）"""
        days = max(0, int(params.get("days", 1) or 1))

        stats = await self.deps.session_manager.get_reading_stats(days=days)

        result = {
            "success": True,
            "total_pages": stats.get("total_pages", 0),
            "total_duration": stats.get("duration_str", "0 分钟"),
            "note_count": stats.get("note_count", 0),
            "bookmark_count": stats.get("bookmark_count", 0),
            "books_read": stats.get("books_read", []),
            "ocr_events": stats.get("ocr_events", 0),
        }

        period_label = "全部时间" if days == 0 else ("今天" if days == 1 else f"近{days}天")
        if stats.get("total_pages", 0) == 0 and stats.get("ocr_events", 0) == 0:
            result["message"] = f"{period_label}还没有阅读记录"
        else:
            books_str = "、".join(f"《{b}》" for b in stats.get("books_read", []) if b) or "未知书籍"
            result["message"] = (
                f"{period_label}阅读了 {books_str}，"
                f"翻页 {stats['total_pages']} 页，"
                f"阅读 {stats['duration_str']}，"
                f"笔记 {stats['note_count']} 条"
            )

        # 附加历史阅读摘要
        storage = getattr(self.deps, "storage", None)
        if storage:
            try:
                recent_digests = await storage.get_recent_digests(days=days)
                if recent_digests:
                    from datetime import datetime
                    result["reading_digests"] = [
                        {
                            "book": d["book_title"],
                            "summary": d["digest_text"][:200],
                            "date": datetime.fromtimestamp(d["created_at"] / 1000).strftime("%m-%d %H:%M"),
                        }
                        for d in recent_digests[:5]
                    ]
            except Exception as e:
                logger.debug(f"reading_history: 获取历史摘要失败（已降级）: {e}")

        return result

    async def exec_reading_notes(self, params: Dict) -> Dict:
        """查询笔记内容列表"""
        days = params.get("days", 7)
        book_filter = params.get("book_name", "").strip()

        notes = await self.deps.session_manager.get_recent_notes(days=days)

        if book_filter:
            notes = [n for n in notes if book_filter in (n.book_name or "")]

        if not notes:
            scope = f"《{book_filter}》" if book_filter else f"最近 {days} 天"
            return {"success": True, "message": f"{scope}暂无读书笔记", "notes": [], "total": 0}

        note_list = []
        for n in notes:
            note_list.append({
                "id": n.id,
                "datetime": n.created_at_str,
                "book_name": n.book_name or "",
                "tags": n.tags,
                "content": n.content,
            })

        # 仅当用户明确要求发截图时才推送
        if params.get("send_images"):
            feishu_pusher = getattr(self.deps, "feishu_pusher", None)
            feishu_chat_id = getattr(self.deps, "feishu_chat_id", None)
            if feishu_pusher and feishu_chat_id:
                notes_with_image = [n for n in notes if n.image_path]
                if notes_with_image:
                    asyncio.create_task(
                        feishu_pusher.push_notes_images(feishu_chat_id, notes_with_image)
                    )

        scope = f"《{book_filter}》" if book_filter else f"最近 {days} 天"
        return {
            "success": True,
            "message": f"{scope}共有 {len(note_list)} 条读书笔记",
            "notes": note_list,
            "total": len(note_list),
        }

    async def exec_note_search(self, params: Dict) -> Dict:
        """语义向量检索笔记"""
        query = params.get("query", "").strip()
        if not query:
            return {"success": False, "error": "请指定搜索关键词"}

        source = params.get("source", "all")

        if not self.deps.embedder or not self.deps.storage:
            return {
                "success": False,
                "error": "向量检索未启用（embedding 未配置），请改用 reading_notes 工具按时间查询",
            }

        table_map = {
            "notes": ["notes"],
            "weread": ["weread_highlights", "weread_notes"],
            "all": ["notes", "weread_highlights", "weread_notes"],
        }
        tables = table_map.get(source, ["notes", "weread_highlights", "weread_notes"])

        embedding = await self.deps.embedder.embed(query)
        if not embedding:
            return {
                "success": False,
                "error": "向量检索暂时不可用（embedding API 超时），请改用 reading_notes 工具",
            }

        results = await self.deps.storage.search_by_embedding(embedding, tables=tables, top_k=5)
        if not results:
            return {
                "success": True,
                "message": f"未找到与「{query}」相关的笔记记录（可能还没有内容被索引）",
                "results": [],
                "total": 0,
            }

        source_map = {
            "notes": "本地笔记",
            "weread_highlights": "微信划线",
            "weread_notes": "微信想法",
        }
        result_list = [
            {
                "source": source_map.get(r.source, r.source),
                "book_name": r.book_name,
                "content": r.content[:200],
                "score": round(r.score, 3),
            }
            for r in results
        ]

        return {
            "success": True,
            "message": f"找到 {len(result_list)} 条与「{query}」相关的记录",
            "results": result_list,
            "total": len(result_list),
        }
