"""
分享相关工具：generate_reading_card, feishu_send_message
"""
import logging
from typing import Dict

logger = logging.getLogger(__name__)


class ShareTools:
    def __init__(self, deps):
        self.deps = deps

    async def exec_generate_reading_card(self, params: Dict) -> Dict:
        """生成阅读卡片并推送飞书"""
        card_type = params.get("card_type", "quote")
        days = max(1, int(params.get("days", 1) or 1))
        content = params.get("content", "").strip()
        book_title = params.get("book_title", "").strip()

        # summary 类型：用 digest + notes 生成摘要
        if not content and card_type == "summary":
            storage = getattr(self.deps, "storage", None)

            # 若 buffer 未压缩（今日读了但不足5页），先强制压缩一次
            buffer = getattr(self.deps.memory, "_new_pages_buffer", [])
            if buffer and self.deps.llm:
                import asyncio as _asyncio
                new_pages = "\n".join(buffer)
                current = getattr(self.deps.memory, "session_reading_digest", "")
                prompt = (
                    f"请将以下阅读进度压缩为300字以内的摘要，保留内容脉络和重要概念，"
                    f"合并到已有摘要中，直接输出新摘要。\n\n已有摘要：{current}\n\n新增内容：{new_pages}"
                ) if current else (
                    f"请将以下阅读内容压缩为300字以内的摘要，保留内容脉络和重要概念，直接输出摘要。\n\n{new_pages}"
                )
                try:
                    resp = await _asyncio.wait_for(
                        self.deps.llm.chat(user_message=prompt, max_tokens=400),
                        timeout=10.0,
                    )
                    if resp and resp.text:
                        self.deps.memory.session_reading_digest = resp.text.strip()
                        self.deps.memory._new_pages_buffer = []
                        if storage:
                            await storage.save_reading_digest(
                                self.deps.memory.session_reading_digest,
                                book_title=self.deps.memory.current_book_context.get("book_title", ""),
                            )
                        logger.info("summary 前强制压缩 buffer 完成")
                except Exception as e:
                    logger.warning(f"强制压缩 digest 失败: {e}")

            # 取时间窗口内所有 digest（可能跨会话有多条）
            digests_text = ""
            if storage:
                try:
                    digests = await storage.get_recent_digests(days=days)
                    if digests:
                        # 多条 digest 按时间正序拼接（最近的在后）
                        digests_text = "\n\n".join(
                            d["digest_text"] for d in reversed(digests)
                        )
                except Exception as e:
                    logger.warning(f"加载阅读摘要失败: {e}")
            if not digests_text:
                digests_text = getattr(self.deps.memory, "session_reading_digest", "")

            # 取时间窗口内的笔记
            notes_text = ""
            sm = getattr(self.deps, "session_manager", None)
            if sm:
                try:
                    notes = await sm.get_recent_notes(days=days, limit=50)
                    if notes:
                        notes_text = "\n".join(f"- {n.content}" for n in notes)
                except Exception as e:
                    logger.warning(f"加载笔记失败: {e}")

            parts = []
            if digests_text:
                parts.append(f"【阅读摘要】\n{digests_text}")
            if notes_text:
                parts.append(f"【笔记】\n{notes_text}")
            content = "\n\n".join(parts)

        # 有书名时，优先从 weread_storage 取用户真实划线/笔记，防止 AI 幻觉
        if book_title and self.deps.weread_storage and not content:
            book = await self.deps.weread_storage.find_book_by_title(book_title)
            if book:
                highlights = await self.deps.weread_storage.list_highlights(book.book_id, limit=10)
                notes = await self.deps.weread_storage.list_notes(book.book_id, limit=10)
                parts = []
                for h in highlights:
                    parts.append(h.content)
                for n in notes:
                    if n.abstract:
                        parts.append(f"{n.abstract}（想法：{n.content}）")
                    else:
                        parts.append(n.content)
                if parts:
                    content = "\n".join(parts)

        # 仍无内容时，使用当前书页 OCR
        if not content:
            content = self.deps.memory.current_page_ocr[:1000]
        if not content:
            return {"success": False, "error": "没有可用的内容生成卡片，请先拍摄书页或指定内容"}
        if not book_title:
            book_title = self.deps.memory.current_book_context.get("book_title", "")

        # 用 AI 生成卡片内容
        type_map = {"quote": "金句", "knowledge": "知识点", "summary": "摘要"}
        type_label = type_map.get(card_type, card_type)

        card_content = content
        if self.deps.llm:
            source_hint = f"近{days}天阅读内容" if (card_type == "summary" and days > 1) else ("今日阅读内容" if card_type == "summary" else "书页内容")
            prompt = (
                f"请从以下{source_hint}中提炼一张「{type_label}卡片」，"
                f"用简洁、有力的语言表达核心内容，200字以内。"
                f"直接输出卡片内容，不要额外说明。\n\n{source_hint}：\n{content[:3000]}"
            )
            try:
                resp = await self.deps.llm.chat(user_message=prompt, max_tokens=200)
                if resp.text:
                    card_content = resp.text.strip()
            except Exception as e:
                logger.error(f"生成卡片内容失败: {e}")

        # 推送飞书
        pushed = False
        if self.deps.feishu_pusher and self.deps.feishu_chat_id:
            try:
                await self.deps.feishu_pusher.push_reading_card(
                    self.deps.feishu_chat_id, card_type, card_content, book_title
                )
                pushed = True
            except Exception as e:
                logger.error(f"飞书推送失败: {e}")

        return {
            "success": True,
            "message": f"已生成{type_label}卡片" + ("并推送到飞书" if pushed else ""),
            "card_type": card_type,
            "card_content": card_content,
            "book_title": book_title,
            "feishu_pushed": pushed,
        }

    async def exec_feishu_send_message(self, params: Dict) -> Dict:
        """发送文本消息到飞书"""
        message = params.get("message", "").strip()
        if not message:
            return {"success": False, "error": "消息内容不能为空"}

        if not self.deps.feishu_pusher or not self.deps.feishu_chat_id:
            return {"success": False, "error": "飞书未配置或 chat_id 为空"}

        try:
            await self.deps.feishu_pusher.push_text(self.deps.feishu_chat_id, message)
            return {"success": True, "message": "消息已发送到飞书"}
        except Exception as e:
            logger.error(f"飞书发送消息失败: {e}")
            return {"success": False, "error": str(e)}
