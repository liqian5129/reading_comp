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
        
        # AI 提供商
        self.AI_PROVIDER = self._get("ai", "provider", "kimi")
        
        # Kimi 配置
        self.KIMI_API_KEY = self._get("ai", "kimi_api_key", "")
        self.KIMI_MODEL = self._get("ai", "kimi_model", "kimi-k2.5")
        self.KIMI_BASE_URL = self._get("ai", "kimi_base_url", "https://api.moonshot.cn/v1")
        self.KIMI_ENABLE_THINKING = self._get("ai", "kimi_enable_thinking", False)
        
        # 豆包配置
        self.DOUBAO_API_KEY = self._get("ai", "doubao_api_key", "")
        self.DOUBAO_MODEL = self._get("ai", "doubao_model", "ep-xxxxxxxxxxxxx")
        self.DOUBAO_BASE_URL = self._get("ai", "doubao_base_url", "https://ark.cn-beijing.volces.com/api/v3")
        
        # 根据提供商选择当前 AI 配置
        if self.AI_PROVIDER == "kimi":
            self.CURRENT_API_KEY = self.KIMI_API_KEY
            self.CURRENT_MODEL = self.KIMI_MODEL
            self.CURRENT_BASE_URL = self.KIMI_BASE_URL
        else:
            self.CURRENT_API_KEY = self.DOUBAO_API_KEY
            self.CURRENT_MODEL = self.DOUBAO_MODEL
            self.CURRENT_BASE_URL = self.DOUBAO_BASE_URL
        
        # TTS 提供商
        self.TTS_PROVIDER = self._get("tts", "provider", "aliyun")
        
        # 阿里云 TTS 配置
        self.ALIYUN_TTS_VOICE = self._get("tts", "aliyun_voice", "xiaoyun")
        self.ALIYUN_TTS_PLAYER = self._get("tts", "aliyun_player_cmd", "afplay")
        
        # ElevenLabs TTS 配置
        self.ELEVENLABS_API_KEY = self._get("tts", "elevenlabs_api_key", "")
        self.ELEVENLABS_VOICE_ID = self._get("tts", "elevenlabs_voice_id", "pNInz6obpgDQGcFmaJgB")
        self.ELEVENLABS_MODEL = self._get("tts", "elevenlabs_model", "eleven_multilingual_v2")
        self.ELEVENLABS_PLAYER = self._get("tts", "elevenlabs_player_cmd", "afplay")
        
        # 豆包 TTS 配置 (火山引擎)
        self.DOUBAO_TTS_APPID = self._get("tts", "doubao_tts_appid", "")
        self.DOUBAO_TTS_TOKEN = self._get("tts", "doubao_tts_token", "")
        self.DOUBAO_TTS_CLUSTER = self._get("tts", "doubao_tts_cluster", "volcano_tts")
        self.DOUBAO_TTS_VOICE_TYPE = self._get("tts", "doubao_tts_voice_type", "BV001_streaming")
        self.DOUBAO_TTS_EMOTION = self._get("tts", "doubao_tts_emotion", "happy")
        self.DOUBAO_TTS_SPEED_RATIO = self._get("tts", "doubao_tts_speed_ratio", 1.0)
        self.DOUBAO_TTS_VOLUME_RATIO = self._get("tts", "doubao_tts_volume_ratio", 1.0)
        self.DOUBAO_TTS_PITCH_RATIO = self._get("tts", "doubao_tts_pitch_ratio", 1.0)
        self.DOUBAO_TTS_PLAYER_CMD = self._get("tts", "doubao_tts_player_cmd", "afplay")
        
        # 阿里云 NLS (ASR)
        self.ALIYUN_NLS_APP_KEY = self._get("aliyun_nls", "app_key", "")
        self.ALIYUN_NLS_TOKEN = self._get("aliyun_nls", "token", "")
        self.ALIYUN_NLS_ACCESS_KEY_ID = self._get("aliyun_nls", "access_key_id", "")
        self.ALIYUN_NLS_ACCESS_KEY_SECRET = self._get("aliyun_nls", "access_key_secret", "")
        
        # 飞书
        self.FEISHU_ENABLED = self._get("feishu", "enabled", False)
        self.FEISHU_APP_ID = self._get("feishu", "app_id", "")
        self.FEISHU_APP_SECRET = self._get("feishu", "app_secret", "")
        self.FEISHU_ENCRYPT_KEY = self._get("feishu", "encrypt_key", "")
        self.FEISHU_VERIFICATION_TOKEN = self._get("feishu", "verification_token", "")
        
        # 摄像头
        self.CAMERA_DEVICE = self._get("camera", "device", 0)
        # True 时自动探测第一个外接 USB 摄像头（忽略 camera.device 设置）
        self.CAMERA_AUTO_DETECT = self._get("camera", "auto_detect", False)
        self.AUTO_SCAN_INTERVAL = self._get("camera", "auto_scan_interval", 2)
        self.SCANNER_ENABLED = self._get("camera", "scanner_enabled", False)

        # 调试模式：跳过 ASR/AI/TTS/飞书，仅运行摄像头+OCR
        self.DEBUG_MODE = self._get("debug", "debug_mode", False)
        
        # 视觉分析器（kimi-k2.5 原生支持图片，默认复用主模型）
        self.VISION_ANALYZER_ENABLED = self._get("vision", "enabled", False)
        self.VISION_MODEL = self._get("vision", "model", self.CURRENT_MODEL)
        self.VISION_BASE_URL = self._get("vision", "base_url", self.CURRENT_BASE_URL)
        self.VISION_API_KEY = self._get("vision", "api_key", self.CURRENT_API_KEY)

        # 数据目录
        data_dir = self._get("data", "data_dir", "./data")
        self.DATA_DIR = Path(data_dir)
        self.SESSIONS_DB = self.DATA_DIR / "sessions.db"
        self.SNAPSHOTS_DIR = self.DATA_DIR / "snapshots"
        self.NOTES_DIR = self.DATA_DIR / "notes"
        self.PERSONA_FILE = self.DATA_DIR / "persona.json"
        self.LONG_TERM_MEMORY_FILE = self.DATA_DIR / "long_term_memory.json"
    
    def _load_json_config(self):
        """从 config.json 加载配置"""
        config_paths = [
            Path("config.json"),
            Path.home() / ".reading_comp" / "config.json",
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
        
        self._json_config = {}
    
    def _get(self, section: str, key: str, default: Any = None) -> Any:
        """获取配置值"""
        # 1. 环境变量
        env_keys = [f"{section.upper()}_{key.upper()}", key.upper()]
        for env_key in env_keys:
            env_val = os.getenv(env_key)
            if env_val is not None:
                if isinstance(default, bool):
                    return env_val.lower() in ('true', '1', 'yes', 'on')
                elif isinstance(default, int):
                    try:
                        return int(env_val)
                    except:
                        return default
                elif isinstance(default, float):
                    try:
                        return float(env_val)
                    except:
                        return default
                return env_val
        
        # 2. config.json
        if section in self._json_config and key in self._json_config[section]:
            value = self._json_config[section][key]
            if isinstance(value, str) and value.startswith(("你的", "sk_")):
                return default
            return value
        
        # 3. 默认值
        return default
    
    def ensure_dirs(self):
        """确保数据目录存在"""
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        self.NOTES_DIR.mkdir(parents=True, exist_ok=True)
    
    def validate(self) -> list[str]:
        """验证配置是否完整"""
        missing = []
        
        if self.AI_PROVIDER == "kimi":
            if not self.KIMI_API_KEY:
                missing.append("ai.kimi_api_key")
        elif self.AI_PROVIDER == "doubao":
            if not self.DOUBAO_API_KEY:
                missing.append("ai.doubao_api_key")
        
        # TTS 验证
        if self.TTS_PROVIDER == "elevenlabs":
            if not self.ELEVENLABS_API_KEY:
                missing.append("tts.elevenlabs_api_key")
        elif self.TTS_PROVIDER == "doubao":
            if not self.DOUBAO_TTS_APPID:
                missing.append("tts.doubao_tts_appid")
            if not self.DOUBAO_TTS_TOKEN:
                missing.append("tts.doubao_tts_token")
        elif self.TTS_PROVIDER == "aliyun":
            if not self.ALIYUN_NLS_APP_KEY:
                missing.append("aliyun_nls.app_key")
        
        if self.FEISHU_ENABLED:
            if not self.FEISHU_APP_ID:
                missing.append("feishu.app_id")
            if not self.FEISHU_APP_SECRET:
                missing.append("feishu.app_secret")
        
        return missing
    
    def print_config(self, hide_secrets: bool = True):
        """打印当前配置"""
        print("=" * 50)
        print("当前配置:")
        print("=" * 50)
        
        def mask_secret(value: str) -> str:
            if not value or len(value) < 8:
                return "未设置"
            if hide_secrets:
                return value[:4] + "****" + value[-4:]
            return value
        
        print(f"\n[AI]")
        print(f"  Provider: {self.AI_PROVIDER}")
        print(f"  Model: {self.CURRENT_MODEL}")
        
        print(f"\n[TTS]")
        print(f"  Provider: {self.TTS_PROVIDER}")
        if self.TTS_PROVIDER == "doubao":
            print(f"  Voice: {self.DOUBAO_TTS_VOICE_TYPE}")
            print(f"  Emotion: {self.DOUBAO_TTS_EMOTION}")
        elif self.TTS_PROVIDER == "elevenlabs":
            print(f"  Voice ID: {self.ELEVENLABS_VOICE_ID}")
        else:
            print(f"  Voice: {self.ALIYUN_TTS_VOICE}")
        
        print("=" * 50)


config = Config()


if __name__ == "__main__":
    config.print_config()
    
    missing = config.validate()
    if missing:
        print(f"\n⚠️  缺少配置项:")
        for item in missing:
            print(f"  - {item}")
    else:
        print("\n✅ 配置完整")
