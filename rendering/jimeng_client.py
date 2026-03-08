"""
火山引擎即梦（Jimeng）文生图 / 图生图客户端
通过 Ark 平台 OpenAI 兼容接口调用

配置项:
  jimeng.api_key   - 火山引擎 API Key
  jimeng.model     - 即梦模型端点 ID (ep-xxxxxxx)
  jimeng.base_url  - Ark API 地址
"""
import base64
import logging
import time
from pathlib import Path
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

# 默认图片尺寸预设（均满足 API 最低 3,686,400 像素要求）
SIZE_PRESETS = {
    "square":    (1920, 1920),   # 1:1  3,686,400 px
    "landscape": (2560, 1440),   # 16:9 3,686,400 px
    "portrait":  (1440, 2560),   # 9:16 3,686,400 px
    "card":      (1600, 2304),   # 约 2:3 3,686,400 px
}


class JimengClient:
    """即梦文生图/图生图客户端"""

    def __init__(
        self,
        api_key: str,
        model: str = "",
        base_url: str = "https://ark.cn-beijing.volces.com/api/v3",
        timeout: int = 60,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def text_to_image(
        self,
        prompt: str,
        size: str = "square",
        output_dir: str = "./data/jimeng",
        filename: Optional[str] = None,
    ) -> Optional[str]:
        """
        文生图，返回本地图片路径。

        Args:
            prompt: 绘图提示词
            size: 尺寸预设名 或 "WxH" 格式
            output_dir: 输出目录
            filename: 文件名（不含扩展名），默认用时间戳
        """
        if not self.api_key or not self.model:
            logger.warning("即梦 API 未配置 (api_key 或 model 为空)")
            return None

        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        # 解析尺寸
        if size in SIZE_PRESETS:
            w, h = SIZE_PRESETS[size]
            size_str = f"{w}x{h}"
        else:
            size_str = size

        url = f"{self.base_url}/images/generations"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "prompt": prompt,
            "size": size_str,
            "response_format": "b64_json",
            "n": 1,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, json=payload, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=self.timeout),
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.error(f"即梦 API 错误 {resp.status}: {body[:500]}")
                        return None
                    data = await resp.json()

            # 解析返回
            images = data.get("data", [])
            if not images:
                logger.error(f"即梦 API 返回空 data: {data}")
                return None

            b64_data = images[0].get("b64_json", "")
            if not b64_data:
                # 尝试 url 格式
                img_url = images[0].get("url", "")
                if img_url:
                    return await self._download_image(img_url, out_path, filename)
                logger.error("即梦 API 无图片数据")
                return None

            # 保存图片
            img_bytes = base64.b64decode(b64_data)
            fname = filename or f"jimeng_{int(time.time())}"
            filepath = out_path / f"{fname}.png"
            filepath.write_bytes(img_bytes)
            logger.info(f"即梦文生图完成: {filepath}")
            return str(filepath)

        except Exception as e:
            logger.error(f"即梦文生图失败: {e}")
            return None

    async def image_to_image(
        self,
        image_path: str,
        prompt: str,
        size: str = "square",
        output_dir: str = "./data/jimeng",
        filename: Optional[str] = None,
    ) -> Optional[str]:
        """
        图文生图（Seedream 图片输入），返回本地图片路径。

        Args:
            image_path: 原图路径
            prompt: 风格/编辑指令
            size: 尺寸预设（默认跟随原图）
            output_dir: 输出目录
            filename: 文件名
        """
        if not self.api_key or not self.model:
            logger.warning("即梦 API 未配置")
            return None

        src = Path(image_path)
        if not src.exists():
            logger.error(f"原图不存在: {image_path}")
            return None

        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        # 读取原图为 base64
        img_b64 = base64.b64encode(src.read_bytes()).decode()
        # 推断 MIME
        suffix = src.suffix.lower()
        mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}.get(
            suffix.lstrip("."), "image/png"
        )

        # 读取原图尺寸，等比放大到满足 API 最小像素要求（3,686,400 px），32 倍数对齐
        try:
            from PIL import Image as _PILImage
            import math
            with _PILImage.open(src) as _im:
                orig_w, orig_h = _im.size

            MIN_PIXELS = 3_686_400  # API 要求最低像素数（来自错误信息）

            cur_pixels = orig_w * orig_h
            if cur_pixels < MIN_PIXELS:
                scale = math.sqrt(MIN_PIXELS / cur_pixels)
                orig_w = int(orig_w * scale)
                orig_h = int(orig_h * scale)

            # 对齐到 32 的倍数（向上取整，确保仍 >= MIN_PIXELS）
            orig_w = math.ceil(orig_w / 32) * 32
            orig_h = math.ceil(orig_h / 32) * 32
            size_str = f"{orig_w}x{orig_h}"
            logger.info(f"图生图尺寸: {size_str} ({orig_w * orig_h:,} px)")
        except Exception:
            if size in SIZE_PRESETS:
                w, h = SIZE_PRESETS[size]
                size_str = f"{w}x{h}"
            else:
                size_str = size

        # 图文生图：/images/generations + image 字段传 base64
        url = f"{self.base_url}/images/generations"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        # image 字段格式：data URI（部分版本需要，纯 base64 也可）
        image_data_uri = f"data:{mime};base64,{img_b64}"
        payload = {
            "model": self.model,
            "prompt": prompt,
            "size": size_str,
            "response_format": "b64_json",
            "n": 1,
            "image": image_data_uri,
        }

        try:
            logger.info(f"即梦图文生图请求: model={self.model} size={size_str} prompt={prompt[:60]}")
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, json=payload, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=self.timeout),
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.error(f"即梦 img2img 错误 {resp.status}: {body[:1000]}")
                        return None
                    data = await resp.json()

            images = data.get("data", [])
            if not images:
                logger.error(f"即梦 img2img 返回空: {data}")
                return None

            b64_data = images[0].get("b64_json", "")
            if not b64_data:
                img_url = images[0].get("url", "")
                if img_url:
                    return await self._download_image(img_url, out_path, filename)
                return None

            img_bytes = base64.b64decode(b64_data)
            fname = filename or f"jimeng_i2i_{int(time.time())}"
            filepath = out_path / f"{fname}.png"
            filepath.write_bytes(img_bytes)
            logger.info(f"即梦图生图完成: {filepath}")
            return str(filepath)

        except Exception as e:
            logger.error(f"即梦图生图失败: {e}")
            return None

    async def _download_image(
        self, url: str, out_path: Path, filename: Optional[str]
    ) -> Optional[str]:
        """下载远程图片到本地"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status != 200:
                        return None
                    img_bytes = await resp.read()
            fname = filename or f"jimeng_{int(time.time())}"
            filepath = out_path / f"{fname}.png"
            filepath.write_bytes(img_bytes)
            return str(filepath)
        except Exception as e:
            logger.error(f"下载图片失败: {e}")
            return None
