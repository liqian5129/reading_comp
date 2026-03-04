"""
Embedding 服务
封装阿里云百炼 DashScope embedding API，带超时和弹性降级
"""
import asyncio
import logging
from typing import List, Optional

import openai

logger = logging.getLogger(__name__)


class Embedder:
    """
    Embedding API 封装。
    所有异常 → 返回 None，调用方负责降级，不中断主流程。
    """

    def __init__(
        self,
        api_key: str,
        model: str = "text-embedding-v4",
        base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
        timeout_s: float = 5.0,
        enabled: bool = True,
    ):
        self.model = model
        self.timeout_s = timeout_s
        self.enabled = enabled

        if enabled:
            self._client = openai.AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=timeout_s + 2,  # httpx timeout > asyncio.wait_for timeout
            )
        else:
            self._client = None

        logger.info(
            f"Embedder 初始化: model={model}, enabled={enabled}, timeout={timeout_s}s"
        )

    async def embed(self, text: str) -> Optional[List[float]]:
        """
        生成单条文本的 embedding 向量。
        失败（超时/API 错误）时返回 None。
        """
        if not self.enabled or not self._client or not text.strip():
            return None

        try:
            resp = await asyncio.wait_for(
                self._client.embeddings.create(
                    model=self.model,
                    input=text[:6000],  # text-embedding-v3 支持 8192 tokens
                ),
                timeout=self.timeout_s,
            )
            return resp.data[0].embedding
        except asyncio.TimeoutError:
            logger.warning(f"embed 超时 ({self.timeout_s}s)，已降级跳过")
            return None
        except Exception as e:
            logger.warning(f"embed 失败: {e}")
            return None

    async def embed_batch(self, texts: List[str]) -> List[Optional[List[float]]]:
        """批量 embed，每条独立调用，失败返回 None（不影响其他条）"""
        results = []
        for text in texts:
            results.append(await self.embed(text))
        return results
