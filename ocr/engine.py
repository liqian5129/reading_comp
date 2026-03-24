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
_crop_ocr_instance = None  # 专用于小图裁剪，不使用 UVDoc


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
        # 3.x：server 检测 + mobile 识别 + UVDoc 书页矫正
        return PaddleOCR(
            lang='ch',
            use_doc_orientation_classify=True,
            use_doc_unwarping=True,
            text_detection_model_name='PP-OCRv5_server_det',
            text_recognition_model_name='PP-OCRv5_server_rec',
            text_det_limit_side_len=1920,
            text_det_limit_type='max',
            text_det_box_thresh=0.5,   # 提高阈值，过滤书脊弯曲处产生的低置信度噪点框
            text_det_unclip_ratio=2.0, # 适当扩框，帮助合并同行相邻碎片
        )
    else:
        # 2.x：传统参数
        return PaddleOCR(
            use_angle_cls=True,
            lang='ch',
            use_gpu=False,
            show_log=False,
        )


def _create_crop_ocr():
    """
    创建专用于指尖小图裁剪的轻量 PaddleOCR 实例。
    关闭 UVDoc（use_doc_unwarping）和方向分类——这两个模块设计用于全页图，
    在 500×55px 的小图上会因尺寸检查失败而返回空结果。
    """
    from paddleocr import PaddleOCR
    import paddleocr as _pkg
    version = getattr(_pkg, '__version__', '2.0')
    major = int(version.split('.')[0])

    if major >= 3:
        return PaddleOCR(
            lang='ch',
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            text_detection_model_name='PP-OCRv5_server_det',
            text_recognition_model_name='PP-OCRv5_server_rec',
            text_det_limit_side_len=960,
            text_det_limit_type='max',
            text_det_box_thresh=0.4,
            text_det_unclip_ratio=1.8,
        )
    else:
        return PaddleOCR(
            use_angle_cls=False,
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


def _parse_paddle_result(result) -> tuple:
    """
    将 PaddleOCR 2.x / 3.x 的原始 result 统一解析为 (polys, texts, scores) 三元组。
    不做过滤，由调用方按需 filter。返回空列表表示解析失败。
    """
    if not result:
        return [], [], []
    if not isinstance(result, list):
        result = list(result)
    if not result:
        return [], [], []

    first = result[0]

    # 3.x: OCRResult dict-like 对象
    if hasattr(first, '__getitem__') and not isinstance(first, list):
        try:
            polys, texts, scores = [], [], []
            for r in result:
                polys.extend(r.get('rec_polys') or r.get('dt_polys') or [])
                texts.extend(r['rec_texts'] or [])
                scores.extend(r['rec_scores'] or [])
            return polys, texts, scores
        except (KeyError, TypeError):
            pass

    # 2.x: 嵌套 list 格式
    try:
        polys, texts, scores = [], [], []
        for block in result:
            if block is None:
                continue
            for item in block:
                if item and len(item) >= 2:
                    polys.append(item[0])
                    texts.append(item[1][0])
                    scores.append(item[1][1])
        return polys, texts, scores
    except (IndexError, TypeError):
        pass

    return [], [], []


def _extract_boxes(result, score_thresh: float = 0.4) -> List[dict]:
    """
    从 PaddleOCR 原始 result 提取每行 bounding box 信息。
    返回列表，每项为 {text, poly, cx, cy}。
    - poly: 4 点列表 [[x, y], ...]，坐标在输入图像的像素空间
    - cx, cy: 多边形中心点

    兼容 PaddleOCR 2.x / 3.x。
    """
    polys, texts, scores = _parse_paddle_result(result)
    boxes = []
    for poly, text, score in zip(polys, texts, scores):
        if score < score_thresh or not text.strip():
            continue
        pts = np.array(poly, dtype=np.float32)
        boxes.append({
            "text": text,
            "poly": [[float(p[0]), float(p[1])] for p in poly],
            "cx": float(pts[:, 0].mean()),
            "cy": float(pts[:, 1].mean()),
        })
    return boxes


def _extract_lines(result, score_thresh: float = 0.5) -> List[str]:
    """
    统一解析 PaddleOCR 2.x / 3.x 的识别结果，返回文字行列表。
    双页摊开时自动检测书脊，左页优先排列。
    """
    polys, texts, scores = _parse_paddle_result(result)
    if not polys:
        # 无坐标信息时退化为顺序输出（过滤低置信度）
        return [t for t, s in zip(texts, scores) if s >= score_thresh and t.strip()]
    return sort_dual_page_lines(polys, texts, scores, score_thresh)


def get_ocr():
    """获取 PaddleOCR 单例（延迟加载）"""
    global _ocr_instance
    if _ocr_instance is None:
        logger.info("正在初始化 PaddleOCR...")
        _ocr_instance = _create_paddle_ocr()
        logger.info("PaddleOCR 初始化完成")
    return _ocr_instance


def get_crop_ocr():
    """
    获取指尖小图专用 PaddleOCR 单例（无 UVDoc，无方向分类）。
    与 get_ocr() 共享检测/识别模型权重文件，只是配置不同，初始化快。
    """
    global _crop_ocr_instance
    if _crop_ocr_instance is None:
        logger.info("正在初始化 Crop PaddleOCR（无 UVDoc）...")
        _crop_ocr_instance = _create_crop_ocr()
        logger.info("Crop PaddleOCR 初始化完成")
    return _crop_ocr_instance


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

    # text_det_limit_side_len 只限制检测步骤，UVDoc 会处理全尺寸图，
    # 必须在这里提前缩图，否则 4K 输入会让 UVDoc 极慢
    _OCR_MAX_SIDE = 1920

    def extract(self, image: np.ndarray) -> str:
        """
        提取文字，双页摊开时分左右两半各自 OCR 再合并。

        分半处理的好处：
        - UVDoc 矫正单页弯曲比矫正双页更准
        - 书脊弯曲处的碎片不再跨页影响检测
        - 每页检测框数减半，识别速度提升约 2x
        """
        import cv2 as _cv2
        ocr = self._init_ocr()
        h, w = image.shape[:2]

        # 缩图（让 UVDoc 也在合理分辨率上跑）
        if max(h, w) > self._OCR_MAX_SIDE:
            scale = self._OCR_MAX_SIDE / max(h, w)
            image = _cv2.resize(image, (int(w * scale), int(h * scale)))
            h, w = image.shape[:2]

        # 分左右两半，书脊附近各留 1% 宽度的缓冲区避免脊边噪点
        margin = max(1, w // 100)
        mid = w // 2
        left_img  = image[:, :mid - margin]
        right_img = image[:, mid + margin:]

        left_lines  = self._extract_half(ocr, left_img)
        right_lines = self._extract_half(ocr, right_img)
        return '\n'.join(left_lines + right_lines)

    def _extract_half(self, ocr, half: np.ndarray) -> List[str]:
        """对单页半幅图像运行 OCR，返回按 Y 排序的文字行。"""
        result = list(ocr.predict(_sharpen(half)))
        return _extract_lines(result)

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


def extract_text_from_crop(image: np.ndarray) -> str:
    """
    对指尖裁剪的小图做快速 OCR。
    使用专用单例（无 UVDoc、无方向分类），兼容 500×55px 等小尺寸输入。
    """
    try:
        ocr = get_crop_ocr()
        result = ocr.predict(image)
        lines = _extract_lines(result)
        text = ' '.join(lines)
        logger.debug(f"Crop OCR 原始结果: lines={lines}")
        return text
    except Exception as e:
        logger.error(f"Crop OCR 失败: {e}")
        return ""


def extract_word_at_x(box: dict, px: float, window: int = 4) -> str:
    """
    在行级 box 中根据指尖 X 坐标提取指向的词。
    1. 等宽插值定位 char_idx
    2. jieba 分词后找到 char_idx 所在词，对齐自然词边界
    3. jieba 不可用时退化为固定窗口截取
    """
    text = box["text"]
    N = len(text)
    if N == 0:
        return text

    poly = np.array(box["poly"], dtype=np.float32)
    sorted_by_x = sorted(poly.tolist(), key=lambda p: p[0])
    left_x  = float(np.mean([p[0] for p in sorted_by_x[:2]]))
    right_x = float(np.mean([p[0] for p in sorted_by_x[2:]]))
    line_w = max(right_x - left_x, 1.0)

    char_idx = int((px - left_x) / line_w * N)
    char_idx = max(0, min(N - 1, char_idx))

    # jieba 分词：找 char_idx 所在词
    try:
        import jieba
        words = list(jieba.cut(text, cut_all=False))
        offset = 0
        for i, w in enumerate(words):
            end = offset + len(w)
            if offset <= char_idx < end:
                # 单字符词（助词/标点）→ 合并下一个词
                if len(w) == 1 and i + 1 < len(words):
                    return w + words[i + 1]
                return w
            offset = end
    except ImportError:
        pass

    # fallback：固定窗口
    half = window // 2
    start = max(0, char_idx - half)
    end   = min(N, start + window)
    return text[start:end]
