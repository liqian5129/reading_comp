"""
ElevenLabs TTS 模块
使用 ElevenLabs API 进行语音合成
"""
import asyncio
import logging
import os
import subprocess
import tempfile
from typing import Optional, List
from dataclasses import dataclass

import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class TTSRequest:
    """TTS 请求"""
    text: str
    voice_id: str = "pNInz6obpgDQGcFmaJgB"
    model: str = "eleven_multilingual_v2"
    interrupt: bool = False


class ElevenLabsTTS:
    """
    ElevenLabs TTS 引擎
    文档: https://elevenlabs.io/docs/api-reference/text-to-speech
    """
    
    API_URL = "https://api.elevenlabs.io/v1/text-to-speech"
    
    def __init__(self, api_key: str, voice_id: str, model: str):
        self.api_key = api_key
        self.voice_id = voice_id
        self.model = model
        self.headers = {
            "Accept": "audio/mpeg",
            "Content-Type": "application/json",
            "xi-api-key": api_key,
        }
        
    async def synthesize(self, text: str) -> Optional[bytes]:
        """
        合成语音
        
        Args:
            text: 要合成的文本
            
        Returns:
            MP3 音频数据
        """
        url = f"{self.API_URL}/{self.voice_id}"
        
        payload = {
            "text": text,
            "model_id": self.model,
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75,
            }
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    headers=self.headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=60)
                ) as resp:
                    if resp.status == 200:
                        audio_data = await resp.read()
                        logger.debug(f"ElevenLabs TTS 合成成功: {len(audio_data)} bytes")
                        return audio_data
                    else:
                        error_text = await resp.text()
                        logger.error(f"ElevenLabs TTS 失败: {resp.status}, {error_text}")
                        return None
        except Exception as e:
            logger.error(f"ElevenLabs TTS 请求失败: {e}")
            return None


class ElevenLabsTTSPlayer:
    """
    ElevenLabs TTS 播放器
    
    特点：
    - 异步队列，串流播放
    - 支持打断
    - 长文本自动分段（每段约 500 字符）
    """
    
    # ElevenLabs 限制每段约 5000 字符，留些余量
    MAX_TEXT_LENGTH = 3000
    
    def __init__(self, 
                 api_key: str,
                 voice_id: str = "pNInz6obpgDQGcFmaJgB",
                 model: str = "eleven_multilingual_v2",
                 player_cmd: str = "afplay",
                 max_queue_size: int = 10):
        """
        Args:
            api_key: ElevenLabs API Key
            voice_id: 声音 ID
            model: 模型名称
            player_cmd: 播放器命令
            max_queue_size: 播放队列最大长度
        """
        self.tts = ElevenLabsTTS(api_key, voice_id, model)
        self.player_cmd = player_cmd
        self.max_queue_size = max_queue_size
        
        # 队列和状态
        self._queue: asyncio.Queue[TTSRequest] = asyncio.Queue(maxsize=max_queue_size)
        self._playing = False
        self._interrupt_event = asyncio.Event()
        
        # 临时文件目录
        self._temp_dir = tempfile.mkdtemp(prefix="reading_comp_elevenlabs_")
        
        # 任务
        self._worker_task: Optional[asyncio.Task] = None
        self._running = False
        
    def _split_text(self, text: str, max_length: int = MAX_TEXT_LENGTH) -> List[str]:
        """将长文本分段"""
        if len(text) <= max_length:
            return [text]
        
        segments = []
        current = ""
        
        # 按句子分割
        import re
        sentences = re.split(r'([。！？；\n])', text)
        
        for i in range(0, len(sentences), 2):
            sentence = sentences[i]
            if i + 1 < len(sentences):
                sentence += sentences[i + 1]
            
            if len(current) + len(sentence) <= max_length:
                current += sentence
            else:
                if current:
                    segments.append(current)
                current = sentence
        
        if current:
            segments.append(current)
        
        # 强制分割超长段落
        final_segments = []
        for seg in segments:
            while len(seg) > max_length:
                final_segments.append(seg[:max_length])
                seg = seg[max_length:]
            if seg:
                final_segments.append(seg)
        
        return final_segments if final_segments else [text[:max_length]]
        
    async def start(self):
        """启动播放器"""
        self._running = True
        self._worker_task = asyncio.create_task(self._play_worker())
        logger.info("ElevenLabs TTS 播放器已启动")
        
    async def stop(self):
        """停止播放器"""
        self._running = False
        self.interrupt()
        
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        
        self._cleanup_temp_files()
        logger.info("ElevenLabs TTS 播放器已停止")
    
    def _cleanup_temp_files(self):
        """清理临时文件"""
        try:
            for f in os.listdir(self._temp_dir):
                try:
                    os.remove(os.path.join(self._temp_dir, f))
                except:
                    pass
            os.rmdir(self._temp_dir)
        except Exception as e:
            logger.warning(f"清理临时文件失败: {e}")
    
    async def speak(self, text: str, interrupt: bool = False) -> bool:
        """
        播放文本
        
        Args:
            text: 要播放的文本
            interrupt: 是否打断当前播放
            
        Returns:
            是否成功加入队列
        """
        if not text.strip():
            return False
        
        # 长文本分段
        segments = self._split_text(text.strip())
        
        try:
            if interrupt:
                self.interrupt()
                while not self._queue.empty():
                    try:
                        self._queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
            
            # 将分段加入队列
            for i, segment in enumerate(segments):
                request = TTSRequest(
                    text=segment,
                    voice_id=self.tts.voice_id,
                    model=self.tts.model,
                    interrupt=(interrupt and i == 0)
                )
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
                request = await asyncio.wait_for(
                    self._queue.get(), 
                    timeout=1.0
                )
            except asyncio.TimeoutError:
                continue
            
            self._interrupt_event.clear()
            await self._synthesize_and_play(request)
    
    async def _synthesize_and_play(self, request: TTSRequest):
        """合成并播放"""
        try:
            self._playing = True
            
            # 合成语音
            import time
            synth_start = time.time()
            audio_data = await self.tts.synthesize(request.text)
            synth_time = (time.time() - synth_start) * 1000
            
            if audio_data:
                logger.info(f"🔊 ElevenLabs TTS 合成完成: {synth_time:.0f} ms, {len(audio_data)} bytes")
            
            if audio_data is None:
                logger.error("TTS 合成失败")
                return
            
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
            
            # 清理
            try:
                os.remove(temp_file)
            except:
                pass
                
        finally:
            self._playing = False
    
    async def _play_audio(self, audio_file: str):
        """播放音频文件"""
        try:
            proc = await asyncio.create_subprocess_exec(
                self.player_cmd, audio_file,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            
            while True:
                if self._interrupt_event.is_set():
                    proc.terminate()
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=1.0)
                    except asyncio.TimeoutError:
                        proc.kill()
                    logger.debug("播放被打断")
                    return
                
                if proc.returncode is not None:
                    break
                
                await asyncio.sleep(0.05)
            
            if proc.returncode == 0:
                logger.debug("播放完成")
            else:
                logger.warning(f"播放异常退出: {proc.returncode}")
                
        except Exception as e:
            logger.error(f"播放音频失败: {e}")
