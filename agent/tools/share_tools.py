"""
分享相关工具：
  - generate_quote_image       (金句图片卡片)
  - generate_summary_image     (摘要+AI插图卡片)
  - stylize_page               (书页转插画风格)
  - export_ppt                 (导出 PPT)
  - export_markdown            (导出 Markdown)
  - feishu_send_message        (飞书文本消息)
"""
import asyncio
import logging
from typing import Dict

logger = logging.getLogger(__name__)

# 风格 prompt 映射（stylize_page 用）
_STYLE_PROMPTS = {
    "watercolor": "将这张书页内容转换为清新水彩插画风格，柔和的色彩过渡，保留核心元素和文字布局",
    "sketch": "将这张书页转换为精致铅笔素描风格，细腻的线条和阴影，保留核心内容",
    "comic": "将这张书页转换为日式漫画风格，鲜明的线条和对比，活泼的表现形式",
    "ghibli": "将这张书页转换为吉卜力动画电影风格，温暖柔和的色调，梦幻的氛围",
    "ink": "将这张书页转换为中国传统水墨画风格，黑白灰的层次，意境深远",
}


def _derive_prompt_from_text(text: str) -> str:
    """从摘要文本关键词匹配插图 prompt（LLM 不可用时的降级逻辑）"""
    t = text.lower()
    if any(k in t for k in ["基因", "dna", "进化", "生物", "细胞", "物种"]):
        return "Scientific illustration of DNA double helix surrounded by glowing biological cells, deep blue and green tones, watercolor style, book illustration"
    if any(k in t for k in ["战争", "帝国", "历史", "征服", "围城", "古代", "朝代"]):
        return "Epic watercolor historical scene, ancient civilization and dramatic sky, warm golden tones, book illustration style"
    if any(k in t for k in ["科学", "技术", "数据", "算法", "ai", "人工智能", "计算"]):
        return "Futuristic digital watercolor, glowing neural networks and data streams, cool blue tones, book illustration style"
    if any(k in t for k in ["想象", "故事", "神话", "宗教", "信仰", "虚构", "小说"]):
        return "Surreal watercolor, human silhouettes connected by glowing cosmic threads, warm golden tones, book illustration style"
    if any(k in t for k in ["协作", "网络", "社会", "合作", "团队", "群体"]):
        return "Watercolor of interconnected people forming a glowing web, warm earthy tones, book illustration style"
    if any(k in t for k in ["自然", "海洋", "森林", "山", "生命", "生态"]):
        return "Serene nature watercolor, misty mountains and flowing water, soft morning light, book illustration style"
    if any(k in t for k in ["心理", "情感", "思维", "意识", "梦", "潜意识"]):
        return "Abstract watercolor of a human mind with swirling thoughts, purple and indigo tones, book illustration style"
    if any(k in t for k in ["经济", "市场", "商业", "财富", "投资", "贸易"]):
        return "Elegant watercolor of global trade routes and interconnected markets, warm amber tones, book illustration style"
    if any(k in t for k in ["哲学", "思想", "智慧", "真理", "道", "本质"]):
        return "Contemplative watercolor of a solitary figure beneath a cosmic sky, deep blue and gold, book illustration style"
    if any(k in t for k in ["艺术", "音乐", "绘画", "创造", "美", "审美"]):
        return "Vibrant watercolor splash of colors forming musical notes and brushstrokes, joyful atmosphere, book illustration style"
    return "Elegant conceptual watercolor illustration, abstract symbolic elements, warm colors, artistic atmosphere, book illustration style"


class ShareTools:
    def __init__(self, deps):
        self.deps = deps

    # ============================================================
    # 金句图片卡片
    # ============================================================

    async def exec_generate_quote_image(self, params: Dict) -> Dict:
        """渲染金句为精美图片卡片（fire-and-forget）"""
        # 快速校验：无文本且无 OCR 时立即返回错误
        text = params.get("text", "").strip()
        if not text and not self.deps.memory.current_page_ocr:
            return {"success": False, "error": "没有可用的内容，请先拍摄书页或指定金句文本"}
        asyncio.create_task(self._bg_generate_quote_image(params))
        return {"status": "ok", "message": "正在为您生成金句卡片，完成后推送到飞书～"}

    async def _bg_generate_quote_image(self, params: Dict):
        try:
            text = params.get("text", "").strip()
            book_title = params.get("book_title", "").strip()
            author = params.get("author", "").strip()
            mood = params.get("mood", "").strip()
            template = params.get("template", "").strip()

            # 无文本时从 OCR 提炼
            if not text:
                ocr_text = self.deps.memory.current_page_ocr[:1500]
                if self.deps.llm:
                    try:
                        resp = await self.deps.llm.chat(
                            user_message=(
                                "请从以下书页内容中提炼一句最有力量的金句，100字以内，直接输出金句本身：\n\n"
                                + ocr_text
                            ),
                            max_tokens=150,
                        )
                        if resp.text:
                            text = resp.text.strip()
                    except Exception as e:
                        logger.error(f"AI 提炼金句失败: {e}")
                if not text:
                    text = ocr_text[:200]

            if not book_title:
                book_title = self.deps.memory.current_book_context.get("book_title", "")

            # AI 判断 mood
            if not mood and self.deps.llm:
                try:
                    resp = await self.deps.llm.chat(
                        user_message=(
                            "判断以下文字的情景类别，只输出一个词：warm/cool/literary/art/neutral\n\n"
                            + text[:300]
                        ),
                        max_tokens=10,
                    )
                    if resp.text:
                        mood = resp.text.strip().lower()
                except Exception:
                    pass
            mood = mood or "neutral"

            from rendering.card_renderer import CardRenderer
            from config import config
            renderer = CardRenderer(output_dir=str(config.CARDS_DIR))
            filepath = renderer.render_quote_card(
                text=text,
                book_title=book_title,
                author=author,
                mood=mood,
                template_name=template or None,
            )
            await self._push_image_to_feishu(filepath)
        except Exception as e:
            logger.error(f"后台金句卡片生成失败: {e}")

    # ============================================================
    # 新工具：摘要 + AI 插图卡片
    # ============================================================

    async def exec_generate_summary_image(self, params: Dict) -> Dict:
        """生成带 AI 插图的摘要卡片（fire-and-forget）"""
        asyncio.create_task(self._bg_generate_summary_image(params))
        return {"status": "ok", "message": "正在为您生成摘要卡片，完成后推送到飞书～"}

    async def _bg_generate_summary_image(self, params: Dict):
        try:
            summary_text = params.get("summary_text", "").strip()
            title = params.get("title", "").strip()
            days = max(1, int(params.get("days", 1) or 1))
            illustration_prompt = params.get("illustration_prompt", "").strip()
            mood = params.get("mood", "neutral").strip()

            # 自动获取摘要
            if not summary_text:
                raw = await self._gather_summary_content(days)
                if raw and self.deps.llm:
                    try:
                        resp = await self.deps.llm.chat(
                            user_message=f"请将以下阅读内容精炼为200字以内的摘要，直接输出：\n\n{raw[:2000]}",
                            max_tokens=300,
                        )
                        if resp.text:
                            summary_text = resp.text.strip()
                    except Exception as e:
                        logger.error(f"生成摘要失败: {e}")
                if not summary_text:
                    summary_text = raw[:500] if raw else ""
            if not summary_text:
                logger.warning("后台摘要卡片生成：没有可用的摘要内容")
                return

            if not title:
                title = self.deps.memory.current_book_context.get("book_title", "阅读摘要")

            # 生成 AI 插图
            illustration_path = None
            jimeng = getattr(self.deps, "jimeng_client", None)
            if jimeng:
                if not illustration_prompt:
                    illustration_prompt = await self._make_illustration_prompt_async(summary_text)

                try:
                    from config import config
                    illustration_path = await jimeng.text_to_image(
                        prompt=illustration_prompt,
                        size="landscape",
                        output_dir=str(config.JIMENG_DIR),
                    )
                except Exception as e:
                    logger.error(f"即梦文生图失败: {e}")

            from rendering.card_renderer import CardRenderer
            from config import config
            renderer = CardRenderer(output_dir=str(config.CARDS_DIR))
            filepath = renderer.render_summary_card(
                title=title,
                summary_text=summary_text,
                illustration_path=illustration_path,
                mood=mood,
            )
            await self._push_image_to_feishu(filepath)
        except Exception as e:
            logger.error(f"后台摘要卡片生成失败: {e}")

    async def _make_illustration_prompt_async(self, text: str) -> str:
        """使用独立豆包 LLM 实例生成插图 prompt，失败时降级关键词匹配"""
        try:
            from agent.ai_client import AIClient
            from config import config
            llm = AIClient(
                provider="doubao",
                api_key=config.ILLUSTRATION_LLM_API_KEY,
                model=config.ILLUSTRATION_LLM_MODEL,
                base_url=config.ILLUSTRATION_LLM_BASE_URL,
            )
            resp = await llm.chat(
                user_message=(
                    "这里有一段读书摘要或金句：\n\n"
                    f"{text[:600]}\n\n"
                    "我想为这张摘要卡/金句卡配一张有意境、审美高级的插图。\n"
                    "请理解内容的核心意境，自由发挥，生成英文文生图提示词（Midjourney风格）。\n"
                    "要求：画面有层次感，意境深远，色调与文字基调一致，"
                    "风格随机（水彩/油画/版画/山水/超现实等），40-80词，直接输出，不要解释。"
                ),
                max_tokens=120,
            )
            if resp.text:
                prompt = resp.text.strip()
                logger.info(f"插图 LLM prompt: {prompt[:100]}")
                return prompt
        except Exception as e:
            logger.warning(f"插图 LLM prompt 生成失败，降级关键词匹配: {e}")
        return _derive_prompt_from_text(text)

    # ============================================================
    # 新工具：书页转插画风格
    # ============================================================

    async def exec_stylize_page(self, params: Dict) -> Dict:
        """将当前书页照片转换为插画风格（fire-and-forget）"""
        jimeng = getattr(self.deps, "jimeng_client", None)
        if not jimeng:
            return {"success": False, "error": "即梦 API 未配置，请在 config.json 中配置 jimeng 节"}

        # 快速校验书页照片是否存在
        scanner = getattr(self.deps, "scanner", None)
        snapshot_path = None
        if scanner:
            snapshot_path = getattr(scanner, "last_snapshot_path", None)
        if not snapshot_path:
            snapshot_path = getattr(self.deps.memory, "last_snapshot_path", None)
        if not snapshot_path:
            return {"success": False, "error": "没有可用的书页照片，请先拍摄书页"}

        asyncio.create_task(self._bg_stylize_page(params, snapshot_path))
        style = params.get("style", "watercolor").strip()
        return {"status": "ok", "message": f"正在将书页转换为{style}风格，完成后推送到飞书～"}

    async def _bg_stylize_page(self, params: Dict, snapshot_path: str):
        try:
            style = params.get("style", "watercolor").strip()
            strength = float(params.get("strength", 0.6) or 0.6)
            strength = max(0.3, min(0.9, strength))
            jimeng = getattr(self.deps, "jimeng_client", None)
            prompt = _STYLE_PROMPTS.get(style, _STYLE_PROMPTS["watercolor"])

            from config import config
            result_path = await jimeng.image_to_image(
                image_path=snapshot_path,
                prompt=prompt,
                size="portrait",
                output_dir=str(config.JIMENG_DIR),
            )
            if result_path:
                await self._push_image_to_feishu(result_path)
            else:
                logger.warning("风格转换未返回结果，请检查即梦 API 配置")
        except Exception as e:
            logger.error(f"后台书页风格转换失败: {e}")

    # ============================================================
    # 新工具：导出 PPT
    # ============================================================

    async def exec_export_ppt(self, params: Dict) -> Dict:
        """导出笔记为 PPT"""
        book_title = params.get("book_title", "").strip()
        days = max(1, int(params.get("days", 7) or 7))
        theme = params.get("theme", "light").strip()
        include_summary = params.get("include_summary", True)

        # 获取笔记
        sm = getattr(self.deps, "session_manager", None)
        if not sm:
            return {"success": False, "error": "session_manager 不可用"}

        try:
            notes = await sm.get_recent_notes(days=days, limit=50)
        except Exception as e:
            return {"success": False, "error": f"获取笔记失败: {e}"}

        if book_title:
            notes = [n for n in notes if book_title.lower() in (n.book_name or "").lower()]

        if not notes:
            return {"success": False, "error": f"最近{days}天没有找到笔记"}

        note_dicts = [
            {"content": n.content, "tags": n.tags or [], "book_name": n.book_name or ""}
            for n in notes
        ]

        try:
            from rendering.ppt_generator import PPTGenerator, SlideContent
            from config import config

            gen = PPTGenerator(output_dir=str(config.PPT_DIR), theme=theme)

            slides = []

            # 可选：AI 生成摘要页
            if include_summary and self.deps.llm:
                all_text = "\n".join(n.content for n in notes)[:2000]
                try:
                    resp = await self.deps.llm.chat(
                        user_message=f"请将以下读书笔记整理为3-5个要点，每个要点一行：\n\n{all_text}",
                        max_tokens=300,
                    )
                    if resp.text:
                        bullets = [l.strip().lstrip("0123456789.-、） ") for l in resp.text.strip().split("\n") if l.strip()]
                        slides.append(SlideContent(
                            layout="bullets",
                            title="核心要点",
                            bullets=bullets[:5],
                        ))
                except Exception:
                    pass

            # 笔记页
            for nd in note_dicts:
                content = nd["content"]
                source = f"《{nd['book_name']}》" if nd["book_name"] else ""
                if len(content) < 100:
                    slides.append(SlideContent(layout="quote", body=content, source=source))
                else:
                    lines = [l.strip() for l in content.split("\n") if l.strip()]
                    slides.append(SlideContent(
                        layout="bullets",
                        title=nd["tags"][0] if nd["tags"] else "",
                        bullets=lines,
                        source=source,
                    ))

            title = f"《{book_title}》读书笔记" if book_title else "读书笔记"
            filepath = gen.generate(slides, title=title)

        except ImportError:
            return {"success": False, "error": "python-pptx 未安装，请运行: pip install python-pptx"}
        except Exception as e:
            logger.error(f"生成 PPT 失败: {e}")
            return {"success": False, "error": f"生成失败: {e}"}

        # 推送飞书文件
        pushed = await self._push_file_to_feishu(filepath, f"{title}.pptx")

        return {
            "success": True,
            "message": f"PPT 已生成（{len(slides)}页）" + ("并推送到飞书" if pushed else ""),
            "file_path": filepath,
            "slide_count": len(slides),
            "feishu_pushed": pushed,
        }

    # ============================================================
    # 新工具：导出 Markdown
    # ============================================================

    async def exec_export_markdown(self, params: Dict) -> Dict:
        """导出笔记为 Markdown"""
        template = params.get("template", "notes").strip()
        book_title = params.get("book_title", "").strip()
        days = max(1, int(params.get("days", 7) or 7))

        sm = getattr(self.deps, "session_manager", None)
        if not sm:
            return {"success": False, "error": "session_manager 不可用"}

        try:
            from rendering.markdown_exporter import MarkdownExporter
            from config import config
            exporter = MarkdownExporter(output_dir=str(config.MARKDOWN_DIR))
        except Exception as e:
            return {"success": False, "error": f"初始化导出器失败: {e}"}

        if template == "notes":
            return await self._export_md_notes(exporter, sm, book_title, days)
        elif template == "daily":
            return await self._export_md_daily(exporter, sm, days)
        elif template == "quotes":
            return await self._export_md_quotes(exporter, sm, book_title, days)
        else:
            return {"success": False, "error": f"未知模板: {template}，支持 notes/daily/quotes"}

    async def _export_md_notes(self, exporter, sm, book_title, days) -> Dict:
        try:
            notes = await sm.get_recent_notes(days=days, limit=100)
        except Exception as e:
            return {"success": False, "error": f"获取笔记失败: {e}"}

        if book_title:
            notes = [n for n in notes if book_title.lower() in (n.book_name or "").lower()]
        if not notes:
            return {"success": False, "error": f"最近{days}天没有找到笔记"}

        note_dicts = [
            {
                "content": n.content,
                "tags": n.tags or [],
                "book_name": n.book_name or "",
                "created_at": getattr(n, "created_at", ""),
            }
            for n in notes
        ]
        filepath = exporter.export_reading_notes(note_dicts, book_title=book_title)
        pushed = await self._push_file_to_feishu(filepath, f"读书笔记.md")
        return {
            "success": True,
            "message": f"读书笔记 Markdown 已导出（{len(notes)}条）" + ("并推送到飞书" if pushed else ""),
            "file_path": filepath,
            "note_count": len(notes),
            "feishu_pushed": pushed,
        }

    async def _export_md_daily(self, exporter, sm, days) -> Dict:
        summary_text = ""
        storage = getattr(self.deps, "storage", None)
        if storage:
            try:
                digests = await storage.get_recent_digests(days=days)
                if digests:
                    summary_text = digests[0]["digest_text"]
            except Exception:
                pass
        if not summary_text:
            summary_text = getattr(self.deps.memory, "session_reading_digest", "")

        try:
            notes = await sm.get_recent_notes(days=days, limit=50)
        except Exception:
            notes = []

        note_dicts = [{"content": n.content, "book_name": n.book_name or ""} for n in notes]
        filepath = exporter.export_daily_summary(
            summary_text=summary_text or "暂无摘要",
            notes=note_dicts if note_dicts else None,
            stats={"notes_count": len(notes)},
        )
        pushed = await self._push_file_to_feishu(filepath, "每日阅读摘要.md")
        return {
            "success": True,
            "message": "每日摘要 Markdown 已导出" + ("并推送到飞书" if pushed else ""),
            "file_path": filepath,
            "feishu_pushed": pushed,
        }

    async def _export_md_quotes(self, exporter, sm, book_title, days) -> Dict:
        try:
            notes = await sm.get_recent_notes(days=days, limit=100)
        except Exception:
            notes = []

        if book_title:
            notes = [n for n in notes if book_title.lower() in (n.book_name or "").lower()]

        # 用 AI 筛选金句（短的笔记更可能是金句）
        quotes = []
        for n in notes:
            if len(n.content) < 150:
                quotes.append({
                    "text": n.content,
                    "book": n.book_name or "",
                    "tags": n.tags or [],
                })

        if not quotes:
            return {"success": False, "error": "没有找到可用的金句"}

        filepath = exporter.export_quote_collection(quotes)
        pushed = await self._push_file_to_feishu(filepath, "金句集锦.md")
        return {
            "success": True,
            "message": f"金句集锦 Markdown 已导出（{len(quotes)}条）" + ("并推送到飞书" if pushed else ""),
            "file_path": filepath,
            "quote_count": len(quotes),
            "feishu_pushed": pushed,
        }

    # ============================================================
    # 原有工具：飞书文本消息
    # ============================================================

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

    # ============================================================
    # 内部辅助方法
    # ============================================================

    async def _gather_summary_content(self, days: int) -> str:
        """收集摘要内容（从 DB 摘要 + 笔记 + 书页记录）"""
        parts = []
        storage = getattr(self.deps, "storage", None)

        # DB 摘要
        digest = ""
        if storage:
            try:
                digests = await storage.get_recent_digests(days=days)
                if digests:
                    digest = digests[0]["digest_text"]
            except Exception as e:
                logger.warning(f"加载阅读摘要失败: {e}")
        if not digest:
            digest = getattr(self.deps.memory, "session_reading_digest", "")
        if digest:
            parts.append(f"【阅读摘要】\n{digest}")

        # 书页记录
        if storage:
            try:
                pages = await storage.get_reading_pages(days=days)
                if pages:
                    from datetime import datetime
                    lines = []
                    for p in pages:
                        time_str = datetime.fromtimestamp(p["ts"] / 1000).strftime("%H:%M")
                        book_hint = f"《{p['book_title']}》" if p["book_title"] else ""
                        page_hint = f"第{p['page_num']}页" if p["page_num"] else ""
                        chapter_hint = f"【{p['chapter']}】" if p["chapter"] else ""
                        header = f"[{time_str} {book_hint}{page_hint}{chapter_hint}]"
                        lines.append(f"{header}\n{p['ocr_text']}")
                    pages_text = "\n\n".join(lines)[:4000]
                    parts.append(f"【书页内容】\n{pages_text}")
            except Exception as e:
                logger.warning(f"加载书页记录失败: {e}")

        # 笔记
        sm = getattr(self.deps, "session_manager", None)
        if sm:
            try:
                notes = await sm.get_recent_notes(days=days, limit=50)
                if notes:
                    notes_text = "\n".join(f"- {n.content}" for n in notes)
                    parts.append(f"【笔记】\n{notes_text}")
            except Exception as e:
                logger.warning(f"加载笔记失败: {e}")

        return "\n\n".join(parts)

    async def _push_image_to_feishu(self, image_path: str) -> bool:
        """上传并推送图片到飞书"""
        if not self.deps.feishu_pusher or not self.deps.feishu_chat_id:
            return False
        bot = getattr(self.deps.feishu_pusher, "bot", None)
        if not bot:
            return False
        try:
            image_key = await bot.upload_image(image_path)
            if image_key:
                await bot.send_image(self.deps.feishu_chat_id, image_key)
                return True
        except Exception as e:
            logger.error(f"飞书图片推送失败: {e}")
        return False

    async def _push_file_to_feishu(self, filepath: str, display_name: str) -> bool:
        """推送文件到飞书（以文本消息附带路径）"""
        if not self.deps.feishu_pusher or not self.deps.feishu_chat_id:
            return False
        try:
            await self.deps.feishu_pusher.push_text(
                self.deps.feishu_chat_id,
                f"文件已生成: {display_name}\n本地路径: {filepath}",
            )
            return True
        except Exception as e:
            logger.error(f"飞书文件通知失败: {e}")
            return False
