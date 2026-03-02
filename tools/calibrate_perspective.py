"""
透视校正标定工具

用法：
    python tools/calibrate_perspective.py

操作流程：
    1. 打开摄像头实时画面
    2. 按顺序点击书本4个角点：左上 → 右上 → 右下 → 左下
    3. 4点齐全后按 Enter 预览校正效果
    4. 按 's' 保存矩阵，按 'r' 重新选点，按 'q' 退出不保存
"""

import sys
import os
import json

# 确保能找到项目模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2
import numpy as np

from config import config
from camera.capture import CameraCapture, find_external_camera

# 保存路径
HOMOGRAPHY_NPY = "camera/homography.npy"
HOMOGRAPHY_META = "camera/homography_meta.json"

# 颜色常量（BGR）
COLOR_POINT = (0, 255, 0)
COLOR_LINE = (0, 200, 255)
COLOR_TEXT = (255, 255, 255)
COLOR_BG = (0, 0, 0)
COLOR_HINT = (180, 180, 180)

POINT_LABELS = ["左上(TL)", "右上(TR)", "右下(BR)", "左下(BL)"]
POINT_COLORS = [
    (0, 255, 0),    # 绿
    (0, 200, 255),  # 黄
    (0, 0, 255),    # 红
    (255, 100, 0),  # 蓝
]


class CalibrationUI:
    def __init__(self, camera: CameraCapture, output_w: int, output_h: int):
        self.camera = camera
        self.output_w = output_w
        self.output_h = output_h
        self.points = []          # 用户已选的角点（图像坐标）
        self.preview_mode = False  # True = 显示校正预览
        self.M = None             # 计算好的单应矩阵

    def _draw_overlay(self, frame: np.ndarray) -> np.ndarray:
        """在帧上绘制已选点、连线、提示文字"""
        out = frame.copy()
        h, w = out.shape[:2]

        # 已选点 & 连线
        for i, pt in enumerate(self.points):
            x, y = int(pt[0]), int(pt[1])
            cv2.circle(out, (x, y), 8, POINT_COLORS[i], -1)
            cv2.circle(out, (x, y), 10, (255, 255, 255), 2)
            label = POINT_LABELS[i]
            cv2.putText(out, label, (x + 12, y - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

        if len(self.points) >= 2:
            pts_arr = [(int(p[0]), int(p[1])) for p in self.points]
            for i in range(len(pts_arr) - 1):
                cv2.line(out, pts_arr[i], pts_arr[i + 1], COLOR_LINE, 2)
            if len(pts_arr) == 4:
                cv2.line(out, pts_arr[3], pts_arr[0], COLOR_LINE, 2)

        # 提示信息
        if len(self.points) < 4:
            next_label = POINT_LABELS[len(self.points)]
            hint = f"点击第 {len(self.points) + 1} 个角点: {next_label}"
        else:
            hint = "4点已选好 | Enter=预览  r=重选  q=退出"

        # 半透明黑底
        overlay = out.copy()
        cv2.rectangle(overlay, (0, h - 50), (w, h), (0, 0, 0), -1)
        cv2.addWeighted(overlay, 0.5, out, 0.5, 0, out)
        cv2.putText(out, hint, (10, h - 18),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, COLOR_TEXT, 2)

        return out

    def _compute_homography(self) -> np.ndarray:
        """根据4个角点计算单应矩阵"""
        src = np.array(self.points, dtype="float32")
        dst = np.array([
            [0, 0],
            [self.output_w - 1, 0],
            [self.output_w - 1, self.output_h - 1],
            [0, self.output_h - 1],
        ], dtype="float32")
        M = cv2.getPerspectiveTransform(src, dst)
        return M

    def mouse_callback(self, event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN and len(self.points) < 4:
            self.points.append((x, y))
            print(f"  已选 {len(self.points)}/4: {POINT_LABELS[len(self.points)-1]} ({x}, {y})")

    def run(self):
        win = "透视校正标定 (点击4个角点)"
        cv2.namedWindow(win, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(win, 1280, 720)
        cv2.setMouseCallback(win, self.mouse_callback)

        print("\n=== 透视校正标定 ===")
        print(f"输出尺寸: {self.output_w} x {self.output_h}")
        print("按顺序点击书本4个角点: 左上 → 右上 → 右下 → 左下")
        print("4点齐全后: Enter=预览  r=重选  s=保存  q=退出\n")

        saved = False
        while True:
            frame = self.camera.read()
            if frame is None:
                print("摄像头读取失败")
                break

            if self.preview_mode and self.M is not None:
                display = cv2.warpPerspective(
                    frame, self.M, (self.output_w, self.output_h)
                )
                h_d, w_d = display.shape[:2]
                overlay = display.copy()
                cv2.rectangle(overlay, (0, h_d - 50), (w_d, h_d), (0, 0, 0), -1)
                cv2.addWeighted(overlay, 0.5, display, 0.5, 0, display)
                cv2.putText(display, "预览模式 | s=保存  r=重选  q=退出",
                            (10, h_d - 18), cv2.FONT_HERSHEY_SIMPLEX,
                            0.65, COLOR_TEXT, 2)
                cv2.imshow(win, display)
            else:
                cv2.imshow(win, self._draw_overlay(frame))

            key = cv2.waitKey(30) & 0xFF

            if key == ord('q'):
                print("退出，未保存。")
                break
            elif key == ord('r'):
                self.points.clear()
                self.preview_mode = False
                self.M = None
                print("已重置，请重新点击角点。")
            elif key == 13:  # Enter
                if len(self.points) == 4:
                    self.M = self._compute_homography()
                    self.preview_mode = True
                    print("显示校正预览，按 s 保存，r 重选。")
                else:
                    print(f"还需要选 {4 - len(self.points)} 个点。")
            elif key == ord('s'):
                if self.M is not None:
                    self._save(self.M, frame.shape)
                    saved = True
                    print("矩阵已保存！按 q 退出。")
                else:
                    print("请先完成4点选取并按 Enter 预览。")

        cv2.destroyAllWindows()
        return saved

    def _save(self, M: np.ndarray, frame_shape: tuple):
        """保存单应矩阵和元数据"""
        os.makedirs(os.path.dirname(HOMOGRAPHY_NPY) or ".", exist_ok=True)
        np.save(HOMOGRAPHY_NPY, M)

        meta = {
            "input_width": frame_shape[1],
            "input_height": frame_shape[0],
            "output_width": self.output_w,
            "output_height": self.output_h,
            "src_points": [list(p) for p in self.points],
        }
        with open(HOMOGRAPHY_META, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        print(f"  保存矩阵: {HOMOGRAPHY_NPY}")
        print(f"  保存元数据: {HOMOGRAPHY_META}")
        print(f"  输入尺寸: {frame_shape[1]}x{frame_shape[0]}")
        print(f"  输出尺寸: {self.output_w}x{self.output_h}")


def main():
    device = find_external_camera() if config.CAMERA_AUTO_DETECT else config.CAMERA_DEVICE
    camera = CameraCapture(device)
    if not camera.open():
        print(f"错误：无法打开摄像头 {device}")
        sys.exit(1)

    # 读一帧获取分辨率
    sample = camera.read()
    if sample is None:
        print("错误：摄像头无法读取帧")
        camera.close()
        sys.exit(1)

    input_h, input_w = sample.shape[:2]
    print(f"摄像头分辨率: {input_w}x{input_h}")

    # 输出尺寸默认与输入相同（保持像素数量）
    output_w, output_h = input_w, input_h

    ui = CalibrationUI(camera, output_w, output_h)
    saved = ui.run()
    camera.close()

    if saved:
        print("\n标定完成！在 config.json 中启用：")
        print('  "perspective": {"enabled": true}')
    else:
        print("\n未保存标定结果。")


if __name__ == "__main__":
    main()
