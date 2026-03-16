#!/usr/bin/env python3
"""
指尖点读功能独立测试脚本

用法：
    # 阶段1：测试 MediaPipe 是否能检测到手指（实时摄像头预览）
    python test_finger.py --stage mediapipe

    # 阶段2：测试 PaddleOCR 裁剪识别（给定图片 + 手动指定坐标）
    python test_finger.py --stage ocr --image path/to/book.jpg --px 1920 --py 1080

    # 阶段3：端到端联调（摄像头 + 手指 + OCR，按 q 退出）
    python test_finger.py --stage e2e

    # 阶段4：测试 fuzzy match（给定全页 OCR 文本文件 + PaddleOCR 识别结果）
    python test_finger.py --stage fuzzy --page-ocr page.txt --raw-text "被测试的字串"
"""
import argparse
import sys
import time
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger("test_finger")


# ─── 阶段1：MediaPipe 摄像头预览 ──────────────────────────────────────────────

def stage_mediapipe(camera_id: int = 0):
    """
    打开摄像头实时预览，在画面上绘制食指指尖位置。
    窗口显示：红点=指尖，白字=归一化坐标。
    按 q 退出。
    """
    try:
        import mediapipe as mp
    except ImportError:
        print("❌ mediapipe 未安装：pip install mediapipe")
        sys.exit(1)

    import cv2
    from scanner.finger_detector import FingerDetector
    from camera.capture import CameraCapture

    fd = FingerDetector()
    cap = CameraCapture(camera_id)
    if not cap.open():
        print(f"❌ 无法打开摄像头 {camera_id}")
        sys.exit(1)

    print(f"✅ 摄像头已打开（4K），将食指指向书页，按 q 退出")
    detected_count = 0
    frame_count = 0

    while True:
        frame = cap.read()
        if frame is None:
            break
        frame_count += 1

        h, w = frame.shape[:2]
        scale = 960 / max(h, w)
        small = cv2.resize(frame, (int(w * scale), int(h * scale))) if scale < 1.0 else frame

        fp = fd.process(small)

        display = cv2.resize(frame, (int(w * 0.5), int(h * 0.5)))
        dh, dw = display.shape[:2]

        if fp:
            detected_count += 1
            # 映射回显示分辨率
            dx = int(fp.x * dw)
            dy = int(fp.y * dh)
            cv2.circle(display, (dx, dy), 12, (0, 0, 255), -1)
            cv2.putText(display, f"({fp.x:.3f}, {fp.y:.3f})",
                        (dx + 15, dy), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
            status = f"frame={frame_count} detected={detected_count}"
        else:
            status = f"frame={frame_count} 未检测到手指"

        cv2.putText(display, status, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
        cv2.imshow("Finger Detection (q=quit)", display)

        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.close()
    cv2.destroyAllWindows()
    fd.close()
    print(f"\n统计：共处理 {frame_count} 帧，检测到手指 {detected_count} 帧"
          f"（检测率 {detected_count/max(frame_count,1)*100:.1f}%）")


# ─── 阶段2：PaddleOCR 裁剪识别 ───────────────────────────────────────────────

def stage_ocr(image_path: str, px: int, py: int,
              crop_w: int = 800, crop_h: int = 120, y_offset: int = 20):
    """
    对给定图片在指定坐标处裁剪，运行 PaddleOCR，显示结果并保存调试图。
    """
    import cv2
    from pathlib import Path
    from ocr.engine import extract_text_from_crop

    img = cv2.imread(image_path)
    if img is None:
        print(f"❌ 无法读取图片: {image_path}")
        sys.exit(1)

    h, w = img.shape[:2]
    print(f"图片尺寸: {w}×{h}，指尖坐标: ({px}, {py})")

    x1 = max(0, px - crop_w // 2)
    x2 = min(w, px + crop_w // 2)
    y1 = max(0, py - crop_h - y_offset)
    y2 = max(0, py - y_offset)
    crop = img[y1:y2, x1:x2]

    if crop.size == 0:
        print(f"❌ 裁剪区域为空（坐标越界？）x1={x1} x2={x2} y1={y1} y2={y2}")
        sys.exit(1)

    # 保存调试图
    out_dir = Path("data/finger_debug")
    out_dir.mkdir(parents=True, exist_ok=True)
    crop_path = out_dir / "test_crop.jpg"
    cv2.imwrite(str(crop_path), crop)

    # 带标注的原图缩略图
    thumb_scale = 480 / max(h, w)
    thumb = cv2.resize(img, (int(w * thumb_scale), int(h * thumb_scale)))
    tx, ty = int(px * thumb_scale), int(py * thumb_scale)
    cv2.circle(thumb, (tx, ty), 8, (0, 0, 255), -1)
    cv2.rectangle(thumb,
                  (int(x1 * thumb_scale), int(y1 * thumb_scale)),
                  (int(x2 * thumb_scale), int(y2 * thumb_scale)),
                  (0, 255, 0), 2)
    thumb_path = out_dir / "test_thumb.jpg"
    cv2.imwrite(str(thumb_path), thumb)

    print(f"调试图已保存：")
    print(f"  裁剪图: {crop_path}  ({crop.shape[1]}×{crop.shape[0]}px)")
    print(f"  标注图: {thumb_path}")
    print(f"\n正在运行 PaddleOCR...")

    t0 = time.time()
    text = extract_text_from_crop(crop)
    elapsed = time.time() - t0

    print(f"耗时: {elapsed*1000:.0f}ms")
    if text.strip():
        print(f"✅ 识别结果: 「{text}」")
    else:
        print("❌ OCR 无结果（裁剪图可能太小/模糊/不含文字）")
        print(f"   请打开 {crop_path} 检查裁剪区域是否正确")


# ─── 阶段3：端到端联调 ────────────────────────────────────────────────────────

def stage_e2e(camera_id: int = 0, dwell_seconds: float = 1.5):
    """
    摄像头实时运行：检测指尖 → 停留 dwell_seconds → 裁剪 OCR → 打印结果。

    坐标空间说明：
    - raw    : 摄像头原始帧，用于预览窗口（用户直觉上看到的画面）
    - corrected : 透视校正后的帧，用于手指检测 + 裁剪 + OCR（与主程序一致）
    - 画叠层时用逆矩阵把 corrected 坐标映射回 raw 空间
    """
    try:
        import mediapipe  # noqa
    except ImportError:
        print("❌ mediapipe 未安装：pip install mediapipe")
        sys.exit(1)

    import cv2
    import numpy as np
    from pathlib import Path
    from datetime import datetime
    from scanner.finger_detector import FingerDetector
    from ocr.engine import get_crop_ocr, _extract_boxes

    fd = FingerDetector()
    from camera.capture import CameraCapture
    cap = CameraCapture(camera_id)
    if not cap.open():
        print(f"❌ 无法打开摄像头 {camera_id}")
        sys.exit(1)

    # 预加载 Crop PaddleOCR，避免首次触发时 4~5s 卡顿
    print("预加载 PaddleOCR（首次需要约 3-5s）...")
    t_preload = time.time()
    get_crop_ocr()
    print(f"✅ PaddleOCR 预加载完成（{(time.time()-t_preload)*1000:.0f}ms）")

    from config import config
    from camera.perspective import load_fixed_homography, apply_fixed_homography
    perspective_M = None
    if config.PERSPECTIVE_ENABLED:
        perspective_M = load_fixed_homography(config.PERSPECTIVE_HOMOGRAPHY_FILE)
        if perspective_M is not None:
            print(f"✅ 透视校正已加载（预览/检测/裁剪均在校正帧上进行）")
        else:
            print(f"⚠️  透视校正矩阵加载失败，将使用原始帧")
    else:
        print("ℹ️  透视校正未启用")

    out_dir = Path("data/finger_debug")
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"✅ 端到端测试启动（停留阈值={dwell_seconds}s，按 q 退出）")
    print("   将食指指向书页某行，静止后自动触发 OCR")

    SEARCH_W, SEARCH_H = 900, 240
    MOVE_THRESH = 0.02
    last_stable_pos = None
    dwell_since = 0.0
    trigger_count = 0
    status = "等待手指..."

    while True:
        raw = cap.read()
        if raw is None:
            break

        # 校正帧：用于预览 / 裁剪 / OCR
        frame = apply_fixed_homography(raw, perspective_M) if perspective_M is not None else raw
        h_r, w_r = raw.shape[:2]
        h_c, w_c = frame.shape[:2]

        # MediaPipe 在原始帧上检测（手的形态自然，不被透视变换扭曲）
        mp_scale = 960 / max(h_r, w_r)
        small = cv2.resize(raw, (int(w_r * mp_scale), int(h_r * mp_scale))) if mp_scale < 1.0 else raw

        fp = fd.process(small)
        now = time.time()

        # 预览窗口：校正帧缩到适合屏幕的尺寸
        disp_scale = min(0.5, 960 / max(h_c, w_c))
        display = cv2.resize(frame, (int(w_c * disp_scale), int(h_c * disp_scale)))

        if fp is None:
            last_stable_pos = None
            dwell_since = 0.0
            status = "no finger"
        else:
            # 原始帧中的指尖像素坐标
            px_r = int(fp.x * w_r)
            py_r = int(fp.y * h_r)

            # 用正向矩阵 M 把原始坐标映射到校正帧坐标
            if perspective_M is not None:
                pt = np.array([[[px_r, py_r]]], dtype=np.float32)
                pt_c = cv2.perspectiveTransform(pt, perspective_M)
                px = int(pt_c[0][0][0])
                py = int(pt_c[0][0][1])
                # 方向：lm6/lm8 都是归一化坐标，先转成 4K 像素再算单位向量。
                # 取 lm8 身后 50px 的近距探测点再变换到校正帧，避免 lm6 因高于
                # 书面导致透视变换偏差大。
                raw_dx = (fp.x - fp.dir_x) * w_r   # 归一化差值 × 4K 宽
                raw_dy = (fp.y - fp.dir_y) * h_r   # 归一化差值 × 4K 高
                raw_len = (raw_dx**2 + raw_dy**2) ** 0.5
                if raw_len > 5:
                    probe_x = px_r - raw_dx / raw_len * 50
                    probe_y = py_r - raw_dy / raw_len * 50
                else:
                    probe_x = fp.dir_x * w_r
                    probe_y = fp.dir_y * h_r
                dpt = np.array([[[probe_x, probe_y]]], dtype=np.float32)
                dpt_c = cv2.perspectiveTransform(dpt, perspective_M)
                dir_cx = int(dpt_c[0][0][0])
                dir_cy = int(dpt_c[0][0][1])
            else:
                px, py = px_r, py_r
                dir_cx, dir_cy = fp.dir_x, fp.dir_y

            x1 = max(0, px - SEARCH_W // 2)
            x2 = min(w_c, px + SEARCH_W // 2)
            y1 = max(0, py - SEARCH_H // 2)
            y2 = min(h_c, py + SEARCH_H // 2)

            # 映射到 display 尺寸
            dx  = int(px * disp_scale)
            dy  = int(py * disp_scale)
            rx1 = int(x1 * disp_scale)
            ry1 = int(y1 * disp_scale)
            rx2 = int(x2 * disp_scale)
            ry2 = int(y2 * disp_scale)

            cv2.circle(display, (dx, dy), 10, (0, 0, 255), -1)
            # 射线方向可视化：从指尖沿 lm6→lm8 方向画蓝色箭头
            rdx_v, rdy_v = px - dir_cx, py - dir_cy
            r_len_v = (rdx_v**2 + rdy_v**2) ** 0.5
            if r_len_v > 5:
                rdx_v, rdy_v = rdx_v / r_len_v, rdy_v / r_len_v
                ray_end = (int((px + rdx_v * 400) * disp_scale),
                           int((py + rdy_v * 400) * disp_scale))
                cv2.arrowedLine(display, (dx, dy), ray_end, (255, 100, 0), 2, tipLength=0.1)
            cv2.putText(display, fp.hand, (dx + 14, dy - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 200, 100), 1)

            if last_stable_pos is None:
                last_stable_pos = (fp.x, fp.y)
                dwell_since = now
                status = "手指检测到，开始计时..."
                box_color = (0, 200, 200)
            else:
                moved = (abs(fp.x - last_stable_pos[0]) > MOVE_THRESH or
                         abs(fp.y - last_stable_pos[1]) > MOVE_THRESH)
                if moved:
                    last_stable_pos = (fp.x, fp.y)
                    dwell_since = now
                    status = "手指移动，重置计时"
                    box_color = (0, 200, 200)
                else:
                    elapsed = now - dwell_since
                    remaining = max(0.0, dwell_seconds - elapsed)
                    ratio = min(elapsed / dwell_seconds, 1.0)
                    g = int(255 * ratio)
                    box_color = (0, g, 255 - g)
                    thickness = 3 if ratio >= 1.0 else 2
                    status = f"稳定中... 还需 {remaining:.1f}s" if remaining > 0 else "触发中..."

                    cv2.rectangle(display, (rx1, ry1), (rx2, ry2), box_color, thickness)

                    if elapsed >= dwell_seconds and dwell_since < now - 0.1:
                        dwell_since = now + 9999
                        trigger_count += 1
                        ts = datetime.now().strftime("%H%M%S")
                        rdx_t, rdy_t = px - dir_cx, py - dir_cy
                        r_len_t = (rdx_t**2 + rdy_t**2) ** 0.5
                        has_dir = r_len_t > 5
                        if has_dir:
                            rdx_t, rdy_t = rdx_t / r_len_t, rdy_t / r_len_t
                        print(f"\n[触发#{trigger_count}] hand={fp.hand} 指尖({px},{py}) "
                              f"方向({rdx_t:.2f},{rdy_t:.2f}) has_dir={has_dir}")

                        search_crop = frame[y1:y2, x1:x2]
                        if search_crop.size == 0:
                            print("  ❌ 搜索区为空（坐标越界）")
                            status = "搜索区失败"
                        else:
                            t0 = time.time()
                            from ocr.engine import _sharpen
                            ocr = get_crop_ocr()
                            result = list(ocr.predict(_sharpen(search_crop)))
                            boxes = _extract_boxes(result)
                            elapsed_ocr = (time.time() - t0) * 1000

                            # 转换到校正帧坐标
                            for box in boxes:
                                box["poly"] = [[pt[0] + x1, pt[1] + y1] for pt in box["poly"]]
                                box["cx"] += x1
                                box["cy"] += y1

                            print(f"  PaddleOCR ({elapsed_ocr:.0f}ms): {len(boxes)} 个 box")

                            # 射线查找
                            RAY_PERP_THRESH, RAY_T_MAX = 80, 600
                            matched_box = None

                            if has_dir:
                                best_perp = float("inf")
                                for box in boxes:
                                    cx_b, cy_b = box["cx"], box["cy"]
                                    t_b = (cx_b - px) * rdx_t + (cy_b - py) * rdy_t
                                    if t_b < 0 or t_b > RAY_T_MAX:
                                        continue
                                    perp = abs((cx_b - px) * rdy_t - (cy_b - py) * rdx_t)
                                    if perp < RAY_PERP_THRESH and perp < best_perp:
                                        best_perp, matched_box = perp, box
                                if matched_box:
                                    print(f"  ✅ 命中 box（ray perp={best_perp:.0f}px）: 「{matched_box['text'][:60]}」")

                            if matched_box is None:
                                best_dist = float("inf")
                                for box in boxes:
                                    dy_b = abs(box["cy"] - py)
                                    if dy_b > 120:
                                        continue
                                    dist = (box["cx"] - px) ** 2 + dy_b ** 2
                                    if dist < best_dist:
                                        best_dist, matched_box = dist, box
                                if matched_box:
                                    print(f"  ✅ 命中 box（fallback nearest {best_dist**0.5:.0f}px）: 「{matched_box['text'][:60]}」")

                            if matched_box is None:
                                print("  ❌ 未命中任何 box")
                                status = "❌ 未命中"
                            else:
                                status = f"✅ {matched_box['text'][:30]}"

                            # 可视化：在 display 上绘制所有 box（灰色），命中 box 高亮（绿色）
                            for box in boxes:
                                pts_d = np.array(
                                    [[int(p[0] * disp_scale), int(p[1] * disp_scale)] for p in box["poly"]],
                                    dtype=np.int32
                                )
                                color = (0, 255, 0) if box is matched_box else (120, 120, 120)
                                thickness = 3 if box is matched_box else 1
                                cv2.polylines(display, [pts_d], isClosed=True, color=color, thickness=thickness)

                            cv2.imwrite(str(out_dir / f"{ts}_thumb.jpg"), display)

                        cv2.rectangle(display, (rx1, ry1), (rx2, ry2), (0, 255, 0), 2)
                        cv2.putText(display, status, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
                        cv2.putText(display, f"触发: {trigger_count}", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 200, 0), 2)
                        cv2.imshow("E2E Test (q=quit)", display)
                        cv2.waitKey(1)
                        continue

            cv2.rectangle(display, (rx1, ry1), (rx2, ry2), box_color, 2)

        cv2.putText(display, status, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
        cv2.putText(display, f"触发: {trigger_count}", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 200, 0), 2)
        cv2.imshow("E2E Test (q=quit)", display)

        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.close()
    cv2.destroyAllWindows()
    fd.close()
    print(f"\n测试结束，共触发 {trigger_count} 次，调试图保存在 data/finger_debug/")


# ─── 阶段4：fuzzy match 测试 ──────────────────────────────────────────────────

def stage_fuzzy(page_ocr_file: str, raw_text: str):
    """
    给定全页 OCR 文本文件和 PaddleOCR 裁剪识别结果，测试 fuzzy match 效果。
    打印每行的相似度分数。
    """
    from difflib import SequenceMatcher
    from pathlib import Path

    page_text = Path(page_ocr_file).read_text(encoding="utf-8")
    lines = [l for l in page_text.split('\n') if l.strip()]

    print(f"全页共 {len(lines)} 行，待匹配: 「{raw_text}」\n")
    print(f"{'Score':>6}  {'行内容'}")
    print("-" * 60)

    scored = []
    for line in lines:
        score = SequenceMatcher(None, raw_text, line).ratio()
        scored.append((score, line))

    scored.sort(key=lambda x: x[0], reverse=True)
    for score, line in scored[:10]:
        marker = " ← 最佳" if score == scored[0][0] else ""
        print(f"  {score:.3f}  {line[:60]}{marker}")

    best_score, best_line = scored[0]
    print()
    if best_score >= 0.4:
        print(f"✅ 命中（score={best_score:.3f}）: 「{best_line}」")
    else:
        print(f"❌ 匹配分太低（{best_score:.3f} < 0.4），将使用 PaddleOCR 原文: 「{raw_text}」")


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="指尖点读功能测试")
    parser.add_argument("--stage", choices=["mediapipe", "ocr", "e2e", "fuzzy"],
                        default="e2e", help="测试阶段（默认 e2e）")
    parser.add_argument("--camera", type=int, default=0, help="摄像头设备号")
    parser.add_argument("--image", type=str, help="[ocr] 书页图片路径")
    parser.add_argument("--px", type=int, help="[ocr] 指尖 X 像素坐标")
    parser.add_argument("--py", type=int, help="[ocr] 指尖 Y 像素坐标")
    parser.add_argument("--dwell", type=float, default=1.5, help="[e2e] 停留阈值秒数")
    parser.add_argument("--page-ocr", type=str, help="[fuzzy] 全页 OCR 文本文件路径")
    parser.add_argument("--raw-text", type=str, help="[fuzzy] PaddleOCR 裁剪识别结果")
    args = parser.parse_args()

    if args.stage == "mediapipe":
        stage_mediapipe(args.camera)
    elif args.stage == "ocr":
        if not args.image or args.px is None or args.py is None:
            print("❌ --stage ocr 需要 --image --px --py")
            sys.exit(1)
        stage_ocr(args.image, args.px, args.py)
    elif args.stage == "e2e":
        stage_e2e(args.camera, args.dwell)
    elif args.stage == "fuzzy":
        if not args.page_ocr or not args.raw_text:
            print("❌ --stage fuzzy 需要 --page-ocr --raw-text")
            sys.exit(1)
        stage_fuzzy(args.page_ocr, args.raw_text)


if __name__ == "__main__":
    main()
