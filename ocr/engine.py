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


def _sharpen(image: np.ndarray) -> np.ndarray:
    """
    对图像做 Unsharp Mask 锐化，提升 OCR 对模糊/低对比度书页的识别率。
    强度适中，不会引入过多噪点。
    """
    import cv2 as _cv2
    blurred = _cv2.GaussianBlur(image, (0, 0), sigmaX=2.0)
    return _cv2.addWeighted(image, 1.5, blurred, -0.5, 0)


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
            text_det_limit_side_len=1280,        # 与相机输出宽度一致，避免缩图损失细节
            text_det_limit_type='max',
            text_det_box_thresh=0.4,             # 略降阈值，减少漏检
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


def sort_dual_page_lines(polys, texts, scores, score_thresh: float = 0.5) -> List[str]:
    """
    对双页摊开书页的 OCR 结果重新排序：左页从上到下，再接右页从上到下。

    原理：找 X 方向最大间隙作为书脊分界线，把文字框分成左右两组，
    各组内部按 Y 坐标（中心点）升序排列，最后合并。
    若检测不到明显书脊（单页），则直接按 Y 排序返回。

    Args:
        polys:       每个文字框的多边形顶点列表
        texts:       对应文字
        scores:      对应置信度
        score_thresh: 过滤低置信度框的阈值

    Returns:
        重排后的文字行列表
    """
    items = []
    for poly, text, score in zip(polys, texts, scores):
        if score < score_thresh or not text.strip():
            continue
        pts = np.array(poly, dtype=np.float32)
        cx = float(pts[:, 0].mean())
        cy = float(pts[:, 1].mean())
        items.append((cx, cy, text))

    if not items:
        return []

    xs = sorted(item[0] for item in items)

    # 框数太少时不做双页检测，直接按 Y 排
    if len(xs) < 4:
        return [t for _, _, t in sorted(items, key=lambda x: x[1])]

    # 找 X 分布中最大的相邻间隙 → 书脊位置
    gaps = [(xs[i + 1] - xs[i], (xs[i] + xs[i + 1]) / 2.0)
            for i in range(len(xs) - 1)]
    max_gap_val, spine_x = max(gaps, key=lambda g: g[0])
    avg_gap = (xs[-1] - xs[0]) / (len(xs) - 1)

    # 最大间隙明显大于平均间隙 → 判定为双页布局
    if max_gap_val > avg_gap * 2.5 and max_gap_val > 30:
        left  = sorted([(cy, t) for cx, cy, t in items if cx < spine_x],  key=lambda x: x[0])
        right = sorted([(cy, t) for cx, cy, t in items if cx >= spine_x], key=lambda x: x[0])
        logger.debug(f"双页布局: 书脊x={spine_x:.0f} 左{len(left)}行 右{len(right)}行")
        return [t for _, t in left] + [t for _, t in right]

    # 单页，直接按 Y 排序
    return [t for _, _, t in sorted(items, key=lambda x: x[1])]


def _extract_lines(result, score_thresh: float = 0.5) -> List[str]:
    """
    统一解析 PaddleOCR 2.x / 3.x 的识别结果，返回文字行列表。
    双页摊开时自动检测书脊，左页优先排列。

    3.x 结果: List[OCRResult]，每个 OCRResult 支持 result['rec_texts'] / result['rec_scores']
    2.x 结果: List[List[List]]，每条记录格式为 [box, (text, confidence)]
    """
    if not result:
        return []

    # PaddleOCR 3.x 的 predict() 返回生成器，先转 list 以支持索引访问
    if not isinstance(result, list):
        result = list(result)
    if not result:
        return []

    first = result[0]

    # 3.x: OCRResult 对象，支持 dict-like 访问，且含 rec_polys 坐标
    if hasattr(first, '__getitem__') and not isinstance(first, list):
        try:
            all_polys, all_texts, all_scores = [], [], []
            for r in result:
                polys  = r.get('rec_polys')  or r.get('dt_polys')  or []
                texts  = r['rec_texts']  or []
                scores = r['rec_scores'] or []
                all_polys.extend(polys)
                all_texts.extend(texts)
                all_scores.extend(scores)
            if all_polys:
                return sort_dual_page_lines(all_polys, all_texts, all_scores, score_thresh)
            # 无坐标信息时退化为顺序输出
            return [t for t, s in zip(all_texts, all_scores)
                    if s >= score_thresh and t.strip()]
        except (KeyError, TypeError):
            pass

    # 2.x: 嵌套 list 格式，box 即多边形顶点
    try:
        all_polys, all_texts, all_scores = [], [], []
        for block in result:
            if block is None:
                continue
            for item in block:
                if item and len(item) >= 2:
                    all_polys.append(item[0])
                    all_texts.append(item[1][0])
                    all_scores.append(item[1][1])
        if all_polys:
            return sort_dual_page_lines(all_polys, all_texts, all_scores, score_thresh)
    except (IndexError, TypeError):
        pass

    return []


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
        """提取文字，自动处理双页书页排序"""
        ocr = self._init_ocr()
        result = list(ocr.predict(_sharpen(image)))
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
