#!/usr/bin/env python3
"""
摄像头 + OCR 完整流水线可视化调试工具（含逐步计时）

布局:
  ┌──────────┬──────────┬──────────┬──────────┐
  │ ①原图    │②方向矫正  │③UVDoc展平│④文字检测  │
  │ capture  │ orient   │ unwarp   │ detect   │
  │ [Xms]   │ [Xms]    │ [Xms]   │ [Xms]   │
  ├──────────┴──────────┴──────────┴──────────┤
  │     ⑤ OCR 识别文字  [recog Xms | total Xs] │
  └──────────────────────────────────────────┘

快捷键:  Q/ESC 退出    S 立即扫描
"""
import sys
import time
import logging
import threading
from typing import Optional, List, Dict
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).parent))
from config import config
from camera.capture import CameraCapture

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("debug_viewer")


# ---------------------------------------------------------------------------
# 中文字体
# ---------------------------------------------------------------------------
_FONT_CANDIDATES = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode MS.ttf",
    "/Library/Fonts/Arial Unicode MS.ttf",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "C:/Windows/Fonts/msyh.ttc",
]
_FONT_PATH = next((p for p in _FONT_CANDIDATES if Path(p).exists()), "")


def _font(size: int) -> ImageFont.FreeTypeFont:
    if _FONT_PATH:
        try:
            return ImageFont.truetype(_FONT_PATH, size)
        except Exception:
            pass
    return ImageFont.load_default()


def draw_cn(img: np.ndarray, text: str, xy: tuple,
            size: int = 15, color=(220, 255, 220)) -> np.ndarray:
    pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    ImageDraw.Draw(pil).text(xy, text, font=_font(size), fill=color)
    return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)


def draw_cn_multiline(img: np.ndarray, lines: List[str], xy: tuple,
                      size: int = 14, gap: int = 4,
                      color=(180, 255, 180), max_chars: int = 40) -> np.ndarray:
    pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(pil)
    font = _font(size)
    x, y = xy
    step = size + gap
    h = img.shape[0]
    for raw in lines:
        while raw:
            chunk, raw = raw[:max_chars], raw[max_chars:]
            if y + step > h:
                break
            draw.text((x, y), chunk, font=font, fill=color)
            y += step
        if y + step > h:
            break
    return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)


# ---------------------------------------------------------------------------
# 布局常量
# ---------------------------------------------------------------------------
COLS        = 4
PANEL_W     = 380
PANEL_H     = 285
TEXT_ROW_H  = 300
WIN_W       = PANEL_W * COLS   # 1520
WIN_H       = PANEL_H + TEXT_ROW_H  # 585

# 计时条高度（面板底部）
TIMING_H = 22


def ms(t: float) -> str:
    return f"{t * 1000:.0f}ms"


def _resize(img: Optional[np.ndarray], w: int, h: int, bg=30) -> np.ndarray:
    if img is None:
        return np.full((h, w, 3), bg, dtype=np.uint8)
    ih, iw = img.shape[:2]
    scale = min(w / iw, h / ih)
    nw, nh = int(iw * scale), int(ih * scale)
    canvas = np.full((h, w, 3), bg, dtype=np.uint8)
    ox, oy = (w - nw) // 2, (h - nh) // 2
    canvas[oy:oy + nh, ox:ox + nw] = cv2.resize(img, (nw, nh))
    return canvas


def _label(p: np.ndarray, title: str, timing_ms: str = "",
           title_color=(0, 220, 220)) -> np.ndarray:
    """在面板顶部写标题，底部写计时。"""
    cv2.putText(p, title, (8, 22),
                cv2.FONT_HERSHEY_SIMPLEX, 0.60, title_color, 2)
    if timing_ms:
        bar = np.full((TIMING_H, PANEL_W, 3), 15, dtype=np.uint8)
        cv2.putText(bar, timing_ms, (6, 15),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.48, (60, 230, 60), 1)
        p[-TIMING_H:] = bar
    return p


# ---------------------------------------------------------------------------
# 各面板
# ---------------------------------------------------------------------------

def panel_original(frame: np.ndarray, t_capture: float,
                   scanning: bool, next_in: float) -> np.ndarray:
    p = _resize(frame, PANEL_W, PANEL_H)
    state = "OCR running..." if scanning else f"Next: {next_in:.1f}s  [S]=now"
    cv2.putText(p, state, (8, PANEL_H - TIMING_H - 4),
                cv2.FONT_HERSHEY_SIMPLEX, 0.40, (255, 220, 60), 1)
    _label(p, "1. Original", f"capture {ms(t_capture)}")
    return p


def panel_orientation(rot_img: Optional[np.ndarray],
                      angle: Optional[int], t: float) -> np.ndarray:
    p = _resize(rot_img, PANEL_W, PANEL_H)
    sub = f"angle={angle}deg" if angle is not None else ""
    if sub:
        cv2.putText(p, sub, (8, 42),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.44, (255, 220, 80), 1)
    _label(p, "2. Orientation", f"orient {ms(t)}" if t else "orient -")
    return p


def panel_unwarped(output_img: Optional[np.ndarray], t: float) -> np.ndarray:
    p = _resize(output_img, PANEL_W, PANEL_H)
    _label(p, "3. UVDoc Unwarp", f"unwarp {ms(t)}" if t else "unwarp -")
    return p


def panel_detection(output_img: Optional[np.ndarray],
                    rec_polys: list, rec_scores: list, t: float) -> np.ndarray:
    if output_img is not None:
        base = output_img.copy()
        for i, poly in enumerate(rec_polys):
            pts = np.array(poly, dtype=np.int32)
            cv2.polylines(base, [pts], True, (0, 230, 0), 2)
            if i < len(rec_scores):
                cx = int(np.mean(pts[:, 0]))
                cy = int(np.min(pts[:, 1])) - 4
                cv2.putText(base, f"{rec_scores[i]:.2f}", (cx, max(cy, 10)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.32, (0, 255, 100), 1)
    else:
        base = None
    p = _resize(base, PANEL_W, PANEL_H)
    n = len(rec_polys)
    cv2.putText(p, f"{n} regions", (8, 42),
                cv2.FONT_HERSHEY_SIMPLEX, 0.44, (255, 220, 80), 1)
    _label(p, "4. Text Detect", f"detect {ms(t)}" if t else "detect -")
    return p


def panel_text(lines: List[str], status: str, t_recog: float, t_total: float) -> np.ndarray:
    p = np.full((TEXT_ROW_H, WIN_W, 3), 18, dtype=np.uint8)

    # 状态行
    cv2.putText(p, f"5. OCR Result  |  {status}", (10, 22),
                cv2.FONT_HERSHEY_SIMPLEX, 0.50, (80, 160, 255), 1)
    cv2.line(p, (0, 30), (WIN_W, 30), (50, 50, 50), 1)

    if lines:
        mid = (len(lines) + 1) // 2
        p = draw_cn_multiline(p, lines[:mid],    xy=(10,           36))
        p = draw_cn_multiline(p, lines[mid:],    xy=(WIN_W // 2 + 10, 36))
    else:
        p = draw_cn(p, "（暂无识别文字）", xy=(10, 40), color=(100, 100, 100))

    # 底部计时汇总条
    summary_bar = np.full((TIMING_H + 4, WIN_W, 3), 10, dtype=np.uint8)
    cv2.line(summary_bar, (0, 0), (WIN_W, 0), (60, 60, 60), 1)
    timing_text = (
        f"recog {ms(t_recog)}   "
        f"TOTAL {t_total * 1000:.0f}ms"
        if t_total else "waiting for first scan..."
    )
    cv2.putText(summary_bar, timing_text, (10, 18),
                cv2.FONT_HERSHEY_SIMPLEX, 0.52, (60, 230, 60), 1)
    p[-(TIMING_H + 4):] = summary_bar
    return p


def build_display(frame, t_capture, scanning, next_in,
                  rot_img, angle, output_img,
                  rec_polys, rec_scores, rec_texts,
                  ocr_lines, status,
                  timings: Dict[str, float]) -> np.ndarray:
    p1 = panel_original(frame, t_capture, scanning, next_in)
    p2 = panel_orientation(rot_img, angle,     timings.get('orientation', 0))
    p3 = panel_unwarped(output_img,            timings.get('unwarping', 0))
    p4 = panel_detection(output_img, rec_polys, rec_scores,
                         timings.get('detection', 0))
    top = np.hstack([p1, p2, p3, p4])

    # 画垂直分割线
    for i in range(1, COLS):
        x = PANEL_W * i
        cv2.line(top, (x, 0), (x, PANEL_H), (60, 60, 60), 1)

    bottom = panel_text(ocr_lines, status,
                        timings.get('recognition', 0),
                        timings.get('total', 0))
    div = np.full((2, WIN_W, 3), 60, dtype=np.uint8)
    return np.vstack([top, div, bottom])


# ---------------------------------------------------------------------------
# TimedOCR — 对内部子模型 predict() 做 monkey-patch 计时
# ---------------------------------------------------------------------------

class TimedOCR:
    """
    包装 PaddleOCR，通过 monkey-patch 各子模型的 predict()
    精确记录每步耗时：orientation / unwarping / detection / recognition
    """

    def __init__(self):
        logger.info("正在初始化 PaddleOCR（含 UVDoc，首次约 10s）...")
        from paddleocr import PaddleOCR
        self._ocr = PaddleOCR(
            lang='ch',
            use_doc_orientation_classify=True,
            use_doc_unwarping=True,
            text_detection_model_name='PP-OCRv5_mobile_det',
            text_recognition_model_name='PP-OCRv5_mobile_rec',
        )
        self.timings: Dict[str, float] = {}
        self._patch_timings()
        logger.info("PaddleOCR 初始化完成，子模型计时已注入")

    def _patch_timings(self):
        """在各子模型的 predict() 前后插入计时。"""
        try:
            inner = self._ocr.paddlex_pipeline._pipeline
            pre   = inner.doc_preprocessor_pipeline._pipeline
            self._patch(pre,   'doc_ori_classify_model', 'orientation')
            self._patch(pre,   'doc_unwarping_model',    'unwarping')
            self._patch(inner, 'text_det_model',         'detection')
            self._patch(inner, 'text_rec_model',         'recognition')
            logger.info("子模型计时 patch 成功")
        except Exception as e:
            logger.warning(f"子模型计时 patch 失败（将只统计总耗时）: {e}")

    def _patch(self, obj, attr: str, name: str):
        model = getattr(obj, attr, None)
        if model is None:
            return
        original = model.predict
        timings = self.timings

        def timed_predict(*args, **kwargs):
            t0 = time.perf_counter()
            # predict() 返回生成器，需全量消费才能计时
            results = list(original(*args, **kwargs))
            timings[name] = time.perf_counter() - t0
            return iter(results)

        model.predict = timed_predict

    def predict(self, frame: np.ndarray):
        self.timings = {}
        t0 = time.perf_counter()
        result = self._ocr.predict(frame)
        self.timings['total'] = time.perf_counter() - t0
        return result

    def log_timings(self):
        t = self.timings
        parts = []
        for k in ('orientation', 'unwarping', 'detection', 'recognition'):
            if k in t:
                parts.append(f"{k[:6]}={t[k]*1000:.0f}ms")
        if 'total' in t:
            parts.append(f"TOTAL={t['total']*1000:.0f}ms")
        logger.info("  计时: " + "  ".join(parts))


# ---------------------------------------------------------------------------
# OCR 后台工作线程
# ---------------------------------------------------------------------------

class OCRWorker:
    def __init__(self):
        self._timed_ocr: Optional[TimedOCR] = None
        self._lock = threading.Lock()
        self._trigger = threading.Event()
        self._stop = threading.Event()
        self._pending_frame: Optional[np.ndarray] = None

        # 共享输出
        self.is_scanning  = False
        self.rot_img:    Optional[np.ndarray] = None
        self.angle:      Optional[int]        = None
        self.output_img: Optional[np.ndarray] = None
        self.rec_polys:  list = []
        self.rec_texts:  list = []
        self.rec_scores: list = []
        self.ocr_lines:  list = []
        self.timings:    Dict[str, float] = {}
        self.status = "等待第一次扫描..."

        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="ocr-worker")
        self._thread.start()

    def trigger(self, frame: np.ndarray):
        if self.is_scanning:
            return
        with self._lock:
            self._pending_frame = frame.copy()
        self._trigger.set()

    def stop(self):
        self._stop.set()
        self._trigger.set()
        self._thread.join(timeout=3)

    def _run(self):
        while not self._stop.is_set():
            self._trigger.wait(timeout=1.0)
            if self._stop.is_set():
                break
            if not self._trigger.is_set():
                continue
            self._trigger.clear()

            with self._lock:
                frame = self._pending_frame
            if frame is None:
                continue

            self.is_scanning = True
            ts = time.strftime("%H:%M:%S")
            try:
                # 懒初始化
                if self._timed_ocr is None:
                    self._timed_ocr = TimedOCR()

                result = self._timed_ocr.predict(frame)
                timings = dict(self._timed_ocr.timings)

                if not result:
                    self.status = f"[{ts}] 无结果"
                    self.timings = timings
                    continue

                r = result[0]

                # 中间图像
                pre = r['doc_preprocessor_res']
                rot_img = pre['rot_img']    if pre else None
                angle   = (int(pre['angle'])
                           if pre and pre['angle'] is not None else None)
                out_img = pre['output_img'] if pre else None

                # 检测结果
                rec_polys  = r['rec_polys']  or []
                rec_texts  = r['rec_texts']  or []
                rec_scores = r['rec_scores'] or []
                if not rec_polys:
                    rec_polys = r['dt_polys'] or []

                lines = [t for t, s in zip(rec_texts, rec_scores)
                         if s >= 0.5 and t.strip()]

                # 更新共享状态
                self.rot_img    = rot_img
                self.angle      = angle
                self.output_img = out_img
                self.rec_polys  = rec_polys
                self.rec_texts  = rec_texts
                self.rec_scores = rec_scores
                self.ocr_lines  = lines
                self.timings    = timings
                n = len(lines)
                total_ms = timings.get('total', 0) * 1000
                self.status = (f"[{ts}] OK  {n}lines  "
                               f"total={total_ms:.0f}ms")
                logger.info(f"[{ts}] OCR {n}行  angle={angle}")
                self._timed_ocr.log_timings()
                if lines:
                    logger.info("前3行: " + " | ".join(lines[:3]))

            except Exception as e:
                self.status = f"[{ts}] ERR: {e}"
                logger.error(f"OCR 失败: {e}", exc_info=True)
            finally:
                self.is_scanning = False


# ---------------------------------------------------------------------------
# 主循环
# ---------------------------------------------------------------------------

def main():
    print("=" * 62)
    print("  摄像头 + OCR 完整流水线可视化（含逐步计时）")
    print(f"  摄像头: {config.CAMERA_DEVICE}   扫描间隔: {config.AUTO_SCAN_INTERVAL}s")
    print(f"  窗口:   {WIN_W} x {WIN_H}")
    print("  Q/ESC 退出   S 立即扫描")
    print("=" * 62)

    if not _FONT_PATH:
        logger.warning("未找到系统中文字体，OCR 文字可能显示为方块")
    else:
        logger.info(f"字体: {_FONT_PATH}")

    camera = CameraCapture(config.CAMERA_DEVICE)
    if not camera.open():
        print(f"错误: 无法打开摄像头 {config.CAMERA_DEVICE}")
        sys.exit(1)

    worker = OCRWorker()
    scan_interval = config.AUTO_SCAN_INTERVAL
    last_scan_time = 0.0

    cv2.namedWindow("Pipeline Debug", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("Pipeline Debug", WIN_W, WIN_H)
    logger.info("调试窗口已打开")

    t_capture = 0.0

    while True:
        now = time.time()

        tc0 = time.perf_counter()
        frame = camera.read()
        t_capture = time.perf_counter() - tc0

        if frame is None:
            if cv2.waitKey(100) in (ord('q'), ord('Q'), 27):
                break
            continue

        if now - last_scan_time >= scan_interval:
            last_scan_time = now
            worker.trigger(frame)

        next_in = max(0.0, scan_interval - (now - last_scan_time))
        display = build_display(
            frame, t_capture, worker.is_scanning, next_in,
            worker.rot_img, worker.angle, worker.output_img,
            worker.rec_polys, worker.rec_scores, worker.rec_texts,
            worker.ocr_lines, worker.status, worker.timings,
        )
        cv2.imshow("Pipeline Debug", display)

        key = cv2.waitKey(33) & 0xFF
        if key in (ord('q'), ord('Q'), 27):
            break
        elif key in (ord('s'), ord('S')):
            last_scan_time = 0.0
            logger.info("手动触发扫描")

    worker.stop()
    camera.close()
    cv2.destroyAllWindows()
    print("已退出")


if __name__ == "__main__":
    main()
