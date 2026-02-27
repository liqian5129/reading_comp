"""
OCR 引擎模块
使用 PaddleOCR 进行文字识别
"""
import os
import numpy as np
from pathlib import Path
from typing import Optional, List
import logging

# 禁用 PaddleOCR 启动时的网络连通性检查（避免几十秒的卡顿）
os.environ.setdefault('PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK', 'True')

logger = logging.getLogger(__name__)

# 延迟导入 PaddleOCR，避免启动时加载
_ocr_instance = None


def get_ocr():
    """获取 PaddleOCR 单例（延迟加载）"""
    global _ocr_instance
    if _ocr_instance is None:
        try:
            from paddleocr import PaddleOCR
            logger.info("正在初始化 PaddleOCR...")
            _ocr_instance = PaddleOCR(
                use_angle_cls=True,      # 使用方向分类器
                lang='ch',               # 中文
                device='cpu',            # CPU 运行（PaddleOCR 3.x 参数）
                show_log=False,          # 减少日志输出
            )
            logger.info("PaddleOCR 初始化完成")
        except Exception as e:
            logger.error(f"PaddleOCR 初始化失败: {e}")
            raise
    return _ocr_instance


def extract_text_from_image(image: np.ndarray) -> str:
    """
    从 numpy 图像中提取文字
    
    Args:
        image: numpy.ndarray, BGR 格式
        
    Returns:
        识别到的文字，多行用 \n 分隔
    """
    try:
        ocr = get_ocr()
        result = ocr.ocr(image, cls=True)
        
        if not result or result[0] is None:
            return ""
        
        lines = []
        for block in result:
            if block is None:
                continue
            for line in block:
                if line:
                    text = line[1][0]  # 提取文字内容
                    confidence = line[1][1]
                    if confidence > 0.5:  # 过滤低置信度
                        lines.append(text)
        
        return '\n'.join(lines)
        
    except Exception as e:
        logger.error(f"OCR 识别失败: {e}")
        return ""


def extract_text(image_path: str) -> str:
    """
    从图片文件中提取文字
    
    Args:
        image_path: 图片文件路径
        
    Returns:
        识别到的文字
    """
    path = Path(image_path)
    if not path.exists():
        logger.error(f"图片文件不存在: {image_path}")
        return ""
    
    try:
        import cv2
        image = cv2.imread(str(path))
        if image is None:
            logger.error(f"无法读取图片: {image_path}")
            return ""
        return extract_text_from_image(image)
    except Exception as e:
        logger.error(f"读取图片失败: {e}")
        return ""


class OCREngine:
    """
    OCR 引擎封装类
    用于在子进程中初始化
    """
    
    def __init__(self):
        self._ocr = None
        
    def _init_ocr(self):
        """延迟初始化"""
        if self._ocr is None:
            from paddleocr import PaddleOCR
            self._ocr = PaddleOCR(
                use_angle_cls=True,
                lang='ch',
                device='cpu',
                show_log=False,
            )
        return self._ocr
    
    def extract(self, image: np.ndarray) -> str:
        """提取文字"""
        ocr = self._init_ocr()
        result = ocr.ocr(image, cls=True)
        
        if not result or result[0] is None:
            return ""
        
        lines = []
        for block in result:
            if block is None:
                continue
            for line in block:
                if line:
                    text = line[1][0]
                    confidence = line[1][1]
                    if confidence > 0.5:
                        lines.append(text)
        
        return '\n'.join(lines)
    
    def extract_from_path(self, image_path: str) -> str:
        """从路径提取"""
        import cv2
        image = cv2.imread(image_path)
        if image is None:
            return ""
        return self.extract(image)


def create_ocr_engine() -> OCREngine:
    """创建新的 OCR 引擎实例（用于子进程）"""
    return OCREngine()
