"""
透视矫正模块
使用 OpenCV 进行书页透视矫正
"""
import cv2
import numpy as np
from typing import Optional, Tuple, List
import logging

logger = logging.getLogger(__name__)


def order_points(pts: np.ndarray) -> np.ndarray:
    """
    对四个点进行排序：左上、右上、右下、左下
    """
    rect = np.zeros((4, 2), dtype="float32")
    
    # 计算每个点的坐标和
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]  # 左上
    rect[2] = pts[np.argmax(s)]  # 右下
    
    # 计算差值
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]  # 右上
    rect[3] = pts[np.argmax(diff)]  # 左下
    
    return rect


def find_page_contour(image: np.ndarray) -> Optional[np.ndarray]:
    """
    在图像中查找书页轮廓
    
    Args:
        image: 输入图像
        
    Returns:
        四边形轮廓点 (4, 2) 或 None
    """
    # 转为灰度图
    if len(image.shape) == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image.copy()
    
    # 高斯模糊降噪
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    
    # Canny 边缘检测
    edges = cv2.Canny(blur, 50, 150)
    
    # 膨胀连接断开的边缘
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    edges = cv2.dilate(edges, kernel, iterations=2)
    edges = cv2.erode(edges, kernel, iterations=1)
    
    # 查找轮廓
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    
    # 按面积排序，取前 10 个
    contours = sorted(contours, key=cv2.contourArea, reverse=True)[:10]
    
    for contour in contours:
        # 计算周长
        peri = cv2.arcLength(contour, True)
        # 多边形逼近
        approx = cv2.approxPolyDP(contour, 0.02 * peri, True)
        
        # 找到四边形
        if len(approx) == 4:
            return approx.reshape(4, 2)
    
    return None


def four_point_transform(image: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """
    四点透视变换
    
    Args:
        image: 输入图像
        pts: 四个角点，按顺序 [左上, 右上, 右下, 左下]
        
    Returns:
        矫正后的图像
    """
    rect = order_points(pts)
    (tl, tr, br, bl) = rect
    
    # 计算宽度
    widthA = np.sqrt(((br[0] - bl[0]) ** 2) + ((br[1] - bl[1]) ** 2))
    widthB = np.sqrt(((tr[0] - tl[0]) ** 2) + ((tr[1] - tl[1]) ** 2))
    maxWidth = max(int(widthA), int(widthB))
    
    # 计算高度
    heightA = np.sqrt(((tr[0] - br[0]) ** 2) + ((tr[1] - br[1]) ** 2))
    heightB = np.sqrt(((tl[0] - bl[0]) ** 2) + ((tl[1] - bl[1]) ** 2))
    maxHeight = max(int(heightA), int(heightB))
    
    # 目标点
    dst = np.array([
        [0, 0],
        [maxWidth - 1, 0],
        [maxWidth - 1, maxHeight - 1],
        [0, maxHeight - 1]], dtype="float32")
    
    # 计算变换矩阵
    M = cv2.getPerspectiveTransform(rect, dst)
    warped = cv2.warpPerspective(image, M, (maxWidth, maxHeight))
    
    return warped


def correct_perspective(image: np.ndarray, debug: bool = False) -> np.ndarray:
    """
    透视矫正主函数
    
    Args:
        image: 输入图像 (BGR)
        debug: 是否输出调试信息
        
    Returns:
        矫正后的图像，失败返回原图
    """
    try:
        # 查找书页轮廓
        contour = find_page_contour(image)
        
        if contour is None:
            logger.warning("未检测到书页轮廓，返回原图")
            return image
        
        # 计算轮廓面积占比
        image_area = image.shape[0] * image.shape[1]
        contour_area = cv2.contourArea(contour)
        area_ratio = contour_area / image_area
        
        # 如果检测到的区域太小，可能是误检
        if area_ratio < 0.1:
            logger.warning(f"检测区域占比过小 ({area_ratio:.2%})，返回原图")
            return image
        
        # 透视变换
        warped = four_point_transform(image, contour)
        
        logger.info(f"透视矫正成功，输出尺寸: {warped.shape}")
        return warped
        
    except Exception as e:
        logger.error(f"透视矫正失败: {e}")
        return image


def correct_perspective_safe(image: np.ndarray) -> Tuple[np.ndarray, bool]:
    """
    安全的透视矫正，返回是否成功
    
    Returns:
        (image, success)
    """
    try:
        result = correct_perspective(image)
        success = result is not image
        return result, success
    except Exception as e:
        logger.error(f"透视矫正异常: {e}")
        return image, False
