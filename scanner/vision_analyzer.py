"""
书页视觉分析器
利用 AIClient 的视觉能力自动识别书名和页码
"""
import asyncio
import io
import json
import logging
import time
from pathlib import Path
from typing import Optional, Callable, Dict

logger = logging.getLogger(__name__)

# 发给视觉 API 前将图片压缩到此宽度（识别书名/页码不需要高分辨率）
_VISION_MAX_WIDTH = 800

VISION_PROMPT = """请分析这张书页图片，提取以下信息并以 JSON 格式返回（仅返回 JSON，不要其他文字）：
{
  "book_title": "书名（若无法识别则留空字符串）",
  "current_page_num": 页码数字（若无法识别则为0，直接是数字不加引号）,
  "content_type": "正文/封面/目录/图片/其他",
  "confidence": 置信度0到1之间的小数
}"""


def _compress_image(image_path: str, max_width: int = _VISION_MAX_WIDTH) -> Optional[str]:
    """
    将图片压缩后保存到临时文件，返回新路径。
    压缩失败则返回原路径。
    """
    try:
        import cv2
        img = cv2.imread(image_path)
        if img is None:
            return image_path
        h, w = img.shape[:2]
        if w <= max_width:
            return image_path  # 已经够小，不需要压缩
        scale = max_width / w
        new_w, new_h = int(w * scale), int(h * scale)
        resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
        compressed_path = str(image_path).replace(".jpg", "_vision.jpg")
        cv2.imwrite(compressed_path, resized, [cv2.IMWRITE_JPEG_QUALITY, 75])
        orig_kb = Path(image_path).stat().st_size / 1024
        comp_kb = Path(compressed_path).stat().st_size / 1024
        logger.debug(f"图片压缩: {orig_kb:.0f}KB → {comp_kb:.0f}KB ({new_w}x{new_h})")
        return compressed_path
    except Exception as e:
        logger.debug(f"图片压缩失败，使用原图: {e}")
        return image_path


class VisionAnalyzer:
    """
    书页视觉分析器（非阻塞）

    控制调用频率，节省 API 成本。
    翻页时可用 force=True 立即触发。
    """

    MIN_INTERVAL_S = 30.0  # 非强制触发的最小间隔（秒）

    def __init__(self, ai_client, on_book_detected: Optional[Callable[[dict], None]] = None):
        """
        Args:
            ai_client: AIClient 实例（支持视觉 API）
            on_book_detected: 识别到书名时的回调，接收 dict 参数
        """
        self._llm = ai_client
        self.on_book_detected = on_book_detected
        self._last_trigger_ts: float = 0.0
        self._pending_task: Optional[asyncio.Task] = None

    def trigger(self, image_path: str, force: bool = False):
        """
        非阻塞触发视觉分析。

        Args:
            image_path: 书页图片路径
            force: True 时跳过间隔限制（翻页时使用）
        """
        now = time.time()
        if not force and (now - self._last_trigger_ts) < self.MIN_INTERVAL_S:
            return  # 未到间隔，跳过

        # 上一个任务还没跑完时，非强制触发直接跳过
        if self._pending_task and not self._pending_task.done():
            if not force:
                return

        self._last_trigger_ts = now
        self._pending_task = asyncio.create_task(self._analyze(image_path))

    async def _analyze(self, image_path: str) -> Optional[Dict]:
        """调用视觉 API，解析并回调结果"""
        response = None
        try:
            # 压缩图片再发，避免原图过大（摄像头原图通常 300-500KB）
            compressed_path = await asyncio.get_event_loop().run_in_executor(
                None, _compress_image, image_path
            )
            response = await self._llm.chat(
                user_message=VISION_PROMPT,
                image_path=compressed_path,
                max_tokens=400,
            )
            text = (response.text or "").strip()

            # 提取 JSON 部分（有时模型会带 ```json ... ```）
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            text = text.strip()

            result = json.loads(text)
            confidence = float(result.get("confidence", 0))
            book_title = (result.get("book_title") or "").strip()

            logger.info(
                f"📷 视觉分析完成: 书名={book_title!r} 页码={result.get('current_page_num')} "
                f"类型={result.get('content_type')} 置信度={confidence:.2f}"
            )

            if confidence >= 0.7 and self.on_book_detected:
                try:
                    self.on_book_detected(result)
                except Exception as e:
                    logger.error(f"on_book_detected 回调失败: {e}")

            return result

        except json.JSONDecodeError as e:
            logger.warning(f"视觉分析 JSON 解析失败: {e}, 原始文本: {response.text[:200] if response else ''}")
            return None
        except Exception as e:
            logger.error(f"视觉分析失败: {e}")
            return None

    async def cancel(self):
        """取消正在进行的分析任务"""
        if self._pending_task and not self._pending_task.done():
            self._pending_task.cancel()
            try:
                await self._pending_task
            except asyncio.CancelledError:
                pass
