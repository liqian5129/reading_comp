"""
自动扫描器

始终在后台运行，每 N 秒执行一次（默认 10s）：
1. 拍照（持久化摄像头连接，无开关开销）
2. OCR 识别（独立子进程，不阻塞主程序；PaddleOCR 内置 UVDoc 书页矫正）
3. 若识别到文字 → 调用 on_snapshot 更新 AI 上下文

阅读 session 激活后额外执行：
4. 翻页检测
5. 存数据库
"""
import asyncio
import logging
import cv2
import numpy as np
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, Callable

from camera import fingerprint, is_page_turn
from camera.capture import CameraCapture, find_external_camera
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

    始终在后台运行（无需阅读 session）：
    - on_snapshot 在每次 OCR 有结果时被调用，供上层更新 AI 上下文

    阅读 session 期间（set_session 后）：
    - 检测翻页并将快照存入数据库
    """

    def __init__(self, session_manager):
        self.session_manager = session_manager
        self.interval = config.AUTO_SCAN_INTERVAL

        # 持久化摄像头（避免每次开关的开销）
        self._camera: Optional[CameraCapture] = None

        # 进程池（OCR 专用，避免阻塞主程序）
        self._executor: Optional[ProcessPoolExecutor] = None

        # 状态
        self._running = False
        self._scan_task: Optional[asyncio.Task] = None

        # 会话相关（可选）
        self._session_id: Optional[str] = None
        self._last_fingerprint: Optional[str] = None
        self._last_snapshot_id: Optional[int] = None
        self._page_turn_count = 0

        # 回调
        self.on_page_turn: Optional[Callable] = None
        self.on_snapshot: Optional[Callable[[str, str], None]] = None

        # 视觉分析器（可选）
        self._vision_analyzer = None

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

        self._session_id = None
        self._last_fingerprint = None
        self._last_snapshot_id = None
        logger.info("自动扫描已停止")

    # ------------------------------------------------------------------
    # Session 控制
    # ------------------------------------------------------------------

    def set_vision_analyzer(self, analyzer):
        """设置视觉分析器"""
        self._vision_analyzer = analyzer

    def set_session(self, session_id: str):
        """绑定阅读 session，后续扫描会存库并检测翻页"""
        self._session_id = session_id
        self._last_fingerprint = None
        self._last_snapshot_id = None
        self._page_turn_count = 0
        logger.info(f"扫描器已绑定 session: {session_id}")

    def clear_session(self):
        """解绑 session，扫描器继续运行但不再存库"""
        self._session_id = None
        self._last_fingerprint = None
        self._last_snapshot_id = None
        logger.info("扫描器已解绑 session，仍继续后台扫描")

    # ------------------------------------------------------------------
    # 手动触发
    # ------------------------------------------------------------------

    async def manual_scan(self) -> Optional[Tuple[str, str, str]]:
        """手动触发一次扫描"""
        return await self._do_scan(force_save=True)

    # ------------------------------------------------------------------
    # 内部逻辑
    # ------------------------------------------------------------------

    async def _scan_loop(self):
        """扫描主循环"""
        while self._running:
            try:
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
            ts = int(datetime.now().timestamp() * 1000)
            image_path = config.SNAPSHOTS_DIR / f"current_{ts}.jpg"
            cv2.imwrite(str(image_path), frame)

            # 5. 始终通知上层（更新 AI 上下文）
            if self.on_snapshot:
                try:
                    self.on_snapshot(ocr_text, str(image_path))
                except Exception as e:
                    logger.error(f"on_snapshot 回调失败: {e}")

            # 6. Session 激活时才做翻页检测和存库
            if not self._session_id:
                # 无 session 时也定期触发视觉分析（由内部间隔控制）
                if self._vision_analyzer:
                    self._vision_analyzer.trigger(str(image_path))
                return None

            is_new_page = is_page_turn(self._last_fingerprint, fp)
            should_save = force_save or is_new_page or self._last_fingerprint is None

            if should_save:
                snapshot = await self.session_manager.add_snapshot(
                    str(image_path), ocr_text, fp
                )
                self._last_snapshot_id = snapshot.id
                self._last_fingerprint = fp

                if is_new_page:
                    self._page_turn_count += 1
                    logger.info(f"检测到翻页，第 {self._page_turn_count} 页")
                    # 翻页时用 force=True 立即触发视觉分析
                    if self._vision_analyzer:
                        self._vision_analyzer.trigger(str(image_path), force=True)
                    if self.on_page_turn:
                        try:
                            self.on_page_turn(self._page_turn_count)
                        except Exception as e:
                            logger.error(f"on_page_turn 回调失败: {e}")
                else:
                    # 非翻页也定期触发（由内部间隔控制）
                    if self._vision_analyzer:
                        self._vision_analyzer.trigger(str(image_path))

                logger.debug(f"快照已保存: {snapshot.id}")
                return str(image_path), ocr_text, fp
            else:
                logger.debug("页面未变化，跳过保存")
                return None

        except Exception as e:
            logger.error(f"扫描失败: {e}")
            return None

    # ------------------------------------------------------------------
    # 状态查询
    # ------------------------------------------------------------------

    def is_running(self) -> bool:
        return self._running

    def get_stats(self) -> dict:
        return {
            "running": self._running,
            "session_id": self._session_id,
            "page_turn_count": self._page_turn_count,
            "last_fingerprint": self._last_fingerprint,
        }
