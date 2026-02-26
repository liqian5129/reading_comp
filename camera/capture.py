"""
摄像头捕获模块
"""
import cv2
import numpy as np
import asyncio
from typing import Optional
import logging

logger = logging.getLogger(__name__)


def capture_frame(device: int = 0) -> Optional[np.ndarray]:
    """
    从摄像头捕获单帧图像
    
    Args:
        device: 摄像头设备索引，默认 0
        
    Returns:
        numpy.ndarray: BGR 格式图像，失败返回 None
    """
    cap = cv2.VideoCapture(device)
    if not cap.isOpened():
        logger.error(f"无法打开摄像头设备 {device}")
        return None
    
    # 设置分辨率
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
    
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
        
    def open(self) -> bool:
        """打开摄像头"""
        self.cap = cv2.VideoCapture(self.device)
        if self.cap.isOpened():
            # 设置分辨率
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
            self._is_opened = True
            logger.info(f"摄像头 {self.device} 已打开")
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
