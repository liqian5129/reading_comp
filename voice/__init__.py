"""
语音模块
"""
from .recorder import VoiceRecorder
from .asr import AliyunStreamASR, create_asr

__all__ = ['VoiceRecorder', 'AliyunStreamASR', 'create_asr']

# 条件导入本地 FunASR（funasr 未安装时不影响整个模块）
try:
    from .funasr_asr import FunasrASR, create_local_asr
    __all__ += ['FunasrASR', 'create_local_asr']
except ImportError:
    pass
