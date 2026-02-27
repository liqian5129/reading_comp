"""
豆包 TTS 模块 (火山引擎)
使用豆包大模型语音合成 API
文档: https://www.volcengine.com/docs/6561/1257584
"""
import asyncio
import gzip
import json
import logging
import subprocess
import uuid
import time
from typing import Optional, List
from dataclasses import dataclass
import os

import websockets

logger = logging.getLogger(__name__)


@dataclass
class TTSRequest:
    """TTS 请求"""
    text: str
    voice_type: str = "BV001_streaming"
    emotion: str = "happy"
    speed_ratio: float = 1.0
    volume_ratio: float = 1.0
    pitch_ratio: float = 1.0
    interrupt: bool = False


class DoubaoTTS:
    """
    豆包 TTS 引擎
    使用火山引擎 WebSocket API 进行流式语音合成
    """
    
    # 火山引擎 TTS WebSocket 地址
    WS_URL = "wss://openspeech.bytedance.com/api/v1/tts/ws_binary"
    
    def __init__(self, 
                 appid: str, 
                 token: str, 
                 cluster: str = "volcano_tts",
                 voice_type: str = "BV001_streaming",
                 emotion: str = "happy",
                 speed_ratio: float = 1.0,
                 volume_ratio: float = 1.0,
                 pitch_ratio: float = 1.0):
        """
        Args:
            appid: 应用 ID
            token: Access Token (从火山引擎控制台获取)
            cluster: 集群 ID
            voice_type: 声音类型
            emotion: 情感类型
            speed_ratio: 语速倍率 0.8-1.2
            volume_ratio: 音量倍率 0.1-3.0
            pitch_ratio: 音调倍率 0.1-3.0
        """
        self.appid = appid
        self.token = token
        self.cluster = cluster
        self.voice_type = voice_type
        self.emotion = emotion
        self.speed_ratio = speed_ratio
        self.volume_ratio = volume_ratio
        self.pitch_ratio = pitch_ratio
        
    def _construct_request(self, text: str, reqid: str) -> bytes:
        """
        构建 TTS 请求
        
        Args:
            text: 要合成的文本
            reqid: 请求 ID
            
        Returns:
            gzip 压缩后的请求数据
        """
        payload = {
            "app": {
                "appid": self.appid,
                "token": self.token,
                "cluster": self.cluster
            },
            "user": {
                "uid": "reading_comp_user"
            },
            "audio": {
                "voice_type": self.voice_type,
                "encoding": "mp3",
                "speed_ratio": self.speed_ratio,
                "volume_ratio": self.volume_ratio,
                "pitch_ratio": self.pitch_ratio,
                "emotion": self.emotion
            },
            "request": {
                "reqid": reqid,
                "text": text,
                "text_type": "plain",
                "operation": "submit"
            }
        }
        
        # 压缩 payload
        payload_bytes = json.dumps(payload).encode('utf-8')
        compressed = gzip.compress(payload_bytes)

        # 火山引擎 TTS 二进制协议 header (4 bytes):
        #   Byte 0: version=1 (高4位) | header_size=1 (低4位, 单位4字节, 即4字节头)
        #   Byte 1: msg_type=1 (高4位, full client request) | flags=0 (低4位)
        #   Byte 2: serial=1 (高4位, JSON) | compression=1 (低4位, gzip)
        #   Byte 3: reserved=0
        header = bytes([0x11, 0x10, 0x11, 0x00])
        # payload size (4 bytes, big endian)
        size = len(compressed).to_bytes(4, 'big')

        return header + size + compressed
    
    async def synthesize(self, text: str) -> Optional[bytes]:
        """
        流式合成语音
        
        Args:
            text: 要合成的文本
            
        Returns:
            MP3 音频数据
        """
        if not text.strip():
            return None
        
        reqid = str(uuid.uuid4())
        audio_chunks = []
        
        try:
            logger.debug(f"🎵 豆包 TTS 开始合成: {text[:50]}...")
            
            auth_headers = {"Authorization": f"Bearer; {self.token}"}
            async with websockets.connect(self.WS_URL, additional_headers=auth_headers) as ws:
                # 发送合成请求
                request_data = self._construct_request(text, reqid)
                await ws.send(request_data)
                
                # 接收音频数据
                while True:
                    try:
                        # 设置接收超时
                        response = await asyncio.wait_for(ws.recv(), timeout=30.0)
                        
                        if isinstance(response, bytes):
                            # 解析二进制协议 header (4 bytes)
                            if len(response) < 4:
                                continue

                            header = response[:4]
                            header_size = (header[0] & 0x0f) * 4  # 低4位 * 4 = 实际header字节数
                            msg_type = (header[1] >> 4) & 0x0f

                            payload_start = header_size  # 通常为 4

                            if msg_type == 0xb:
                                # Audio-only response: 4字节序列号 + 4字节size + 音频数据
                                if len(response) < payload_start + 8:
                                    continue
                                seq_num = int.from_bytes(
                                    response[payload_start:payload_start + 4],
                                    'big', signed=True
                                )
                                audio_size = int.from_bytes(
                                    response[payload_start + 4:payload_start + 8], 'big'
                                )
                                audio_data = response[payload_start + 8:payload_start + 8 + audio_size]
                                if audio_data:
                                    audio_chunks.append(audio_data)
                                    logger.debug(f"🎵 收到音频数据: {len(audio_data)} bytes")
                                # 负序列号表示最后一包
                                if seq_num < 0:
                                    logger.debug("✅ 豆包 TTS 合成完成")
                                    break

                            elif msg_type == 0x9:
                                # Full server response: 4字节序列号 + 4字节size + JSON payload
                                if len(response) < payload_start + 8:
                                    continue
                                payload_size = int.from_bytes(
                                    response[payload_start + 4:payload_start + 8], 'big'
                                )
                                payload_data = response[payload_start + 8:payload_start + 8 + payload_size]
                                compression = header[2] & 0x0f
                                if compression == 1:
                                    payload_data = gzip.decompress(payload_data)
                                result = json.loads(payload_data.decode('utf-8'))
                                code = result.get('code', -1)
                                if code == 1000:
                                    logger.debug("✅ 豆包 TTS 合成完成")
                                    break
                                else:
                                    logger.error(f"❌ 豆包 TTS 错误: {code} - {result.get('message', '')}")
                                    return None

                            elif msg_type == 0xf:
                                # Error response
                                if len(response) < payload_start + 8:
                                    continue
                                error_code = int.from_bytes(
                                    response[payload_start:payload_start + 4], 'big'
                                )
                                payload_size = int.from_bytes(
                                    response[payload_start + 4:payload_start + 8], 'big'
                                )
                                payload_data = response[payload_start + 8:payload_start + 8 + payload_size]
                                # 尝试解压 gzip
                                if payload_data[:2] == b'\x1f\x8b':
                                    try:
                                        payload_data = gzip.decompress(payload_data)
                                    except Exception:
                                        pass
                                try:
                                    error_info = json.loads(payload_data.decode('utf-8'))
                                    logger.error(f"❌ 豆包 TTS 错误: {error_code} - {error_info}")
                                except Exception:
                                    logger.error(f"❌ 豆包 TTS 错误: {error_code} - {payload_data}")
                                return None
                        
                    except asyncio.TimeoutError:
                        logger.warning("⚠️ 豆包 TTS 接收超时")
                        break
            
            # 合并所有音频数据
            if audio_chunks:
                full_audio = b''.join(audio_chunks)
                logger.info(f"✅ 豆包 TTS 合成成功: {len(full_audio)} bytes")
                return full_audio
            else:
                logger.error("❌ 豆包 TTS 未收到音频数据")
                return None
                
        except Exception as e:
            logger.error(f"❌ 豆包 TTS 请求失败: {e}")
            return None


class DoubaoTTSPlayer:
    """
    豆包 TTS 播放器
    
    特点：
    - 异步队列，串流播放
    - 支持打断
    - 长文本自动分段
    """
    
    # 豆包 TTS 单次最大字符数（官方限制约 300，留余量）
    MAX_TEXT_LENGTH = 250

    def __init__(self,
                 appid: str,
                 token: str,
                 cluster: str = "volcano_tts",
                 voice_type: str = "BV001_streaming",
                 emotion: str = "happy",
                 speed_ratio: float = 1.0,
                 volume_ratio: float = 1.0,
                 pitch_ratio: float = 1.0,
                 player_cmd: str = "afplay",
                 max_queue_size: int = 10):
        """
        Args:
            appid: 应用 ID
            token: Access Token
            cluster: 集群 ID
            voice_type: 声音类型
            emotion: 情感
            speed_ratio: 语速
            volume_ratio: 音量
            pitch_ratio: 音调
            player_cmd: 播放器命令
            max_queue_size: 队列大小
        """
        self.tts = DoubaoTTS(
            appid=appid,
            token=token,
            cluster=cluster,
            voice_type=voice_type,
            emotion=emotion,
            speed_ratio=speed_ratio,
            volume_ratio=volume_ratio,
            pitch_ratio=pitch_ratio
        )
        self.player_cmd = player_cmd
        self.max_queue_size = max_queue_size
        
        # 队列和状态
        self._queue: asyncio.Queue[TTSRequest] = asyncio.Queue(maxsize=max_queue_size)
        self._playing = False
        self._interrupt_event = asyncio.Event()
        # 合成完成信号（用于外部精确计时）
        self._synthesis_done = asyncio.Event()
        self.last_synthesis_ms: float = 0.0

        # 临时文件目录
        import tempfile
        self._temp_dir = tempfile.mkdtemp(prefix="reading_comp_doubao_")
        
        # 任务
        self._worker_task: Optional[asyncio.Task] = None
        self._running = False
        
    @staticmethod
    def _clean_markdown(text: str) -> str:
        """去除 Markdown 格式，使 TTS 只读纯文本"""
        import re
        # 去掉粗体/斜体标记 **text** / *text*
        text = re.sub(r'\*{1,3}(.*?)\*{1,3}', r'\1', text)
        # 去掉标题 # ## ###
        text = re.sub(r'^#{1,6}\s*', '', text, flags=re.MULTILINE)
        # 去掉列表符号 - / * / 数字. 开头
        text = re.sub(r'^\s*[-*]\s+', '', text, flags=re.MULTILINE)
        text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)
        # 去掉行内代码 `code`
        text = re.sub(r'`[^`]*`', '', text)
        # 合并多个空行为单个换行
        text = re.sub(r'\n{2,}', '\n', text)
        # 去掉行首行尾空白
        text = '\n'.join(line.strip() for line in text.splitlines())
        return text.strip()

    def _split_text(self, text: str, max_length: int = MAX_TEXT_LENGTH) -> List[str]:
        """去 Markdown 后分段"""
        import re
        text = self._clean_markdown(text)

        if len(text) <= max_length:
            return [text] if text else []

        segments = []
        current = ""

        # 按中文句子边界分割
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
        logger.info("豆包 TTS 播放器已启动")
        
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
        logger.info("豆包 TTS 播放器已停止")
    
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

        # 重置合成完成信号
        self._synthesis_done.clear()

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
                    voice_type=self.tts.voice_type,
                    emotion=self.tts.emotion,
                    speed_ratio=self.tts.speed_ratio,
                    volume_ratio=self.tts.volume_ratio,
                    pitch_ratio=self.tts.pitch_ratio,
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

    async def wait_synthesized(self, timeout: float = 30.0) -> float:
        """等待第一段 TTS 合成完成（不等待播放），返回合成耗时 ms"""
        try:
            await asyncio.wait_for(self._synthesis_done.wait(), timeout=timeout)
            return self.last_synthesis_ms
        except asyncio.TimeoutError:
            return 0.0

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

            # 通知外部合成已完成（用于精确计时）
            self.last_synthesis_ms = synth_time
            self._synthesis_done.set()


            if audio_data is None:
                logger.error("TTS 合成失败")
                return
            
            if self._interrupt_event.is_set():
                logger.debug("TTS 被打断，跳过播放")
                return
            
            logger.info(f"🔊 豆包 TTS 合成完成: {synth_time:.0f} ms, {len(audio_data)} bytes")
            
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
