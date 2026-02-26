"""
TTS 播放模块
使用阿里云 NLS TTS
支持流式合成和播放队列
"""
import asyncio
import logging
import os
import subprocess
import tempfile
import threading
import queue
from typing import Optional, Callable
from dataclasses import dataclass
from enum import Enum, auto

import aiohttp

logger = logging.getLogger(__name__)


class TTSState(Enum):
    """TTS 状态"""
    IDLE = auto()
    SYNTHESIZING = auto()
    PLAYING = auto()


@dataclass
class TTSRequest:
    """TTS 请求"""
    text: str
    voice: str = "zh-CN-XiaoxiaoNeural"
    interrupt: bool = False  # 是否打断当前播放


class AliyunTTS:
    """
    阿里云 NLS TTS
    使用长文本语音合成接口
    """
    
    TTS_URL = "https://nls-gateway-cn-shanghai.aliyuncs.com/stream/v1/tts"
    
    def __init__(self, app_key: str, token: str):
        self.app_key = app_key
        self.token = token
        
    async def synthesize(self, text: str, voice: str = "xiaoyun", 
                        speech_rate: int = 0, pitch_rate: int = 0) -> Optional[bytes]:
        """
        合成语音
        
        Args:
            text: 要合成的文本
            voice: 发音人 (xiaoyun, xiaogang, etc.)
            speech_rate: 语速 -500~500
            pitch_rate: 音调 -500~500
            
        Returns:
            MP3 音频数据
        """
        headers = {
            "Content-Type": "application/json",
            "X-NLS-Token": self.token,
        }
        
        payload = {
            "appkey": self.app_key,
            "text": text,
            "format": "mp3",
            "sample_rate": 16000,
            "voice": voice,
            "volume": 50,
            "speech_rate": speech_rate,
            "pitch_rate": pitch_rate,
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.TTS_URL, 
                    headers=headers, 
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status == 200:
                        audio_data = await resp.read()
                        logger.debug(f"TTS 合成成功: {len(audio_data)} bytes")
                        return audio_data
                    else:
                        error_text = await resp.text()
                        logger.error(f"TTS 合成失败: {resp.status}, {error_text}")
                        return None
        except Exception as e:
            logger.error(f"TTS 请求失败: {e}")
            return None


class TTSPlayer:
    """
    TTS 播放器
    
    特点：
    - 异步队列，串流播放
    - 支持打断
    - 自动清理临时文件
    """
    
    def __init__(self, tts_engine: AliyunTTS, 
                 player_cmd: str = "afplay",
                 max_queue_size: int = 10):
        """
        Args:
            tts_engine: TTS 引擎
            player_cmd: 播放器命令 (afplay for macOS, aplay for Linux, etc.)
            max_queue_size: 播放队列最大长度
        """
        self.tts = tts_engine
        self.player_cmd = player_cmd
        self.max_queue_size = max_queue_size
        
        # 队列和状态
        self._queue: asyncio.Queue[TTSRequest] = asyncio.Queue(maxsize=max_queue_size)
        self._current_task: Optional[asyncio.Task] = None
        self._playing = False
        self._interrupt_event = asyncio.Event()
        
        # 临时文件目录
        self._temp_dir = tempfile.mkdtemp(prefix="reading_comp_tts_")
        
        # 任务
        self._worker_task: Optional[asyncio.Task] = None
        self._running = False
        
    async def start(self):
        """启动播放器"""
        self._running = True
        self._worker_task = asyncio.create_task(self._play_worker())
        logger.info("TTS 播放器已启动")
        
    async def stop(self):
        """停止播放器"""
        self._running = False
        
        # 打断当前播放
        self.interrupt()
        
        # 清空队列
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        
        # 等待工作线程结束
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        
        # 清理临时文件
        self._cleanup_temp_files()
        
        logger.info("TTS 播放器已停止")
    
    def _cleanup_temp_files(self):
        """清理临时文件"""
        try:
            for f in os.listdir(self._temp_dir):
                os.remove(os.path.join(self._temp_dir, f))
            os.rmdir(self._temp_dir)
        except Exception as e:
            logger.warning(f"清理临时文件失败: {e}")
    
    async def speak(self, text: str, voice: str = "xiaoyun", 
                   interrupt: bool = False) -> bool:
        """
        播放文本
        
        Args:
            text: 要播放的文本
            voice: 发音人
            interrupt: 是否打断当前播放
            
        Returns:
            是否成功加入队列
        """
        if not text.strip():
            return False
        
        request = TTSRequest(text=text, voice=voice, interrupt=interrupt)
        
        try:
            if interrupt:
                # 打断当前播放
                self.interrupt()
                # 尝试立即播放（如果队列满了，移除最旧的）
                if self._queue.full():
                    try:
                        old = self._queue.get_nowait()
                        logger.debug(f"丢弃旧请求: {old.text[:20]}...")
                    except asyncio.QueueEmpty:
                        pass
            
            await self._queue.put(request)
            return True
            
        except Exception as e:
            logger.error(f"添加 TTS 请求失败: {e}")
            return False
    
    def interrupt(self):
        """打断当前播放"""
        if self._playing:
            self._interrupt_event.set()
            logger.debug("TTS 播放被打断")
    
    def is_playing(self) -> bool:
        """是否正在播放"""
        return self._playing
    
    async def _play_worker(self):
        """播放工作协程"""
        while self._running:
            try:
                # 等待队列中的请求
                request = await asyncio.wait_for(
                    self._queue.get(), 
                    timeout=1.0
                )
            except asyncio.TimeoutError:
                continue
            
            # 重置打断事件
            self._interrupt_event.clear()
            
            # 合成并播放
            await self._synthesize_and_play(request)
    
    async def _synthesize_and_play(self, request: TTSRequest):
        """合成并播放"""
        try:
            self._playing = True
            
            # 合成语音
            audio_data = await self.tts.synthesize(
                request.text, 
                voice=request.voice
            )
            
            if audio_data is None:
                logger.error("TTS 合成失败")
                return
            
            # 检查是否被打断
            if self._interrupt_event.is_set():
                logger.debug("TTS 被打断，跳过播放")
                return
            
            # 保存临时文件
            temp_file = os.path.join(
                self._temp_dir, 
                f"tts_{asyncio.get_event_loop().time()}.mp3"
            )
            with open(temp_file, "wb") as f:
                f.write(audio_data)
            
            # 播放
            await self._play_audio(temp_file)
            
            # 清理临时文件
            try:
                os.remove(temp_file)
            except:
                pass
                
        finally:
            self._playing = False
    
    async def _play_audio(self, audio_file: str):
        """
        播放音频文件
        
        Args:
            audio_file: 音频文件路径
        """
        try:
            # 使用 subprocess 播放，同时监听打断事件
            proc = await asyncio.create_subprocess_exec(
                self.player_cmd, audio_file,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            
            # 等待播放完成或被打断
            while True:
                # 检查是否被打断
                if self._interrupt_event.is_set():
                    proc.terminate()
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=1.0)
                    except asyncio.TimeoutError:
                        proc.kill()
                    logger.debug("播放被打断")
                    return
                
                # 检查播放是否结束
                if proc.returncode is not None:
                    break
                
                await asyncio.sleep(0.05)  # 50ms 检查一次
            
            if proc.returncode == 0:
                logger.debug("播放完成")
            else:
                logger.warning(f"播放异常退出: {proc.returncode}")
                
        except Exception as e:
            logger.error(f"播放音频失败: {e}")


def detect_player() -> str:
    """检测可用的播放器"""
    import shutil
    
    players = ["afplay", "mpg123", "mpg321", "cvlc", "ffplay"]
    
    for player in players:
        if shutil.which(player):
            logger.info(f"检测到播放器: {player}")
            return player
    
    # 默认返回 afplay（macOS）
    logger.warning("未检测到播放器，默认使用 afplay")
    return "afplay"
