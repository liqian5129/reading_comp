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

logger = logging.getLogger(__name__)

OCR_PROMPT = """请识别图片里拍摄的书页内容，完整整理给我。
请严格按以下格式返回，不要输出其他内容：

<meta>{"book_title":"书名","page_num":页码数字,"chapter":"章节名（无则留空）"}</meta>
<content>
（书页完整正文，双页时左页在前右页在后，保持原文换行和段落）
</content>"""


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
        logger.debug(f"KimiOCR 图片压缩: {orig_kb:.0f}KB → {comp_kb:.0f}KB ({new_w}x{new_h})")
        return compressed_path
    except Exception as e:
        logger.debug(f"KimiOCR 图片压缩失败，使用原图: {e}")
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
    TIMEOUT_S = 60.0    # API 调用超时（含 openai SDK 重试时间）

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

    def trigger(self, image_path: str):
        """
        非阻塞触发一次 OCR 识别。
        若上一次识别尚未完成、或距上次触发未超过 min_interval_s，跳过本次触发。
        """
        if self._pending_task and not self._pending_task.done():
            logger.debug("KimiOCR: 上次识别尚未完成，跳过本次触发")
            return
        now = time.time()
        if now - self._last_trigger_ts < self._min_interval_s:
            logger.debug(f"KimiOCR: 距上次触发仅 {now - self._last_trigger_ts:.0f}s，跳过")
            return
        self._last_trigger_ts = now
        self._pending_task = asyncio.create_task(self._recognize(image_path))

    async def _recognize(self, image_path: str):
        """调用 Kimi vision API 识别书页内容"""
        try:
            # 1. 压缩图片
            loop = asyncio.get_event_loop()
            compressed_path = await loop.run_in_executor(
                None, _compress_image, image_path, self.MAX_SIDE
            )

            # 2. 调用 vision API（独立通道，不传 history/system_prompt）
            response = await asyncio.wait_for(
                self._client.chat(
                    user_message=OCR_PROMPT,
                    image_path=compressed_path,
                    max_tokens=4096,
                ),
                timeout=self.TIMEOUT_S,
            )

            # 3. 过滤 API 错误响应（stop_reason="error" 时 text 为错误信息，不能注入上下文）
            if response.stop_reason == "error":
                logger.warning(f"KimiOCR: API 返回错误，跳过回调: {(response.text or '')[:120]}")
                return

            raw_text = (response.text or "").strip()
            if not raw_text:
                logger.warning("KimiOCR: 返回空响应")
                return

            # 4. 解析响应
            content, meta = _parse_response(raw_text)
            logger.info(
                f"KimiOCR 识别完成: {len(content)}字, "
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

            # 6. 回调书籍元数据（→ 翻页/换书检测）
            if meta and meta.get("book_title") and self.on_book_info:
                # 补充 confidence 字段以兼容 _on_book_detected 的置信度检查
                meta.setdefault("confidence", 1.0)
                try:
                    self.on_book_info(meta, image_path)
                except Exception as e:
                    logger.error(f"KimiOCR on_book_info 回调失败: {e}")

        except asyncio.TimeoutError:
            logger.error(f"KimiOCR 调用超时 ({self.TIMEOUT_S}s)")
        except Exception as e:
            logger.error(f"KimiOCR 识别失败: {e}")

    async def cancel(self):
        """取消正在进行的识别任务"""
        if self._pending_task and not self._pending_task.done():
            self._pending_task.cancel()
            try:
                await self._pending_task
            except asyncio.CancelledError:
                pass
