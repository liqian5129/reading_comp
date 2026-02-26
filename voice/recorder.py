"""
语音录音模块
使用 pynput 监听右 Alt 键 + sounddevice 录音
实时将音频流推送到 ASR
"""
import asyncio
import logging
import threading
import time
from typing import Callable, Optional
from dataclasses import dataclass
from enum import Enum, auto

import numpy as np
import sounddevice as sd
from pynput import keyboard

logger = logging.getLogger(__name__)


class RecordingState(Enum):
    """录音状态"""
    IDLE = auto()
    RECORDING = auto()
    PROCESSING = auto()


@dataclass
class VoiceSegment:
    """语音片段"""
    text: str
    duration_ms: float


class VoiceRecorder:
    """
    语音录音器
    
    按住右 Alt 键开始录音，松开结束
    实时将音频推送到 ASR 进行识别
    """
    
    def __init__(self, 
                 asr_engine,
                 loop: Optional[asyncio.AbstractEventLoop] = None,
                 sample_rate: int = 16000,
                 channels: int = 1,
                 min_duration: float = 0.3,
                 trigger_key=keyboard.Key.alt_r):
        """
        Args:
            asr_engine: ASR 引擎实例 (如 AliyunStreamASR)
            loop: 事件循环，用于跨线程调度协程
            sample_rate: 采样率，默认 16kHz
            channels: 声道数，默认单声道
            min_duration: 最短录音时长（秒），低于此值丢弃
            trigger_key: 触发录音的按键，默认右 Alt
        """
        self.asr = asr_engine
        self.loop = loop
        self.sample_rate = sample_rate
        self.channels = channels
        self.min_duration = min_duration
        self.trigger_key = trigger_key
        
        # 状态
        self.state = RecordingState.IDLE
        self._recording_start_time: Optional[float] = None
        self._audio_buffer: list[np.ndarray] = []
        self._pre_buffer: list[np.ndarray] = []  # 预缓冲，解决 ASR 启动延迟
        
        # 回调
        self.on_text: Optional[Callable[[str], None]] = None
        self.on_segment: Optional[Callable[[VoiceSegment], None]] = None
        
        # 组件
        self._stream: Optional[sd.InputStream] = None
        self._keyboard_listener: Optional[keyboard.Listener] = None
        self._lock = threading.Lock()
        
        # ASR 就绪等待
        self._asr_ready = threading.Event()
        
    def _audio_callback(self, indata, frames, time_info, status):
        """sounddevice 音频回调"""
        if status:
            logger.warning(f"音频状态: {status}")
        
        if self.state == RecordingState.RECORDING:
            # 将 float32 转为 int16 PCM
            pcm_data = (indata * 32767).astype(np.int16).tobytes()
            
            # 如果 ASR 还没准备好，先缓冲
            if not self._asr_ready.is_set():
                self._pre_buffer.append(pcm_data)
                # 限制预缓冲大小（最多 2 秒）
                max_pre_buffer = int(2 * self.sample_rate / 1024)
                if len(self._pre_buffer) > max_pre_buffer:
                    self._pre_buffer.pop(0)
            else:
                # 先发送预缓冲的数据
                if self._pre_buffer:
                    for data in self._pre_buffer:
                        self.asr.send_audio(data)
                    self._pre_buffer = []
                    logger.debug(f"发送了 {len(self._pre_buffer)} 块预缓冲音频")
                
                # 实时推送到 ASR
                self.asr.send_audio(pcm_data)
            
            # 同时缓存（用于计算时长等）
            self._audio_buffer.append(indata.copy())
    
    def _on_key_press(self, key):
        """按键按下"""
        if key == self.trigger_key and self.state == RecordingState.IDLE:
            self._start_recording()
    
    def _on_key_release(self, key):
        """按键释放"""
        if key == self.trigger_key and self.state == RecordingState.RECORDING:
            self._stop_recording()
    
    def _start_recording(self):
        """开始录音"""
        with self._lock:
            if self.state != RecordingState.IDLE:
                return
            
            self.state = RecordingState.RECORDING
            self._recording_start_time = time.time()
            self._audio_buffer = []
            self._pre_buffer = []
            self._asr_ready.clear()
            
            logger.info("🎤 开始录音...")
            
            # 启动 ASR（在后台线程，避免阻塞）
            threading.Thread(target=self._start_asr, daemon=True).start()
    
    def _start_asr(self):
        """在后台启动 ASR"""
        try:
            self.asr.start(on_result=self._on_asr_result)
            self._asr_ready.set()
            logger.debug("ASR 已就绪")
        except Exception as e:
            logger.error(f"启动 ASR 失败: {e}")
            # ASR 启动失败，结束录音
            self._stop_recording()
    
    def _stop_recording(self):
        """停止录音"""
        with self._lock:
            if self.state != RecordingState.RECORDING:
                return
            
            self.state = RecordingState.PROCESSING
            duration = time.time() - self._recording_start_time
            
            # 检查最短时长
            if duration < self.min_duration:
                logger.debug(f"录音时长过短 ({duration:.2f}s)，丢弃")
                self.asr.stop()
                self.state = RecordingState.IDLE
                return
            
            logger.info(f"🛑 停止录音，时长: {duration:.2f}s")
            
            # 停止 ASR 并获取结果
            text = self.asr.stop()
            
            if text.strip():
                segment = VoiceSegment(text=text.strip(), duration_ms=duration * 1000)
                
                if self.on_segment:
                    try:
                        self.on_segment(segment)
                    except Exception as e:
                        logger.error(f"on_segment 回调错误: {e}")
                
                if self.on_text:
                    try:
                        # 如果有事件循环，使用 run_coroutine_threadsafe
                        if self.loop and asyncio.iscoroutinefunction(self.on_text):
                            asyncio.run_coroutine_threadsafe(
                                self.on_text(text.strip()), 
                                self.loop
                            )
                        else:
                            self.on_text(text.strip())
                    except Exception as e:
                        logger.error(f"on_text 回调错误: {e}")
            else:
                logger.info("未识别到语音")
            
            self.state = RecordingState.IDLE
    
    def _on_asr_result(self, result):
        """ASR 中间结果回调（可选使用）"""
        logger.debug(f"ASR 实时结果: {result.text}")
    
    def start(self):
        """启动录音监听器"""
        # 启动音频流（保持打开，减少启动延迟）
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype=np.float32,
            callback=self._audio_callback,
            blocksize=1024,  # 约 64ms @ 16kHz
        )
        self._stream.start()
        
        # 启动键盘监听
        self._keyboard_listener = keyboard.Listener(
            on_press=self._on_key_press,
            on_release=self._on_key_release
        )
        self._keyboard_listener.start()
        
        logger.info(f"语音录音已启动，按住 [{self.trigger_key}] 说话")
    
    def stop(self):
        """停止录音监听器"""
        if self._keyboard_listener:
            self._keyboard_listener.stop()
            self._keyboard_listener = None
        
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        
        logger.info("语音录音已停止")
    
    def is_recording(self) -> bool:
        """是否正在录音"""
        return self.state == RecordingState.RECORDING


async def create_voice_recorder(asr_engine, loop=None, **kwargs) -> VoiceRecorder:
    """异步创建录音器"""
    recorder = VoiceRecorder(asr_engine, loop=loop, **kwargs)
    return recorder
