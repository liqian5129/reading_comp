"""
自动扫描器

始终在后台运行，每 N 秒执行一次（默认 10s）：
1. 拍照（持久化摄像头连接，无开关开销）
2. OCR 识别（独立子进程，不阻塞主程序；PaddleOCR 内置 UVDoc 书页矫正）
3. 若识别到文字 → 调用 on_snapshot 更新 AI 上下文
4. 翻页/换书检测 → 调用 on_book_info 记录阅读活动
"""
import asyncio
import logging
import time
import cv2
import numpy as np  # noqa: F401 — used in _finger_loop perspectiveTransform
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, Callable

from camera import fingerprint, is_page_turn
from camera.capture import CameraCapture, find_external_camera
from camera.perspective import load_fixed_homography, apply_fixed_homography, transform_point
from scanner.finger_detector import compute_probe_point
from ocr.engine import create_ocr_engine
from config import config

logger = logging.getLogger(__name__)

# OCR 内容少于此字数视为无效帧（与 main.py 保持一致）
_MIN_OCR_LEN = 6

# 全局 OCR 引擎（在子进程中使用）
_ocr_engine = None


def init_ocr_in_process():
    """在子进程中初始化 OCR 引擎"""
    global _ocr_engine
    if _ocr_engine is None:
        _ocr_engine = create_ocr_engine()
    return _ocr_engine


def process_image_worker(image_bytes: bytes) -> Tuple[str, str]:
    """
    在子进程中处理图像的 Worker 函数

    Args:
        image_bytes: 图像的字节数据（numpy 转 bytes）

    Returns:
        (ocr_text, fingerprint)
    """
    try:
        nparr = np.frombuffer(image_bytes, np.uint8)
        image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if image is None:
            return "", ""

        ocr = init_ocr_in_process()
        ocr_text = ocr.extract(image)
        fp = fingerprint(image)

        return ocr_text, fp

    except Exception as e:
        import logging as _logging
        _logging.getLogger(__name__).error(f"OCR 子进程处理失败: {e}")
        return "", ""


class AutoScanner:
    """
    自动扫描器

    始终在后台运行：
    - on_snapshot 在每次 OCR 有结果时被调用，供上层更新 AI 上下文
    - on_book_info 在识别到页码/书名时被调用，供上层记录阅读活动
    """

    def __init__(self, session_manager=None):
        self.session_manager = session_manager  # 保留引用兼容，但不用于 session 管理
        self.interval = config.AUTO_SCAN_INTERVAL

        # 持久化摄像头（避免每次开关的开销）
        self._camera: Optional[CameraCapture] = None

        # 进程池（OCR 专用，避免阻塞主程序）
        self._executor: Optional[ProcessPoolExecutor] = None

        # 状态
        self._running = False
        self._scan_task: Optional[asyncio.Task] = None

        self._last_fingerprint: Optional[str] = None
        self._page_turn_count = 0

        # 回调
        self.on_page_turn: Optional[Callable] = None
        self.on_snapshot: Optional[Callable[[str, str], None]] = None
        self.on_book_info: Optional[Callable[[dict, str], None]] = None

        # KimiOCR（可选，替代本地 PaddleOCR）
        self._kimi_ocr = None
        self._last_page_num: int = -1
        self._last_book_title: str = ""

        # 语音录音器引用（用于 OCR/ASR 资源冲突规避）
        self._voice_recorder = None

        # 指尖检测
        self._finger_detector = None
        self._last_stable_pos: Optional[tuple] = None   # (norm_x, norm_y)
        self._finger_dwell_since: float = 0.0
        self._finger_dwell_threshold: float = config.FINGER_DWELL_SECONDS
        self._last_corrected_frame: Optional[np.ndarray] = None  # 保存最新透视校正帧
        self.on_finger_stable: Optional[Callable[[int, int, np.ndarray, int, int], None]] = None
        # 回调参数: (pixel_x, pixel_y, corrected_frame, probe_x, probe_y)

        # 固定透视校正矩阵（可选，由标定脚本生成）
        if config.PERSPECTIVE_ENABLED:
            self._perspective_M = load_fixed_homography(config.PERSPECTIVE_HOMOGRAPHY_FILE)
            if self._perspective_M is not None:
                logger.info("透视校正已启用，将对每帧应用固定单应矩阵")
        else:
            self._perspective_M = None

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def start(self):
        """启动扫描器（不需要 session）"""
        if self._running:
            logger.warning("扫描器已在运行")
            return

        self._running = True
        self._executor = ProcessPoolExecutor(max_workers=1)

        device = find_external_camera() if config.CAMERA_AUTO_DETECT else config.CAMERA_DEVICE
        self._camera = CameraCapture(device)
        if not self._camera.open():
            logger.error("摄像头无法打开，扫描器启动失败")
            self._running = False
            return

        self._scan_task = asyncio.create_task(self._scan_loop())
        logger.info(f"自动扫描已启动，间隔 {self.interval} 秒")

    async def stop(self):
        """停止扫描器"""
        self._running = False

        if self._scan_task:
            self._scan_task.cancel()
            try:
                await self._scan_task
            except asyncio.CancelledError:
                pass
            self._scan_task = None

        if self._camera:
            self._camera.close()
            self._camera = None

        if self._executor:
            self._executor.shutdown(wait=False)
            self._executor = None

        self._last_fingerprint = None
        logger.info("自动扫描已停止")

    # ------------------------------------------------------------------
    # 配置
    # ------------------------------------------------------------------

    def set_kimi_ocr(self, kimi_ocr):
        """设置 KimiOCR，同时注册内部回调"""
        self._kimi_ocr = kimi_ocr
        kimi_ocr.on_book_info = self._on_kimi_book_info

    def set_voice_recorder(self, recorder):
        """绑定语音录音器，扫描时可感知 ASR 状态，避免与 OCR 争抢 CPU"""
        self._voice_recorder = recorder

    # ------------------------------------------------------------------
    # 手动触发
    # ------------------------------------------------------------------

    async def manual_scan(self) -> Optional[Tuple[str, str, str]]:
        """手动触发一次扫描"""
        return await self._do_scan(force_save=True)

    # ------------------------------------------------------------------
    # KimiOCR 回调
    # ------------------------------------------------------------------

    def _on_kimi_book_info(self, book_info: dict, image_path: str):
        """KimiOCR 元数据回调：翻页/换书检测"""
        page_num = book_info.get("page_num", -1)
        # 模型可能返回 None（无页码）或列表 [302, 303]（双页），统一转为 int
        if page_num is None:
            page_num = -1
        elif isinstance(page_num, list):
            page_num = page_num[0] if page_num else -1
        book_title = book_info.get("book_title", "")

        # 换书检测
        if book_title and book_title != self._last_book_title:
            self._last_book_title = book_title
            self._last_page_num = -1
            logger.info(f"检测到新书: 《{book_title}》")

        # 翻页检测
        if page_num > 0 and page_num != self._last_page_num:
            self._last_page_num = page_num
            self._page_turn_count += 1
            logger.info(f"翻页: 第 {page_num} 页 ({self._page_turn_count} 次)")
            if self.on_page_turn:
                try:
                    self.on_page_turn(self._page_turn_count)
                except Exception as e:
                    logger.error(f"on_page_turn 回调失败: {e}")

        # 通知上层（记录阅读活动 + 更新书籍上下文）
        if self.on_book_info:
            try:
                self.on_book_info(book_info, image_path)
            except Exception as e:
                logger.error(f"on_book_info 回调失败: {e}")

    # ------------------------------------------------------------------
    # 内部逻辑
    # ------------------------------------------------------------------

    async def _scan_loop(self):
        """扫描主循环"""
        while self._running:
            try:
                # ASR 录音/推理期间跳过 OCR：
                # PaddleOCR server 模型是 CPU 密集型（5-6s），会与 FunASR MPS 推理争抢 CPU
                if self._voice_recorder and self._voice_recorder.is_busy():
                    logger.debug("⏸️ OCR 扫描跳过（ASR 处理中，避免 CPU 竞争）")
                else:
                    await self._do_scan()
                await asyncio.sleep(self.interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"扫描循环异常: {e}")
                await asyncio.sleep(self.interval)

    async def _do_scan(self, force_save: bool = False) -> Optional[Tuple[str, str, str]]:
        """
        执行一次扫描

        Returns:
            (image_path, ocr_text, fingerprint) 当有结果时；否则 None
        """
        if not self._camera or not self._camera.is_opened():
            return None

        try:
            loop = asyncio.get_event_loop()

            # 1. 拍照（线程池，不阻塞事件循环）
            frame = await loop.run_in_executor(None, self._camera.read)
            if frame is None:
                logger.warning("拍照失败")
                return None

            # 1.5. 透视校正（固定单应矩阵，~1ms，OCR/Vision/哈希全部受益）
            if self._perspective_M is not None:
                frame = apply_fixed_homography(frame, self._perspective_M)

            # 保存校正后的帧供指尖裁剪使用（与 OCR 坐标空间一致）
            self._last_corrected_frame = frame

            # KimiOCR 路径：指纹去重后 fire-and-forget，结果通过回调返回
            if self._kimi_ocr is not None:
                fp = fingerprint(frame)
                if fp and self._last_fingerprint:
                    from camera.page_tracker import hamming_distance
                    dist = hamming_distance(self._last_fingerprint, fp)
                    logger.info(f"[指纹] 汉明距离={dist}（阈值12，>12判定为翻页）")
                if fp and not is_page_turn(self._last_fingerprint, fp, threshold=12):
                    logger.debug("KimiOCR: 页面未变化（指纹相同），跳过")
                    return None

                now = datetime.now()
                ts = now.strftime("%Y%m%d_%H%M%S_") + f"{now.microsecond // 1000:03d}"
                image_path = config.SNAPSHOTS_DIR / f"snapshot_{ts}.jpg"

                def _save(f, p):
                    cv2.imwrite(str(p), f)

                await loop.run_in_executor(None, _save, frame, image_path)
                triggered = self._kimi_ocr.trigger(str(image_path))
                if triggered:
                    # 只有 OCR 成功触发时才更新指纹，否则下次扫描仍可重试
                    self._last_fingerprint = fp
                else:
                    logger.debug("KimiOCR: trigger 被跳过，保留旧指纹以便下次重试")
                return None

            # 2. 编码原始帧（在线程池中执行，避免阻塞）
            # 注：OCR 子进程内部的 PaddleOCR 会通过 UVDoc 做书页矫正，无需在此重复矫正
            def encode_frame(f):
                _, encoded = cv2.imencode('.jpg', f)
                return encoded.tobytes()

            image_bytes = await loop.run_in_executor(None, encode_frame, frame)

            # 3. OCR 识别（独立进程池，不阻塞事件循环）
            ocr_text, fp = await loop.run_in_executor(
                self._executor,
                process_image_worker,
                image_bytes
            )

            # 4. OCR 内容不足时通知上层（不触发视觉分析，节省 token）
            if not ocr_text or len(ocr_text.strip()) < _MIN_OCR_LEN:
                logger.debug(f"OCR 内容不足（{len(ocr_text.strip()) if ocr_text else 0}字），跳过视觉分析")
                if self.on_snapshot:
                    try:
                        self.on_snapshot("", "")
                    except Exception as e:
                        logger.error(f"on_snapshot 回调失败: {e}")
                return None

            # 4. 保存原始帧供回调使用
            now = datetime.now()
            ts = now.strftime("%Y%m%d_%H%M%S_") + f"{now.microsecond // 1000:03d}"
            image_path = config.SNAPSHOTS_DIR / f"snapshot_{ts}.jpg"
            cv2.imwrite(str(image_path), frame)

            # 5. 始终通知上层（更新 AI 上下文）
            if self.on_snapshot:
                try:
                    self.on_snapshot(ocr_text, str(image_path))
                except Exception as e:
                    logger.error(f"on_snapshot 回调失败: {e}")

            return str(image_path), ocr_text, fp

        except Exception as e:
            logger.error(f"扫描失败: {e}")
            return None

    # ------------------------------------------------------------------
    # 指尖检测
    # ------------------------------------------------------------------

    async def _finger_loop(self):
        """指尖检测主循环，在独立 task 中运行"""
        DETECT_INTERVAL = 1.0 / config.FINGER_DETECTION_FPS
        DWELL_MOVE_THRESHOLD = 0.02  # 归一化距离，超过则重置计时

        loop = asyncio.get_event_loop()
        logger.info("[Finger] 指尖检测循环已启动")

        while self._running:
            try:
                if self._camera is None or not self._camera.is_opened():
                    await asyncio.sleep(DETECT_INTERVAL)
                    continue

                raw_frame = await loop.run_in_executor(None, self._camera.read)
                if raw_frame is None:
                    await asyncio.sleep(DETECT_INTERVAL)
                    continue

                # 校正帧：用于裁剪和 OCR
                if self._perspective_M is not None:
                    corrected = apply_fixed_homography(raw_frame, self._perspective_M)
                else:
                    corrected = raw_frame
                self._last_corrected_frame = corrected

                # MediaPipe 在原始帧上检测（手的形态自然，不被透视扭曲）
                h_r, w_r = raw_frame.shape[:2]
                mp_scale = 960 / max(h_r, w_r)
                if mp_scale < 1.0:
                    small = cv2.resize(raw_frame, (int(w_r * mp_scale), int(h_r * mp_scale)))
                else:
                    small = raw_frame

                finger = await loop.run_in_executor(
                    None, self._finger_detector.process, small
                )

                if finger is None:
                    self._last_stable_pos = None
                    self._finger_dwell_since = 0.0
                    await asyncio.sleep(DETECT_INTERVAL)
                    continue

                now = time.time()
                if self._last_stable_pos is None:
                    self._last_stable_pos = (finger.x, finger.y)
                    self._finger_dwell_since = now
                else:
                    dx = abs(finger.x - self._last_stable_pos[0])
                    dy = abs(finger.y - self._last_stable_pos[1])
                    if dx > DWELL_MOVE_THRESHOLD or dy > DWELL_MOVE_THRESHOLD:
                        self._last_stable_pos = (finger.x, finger.y)
                        self._finger_dwell_since = now
                    elif now - self._finger_dwell_since >= self._finger_dwell_threshold:
                        self._finger_dwell_since = float('inf')  # 防止重复触发直到手指移动
                        px_r = int(finger.x * w_r)
                        py_r = int(finger.y * h_r)
                        if self._perspective_M is not None:
                            px, py = transform_point(px_r, py_r, self._perspective_M)
                        else:
                            px, py = px_r, py_r
                        if self.on_finger_stable and self._last_corrected_frame is not None:
                            # 探测点：lm8 身后 50px，与指尖同高，透视变换误差极小
                            probe_x, probe_y = compute_probe_point(
                                finger.x, finger.y, finger.dir_x, finger.dir_y, w_r, h_r
                            )
                            if self._perspective_M is not None:
                                dir_px, dir_py = transform_point(probe_x, probe_y, self._perspective_M)
                            else:
                                dir_px, dir_py = int(probe_x), int(probe_y)
                            self.on_finger_stable(px, py, self._last_corrected_frame.copy(),
                                                  dir_px, dir_py)

                await asyncio.sleep(DETECT_INTERVAL)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[Finger] 检测循环异常: {e}")
                await asyncio.sleep(DETECT_INTERVAL)

        logger.info("[Finger] 指尖检测循环已退出")

    # ------------------------------------------------------------------
    # 状态查询
    # ------------------------------------------------------------------

    def is_running(self) -> bool:
        return self._running

    def get_stats(self) -> dict:
        return {
            "running": self._running,
            "page_turn_count": self._page_turn_count,
            "last_fingerprint": self._last_fingerprint,
        }
