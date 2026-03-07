"""
Kimi 多模态 OCR

使用 Kimi vision API 替代本地 PaddleOCR：
- 一次 API 调用返回书页正文 + 结构化元数据（书名、页码、章节）
- 替代现有 PaddleOCR subprocess 路径（走独立异步通道）
- 同时替代 VisionAnalyzer 的元数据提取功能
"""
import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Optional, Callable, Tuple

import openai

logger = logging.getLogger(__name__)

OCR_PROMPT = """请识别图片里拍摄的书页内容，完整整理给我。
请严格按以下格式返回，不要输出其他内容：

<meta>{"book_title":"书名","page_num":页码数字,"page_num_confidence":0.0到1.0,"visible_pages":1或2,"chapter":"章节名（无则留空）","is_reading":true/false}</meta>
<content>
（书页完整正文，双页时左页在前右页在后，保持原文换行和段落）
</content>

字段说明：
- page_num: 页面上印刷的页码数字（双页时填左页页码），找不到则填 0
- page_num_confidence: 页码识别置信度。清晰可见=1.0，模糊/部分遮挡=0.5，猜测/推断=0.2，无页码=0
- visible_pages: 画面中可见的书页数量。书摊开露出左右两页=2，只有单页=1
- is_reading: 书本打开且能看到正文内容=true；书本合上、没有书、空桌面、画面模糊无法阅读=false。画面中能看到手指、笔、荧光笔等互动痕迹也视为 true"""


def _compress_image(image_path: str, max_side: int) -> str:
    """将图片压缩到 max_side 长边，保存临时文件，返回路径。失败时返回原路径。"""
    try:
        import cv2
        img = cv2.imread(image_path)
        if img is None:
            return image_path
        h, w = img.shape[:2]
        longer = max(h, w)
        if longer <= max_side:
            return image_path
        scale = max_side / longer
        new_w, new_h = int(w * scale), int(h * scale)
        resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
        compressed_path = str(image_path).replace(".jpg", "_kimi_ocr.jpg")
        cv2.imwrite(compressed_path, resized, [cv2.IMWRITE_JPEG_QUALITY, 85])
        orig_kb = Path(image_path).stat().st_size / 1024
        comp_kb = Path(compressed_path).stat().st_size / 1024
        logger.debug(f"VisionOCR 图片压缩: {orig_kb:.0f}KB → {comp_kb:.0f}KB ({new_w}x{new_h})")
        return compressed_path
    except Exception as e:
        logger.debug(f"VisionOCR 图片压缩失败，使用原图: {e}")
        return image_path


def _parse_response(text: str) -> Tuple[str, dict]:
    """从 Kimi 返回中提取 content 和 meta，容错处理。"""
    content = text
    meta = {}

    # 提取 <content>...</content>
    content_match = re.search(r'<content>(.*?)</content>', text, re.DOTALL)
    if content_match:
        content = content_match.group(1).strip()

    # 提取 <meta>...</meta>
    meta_match = re.search(r'<meta>(.*?)</meta>', text, re.DOTALL)
    if meta_match:
        try:
            meta = json.loads(meta_match.group(1).strip())
        except json.JSONDecodeError:
            logger.warning(f"KimiOCR meta JSON 解析失败: {meta_match.group(1)[:100]}")

    return content, meta


class KimiOCR:
    """
    Kimi 多模态 OCR（非阻塞）

    fire-and-forget：触发后异步执行，不阻塞主程序。
    同时提取书页正文（替代 PaddleOCR）和书籍元数据（替代 VisionAnalyzer）。
    """

    MAX_SIDE = 1280     # 压缩到此长边（书页 OCR 足够，减少请求体大小）
    TIMEOUT_S = 120.0   # 单次 API 调用超时（豆包 seed 大模型 OCR 可能需要 70-90s）
    MAX_RETRIES = 2     # 最大重试次数（共尝试 MAX_RETRIES 次）

    def __init__(self, ai_client, min_interval_s: float = 30.0,
                 results_dir: Optional[Path] = None):
        """
        Args:
            ai_client: AIClient 实例（支持 vision API）
            min_interval_s: 两次触发的最小间隔秒数（默认 30s）
            results_dir: 结果保存目录（None 则不保存）
        """
        self._client = ai_client
        self._min_interval_s = min_interval_s
        self._results_dir = results_dir
        if results_dir:
            results_dir.mkdir(parents=True, exist_ok=True)
        self._pending_task: Optional[asyncio.Task] = None
        self._last_trigger_ts: float = 0.0

        # 回调
        self.on_text_ready: Optional[Callable[[str, str], None]] = None   # (text, image_path)
        self.on_book_info: Optional[Callable[[dict, str], None]] = None   # (book_info, image_path)

    def trigger(self, image_path: str) -> bool:
        """
        非阻塞触发一次 OCR 识别。
        若上一次识别尚未完成、或距上次触发未超过 min_interval_s，跳过本次触发。
        返回 True 表示成功触发，False 表示被跳过。
        """
        if self._pending_task and not self._pending_task.done():
            logger.debug("KimiOCR: 上次识别尚未完成，跳过本次触发")
            return False
        now = time.time()
        if now - self._last_trigger_ts < self._min_interval_s:
            logger.debug(f"KimiOCR: 距上次触发仅 {now - self._last_trigger_ts:.0f}s，跳过")
            return False
        self._last_trigger_ts = now
        self._pending_task = asyncio.create_task(self._recognize(image_path))
        return True

    async def _recognize(self, image_path: str):
        """调用 vision API 识别书页内容，失败自动重试"""
        provider = getattr(self._client, 'provider', 'unknown')
        model = getattr(self._client, 'model', 'unknown')
        tag = f"{provider.upper()}OCR({model})"

        # 1. 压缩图片（只做一次）
        loop = asyncio.get_event_loop()
        compressed_path = await loop.run_in_executor(
            None, _compress_image, image_path, self.MAX_SIDE
        )
        try:
            img_kb = Path(compressed_path).stat().st_size / 1024
        except Exception:
            img_kb = 0
        logger.info(f"{tag} 开始识别: {Path(image_path).name}, 压缩后 {img_kb:.0f}KB")

        response = None
        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                logger.debug(f"{tag} 第{attempt}次请求，超时={self.TIMEOUT_S}s")
                t0 = time.time()
                # 2. 调用 vision API（独立通道，不传 history/system_prompt）
                response = await asyncio.wait_for(
                    self._client.chat(
                        user_message=OCR_PROMPT,
                        image_path=compressed_path,
                        max_tokens=4096,
                    ),
                    timeout=self.TIMEOUT_S,
                )
                elapsed = time.time() - t0
                logger.info(f"{tag} 第{attempt}次请求完成，耗时 {elapsed:.1f}s，stop_reason={response.stop_reason}")
                break  # 成功，跳出重试循环
            except openai.RateLimitError as e:
                # 429 服务过载：立即放弃，不重试（重试也大概率失败）
                logger.warning(f"{tag}: 429 服务过载，跳过本次识别（下次翻页重试）: {e}")
                return
            except (asyncio.TimeoutError, Exception) as e:
                elapsed = time.time() - t0
                if isinstance(e, asyncio.TimeoutError):
                    err_desc = f"超时({elapsed:.0f}s >= {self.TIMEOUT_S}s)"
                else:
                    err_desc = f"{type(e).__name__}: {e}"
                if attempt < self.MAX_RETRIES:
                    logger.warning(f"{tag} 第{attempt}次失败（{err_desc}），2s 后重试...")
                    await asyncio.sleep(2)
                else:
                    logger.error(f"{tag} 已重试 {self.MAX_RETRIES} 次仍失败（{err_desc}），放弃")
                    return

        try:
            # 3. 过滤 API 错误响应（stop_reason="error" 时 text 为错误信息，不能注入上下文）
            if response.stop_reason == "error":
                logger.warning(f"{tag}: API 返回错误，跳过回调: {(response.text or '')[:200]}")
                return

            raw_text = (response.text or "").strip()
            if not raw_text:
                logger.warning(f"{tag}: 返回空响应（stop_reason={response.stop_reason}）")
                return

            # 4. 解析响应
            content, meta = _parse_response(raw_text)
            logger.info(
                f"{tag} 识别完成: {len(content)}字, "
                f"书名={meta.get('book_title', '')!r}, "
                f"页码={meta.get('page_num', '')}"
            )

            # 4b. 保存结果到文件（若启用）
            if self._results_dir:
                ts = time.strftime("%Y%m%d_%H%M%S")
                result = {"meta": meta, "content": content, "image_path": image_path}
                out_path = self._results_dir / f"{ts}.json"
                try:
                    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                    logger.debug(f"KimiOCR 结果已保存: {out_path.name}")
                except Exception as e:
                    logger.warning(f"KimiOCR 结果保存失败: {e}")

            # 5. 回调文字内容（→ 注入 AI 上下文）
            if self.on_text_ready:
                try:
                    self.on_text_ready(content, image_path)
                except Exception as e:
                    logger.error(f"KimiOCR on_text_ready 回调失败: {e}")

            # 6. 回调书籍元数据（→ 阅读活动记录 + 翻页检测）
            #    只要有 meta 就回调（is_reading 用于计算阅读时长，page_num/book_title 用于翻页统计）
            if meta and self.on_book_info:
                meta.setdefault("confidence", 1.0)
                meta["ocr_chars"] = len(content)
                try:
                    self.on_book_info(meta, image_path)
                except Exception as e:
                    logger.error(f"KimiOCR on_book_info 回调失败: {e}")

        except Exception as e:
            logger.error(f"{tag} 后处理失败: {e}", exc_info=True)

    async def cancel(self):
        """取消正在进行的识别任务"""
        if self._pending_task and not self._pending_task.done():
            self._pending_task.cancel()
            try:
                await self._pending_task
            except asyncio.CancelledError:
                pass


# 豆包 OCR 与 KimiOCR 共用同一套逻辑，仅 AIClient 不同
DoubaoOCR = KimiOCR


class OcrChain:
    """
    双 OCR 路由器：primary 失败时自动切换到 fallback。
    接口与 KimiOCR 完全相同，可直接传给 scanner.set_kimi_ocr()。
    """

    def __init__(self, primary: KimiOCR, fallback: Optional[KimiOCR] = None):
        self.primary = primary
        self.fallback = fallback
        self._pending_task: Optional[asyncio.Task] = None
        self._last_trigger_ts: float = 0.0
        self._min_interval_s = primary._min_interval_s

        # 与 KimiOCR 相同的回调接口
        self.on_text_ready: Optional[Callable[[str, str], None]] = None
        self.on_book_info: Optional[Callable[[dict, str], None]] = None

    def trigger(self, image_path: str) -> bool:
        if self._pending_task and not self._pending_task.done():
            logger.debug("OcrChain: 上次识别尚未完成，跳过")
            return False
        now = time.time()
        if now - self._last_trigger_ts < self._min_interval_s:
            logger.debug(f"OcrChain: 距上次触发仅 {now - self._last_trigger_ts:.0f}s，跳过")
            return False
        self._last_trigger_ts = now
        self._pending_task = asyncio.create_task(self._run(image_path))
        return True

    async def _run(self, image_path: str):
        primary_name = getattr(getattr(self.primary, '_client', None), 'provider', 'primary').upper() + "OCR"
        fallback_name = (getattr(getattr(self.fallback, '_client', None), 'provider', 'fallback').upper() + "OCR") if self.fallback else None

        # 用 flag 检测主 OCR 是否成功触发了 on_text_ready
        success = False

        def _on_text(text, path):
            nonlocal success
            success = True
            if self.on_text_ready:
                self.on_text_ready(text, path)

        def _on_book(info, path):
            if self.on_book_info:
                self.on_book_info(info, path)

        self.primary.on_text_ready = _on_text
        self.primary.on_book_info = _on_book
        await self.primary._recognize(image_path)

        if not success and self.fallback:
            logger.info(f"OcrChain: {primary_name} 未返回结果，切换到 {fallback_name}")
            self.fallback.on_text_ready = self.on_text_ready
            self.fallback.on_book_info = self.on_book_info
            await self.fallback._recognize(image_path)

    async def cancel(self):
        if self._pending_task and not self._pending_task.done():
            self._pending_task.cancel()
            try:
                await self._pending_task
            except asyncio.CancelledError:
                pass
        await self.primary.cancel()
        if self.fallback:
            await self.fallback.cancel()
