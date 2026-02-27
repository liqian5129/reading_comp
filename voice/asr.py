"""
阿里云 NLS 实时流式语音识别
文档：https://help.aliyun.com/document_detail/84428.html
"""
import json
import logging
import threading
import queue
import time
from typing import Callable, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# 尝试导入 nls
try:
    import nls
except ImportError:
    logger.warning("nls 模块未安装，ASR 功能不可用")
    nls = None


@dataclass
class ASRResult:
    """ASR 结果"""
    text: str
    is_final: bool
    confidence: float = 1.0


class AliyunStreamASR:
    """
    阿里云 NLS 实时语音识别（流式）
    边录边传，低延迟
    """
    
    def __init__(self, app_key: str, token: str, 
                 url: str = "wss://nls-gateway-cn-shanghai.aliyuncs.com/ws/v1"):
        self.app_key = app_key
        self.token = token
        self.url = url
        
        self.transcriber = None
        self._result_callback: Optional[Callable[[ASRResult], None]] = None
        
        # 状态
        self._connected = threading.Event()
        self._closed = threading.Event()
        self._results: list[str] = []
        self._lock = threading.Lock()
        
        # 调试统计
        self._audio_bytes_sent = 0
        self._audio_chunks_sent = 0
        
    def _on_sentence_begin(self, message, *args):
        """一句话开始"""
        logger.info(f"🎤 ASR: 句子开始")
        
    def _on_sentence_end(self, message, *args):
        """一句话结束（有结果）"""
        try:
            if isinstance(message, str):
                msg = json.loads(message)
            else:
                msg = message
            
            payload = msg.get('payload', {})
            result = payload.get('result', '')
            confidence = payload.get('confidence', 1.0)
            
            logger.info(f"📝 ASR 识别到: {result} (置信度: {confidence})")
            
            if result:
                with self._lock:
                    self._results.append(result)
                
                if self._result_callback:
                    self._result_callback(ASRResult(
                        text=result,
                        is_final=False,
                        confidence=confidence
                    ))
                
        except Exception as e:
            logger.error(f"处理 ASR 结果失败: {e}")
    
    def _on_completed(self, message, *args):
        """识别完成"""
        logger.info(f"✅ ASR 识别完成: {message}")
        self._closed.set()
        
    def _on_error(self, message, *args):
        """识别错误"""
        logger.error(f"❌ ASR 错误: {message}")
        self._closed.set()
        
    def _on_close(self, *args):
        """连接关闭"""
        logger.info("🔌 ASR 连接关闭")
        self._closed.set()
        self._connected.clear()

    def start(self, on_result: Optional[Callable[[ASRResult], None]] = None):
        """
        启动实时识别
        
        Args:
            on_result: 结果回调函数，接收 ASRResult
        """
        if nls is None:
            raise RuntimeError("nls 模块未安装，请运行: pip install nls-python-sdk")
        
        self._result_callback = on_result
        self._results = []
        self._connected.clear()
        self._closed.clear()
        self._audio_bytes_sent = 0
        self._audio_chunks_sent = 0
        
        logger.info(f"🔑 使用 AppKey: {self.app_key[:8]}... Token: {self.token[:8]}...")
        
        try:
            # 使用更简单的参数配置
            self.transcriber = nls.NlsSpeechTranscriber(
                url=self.url,
                token=self.token,
                appkey=self.app_key,
                on_sentence_begin=self._on_sentence_begin,
                on_sentence_end=self._on_sentence_end,
                on_completed=self._on_completed,
                on_error=self._on_error,
                on_close=self._on_close,
            )
            
            logger.info("🚀 正在启动 ASR 连接...")
            
            self.transcriber.start(
                aformat="pcm",
                sample_rate=16000,
                enable_intermediate_result=False,
                enable_punctuation_prediction=True,
                enable_inverse_text_normalization=True,
            )
            
            self._connected.set()
            logger.info("✅ ASR 实时识别已启动")
            
        except Exception as e:
            logger.error(f"❌ 启动 ASR 失败: {e}")
            raise
        
    def send_audio(self, pcm_data: bytes):
        """
        发送音频数据（实时流式）
        
        Args:
            pcm_data: PCM 格式音频数据 (16kHz, 16bit, mono)
        """
        if self.transcriber and self._connected.is_set():
            try:
                self.transcriber.send_audio(pcm_data)
                self._audio_bytes_sent += len(pcm_data)
                self._audio_chunks_sent += 1
                
                # 每 50 个包打印一次统计
                if self._audio_chunks_sent % 50 == 0:
                    logger.info(f"📊 ASR: 已发送 {self._audio_chunks_sent} 包, {self._audio_bytes_sent} 字节")
                    
            except Exception as e:
                logger.error(f"发送音频数据失败: {e}")
        else:
            logger.debug(f"⚠️ ASR 未就绪，跳过音频发送 (connected={self._connected.is_set()})")
    
    def stop(self, timeout: float = 3.0) -> str:
        """
        停止识别，返回完整结果
        
        Args:
            timeout: 等待完成的超时时间（秒）
            
        Returns:
            完整的识别文本
        """
        logger.info(f"🛑 停止 ASR: 共发送 {self._audio_chunks_sent} 包, {self._audio_bytes_sent} 字节")
        
        if self.transcriber:
            # 在后台线程执行 stop，避免阻塞
            stop_result = {"done": False, "error": None}
            
            def do_stop():
                try:
                    logger.info("⏳ 正在调用 ASR stop()...")
                    self.transcriber.stop()
                    stop_result["done"] = True
                    logger.info("✅ ASR stop() 完成")
                except Exception as e:
                    stop_result["error"] = str(e)
                    logger.error(f"ASR stop 出错: {e}")
            
            # 启动后台线程执行 stop
            stop_thread = threading.Thread(target=do_stop, daemon=True)
            stop_thread.start()
            
            # 等待 stop 完成或超时
            stop_thread.join(timeout=timeout)
            
            if not stop_result["done"]:
                logger.warning(f"⚠️ ASR stop 超时（{timeout}s），强制结束")
            
            # 强制清理
            self.transcriber = None
            self._connected.clear()
            self._closed.set()
        
        with self._lock:
            final_text = ''.join(self._results)
        
        logger.info(f"📄 ASR 最终识别结果: '{final_text}' (共 {len(self._results)} 句)")
        return final_text
    
    def is_active(self) -> bool:
        """检查是否处于识别状态"""
        return self._connected.is_set()


def create_asr(app_key: str, token: str) -> 'PrewarmedASR':
    """创建预热式 ASR 实例，启动时立即开始建立首次连接"""
    asr = PrewarmedASR(app_key=app_key, token=token)
    asr.prepare()
    return asr


class _CallbackProxy:
    """
    线程安全的回调转发器。

    预热阶段将此对象注册到 AliyunStreamASR，
    录音开始时通过 set_target() 绑定真实回调，
    录音结束时 set_target(None) 切断转发。
    """

    def __init__(self):
        self._target = None
        self._lock = threading.Lock()

    def set_target(self, callback):
        with self._lock:
            self._target = callback

    def __call__(self, result):
        with self._lock:
            target = self._target
        if target:
            target(result)


# 预热连接的最长保活时间（秒）。
# 阿里云 NLS 空闲连接约 30s 超时，保守取 25s。
_STANDBY_MAX_AGE = 25


class PrewarmedASR:
    """
    预热式 ASR 管理器。

    start() 完全非阻塞：
    - standby 就绪 → 立即激活（~0ms）
    - standby 未就绪/已过期 → 后台等待激活，期间 send_audio() 自动缓冲

    这样键盘监听回调永远不会阻塞或抛出异常。
    """

    def __init__(self, app_key: str, token: str,
                 url: str = "wss://nls-gateway-cn-shanghai.aliyuncs.com/ws/v1"):
        self._app_key = app_key
        self._token = token
        self._url = url

        self._active: Optional[AliyunStreamASR] = None
        self._standby: Optional[AliyunStreamASR] = None
        self._standby_ready = threading.Event()
        self._standby_created_at: float = 0.0
        self._preparing = False
        self._pool_lock = threading.Lock()

        # 代理回调
        self._proxy = _CallbackProxy()

        # 非阻塞 start 支持
        self._stop_requested = False       # stop() 在后台激活完成前被调用
        self._pending_audio: list = []     # 激活前的音频缓冲
        self._pending_lock = threading.Lock()

        # 主动刷新定时器（standby 到期前 5s 自动重建）
        self._refresh_timer: Optional[threading.Timer] = None

    # ------------------------------------------------------------------
    # 预热
    # ------------------------------------------------------------------

    def prepare(self):
        """在后台建立下一条 ASR 连接（幂等）。"""
        with self._pool_lock:
            if self._preparing or self._standby_ready.is_set():
                return
            self._preparing = True

        threading.Thread(target=self._do_prepare, daemon=True).start()

    def _do_prepare(self):
        max_retries = 3
        retry_delay = 3.0

        for attempt in range(max_retries):
            try:
                logger.info("🔌 ASR 预热：正在建立备用连接...")
                asr = AliyunStreamASR(self._app_key, self._token, self._url)
                asr.start(on_result=self._proxy)

                # 等待连接就绪（_connected 被设置）或失败（_closed 被设置）
                # 最多等 5 秒，网络正常情况下应该很快
                ready = asr._connected.wait(timeout=5.0)
                if not ready:
                    # 连接未就绪，检查是否已失败
                    if asr._closed.is_set():
                        logger.warning(
                            f"⚠️ ASR 预热连接建立失败，{retry_delay:.0f}s 后重试"
                            f" ({attempt + 1}/{max_retries})..."
                        )
                    else:
                        logger.warning(
                            f"⚠️ ASR 预热连接超时，{retry_delay:.0f}s 后重试"
                            f" ({attempt + 1}/{max_retries})..."
                        )
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay)
                    continue

                # 连接已就绪，再等待一小段时间确认没有立即断开
                connection_failed = asr._closed.wait(timeout=0.5)
                if connection_failed:
                    logger.warning(
                        f"⚠️ ASR 预热连接建立后立即断开，{retry_delay:.0f}s 后重试"
                        f" ({attempt + 1}/{max_retries})..."
                    )
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay)
                    continue

                with self._pool_lock:
                    self._standby = asr
                    self._standby_created_at = time.time()
                    self._standby_ready.set()
                    self._preparing = False

                # 在到期前 5s 主动刷新，避免用户按键时 standby 已过期
                if self._refresh_timer:
                    self._refresh_timer.cancel()
                refresh_delay = max(_STANDBY_MAX_AGE - 5, 10)
                t = threading.Timer(refresh_delay, self._refresh_standby)
                t.daemon = True
                t.start()
                self._refresh_timer = t

                logger.info("✅ ASR 预热完成，下次按键可立即使用")
                return

            except Exception as e:
                logger.error(f"❌ ASR 预热失败: {e}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)

        logger.error("❌ ASR 预热彻底失败（已重试 3 次），请检查网络或 API 配额")
        with self._pool_lock:
            self._preparing = False
            self._standby_ready.clear()

    def _refresh_standby(self):
        """standby 即将到期时主动关闭并重建，保持随时可用。"""
        with self._pool_lock:
            # 如果正在准备中或已被激活，不要重复刷新
            if self._preparing:
                logger.debug("ASR 已在准备中，跳过刷新")
                return
            if self._standby is None or not self._standby_ready.is_set():
                return  # 已被激活或已在重建中
            old = self._standby
            self._standby = None
            self._standby_ready.clear()
            self._preparing = True  # 标记为准备中，防止并发
        
        logger.info("🔄 ASR standby 即将到期，主动刷新中...")
        
        # 先等待旧连接完全关闭
        def close_and_prepare():
            try:
                old.stop(timeout=3.0)
            except Exception as e:
                logger.warning(f"刷新时关闭旧连接出错: {e}")
            finally:
                with self._pool_lock:
                    self._preparing = False  # 重置状态
                self.prepare()
        
        threading.Thread(target=close_and_prepare, daemon=True).start()

    # ------------------------------------------------------------------
    # 录音接口
    # ------------------------------------------------------------------

    def start(self, on_result=None):
        """
        非阻塞激活：立即返回，不会阻塞键盘监听线程，不抛出异常。

        standby 就绪 → 立即激活
        standby 未就绪/已过期 → 启动后台线程等待激活，同期音频自动缓冲
        """
        with self._pending_lock:
            self._pending_audio.clear()
        self._stop_requested = False
        self._proxy.set_target(on_result)

        if self._standby_ready.is_set():
            age = time.time() - self._standby_created_at
            if age <= _STANDBY_MAX_AGE:
                # 立即激活，取消刷新定时器
                if self._refresh_timer:
                    self._refresh_timer.cancel()
                    self._refresh_timer = None
                with self._pool_lock:
                    self._active = self._standby
                    self._standby = None
                    self._standby_ready.clear()
                logger.info("⚡ ASR 预热连接已激活，可立即发送音频")
                return

        # standby 未就绪或已过期 → 后台等待
        logger.info("⏳ ASR standby 未就绪，后台等待激活中...")
        threading.Thread(target=self._background_activate, daemon=True).start()

    def _background_activate(self):
        """后台线程：等待 standby 就绪并激活；支持 stop() 提前取消。"""
        try:
            # 检查是否正在准备中
            with self._pool_lock:
                is_preparing = self._preparing
            
            # 若 standby 已过期或不存在，且不在准备中，则重新预热
            if not self._standby_ready.is_set() and not is_preparing:
                # 先关闭可能存在的旧连接
                with self._pool_lock:
                    old = self._standby
                    self._standby = None
                    self._standby_ready.clear()
                if old:
                    threading.Thread(
                        target=lambda: old.stop(timeout=3.0), daemon=True
                    ).start()
                self.prepare()
            elif is_preparing:
                logger.info("ASR 准备中，等待完成...")

            # 等待 standby 就绪（最多 20 秒）
            if not self._standby_ready.wait(timeout=20.0):
                logger.error("❌ ASR 后台激活超时（20s），本次录音无识别")
                # 重置准备状态，允许下次重试
                with self._pool_lock:
                    self._preparing = False
                return

            # stop() 已在激活完成前被调用 → 保留 standby 供下次使用，不激活
            if self._stop_requested:
                logger.info("ASR 后台激活：录音已提前结束，standby 保留供下次使用")
                return

            with self._pool_lock:
                if self._stop_requested:
                    return
                self._active = self._standby
                self._standby = None
                self._standby_ready.clear()

            logger.info("⚡ ASR 后台激活完成")

            # 冲送缓冲音频
            with self._pending_lock:
                buffered = self._pending_audio.copy()
                self._pending_audio.clear()

            if buffered and self._active:
                logger.info(f"📤 冲送缓冲音频: {len(buffered)} 包")
                for chunk in buffered:
                    self._active.send_audio(chunk)

        except Exception as e:
            logger.error(f"❌ ASR 后台激活失败: {e}")
            # 出错时重置准备状态
            with self._pool_lock:
                self._preparing = False

    def send_audio(self, pcm_data: bytes):
        if self._active:
            self._active.send_audio(pcm_data)
        else:
            # 后台激活中，缓冲音频（最多 5 秒 ≈ 78 包）
            with self._pending_lock:
                self._pending_audio.append(pcm_data)
                max_chunks = 5 * 16000 // 1024
                if len(self._pending_audio) > max_chunks:
                    self._pending_audio.pop(0)

    def stop(self, timeout: float = 5.0) -> str:
        """停止当前识别，并触发下一次预热。"""
        self._stop_requested = True

        if self._refresh_timer:
            self._refresh_timer.cancel()
            self._refresh_timer = None

        with self._pending_lock:
            self._pending_audio.clear()

        if not self._active:
            self.prepare()
            return ""

        self._proxy.set_target(None)
        result = self._active.stop(timeout=timeout)
        self._active = None
        self.prepare()
        return result

    def is_active(self) -> bool:
        return self._active is not None and self._active.is_active()


class MockASR:
    """
    模拟 ASR，用于测试
    """
    
    def __init__(self, mock_text: str = "这是一段测试文本"):
        self.mock_text = mock_text
        self._results = []
        
    def start(self, on_result=None):
        logger.info("Mock ASR 启动")
        
    def send_audio(self, pcm_data: bytes):
        pass
    
    def stop(self, timeout: float = 5.0) -> str:
        return self.mock_text
    
    def is_active(self) -> bool:
        return True
