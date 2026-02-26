"""
统一配置管理
支持从 .env 文件或 config.json 加载配置
"""
import os
import json
import logging
from pathlib import Path
from typing import Optional, Any

# 尝试加载 python-dotenv
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger(__name__)


class Config:
    """配置类"""
    
    def __init__(self):
        self._config_file: Optional[Path] = None
        self._json_config: dict = {}
        
        # 尝试加载 config.json
        self._load_json_config()
        
        # AI API (Kimi)
        self.KIMI_API_KEY = self._get("ai", "api_key", "")
        self.KIMI_MODEL = self._get("ai", "model", "kimi-latest")
        self.KIMI_BASE_URL = self._get("ai", "base_url", "https://api.moonshot.cn/v1")
        
        # 阿里云 NLS (ASR & TTS)
        self.ALIYUN_NLS_APP_KEY = self._get("aliyun_nls", "app_key", "")
        self.ALIYUN_NLS_TOKEN = self._get("aliyun_nls", "token", "")
        self.ALIYUN_NLS_ACCESS_KEY_ID = self._get("aliyun_nls", "access_key_id", "")
        self.ALIYUN_NLS_ACCESS_KEY_SECRET = self._get("aliyun_nls", "access_key_secret", "")
        
        # TTS
        self.TTS_VOICE = self._get("tts", "voice", "zh-CN-XiaoxiaoNeural")
        self.TTS_PLAYER_CMD = self._get("tts", "player_cmd", "afplay")
        
        # 飞书
        self.FEISHU_ENABLED = self._get("feishu", "enabled", False)
        self.FEISHU_APP_ID = self._get("feishu", "app_id", "")
        self.FEISHU_APP_SECRET = self._get("feishu", "app_secret", "")
        self.FEISHU_ENCRYPT_KEY = self._get("feishu", "encrypt_key", "")
        self.FEISHU_VERIFICATION_TOKEN = self._get("feishu", "verification_token", "")
        
        # 摄像头 & 扫描
        self.CAMERA_DEVICE = self._get("camera", "device", 0)
        self.AUTO_SCAN_INTERVAL = self._get("camera", "auto_scan_interval", 2)
        
        # 数据目录
        data_dir = self._get("data", "data_dir", "./data")
        self.DATA_DIR = Path(data_dir)
        self.SESSIONS_DB = self.DATA_DIR / "sessions.db"
        self.SNAPSHOTS_DIR = self.DATA_DIR / "snapshots"
        self.NOTES_DIR = self.DATA_DIR / "notes"
        self.PERSONA_FILE = self.DATA_DIR / "persona.json"
    
    def _load_json_config(self):
        """从 config.json 加载配置"""
        config_paths = [
            Path("config.json"),
            Path.home() / ".reading_comp" / "config.json",
            Path("/etc/reading_comp/config.json"),
        ]
        
        for path in config_paths:
            if path.exists():
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        self._json_config = json.load(f)
                    self._config_file = path
                    logger.info(f"已加载配置文件: {path}")
                    return
                except Exception as e:
                    logger.warning(f"加载配置文件失败 {path}: {e}")
        
        # 如果没有找到配置文件，使用空配置
        self._json_config = {}
    
    def _get(self, section: str, key: str, default: Any = None) -> Any:
        """
        获取配置值，优先级：
        1. 环境变量（大写，如 KIMI_API_KEY）
        2. config.json 文件
        3. 默认值
        """
        # 1. 环境变量（支持 section_key 格式）
        env_keys = [
            f"{section.upper()}_{key.upper()}",
            key.upper(),
        ]
        for env_key in env_keys:
            env_val = os.getenv(env_key)
            if env_val is not None:
                # 类型转换
                if isinstance(default, bool):
                    return env_val.lower() in ('true', '1', 'yes', 'on')
                elif isinstance(default, int):
                    try:
                        return int(env_val)
                    except:
                        return default
                return env_val
        
        # 2. config.json
        if section in self._json_config and key in self._json_config[section]:
            value = self._json_config[section][key]
            # 忽略注释字段
            if isinstance(value, str) and value.startswith("你的"):
                return default
            if isinstance(value, str) and value.startswith("可选"):
                return default if value.startswith("可选") else value
            return value
        
        # 3. 默认值
        return default
    
    def ensure_dirs(self):
        """确保数据目录存在"""
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        self.NOTES_DIR.mkdir(parents=True, exist_ok=True)
    
    def validate(self) -> list[str]:
        """验证配置是否完整，返回缺失的配置项"""
        missing = []
        
        if not self.KIMI_API_KEY or self.KIMI_API_KEY.startswith("你的"):
            missing.append("ai.api_key (或环境变量 KIMI_API_KEY)")
        
        if not self.ALIYUN_NLS_APP_KEY or self.ALIYUN_NLS_APP_KEY.startswith("你的"):
            missing.append("aliyun_nls.app_key (或环境变量 ALIYUN_NLS_APP_KEY)")
        
        if not self.ALIYUN_NLS_TOKEN or self.ALIYUN_NLS_TOKEN.startswith("你的"):
            # 如果有 AK/SK，可以自动获取 Token
            if not (self.ALIYUN_NLS_ACCESS_KEY_ID and self.ALIYUN_NLS_ACCESS_KEY_SECRET):
                missing.append("aliyun_nls.token (或环境变量 ALIYUN_NLS_TOKEN，或提供 access_key_id + access_key_secret)")
        
        if self.FEISHU_ENABLED:
            if not self.FEISHU_APP_ID:
                missing.append("feishu.app_id (飞书功能已启用)")
            if not self.FEISHU_APP_SECRET:
                missing.append("feishu.app_secret (飞书功能已启用)")
        
        return missing
    
    def print_config(self, hide_secrets: bool = True):
        """打印当前配置（用于调试）"""
        print("=" * 50)
        print("当前配置:")
        print("=" * 50)
        
        def mask_secret(value: str) -> str:
            if not value or len(value) < 8:
                return "未设置"
            if hide_secrets:
                return value[:4] + "****" + value[-4:]
            return value
        
        print(f"\n[AI - Kimi]")
        print(f"  API Key: {mask_secret(self.KIMI_API_KEY)}")
        print(f"  Model: {self.KIMI_MODEL}")
        print(f"  Base URL: {self.KIMI_BASE_URL}")
        
        print(f"\n[阿里云 NLS]")
        print(f"  App Key: {mask_secret(self.ALIYUN_NLS_APP_KEY)}")
        print(f"  Token: {mask_secret(self.ALIYUN_NLS_TOKEN)}")
        print(f"  Access Key ID: {mask_secret(self.ALIYUN_NLS_ACCESS_KEY_ID)}")
        
        print(f"\n[TTS]")
        print(f"  Voice: {self.TTS_VOICE}")
        print(f"  Player: {self.TTS_PLAYER_CMD}")
        
        print(f"\n[飞书]")
        print(f"  Enabled: {self.FEISHU_ENABLED}")
        print(f"  App ID: {self.FEISHU_APP_ID or '未设置'}")
        
        print(f"\n[摄像头]")
        print(f"  Device: {self.CAMERA_DEVICE}")
        print(f"  Scan Interval: {self.AUTO_SCAN_INTERVAL}s")
        
        print(f"\n[数据目录]")
        print(f"  Path: {self.DATA_DIR}")
        print("=" * 50)


# 全局配置实例
config = Config()


if __name__ == "__main__":
    # 直接运行 config.py 可以查看当前配置
    config.print_config()
    
    missing = config.validate()
    if missing:
        print(f"\n⚠️  缺少配置项:")
        for item in missing:
            print(f"  - {item}")
    else:
        print("\n✅ 配置完整")
