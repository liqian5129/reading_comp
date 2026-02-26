#!/usr/bin/env python3
"""
基础功能测试脚本

测试各模块是否能正常导入和初始化

运行：
    python test_basic.py
"""
import sys
import asyncio
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test")


def test_imports():
    """测试模块导入"""
    logger.info("测试模块导入...")
    try:
        from config import config
        from camera import capture_frame, correct_perspective
        from ocr.engine import extract_text
        from agent.kimi_client import KimiClient
        from agent.memory import Memory
        from agent.tools import ToolRegistry
        from session.models import ReadingSession
        from session.storage import Storage
        from session.manager import SessionManager
        from scanner.auto_scanner import AutoScanner
        from voice.asr import AliyunStreamASR
        from voice.recorder import VoiceRecorder
        from tts.speaker import AliyunTTS, TTSPlayer
        from feishu.bot import FeishuBot
        from feishu.push import SummaryPusher
        logger.info("✓ 所有模块导入成功")
        return True
    except Exception as e:
        logger.error(f"✗ 模块导入失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_config():
    """测试配置"""
    logger.info("测试配置...")
    from config import config
    
    missing = config.validate()
    if missing:
        logger.warning(f"⚠ 缺少配置项: {missing}")
        logger.info("提示: 运行 python setup.py 生成配置文件")
    else:
        logger.info("✓ 配置完整")
    
    # 确保目录
    config.ensure_dirs()
    logger.info(f"✓ 数据目录: {config.DATA_DIR}")
    return True


async def test_database():
    """测试数据库"""
    logger.info("测试数据库...")
    try:
        from config import config
        from session.storage import Storage
        
        storage = Storage(config.SESSIONS_DB)
        await storage.initialize()
        logger.info("✓ 数据库初始化成功")
        
        await storage.close()
        logger.info("✓ 数据库关闭成功")
        return True
    except Exception as e:
        logger.error(f"✗ 数据库测试失败: {e}")
        return False


def test_camera():
    """测试摄像头"""
    logger.info("测试摄像头...")
    try:
        from camera import capture_frame
        import cv2
        
        # 尝试打开摄像头
        cap = cv2.VideoCapture(0)
        if cap.isOpened():
            ret, frame = cap.read()
            cap.release()
            if ret:
                logger.info(f"✓ 摄像头可用，分辨率: {frame.shape}")
                return True
            else:
                logger.warning("⚠ 摄像头打开但无法读取帧")
                return False
        else:
            logger.warning("⚠ 无法打开摄像头（可能未连接或无权限）")
            return False
    except Exception as e:
        logger.error(f"✗ 摄像头测试失败: {e}")
        return False


def test_perspective():
    """测试透视矫正"""
    logger.info("测试透视矫正...")
    try:
        import numpy as np
        from camera.perspective import correct_perspective
        
        # 创建测试图像
        test_img = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)
        result = correct_perspective(test_img)
        
        logger.info(f"✓ 透视矫正函数可用，输出尺寸: {result.shape}")
        return True
    except Exception as e:
        logger.error(f"✗ 透视矫正测试失败: {e}")
        return False


async def main():
    """运行所有测试"""
    logger.info("=" * 50)
    logger.info("AI 读书搭子 - 基础功能测试")
    logger.info("=" * 50)
    
    results = []
    
    # 基础导入测试
    results.append(("模块导入", test_imports()))
    results.append(("配置检查", test_config()))
    results.append(("透视矫正", test_perspective()))
    
    # 异步测试
    results.append(("数据库", await test_database()))
    
    # 摄像头测试（可能因硬件不可用而失败，不影响整体）
    results.append(("摄像头", test_camera()))
    
    # 总结
    logger.info("=" * 50)
    logger.info("测试结果:")
    for name, result in results:
        status = "✓ 通过" if result else "✗ 失败"
        logger.info(f"  {name}: {status}")
    
    passed = sum(1 for _, r in results if r)
    total = len(results)
    logger.info(f"\n总计: {passed}/{total} 项通过")
    
    if passed == total:
        logger.info("🎉 所有测试通过！可以运行 python main.py 启动")
    else:
        logger.warning("⚠ 部分测试未通过，请检查配置和依赖")


if __name__ == "__main__":
    asyncio.run(main())
