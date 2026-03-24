"""
调焦辅助工具

以 1:1 像素显示摄像头画面，用方向键或 WASD 平移视口，方便手动调整焦距。

用法：
    python tools/focus_helper.py

按键：
    ← / A      向左移动
    → / D      向右移动
    ↑ / W      向上移动
    ↓ / S      向下移动
    Shift+方向  大步移动（×5）
    q          退出
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2
import numpy as np

from config import config
from camera.capture import CameraCapture, find_external_camera

# 视口尺寸（屏幕上显示的窗口大小，1:1 像素）
VP_W = 1280
VP_H = 720

# 每次按键移动的像素数
PAN_STEP = 150
PAN_STEP_LARGE = PAN_STEP * 5  # Shift 加速

# macOS OpenCV 方向键 raw keycode
KEY_LEFT  = 63234
KEY_RIGHT = 63235
KEY_UP    = 63232
KEY_DOWN  = 63233


def draw_minimap(frame: np.ndarray, img_w: int, img_h: int,
                 pan_x: int, pan_y: int) -> np.ndarray:
    """在帧右上角绘制小地图，显示当前视口在全图中的位置。"""
    MAP_W, MAP_H = 200, 112  # 小地图尺寸（保持 16:9）
    margin = 10

    # 按比例缩略图
    thumb = cv2.resize(frame, (MAP_W, MAP_H))

    # 视口矩形在小地图上的位置
    rx1 = int(pan_x / img_w * MAP_W)
    ry1 = int(pan_y / img_h * MAP_H)
    rx2 = int(min(pan_x + VP_W, img_w) / img_w * MAP_W)
    ry2 = int(min(pan_y + VP_H, img_h) / img_h * MAP_H)

    cv2.rectangle(thumb, (rx1, ry1), (rx2, ry2), (0, 255, 0), 2)

    # 贴到画面右上角
    out = frame.copy()
    x0 = VP_W - MAP_W - margin
    y0 = margin
    out[y0:y0 + MAP_H, x0:x0 + MAP_W] = thumb
    # 小地图外框
    cv2.rectangle(out, (x0 - 1, y0 - 1), (x0 + MAP_W, y0 + MAP_H), (200, 200, 200), 1)
    return out


def draw_hint(frame: np.ndarray, img_w: int, img_h: int,
              pan_x: int, pan_y: int) -> np.ndarray:
    """底部提示条"""
    out = frame.copy()
    h, w = out.shape[:2]
    bar_h = 36
    overlay = out.copy()
    cv2.rectangle(overlay, (0, h - bar_h), (w, h), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.6, out, 0.4, 0, out)

    pos_text = f"位置 ({pan_x}, {pan_y})  全图 {img_w}x{img_h}  视口 {VP_W}x{VP_H}"
    hint_text = "← → ↑ ↓ / WASD 移动  Shift 加速  q 退出"
    cv2.putText(out, pos_text,  (10, h - bar_h + 14),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 180, 180), 1)
    cv2.putText(out, hint_text, (10, h - 6),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
    return out


def main():
    device = find_external_camera() if config.CAMERA_AUTO_DETECT else config.CAMERA_DEVICE
    camera = CameraCapture(device)
    if not camera.open():
        print(f"错误：无法打开摄像头 {device}")
        sys.exit(1)

    # 读一帧确认分辨率
    sample = camera.read()
    if sample is None:
        print("错误：摄像头无法读取帧")
        camera.close()
        sys.exit(1)

    img_h, img_w = sample.shape[:2]
    print(f"摄像头分辨率: {img_w}x{img_h}，视口: {VP_W}x{VP_H}（1:1 像素，无缩放）")
    print("按 WASD 或方向键平移，Shift+方向键大步移动，q 退出")

    # 初始视口居中
    pan_x = max(0, (img_w - VP_W) // 2)
    pan_y = max(0, (img_h - VP_H) // 2)

    win = "调焦辅助 (1:1 无缩放)"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(win, VP_W, VP_H)

    while True:
        frame = camera.read()
        if frame is None:
            continue

        # 裁剪视口（钳位保证不越界）
        pan_x = max(0, min(pan_x, img_w - VP_W))
        pan_y = max(0, min(pan_y, img_h - VP_H))
        viewport = frame[pan_y:pan_y + VP_H, pan_x:pan_x + VP_W].copy()

        # 如果帧比视口小（理论上不会），补黑边
        if viewport.shape[0] < VP_H or viewport.shape[1] < VP_W:
            canvas = np.zeros((VP_H, VP_W, 3), dtype=np.uint8)
            canvas[:viewport.shape[0], :viewport.shape[1]] = viewport
            viewport = canvas

        viewport = draw_minimap(viewport, img_w, img_h, pan_x, pan_y)
        viewport = draw_hint(viewport, img_w, img_h, pan_x, pan_y)
        cv2.imshow(win, viewport)

        raw = cv2.waitKey(30)
        if raw == -1:
            continue
        key = raw & 0xFF

        # 判断是否按住 Shift（raw > 0xFFFF 通常是 shift 修饰，但 OpenCV 不直接暴露；
        # 改用大写字母判断）
        step = PAN_STEP

        if raw == KEY_LEFT  or key == ord('a'):
            pan_x -= step
        elif raw == KEY_RIGHT or key == ord('d'):
            pan_x += step
        elif raw == KEY_UP   or key == ord('w'):
            pan_y -= step
        elif raw == KEY_DOWN  or key == ord('s'):
            pan_y += step
        elif key == ord('A'):   # Shift+A
            pan_x -= PAN_STEP_LARGE
        elif key == ord('D'):   # Shift+D
            pan_x += PAN_STEP_LARGE
        elif key == ord('W'):   # Shift+W
            pan_y -= PAN_STEP_LARGE
        elif key == ord('S'):   # Shift+S
            pan_y += PAN_STEP_LARGE
        elif key == ord('q'):
            break

    cv2.destroyAllWindows()
    camera.close()
    print("已退出调焦工具。")


if __name__ == "__main__":
    main()
