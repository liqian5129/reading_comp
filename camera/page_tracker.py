"""
页面追踪模块
用于检测翻页动作
"""
import cv2
import numpy as np
import hashlib
from typing import Optional
import logging

logger = logging.getLogger(__name__)


def fingerprint(image: np.ndarray, hash_size: int = 16) -> str:
    """
    计算图像指纹（感知哈希 + 内容哈希混合）
    
    Args:
        image: 输入图像
        hash_size: 哈希尺寸
        
    Returns:
        指纹字符串
    """
    try:
        # 转为灰度图
        if len(image.shape) == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image
        
        # 缩放为小图
        small = cv2.resize(gray, (hash_size, hash_size), interpolation=cv2.INTER_AREA)
        
        # 计算平均亮度
        avg = small.mean()
        
        # 生成二值哈希
        diff = small > avg
        hash_bits = diff.flatten().tolist()
        
        # 转为十六进制字符串
        hash_str = ''.join(['1' if b else '0' for b in hash_bits])
        hash_hex = hex(int(hash_str, 2))[2:].zfill(hash_size * hash_size // 4)
        
        return hash_hex
        
    except Exception as e:
        logger.error(f"计算指纹失败: {e}")
        return ""


def hamming_distance(hash1: str, hash2: str) -> int:
    """
    计算两个哈希的汉明距离
    """
    if len(hash1) != len(hash2):
        return float('inf')
    
    try:
        # 转为二进制
        bin1 = bin(int(hash1, 16))[2:].zfill(len(hash1) * 4)
        bin2 = bin(int(hash2, 16))[2:].zfill(len(hash2) * 4)
        
        # 计算不同位数
        return sum(c1 != c2 for c1, c2 in zip(bin1, bin2))
    except:
        return float('inf')


def is_page_turn(fp1: Optional[str], fp2: Optional[str], threshold: int = 10) -> bool:
    """
    判断是否翻页
    
    Args:
        fp1: 上一页指纹
        fp2: 当前页指纹
        threshold: 汉明距离阈值，超过则认为是不同页面
        
    Returns:
        True 表示翻页，False 表示同一页
    """
    if not fp1 or not fp2:
        return True  # 没有历史记录，视为翻页
    
    if fp1 == fp2:
        return False  # 完全相同
    
    distance = hamming_distance(fp1, fp2)
    return distance > threshold


class PageTracker:
    """
    页面追踪器
    """
    
    def __init__(self, threshold: int = 10):
        self.current_fingerprint: Optional[str] = None
        self.threshold = threshold
        self.page_count = 0
        
    def update(self, image: np.ndarray) -> bool:
        """
        更新当前页面
        
        Args:
            image: 当前帧图像
            
        Returns:
            True 表示翻页，False 表示未翻页
        """
        new_fp = fingerprint(image)
        
        if not new_fp:
            return False
        
        is_new_page = is_page_turn(self.current_fingerprint, new_fp, self.threshold)
        
        if is_new_page:
            self.page_count += 1
            logger.info(f"检测到翻页 (第 {self.page_count} 页)")
        
        self.current_fingerprint = new_fp
        return is_new_page
    
    def reset(self):
        """重置追踪器"""
        self.current_fingerprint = None
        self.page_count = 0
