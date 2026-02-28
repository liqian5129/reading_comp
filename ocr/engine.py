"""
OCR 引擎模块
使用 PaddleOCR 进行文字识别（兼容 PaddleOCR 3.x）
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


def _create_paddle_ocr():
    """创建 PaddleOCR 实例，兼容 2.x / 3.x 参数差异。"""
    from paddleocr import PaddleOCR
    import paddleocr as _pkg
    version = getattr(_pkg, '__version__', '2.0')
    major = int(version.split('.')[0])

    if major >= 3:
        # 3.x：server 级模型 + UVDoc 书页矫正（5-6s，精度最高）
        return PaddleOCR(
            lang='ch',
            use_doc_orientation_classify=True,   # PP-LCNet 检测页面旋转方向
            use_doc_unwarping=True,              # UVDoc 书页展平矫正
            text_detection_model_name='PP-OCRv5_server_det',
            text_recognition_model_name='PP-OCRv5_server_rec',
            text_det_limit_side_len=960,         # 限最长边 960px → 高分辨率检测
            text_det_limit_type='max',
            text_det_box_thresh=0.5,             # 降低阈值减少漏检
            text_det_unclip_ratio=1.8,           # 扩大框，覆盖密排书页文字
        )
    else:
        # 2.x：传统参数
        return PaddleOCR(
            use_angle_cls=True,
            lang='ch',
            use_gpu=False,
            show_log=False,
        )


def _extract_lines(result, score_thresh: float = 0.5) -> List[str]:
    """
    统一解析 PaddleOCR 2.x / 3.x 的识别结果，返回文字行列表。

    3.x 结果: List[OCRResult]，每个 OCRResult 支持 result['rec_texts'] / result['rec_scores']
    2.x 结果: List[List[List]]，每条记录格式为 [box, (text, confidence)]
    """
    if not result:
        return []

    lines = []
    first = result[0]

    # 3.x: OCRResult 对象，支持 dict-like 访问
    if hasattr(first, '__getitem__') and not isinstance(first, list):
        try:
            for r in result:
                texts = r['rec_texts']
                scores = r['rec_scores']
                for text, score in zip(texts, scores):
                    if score >= score_thresh and text.strip():
                        lines.append(text)
            return lines
        except (KeyError, TypeError):
            pass

    # 2.x: 嵌套 list 格式
    try:
        for block in result:
            if block is None:
                continue
            for item in block:
                if item and len(item) >= 2:
                    text = item[1][0]
                    confidence = item[1][1]
                    if confidence >= score_thresh and text.strip():
                        lines.append(text)
    except (IndexError, TypeError):
        pass

    return lines


def get_ocr():
    """获取 PaddleOCR 单例（延迟加载）"""
    global _ocr_instance
    if _ocr_instance is None:
        logger.info("正在初始化 PaddleOCR...")
        _ocr_instance = _create_paddle_ocr()
        logger.info("PaddleOCR 初始化完成")
    return _ocr_instance


def extract_text_from_image(image: np.ndarray) -> str:
    """
    从 numpy 图像中提取文字

    Args:
        image: numpy.ndarray, BGR 格式

    Returns:
        识别到的文字，多行用 \\n 分隔
    """
    try:
        ocr = get_ocr()
        result = ocr.predict(image)
        lines = _extract_lines(result)
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
    OCR 引擎封装类（用于在子进程中初始化）
    """

    def __init__(self):
        self._ocr = None

    def _init_ocr(self):
        """延迟初始化"""
        if self._ocr is None:
            self._ocr = _create_paddle_ocr()
        return self._ocr

    def extract(self, image: np.ndarray) -> str:
        """提取文字"""
        ocr = self._init_ocr()
        result = ocr.predict(image)
        lines = _extract_lines(result)
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
