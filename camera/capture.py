"""
摄像头捕获模块
支持 Mac 内置摄像头和外接 USB 摄像头（如 RealSense）
"""
import cv2
import os
import sys
import numpy as np
import asyncio
from contextlib import contextmanager
from typing import Optional
import logging

logger = logging.getLogger(__name__)

# macOS 下使用 AVFoundation 后端，兼容性更好
_BACKEND = cv2.CAP_AVFOUNDATION if sys.platform == "darwin" else cv2.CAP_ANY


@contextmanager
def _suppress_stderr():
    """临时将 stderr 重定向到 /dev/null，压制 OpenCV 的冗余输出"""
    devnull_fd = os.open(os.devnull, os.O_WRONLY)
    old_stderr_fd = os.dup(2)
    os.dup2(devnull_fd, 2)
    try:
        yield
    finally:
        os.dup2(old_stderr_fd, 2)
        os.close(devnull_fd)
        os.close(old_stderr_fd)


def list_cameras(max_index: int = 5) -> list[int]:
    """
    列出当前可用的摄像头设备索引。

    Returns:
        可用设备索引列表，按索引升序排列
    """
    available = []
    with _suppress_stderr():
        for i in range(max_index):
            cap = cv2.VideoCapture(i, _BACKEND)
            if cap.isOpened():
                available.append(i)
                cap.release()
    return available


def find_external_camera() -> int:
    """
    列出可用摄像头并返回索引 0。

    警告：macOS AVFoundation 会动态分配设备索引，USB 摄像头连接后
    索引顺序不固定（外接设备可能是 0 也可能是 1），分辨率也不可靠。
    建议在 config.json 关闭 auto_detect，手动指定 camera.device。

    Returns:
        0（打印设备列表供参考，实际请手动配置）
    """
    devices = list_cameras()
    logger.info(f"已发现摄像头设备: {devices}")
    logger.warning(
        "macOS 下设备索引不可靠，auto_detect 返回 0 仅供参考。"
        "建议在 config.json 设置 camera.device 为实际的 RealSense 索引。"
    )
    return 0


def capture_frame(device: int = 0) -> Optional[np.ndarray]:
    """
    从摄像头捕获单帧图像（RGB 模式）

    Args:
        device: 摄像头设备索引，默认 0

    Returns:
        numpy.ndarray: BGR 格式图像，失败返回 None
    """
    cap = cv2.VideoCapture(device, _BACKEND)
    if not cap.isOpened():
        logger.error(f"无法打开摄像头设备 {device}")
        return None

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    ret, frame = cap.read()
    cap.release()

    if not ret or frame is None:
        logger.error("读取摄像头帧失败")
        return None

    return frame


async def capture_frame_async(device: int = 0) -> Optional[np.ndarray]:
    """异步包装 capture_frame"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, capture_frame, device)


class CameraCapture:
    """
    摄像头捕获类（支持连续捕获）
    """

    def __init__(self, device: int = 0):
        self.device = device
        self.cap: Optional[cv2.VideoCapture] = None
        self._is_opened = False

    # RealSense 等 USB 摄像头冷启动时前几帧全黑，需丢弃预热
    _WARMUP_FRAMES = 10

    def open(self) -> bool:
        """打开摄像头"""
        self.cap = cv2.VideoCapture(self.device, _BACKEND)
        if self.cap.isOpened():
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
            self._is_opened = True
            for _ in range(self._WARMUP_FRAMES):
                self.cap.read()
            logger.info(f"摄像头 {self.device} 已打开（后端: {_BACKEND}，预热 {self._WARMUP_FRAMES} 帧）")
            return True
        else:
            logger.error(f"无法打开摄像头 {self.device}")
            return False

    def close(self):
        """关闭摄像头"""
        if self.cap:
            self.cap.release()
            self.cap = None
        self._is_opened = False
        logger.info("摄像头已关闭")

    def read(self) -> Optional[np.ndarray]:
        """读取一帧"""
        if not self._is_opened or not self.cap:
            logger.error("摄像头未打开")
            return None
        ret, frame = self.cap.read()
        return frame if ret else None

    def is_opened(self) -> bool:
        return self._is_opened

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
