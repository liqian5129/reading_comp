"""
TTS 模块
支持阿里云、ElevenLabs、豆包三种 TTS 服务
"""
from .speaker import AliyunTTS, TTSPlayer
from .elevenlabs_speaker import ElevenLabsTTS, ElevenLabsTTSPlayer
from .doubao_speaker import DoubaoTTS, DoubaoTTSPlayer


def create_tts_player(config):
    """
    根据配置创建 TTS 播放器
    
    Args:
        config: 配置对象
        
    Returns:
        TTSPlayer 或 ElevenLabsTTSPlayer 或 DoubaoTTSPlayer
    """
    provider = getattr(config, 'TTS_PROVIDER', 'aliyun')
    
    if provider == "elevenlabs":
        logger.info(f"🔊 使用 ElevenLabs TTS")
        return ElevenLabsTTSPlayer(
            api_key=config.ELEVENLABS_API_KEY,
            voice_id=config.ELEVENLABS_VOICE_ID,
            model=config.ELEVENLABS_MODEL,
            player_cmd=config.ELEVENLABS_PLAYER
        )
    elif provider == "doubao":
        logger.info(f"🔊 使用豆包 TTS (火山引擎)")
        return DoubaoTTSPlayer(
            appid=config.DOUBAO_TTS_APPID,
            token=config.DOUBAO_TTS_TOKEN,
            cluster=config.DOUBAO_TTS_CLUSTER,
            voice_type=config.DOUBAO_TTS_VOICE_TYPE,
            emotion=config.DOUBAO_TTS_EMOTION,
            speed_ratio=config.DOUBAO_TTS_SPEED_RATIO,
            volume_ratio=config.DOUBAO_TTS_VOLUME_RATIO,
            pitch_ratio=config.DOUBAO_TTS_PITCH_RATIO,
            player_cmd=config.DOUBAO_TTS_PLAYER_CMD
        )
    else:  # aliyun
        logger.info(f"🔊 使用阿里云 TTS")
        from .speaker import AliyunTTS, TTSPlayer
        tts = AliyunTTS(
            app_key=config.ALIYUN_NLS_APP_KEY, 
            token=config.ALIYUN_NLS_TOKEN
        )
        return TTSPlayer(
            tts, 
            player_cmd=config.ALIYUN_TTS_PLAYER
        )


import logging
logger = logging.getLogger(__name__)

__all__ = [
    'AliyunTTS', 
    'TTSPlayer', 
    'ElevenLabsTTS', 
    'ElevenLabsTTSPlayer',
    'DoubaoTTS',
    'DoubaoTTSPlayer',
    'create_tts_player'
]
