"""
阿里云 NLS 实时流式语音识别
文档：https://help.aliyun.com/document_detail/84428.html
"""
import json
import logging
import threading
import queue
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
        
    def _on_sentence_begin(self, message, *args):
        """一句话开始"""
        logger.debug(f"ASR 句子开始")
        
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
            
            if result:
                with self._lock:
                    self._results.append(result)
                
                if self._result_callback:
                    self._result_callback(ASRResult(
                        text=result,
                        is_final=False,
                        confidence=confidence
                    ))
                logger.debug(f"ASR 结果: {result}")
                
        except Exception as e:
            logger.error(f"处理 ASR 结果失败: {e}")
    
    def _on_completed(self, message, *args):
        """识别完成"""
        logger.debug("ASR 识别完成")
        self._closed.set()
        
    def _on_error(self, message, *args):
        """识别错误"""
        logger.error(f"ASR 错误: {message}")
        self._closed.set()
        
    def _on_close(self, *args):
        """连接关闭"""
        logger.debug("ASR 连接关闭")
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
            
            self.transcriber.start(
                aformat="pcm",
                sample_rate=16000,
                enable_intermediate_result=False,
                enable_punctuation_prediction=True,
                enable_inverse_text_normalization=True,
            )
            
            self._connected.set()
            logger.info("ASR 实时识别已启动")
            
        except Exception as e:
            logger.error(f"启动 ASR 失败: {e}")
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
            except Exception as e:
                logger.error(f"发送音频数据失败: {e}")
    
    def stop(self, timeout: float = 5.0) -> str:
        """
        停止识别，返回完整结果
        
        Args:
            timeout: 等待完成的超时时间
            
        Returns:
            完整的识别文本
        """
        if self.transcriber:
            try:
                self.transcriber.stop()
                # 等待关闭事件
                self._closed.wait(timeout=timeout)
            except Exception as e:
                logger.error(f"停止 ASR 失败: {e}")
            finally:
                self.transcriber = None
                self._connected.clear()
        
        with self._lock:
            final_text = ''.join(self._results)
        
        logger.info(f"ASR 最终识别结果: {final_text}")
        return final_text
    
    def is_active(self) -> bool:
        """检查是否处于识别状态"""
        return self._connected.is_set()


def create_asr(app_key: str, token: str) -> AliyunStreamASR:
    """创建 ASR 实例的工厂函数"""
    return AliyunStreamASR(app_key=app_key, token=token)


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
