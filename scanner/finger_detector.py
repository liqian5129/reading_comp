"""
指尖检测器
使用 MediaPipe Tasks Hand Landmarker API（mediapipe >= 0.10）
检测食指指尖位置（landmark 8）
"""
import logging
import urllib.request
from pathlib import Path

import numpy as np
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# 模型文件本地缓存路径
_MODEL_PATH = Path(__file__).parent.parent / "data" / "hand_landmarker.task"
_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task"
)


def _ensure_model() -> str:
    """下载模型文件（首次使用时，约 10MB）"""
    if _MODEL_PATH.exists():
        return str(_MODEL_PATH)
    _MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    logger.info(f"[FingerDetector] 下载 hand_landmarker.task 模型（约 10MB）...")
    urllib.request.urlretrieve(_MODEL_URL, str(_MODEL_PATH))
    logger.info(f"[FingerDetector] 模型已保存到 {_MODEL_PATH}")
    return str(_MODEL_PATH)


@dataclass
class FingerPoint:
    x: float      # 归一化 [0,1]，相对于输入图像宽度
    y: float      # 归一化 [0,1]，相对于输入图像高度
    pixel_x: int  # 在输入图像的像素坐标（landmark 8，指尖）
    pixel_y: int
    hand: str = "Unknown"   # "Left" | "Right" | "Unknown"
    # landmark 6（PIP 关节）归一化坐标，与 x/y 同比例，用于计算指向向量 lm6→lm8
    dir_x: float = 0.0
    dir_y: float = 0.0


class FingerDetector:
    """MediaPipe Tasks Hand Landmarker 封装，检测食指指尖（landmark 8）"""

    def __init__(self):
        self._detector = None

    def is_available(self) -> bool:
        try:
            import mediapipe  # noqa: F401
            return True
        except ImportError:
            return False

    def _ensure_init(self):
        if self._detector is not None:
            return
        import mediapipe as mp
        model_path = _ensure_model()
        base_options = mp.tasks.BaseOptions(model_asset_path=model_path)
        options = mp.tasks.vision.HandLandmarkerOptions(
            base_options=base_options,
            num_hands=2,
            min_hand_detection_confidence=0.7,
            min_hand_presence_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        self._detector = mp.tasks.vision.HandLandmarker.create_from_options(options)
        logger.info("[FingerDetector] HandLandmarker 初始化完成")

    def process(self, image_bgr: np.ndarray) -> Optional[FingerPoint]:
        """
        同步方法，供 run_in_executor 调用。
        输入图像建议降采样到 960p 以降低 CPU 负担。
        多只手取离图像中心最近的那只（食指指尖 landmark 8）。
        """
        try:
            import cv2
            import mediapipe as mp
            self._ensure_init()

            h, w = image_bgr.shape[:2]
            image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=image_rgb)

            result = self._detector.detect(mp_image)

            if not result.hand_landmarks:
                return None

            cx_img, cy_img = w / 2.0, h / 2.0
            best: Optional[FingerPoint] = None
            best_dist = float("inf")

            for i, hand_landmarks in enumerate(result.hand_landmarks):
                # landmark 8 = 食指指尖，landmark 6 = PIP 关节（用于算指向方向）
                lm8 = hand_landmarks[8]
                lm6 = hand_landmarks[6]
                px = int(lm8.x * w)
                py = int(lm8.y * h)
                dist = (px - cx_img) ** 2 + (py - cy_img) ** 2
                if dist < best_dist:
                    best_dist = dist
                    hand_name = "Unknown"
                    if result.handedness and i < len(result.handedness):
                        cats = result.handedness[i]
                        if cats:
                            hand_name = cats[0].category_name
                    best = FingerPoint(
                        x=lm8.x, y=lm8.y, pixel_x=px, pixel_y=py,
                        hand=hand_name,
                        dir_x=lm6.x, dir_y=lm6.y,   # 归一化，与 lm8.x/y 同比例
                    )

            return best

        except Exception as e:
            logger.error(f"[FingerDetector] process 失败: {e}")
            return None

    def close(self):
        if self._detector:
            self._detector.close()
            self._detector = None
