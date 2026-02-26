#!/usr/bin/env python3
"""
配置向导脚本

帮助你生成 config.json 配置文件

运行：
    python setup.py
"""
import json
import os
from pathlib import Path


def input_with_default(prompt: str, default: str = "") -> str:
    """带默认值的输入"""
    if default:
        user_input = input(f"{prompt} [{default}]: ").strip()
    else:
        user_input = input(f"{prompt}: ").strip()
    return user_input if user_input else default


def input_yes_no(prompt: str, default: bool = False) -> bool:
    """Yes/No 输入"""
    default_str = "Y/n" if default else "y/N"
    user_input = input(f"{prompt} [{default_str}]: ").strip().lower()
    if not user_input:
        return default
    return user_input in ('y', 'yes', 'true', '1')


def main():
    print("=" * 60)
    print("🎉 AI 读书搭子 - 配置向导")
    print("=" * 60)
    print()
    print("本向导将帮助你创建 config.json 配置文件")
    print("你可以直接回车使用默认值，或输入你自己的值")
    print()
    
    config = {}
    
    # AI 配置
    print("-" * 60)
    print("[1/5] AI 配置 (Kimi)")
    print("-" * 60)
    print("获取 API Key: https://platform.moonshot.cn/")
    print()
    
    ai_config = {
        "api_key": input_with_default("Kimi API Key"),
        "model": input_with_default("模型", "kimi-latest"),
        "base_url": input_with_default("API 地址", "https://api.moonshot.cn/v1")
    }
    config["ai"] = ai_config
    
    # 阿里云 NLS 配置
    print()
    print("-" * 60)
    print("[2/5] 阿里云 NLS 配置 (ASR + TTS)")
    print("-" * 60)
    print("获取 App Key: https://nls-portal.console.aliyun.com/")
    print("获取 Token: 在控制台创建项目后获取")
    print()
    
    nls_config = {
        "app_key": input_with_default("NLS App Key"),
        "token": input_with_default("NLS Token（可选，可留空）"),
        "access_key_id": input_with_default("阿里云 AccessKey ID（可选）"),
        "access_key_secret": input_with_default("阿里云 AccessKey Secret（可选）")
    }
    config["aliyun_nls"] = nls_config
    
    # TTS 配置
    print()
    print("-" * 60)
    print("[3/5] TTS 配置")
    print("-" * 60)
    
    import platform
    system = platform.system()
    if system == "Darwin":
        default_player = "afplay"
    elif system == "Linux":
        default_player = "aplay"
    else:
        default_player = "afplay"
    
    tts_config = {
        "voice": input_with_default("发音人", "zh-CN-XiaoxiaoNeural"),
        "player_cmd": input_with_default("播放器命令", default_player)
    }
    config["tts"] = tts_config
    
    # 飞书配置
    print()
    print("-" * 60)
    print("[4/5] 飞书配置（可选）")
    print("-" * 60)
    print("创建应用: https://open.feishu.cn/app/")
    print()
    
    feishu_enabled = input_yes_no("是否启用飞书 Bot", default=False)
    
    if feishu_enabled:
        feishu_config = {
            "enabled": True,
            "app_id": input_with_default("飞书 App ID (cli_xxx)"),
            "app_secret": input_with_default("飞书 App Secret"),
            "encrypt_key": input_with_default("加密密钥（可选）"),
            "verification_token": input_with_default("验证 Token（可选）")
        }
    else:
        feishu_config = {
            "enabled": False,
            "app_id": "",
            "app_secret": "",
            "encrypt_key": "",
            "verification_token": ""
        }
    config["feishu"] = feishu_config
    
    # 摄像头配置
    print()
    print("-" * 60)
    print("[5/5] 摄像头配置")
    print("-" * 60)
    
    try:
        import cv2
        cap = cv2.VideoCapture(0)
        if cap.isOpened():
            print("✅ 检测到摄像头设备 0")
            default_device = "0"
        else:
            print("⚠️ 未检测到默认摄像头")
            default_device = "0"
        cap.release()
    except:
        default_device = "0"
    
    camera_config = {
        "device": int(input_with_default("摄像头设备号", default_device)),
        "auto_scan_interval": int(input_with_default("自动扫描间隔（秒）", "2"))
    }
    config["camera"] = camera_config
    
    # 数据目录
    config["data"] = {
        "data_dir": "./data"
    }
    
    # 保存配置
    print()
    print("-" * 60)
    print("正在保存配置...")
    print("-" * 60)
    
    config_path = Path("config.json")
    
    # 如果已存在，备份
    if config_path.exists():
        backup_path = Path("config.json.backup")
        config_path.rename(backup_path)
        print(f"⚠️ 已备份旧配置到 {backup_path}")
    
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    
    print(f"✅ 配置已保存到: {config_path.absolute()}")
    print()
    print("=" * 60)
    print("配置完成！你可以：")
    print("  1. 运行 python config.py 查看配置")
    print("  2. 运行 python test_basic.py 测试功能")
    print("  3. 运行 python main.py 启动程序")
    print("=" * 60)
    
    # 检查关键配置
    missing = []
    if not config["ai"]["api_key"]:
        missing.append("Kimi API Key")
    if not config["aliyun_nls"]["app_key"]:
        missing.append("阿里云 NLS App Key")
    
    if missing:
        print()
        print("⚠️  注意：以下配置项未填写，运行时会报错：")
        for item in missing:
            print(f"    - {item}")
        print("你可以手动编辑 config.json 补充这些信息")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n已取消")
        exit(1)
