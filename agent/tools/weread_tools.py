"""
微信读书相关工具：weread_shelf, weread_notebook, weread_get_notes,
weread_progress, weread_best_highlights, weread_merge_notes
"""
import asyncio
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class WeReadTools:
    def __init__(self, deps):
        self.deps = deps

    def _check_weread(self) -> Optional[Dict]:
        """检查微信读书是否可用，不可用时返回友好提示 dict"""
        if not self.deps.weread_client or not self.deps.weread_storage:
            return {
                "success": False,
                "error": "微信读书未配置，请在 config.json 中设置 weread.enabled=true 和 weread.cookie_string",
            }
        return None

    async def exec_weread_shelf(self, params: Dict) -> Dict:
        """查看/刷新书架"""
        err = self._check_weread()
        if err:
            return err

        refresh = params.get("refresh", False)

        if refresh:
            data = await self.deps.weread_client.get_shelf()
            if data is None:
                return {"success": False, "error": "微信读书 Cookie 已过期，请在 config.json 更新 weread.cookie_string"}

            books = data.get("books", [])
            progress_list = data.get("progress", [])

            await self.deps.weread_storage.upsert_books(books)
            for prog in progress_list:
                await self.deps.weread_storage.upsert_progress(prog)
        else:
            books = await self.deps.weread_storage.list_books()

        if not books:
            return {
                "success": True,
                "message": "书架为空，请先说'刷新微信书架'同步数据",
                "books": [],
                "total": 0,
            }

        book_list = [
            {
                "title": b.title,
                "author": b.author,
                "progress": b.progress_str,
                "reading_time": b.reading_time_str,
                "finished": b.finish_reading,
            }
            for b in books
        ]
        return {
            "success": True,
            "message": f"书架共 {len(book_list)} 本书",
            "books": book_list,
            "total": len(book_list),
            "refreshed": refresh,
        }

    async def exec_weread_notebook(self, params: Dict) -> Dict:
        """列出有笔记的书单"""
        err = self._check_weread()
        if err:
            return err

        raw_books = await self.deps.weread_client.get_notebook_books()
        if raw_books is None:
            return {"success": False, "error": "获取笔记书单失败，Cookie 可能已过期"}

        if not raw_books:
            return {"success": True, "message": "暂无有笔记的书籍", "books": [], "total": 0}

        book_list = []
        for item in raw_books:
            book_info = item.get("book") or item.get("bookInfo") or item
            title = book_info.get("title", "") if isinstance(book_info, dict) else ""
            if not title:
                continue
            book_list.append({
                "title": title,
                "highlights": item.get("noteCount", 0),
                "notes": item.get("reviewCount", 0),
            })

        return {
            "success": True,
            "message": f"共 {len(book_list)} 本书有笔记，可用 weread_get_notes 查看某本书的详细内容",
            "books": book_list,
            "total": len(book_list),
        }

    async def exec_weread_get_notes(self, params: Dict) -> Dict:
        """自动同步并分类展示某本书的全部笔记（划线/想法/点评）"""
        err = self._check_weread()
        if err:
            return err

        book_title = params.get("book_title", "").strip()
        if not book_title:
            return {"success": False, "error": "请指定书名"}

        limit = params.get("limit", 20)

        book = await self.deps.weread_storage.find_book_by_title(book_title)
        if not book:
            shelf_data = await self.deps.weread_client.get_shelf()
            if shelf_data:
                await self.deps.weread_storage.upsert_books(shelf_data.get("books", []))
                for prog in shelf_data.get("progress", []):
                    await self.deps.weread_storage.upsert_progress(prog)
            book = await self.deps.weread_storage.find_book_by_title(book_title)
            if not book:
                return {
                    "success": False,
                    "error": f"未找到《{book_title}》，请确认书名是否正确或书是否在书架中",
                }

        highlights_api = await self.deps.weread_client.get_highlights(book.book_id, book_title=book.title)
        if highlights_api is not None:
            await self.deps.weread_storage.upsert_highlights(highlights_api)

        notes_api = await self.deps.weread_client.get_notes(book.book_id)
        if notes_api:
            for n in notes_api:
                if not n.book_title:
                    n.book_title = book.title
            await self.deps.weread_storage.upsert_notes(notes_api)

        # 异步补全同步内容的 embedding（fire-and-forget）
        embedder = getattr(self.deps, "embedder", None)
        storage = getattr(self.deps, "storage", None)
        if embedder and storage:
            asyncio.create_task(self._embed_synced_content(book, storage, embedder))

        bookmarks_api = await self.deps.weread_client.get_bookmarks(book.book_id, book_title=book.title)

        highlights = await self.deps.weread_storage.list_highlights(book.book_id, limit=limit)
        thoughts = await self.deps.weread_storage.list_notes(book.book_id, limit=limit, note_type="想法")
        reviews = await self.deps.weread_storage.list_notes(book.book_id, limit=limit, note_type="点评")

        return {
            "success": True,
            "book_title": book.title,
            "message": (
                f"《{book.title}》：划线 {len(highlights)} 条、书签 {len(bookmarks_api)} 个、"
                f"想法 {len(thoughts)} 条、点评 {len(reviews)} 条"
            ),
            "highlights": [
                {"chapter": h.chapter_title, "content": h.content, "time": h.created_at_str}
                for h in highlights
            ],
            "bookmarks": [
                {"chapter": b.chapter_title, "content": b.content, "time": b.created_at_str}
                for b in bookmarks_api
            ],
            "thoughts": [
                {
                    "chapter": n.chapter_title,
                    "abstract": n.abstract,
                    "content": n.content,
                    "time": n.created_at_str,
                }
                for n in thoughts
            ],
            "reviews": [
                {"chapter": n.chapter_title, "content": n.content, "time": n.created_at_str}
                for n in reviews
            ],
            "highlights_count": len(highlights),
            "bookmarks_count": len(bookmarks_api),
            "thoughts_count": len(thoughts),
            "reviews_count": len(reviews),
        }

    async def _embed_synced_content(self, book, storage, embedder) -> None:
        """为刚同步的微信读书内容补写 embedding（fire-and-forget）"""
        total = 0
        for table in ("weread_highlights", "weread_notes"):
            try:
                rows = await storage.get_rows_missing_embedding(
                    table, limit=200, book_id=book.book_id
                )
                for row in rows:
                    try:
                        content = row.get("content", "")
                        book_title = row.get("book_title", "")
                        text = f"{book_title} {content}".strip()
                        if not text:
                            continue
                        embedding = await embedder.embed(text)
                        if embedding:
                            await storage.save_embedding(table, row["id"], embedding)
                            total += 1
                        await asyncio.sleep(0.05)
                    except Exception as e:
                        logger.debug(f"_embed_synced_content {table}#{row.get('id')}: {e}")
            except Exception as e:
                logger.warning(f"_embed_synced_content 表 {table} 失败: {e}")
        if total:
            logger.info(f"微信读书 embedding 补全完成（{book.title}）：{total} 条")

    async def exec_weread_progress(self, params: Dict) -> Dict:
        """查询实时阅读进度"""
        err = self._check_weread()
        if err:
            return err

        book_title = params.get("book_title", "").strip()
        if not book_title:
            return {"success": False, "error": "请指定书名"}

        book = await self.deps.weread_storage.find_book_by_title(book_title)
        if not book:
            return {
                "success": False,
                "error": f"本地未找到《{book_title}》，请先说'刷新微信书架'",
            }

        progress = await self.deps.weread_client.get_progress(book.book_id)
        if not progress:
            return {"success": False, "error": f"获取《{book_title}》进度失败，Cookie 可能已过期"}

        progress.book_title = book.title
        await self.deps.weread_storage.upsert_progress(progress)

        return {
            "success": True,
            "message": (
                f"《{book.title}》已读 {progress.progress_str}，"
                f"阅读时长 {progress.reading_time_str}"
            ),
            "book_title": book.title,
            "progress": progress.progress,
            "progress_str": progress.progress_str,
            "reading_time_str": progress.reading_time_str,
            "chapter_uid": progress.chapter_uid,
        }

    async def exec_weread_best_highlights(self, params: Dict) -> Dict:
        """获取热门划线"""
        err = self._check_weread()
        if err:
            return err

        book_title = params.get("book_title", "").strip()
        if not book_title:
            return {"success": False, "error": "请指定书名"}

        book = await self.deps.weread_storage.find_book_by_title(book_title)
        if not book:
            shelf_data = await self.deps.weread_client.get_shelf()
            if shelf_data:
                await self.deps.weread_storage.upsert_books(shelf_data.get("books", []))
                for prog in shelf_data.get("progress", []):
                    await self.deps.weread_storage.upsert_progress(prog)
            book = await self.deps.weread_storage.find_book_by_title(book_title)
            if not book:
                return {"success": False, "error": f"未找到《{book_title}》，请确认书名或先同步书架"}

        best = await self.deps.weread_client.get_best_highlights(book.book_id)
        if not best:
            return {
                "success": True,
                "message": f"《{book.title}》暂无热门划线（接口可能不支持此书）",
                "highlights": [],
                "total": 0,
            }

        return {
            "success": True,
            "book_title": book.title,
            "message": f"《{book.title}》共 {len(best)} 条热门划线",
            "highlights": [
                {"chapter": h.chapter_title, "content": h.content}
                for h in best
            ],
            "total": len(best),
        }

    async def exec_weread_merge_notes(self, params: Dict) -> Dict:
        """合并微信读书划线+想法与本地笔记"""
        err = self._check_weread()
        if err:
            return err

        book_title = params.get("book_title", "").strip()
        if not book_title:
            return {"success": False, "error": "请指定书名"}

        push_to_feishu = params.get("push_to_feishu", False)

        book = await self.deps.weread_storage.find_book_by_title(book_title)
        if not book:
            return {"success": False, "error": f"本地未找到《{book_title}》，请先同步书架"}

        highlights = await self.deps.weread_storage.list_highlights(book.book_id, limit=30)
        wr_notes = await self.deps.weread_storage.list_notes(book.book_id, limit=20)
        local_notes = await self.deps.session_manager.get_recent_notes(days=30)
        local_notes = [n for n in local_notes if book_title in (n.book_name or "")]

        sections = []
        if highlights:
            hl_text = "\n".join(f"- {h.content}" for h in highlights[:20])
            sections.append(f"【微信读书划线 {len(highlights)} 条】\n{hl_text}")
        if wr_notes:
            thoughts = [n for n in wr_notes if n.note_type == "想法"]
            reviews = [n for n in wr_notes if n.note_type == "点评"]
            if thoughts:
                thought_text = "\n".join(
                    f"- [原文] {n.abstract}\n  [想法] {n.content}" if n.abstract else f"- {n.content}"
                    for n in thoughts[:10]
                )
                sections.append(f"【微信读书想法 {len(thoughts)} 条】\n{thought_text}")
            if reviews:
                review_text = "\n".join(f"- {n.content}" for n in reviews[:10])
                sections.append(f"【微信读书点评 {len(reviews)} 条】\n{review_text}")
        if local_notes:
            local_text = "\n".join(f"- {n.content}" for n in local_notes[:10])
            sections.append(f"【本地笔记 {len(local_notes)} 条】\n{local_text}")

        if not sections:
            return {"success": True, "message": f"《{book.title}》暂无划线、笔记数据", "summary": ""}

        combined = "\n\n".join(sections)
        summary = combined

        pushed = False
        if push_to_feishu and self.deps.feishu_pusher and self.deps.feishu_chat_id:
            try:
                msg = f"📚《{book.title}》笔记合集\n\n{summary[:2000]}"
                await self.deps.feishu_pusher.push_text(self.deps.feishu_chat_id, msg)
                pushed = True
            except Exception as e:
                logger.error(f"飞书推送失败: {e}")

        return {
            "success": True,
            "message": f"《{book.title}》笔记合并完成" + ("，已推送飞书" if pushed else ""),
            "book_title": book.title,
            "highlights_count": len(highlights),
            "thoughts_count": len([n for n in wr_notes if n.note_type == "想法"]),
            "reviews_count": len([n for n in wr_notes if n.note_type == "点评"]),
            "local_notes_count": len(local_notes),
            "summary": summary,
            "feishu_pushed": pushed,
        }
