"""
语音模块
"""
from .recorder import VoiceRecorder
from .asr import AliyunStreamASR, create_asr

__all__ = ['VoiceRecorder', 'AliyunStreamASR', 'create_asr']
