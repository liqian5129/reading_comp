"""
自动扫描器

每 2 秒执行一次：
1. 拍照
2. 透视矫正
3. OCR 识别（独立子进程，不阻塞主程序）
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

from camera import capture_frame_async, correct_perspective, fingerprint, is_page_turn
from ocr.engine import create_ocr_engine, extract_text_from_image
from config import config

logger = logging.getLogger(__name__)


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
        # 解码图像
        nparr = np.frombuffer(image_bytes, np.uint8)
        image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        if image is None:
            return "", ""
        
        # 初始化 OCR 引擎
        ocr = init_ocr_in_process()
        
        # OCR 识别
        ocr_text = ocr.extract(image)
        
        # 计算指纹
        fp = fingerprint(image)
        
        return ocr_text, fp
        
    except Exception as e:
        logger.error(f"OCR 子进程处理失败: {e}")
        return "", ""


class AutoScanner:
    """
    自动扫描器
    
    后台协程，定期扫描书页并识别文字
    """
    
    def __init__(self, session_manager):
        self.session_manager = session_manager
        self.interval = config.AUTO_SCAN_INTERVAL
        
        # 进程池（用于 OCR，避免阻塞主程序）
        self._executor: Optional[ProcessPoolExecutor] = None
        
        # 状态
        self._running = False
        self._session_id: Optional[str] = None
        self._scan_task: Optional[asyncio.Task] = None
        
        # 页面追踪
        self._last_fingerprint: Optional[str] = None
        self._last_snapshot_id: Optional[int] = None
        self._page_turn_count = 0
        
        # 回调
        self.on_page_turn: Optional[Callable] = None
        self.on_snapshot: Optional[Callable[[str, str], None]] = None
        
    async def start(self, session_id: str):
        """
        启动自动扫描
        
        Args:
            session_id: 当前会话 ID
        """
        if self._running:
            logger.warning("扫描器已在运行")
            return
        
        self._session_id = session_id
        self._running = True
        
        # 创建进程池
        self._executor = ProcessPoolExecutor(max_workers=1)
        
        # 启动扫描任务
        self._scan_task = asyncio.create_task(self._scan_loop())
        
        logger.info(f"自动扫描已启动，间隔 {self.interval} 秒")
    
    async def stop(self):
        """停止自动扫描"""
        self._running = False
        
        if self._scan_task:
            self._scan_task.cancel()
            try:
                await self._scan_task
            except asyncio.CancelledError:
                pass
            self._scan_task = None
        
        if self._executor:
            self._executor.shutdown(wait=True)
            self._executor = None
        
        self._session_id = None
        self._last_fingerprint = None
        self._last_snapshot_id = None
        
        logger.info("自动扫描已停止")
    
    async def manual_scan(self) -> Optional[Tuple[str, str, str]]:
        """
        手动触发一次扫描
        
        Returns:
            (image_path, ocr_text, fingerprint) 或 None
        """
        return await self._do_scan(force_save=True)
    
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
        
        Args:
            force_save: 强制保存（用于手动扫描）
            
        Returns:
            (image_path, ocr_text, fingerprint) 或 None
        """
        if not self._session_id:
            return None
        
        try:
            # 1. 拍照（异步，不阻塞）
            frame = await capture_frame_async(config.CAMERA_DEVICE)
            if frame is None:
                logger.warning("拍照失败")
                return None
            
            # 2. 透视矫正（CPU 但很快）
            corrected = correct_perspective(frame)
            
            # 3. 编码图像为 bytes（用于进程间传输）
            _, encoded = cv2.imencode('.jpg', corrected)
            image_bytes = encoded.tobytes()
            
            # 4. OCR 识别（在子进程中执行，不阻塞主程序）
            loop = asyncio.get_event_loop()
            ocr_text, fp = await loop.run_in_executor(
                self._executor,
                process_image_worker,
                image_bytes
            )
            
            if not ocr_text:
                logger.debug("OCR 未识别到文字")
                return None
            
            # 5. 翻页检测
            is_new_page = is_page_turn(self._last_fingerprint, fp)
            
            # 6. 保存决策
            should_save = force_save or is_new_page or self._last_fingerprint is None
            
            if should_save:
                # 保存图片
                ts = int(datetime.now().timestamp() * 1000)
                image_path = config.SNAPSHOTS_DIR / f"{self._session_id}_{ts}.jpg"
                cv2.imwrite(str(image_path), corrected)
                
                # 添加到数据库
                snapshot = await self.session_manager.add_snapshot(
                    str(image_path), ocr_text, fp
                )
                
                self._last_snapshot_id = snapshot.id
                self._last_fingerprint = fp
                
                if is_new_page:
                    self._page_turn_count += 1
                    logger.info(f"检测到翻页，第 {self._page_turn_count} 页")
                    if self.on_page_turn:
                        try:
                            self.on_page_turn(self._page_turn_count)
                        except Exception as e:
                            logger.error(f"on_page_turn 回调失败: {e}")
                
                if self.on_snapshot:
                    try:
                        self.on_snapshot(ocr_text, str(image_path))
                    except Exception as e:
                        logger.error(f"on_snapshot 回调失败: {e}")
                
                logger.debug(f"快照已保存: {snapshot.id}")
                return str(image_path), ocr_text, fp
            else:
                logger.debug("页面未变化，跳过保存")
                return None
                
        except Exception as e:
            logger.error(f"扫描失败: {e}")
            return None
    
    def is_running(self) -> bool:
        """是否正在运行"""
        return self._running
    
    def get_stats(self) -> dict:
        """获取统计信息"""
        return {
            "running": self._running,
            "session_id": self._session_id,
            "page_turn_count": self._page_turn_count,
            "last_fingerprint": self._last_fingerprint
        }
