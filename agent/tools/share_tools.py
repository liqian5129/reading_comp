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
        content = params.get("content", "").strip()
        book_title = params.get("book_title", "").strip()

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
            prompt = (
                f"请从以下书页内容中提炼一张「{type_label}卡片」，"
                f"用简洁、有力的语言表达核心内容，100字以内。"
                f"直接输出卡片内容，不要额外说明。\n\n书页内容：\n{content[:800]}"
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
