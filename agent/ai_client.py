"""
AI 客户端
支持 Kimi 和豆包双模型
"""
import base64
import json
import logging
import time
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from pathlib import Path

import openai
import httpx

logger = logging.getLogger(__name__)


@dataclass
class LLMResponse:
    """LLM 响应"""
    text: str
    tool_calls: List[Dict[str, Any]]
    stop_reason: str
    raw_assistant_message: Optional[Dict] = None  # 含 tool_calls 的原始 assistant 消息


class AIClient:
    """
    AI 客户端
    支持 Kimi (Moonshot) 和豆包 (Volces/字节)
    """
    
    def __init__(self, 
                 provider: str = "kimi",
                 api_key: str = "",
                 model: str = "",
                 base_url: str = "",
                 enable_thinking: bool = False):
        """
        Args:
            provider: 提供商 - "kimi" 或 "doubao"
            api_key: API 密钥
            model: 模型名称
            base_url: API 基础 URL
            enable_thinking: 是否启用思考模式（仅 Kimi K2.5 有效）
        """
        self.provider = provider.lower()
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.enable_thinking = enable_thinking
        
        # 创建 OpenAI 客户端，添加详细的 HTTP 日志
        self.client = openai.AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=60.0,
        )
        
        logger.info(f"🤖 AI 客户端初始化: {provider} / {model}")
        
        if self.provider == "kimi" and "k2" in model and not enable_thinking:
            logger.info("🚀 Kimi K2.5 已关闭 thinking 模式")
    
    def _get_temperature(self) -> float:
        """获取合适的 temperature"""
        if self.provider == "kimi":
            # Kimi K2.5 关闭 thinking 时必须用 0.6
            if "k2" in self.model and not self.enable_thinking:
                return 0.6
            return 1.0
        else:  # doubao
            return 0.7
    
    def _get_extra_body(self) -> Optional[Dict]:
        """获取额外的请求体参数"""
        # Kimi K2.5 关闭 thinking
        if self.provider == "kimi" and "k2" in self.model and not self.enable_thinking:
            return {"thinking": {"type": "disabled"}}
        return None
    
    def _encode_image(self, image_path: str) -> Optional[str]:
        """将图片转为 base64"""
        try:
            path = Path(image_path)
            if not path.exists():
                return None
            
            with open(path, "rb") as f:
                image_data = f.read()
            
            ext = path.suffix.lower()
            media_type = {
                '.jpg': 'image/jpeg',
                '.jpeg': 'image/jpeg',
                '.png': 'image/png',
                '.gif': 'image/gif',
                '.webp': 'image/webp',
            }.get(ext, 'image/jpeg')
            
            return f"data:{media_type};base64,{base64.b64encode(image_data).decode()}"
        except Exception as e:
            logger.error(f"图片编码失败: {e}")
            return None
    
    def _build_messages(self, 
                       system_prompt: str,
                       history: List[Dict[str, str]], 
                       user_message: str,
                       image_path: Optional[str] = None) -> List[Dict]:
        """构建消息列表"""
        messages = []
        
        if system_prompt:
            messages.append({
                "role": "system",
                "content": system_prompt
            })
        
        # 添加历史消息
        for msg in history[-20:]:
            messages.append(msg)
        
        # 添加当前用户消息
        content = []
        
        if image_path:
            image_data = self._encode_image(image_path)
            if image_data:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": image_data}
                })
        
        if isinstance(content, list):
            content.append({"type": "text", "text": user_message})
            messages.append({"role": "user", "content": content})
        else:
            messages.append({"role": "user", "content": user_message})
        
        return messages
    
    def _convert_tools(self, tools: List[Dict]) -> List[Dict]:
        """转换工具格式为 OpenAI 格式"""
        converted = []
        for tool in tools:
            converted.append({
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": tool.get("input_schema", {})
                }
            })
        return converted
    
    async def chat(self,
                   user_message: str,
                   system_prompt: str = "",
                   history: List[Dict[str, str]] = None,
                   image_path: Optional[str] = None,
                   tools: List[Dict] = None,
                   max_tokens: int = 4096) -> LLMResponse:
        """与 AI 对话 - 带详细计时"""
        if history is None:
            history = []
        
        messages = self._build_messages(
            system_prompt, history, user_message, image_path
        )
        
        # 计算请求大小
        request_json = json.dumps({"messages": messages, "model": self.model})
        request_size_kb = len(request_json.encode('utf-8')) / 1024
        
        # 开始计时
        total_start = time.time()
        ttfb_start = None
        ttfb_end = None
        
        try:
            kwargs = {
                "model": self.model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": self._get_temperature(),
            }
            
            extra_body = self._get_extra_body()
            if extra_body:
                kwargs["extra_body"] = extra_body
            
            if tools:
                kwargs["tools"] = self._convert_tools(tools)
                kwargs["tool_choice"] = "auto"
            
            logger.info("=" * 60)
            logger.info(f"📤 AI 请求开始")
            logger.info(f"   模型: {self.model}")
            logger.info(f"   消息数: {len(messages)}")
            logger.info(f"   请求大小: {request_size_kb:.2f} KB")
            logger.info("-" * 60)
            
            # 发送请求并计时
            ttfb_start = time.time()
            
            response = await self.client.chat.completions.create(**kwargs)
            
            # 首字节到达时间
            ttfb_end = time.time()
            ttfb_ms = (ttfb_end - ttfb_start) * 1000
            
            # 完整响应时间
            total_end = time.time()
            total_ms = (total_end - total_start) * 1000
            
            # 解析响应
            message = response.choices[0].message
            text = message.content or ""
            
            # 响应大小估算
            response_json = json.dumps({"content": text}, ensure_ascii=False)
            response_size_kb = len(response_json.encode('utf-8')) / 1024
            
            # 提取工具调用
            tool_calls = []
            raw_assistant_message = None
            if message.tool_calls:
                for tc in message.tool_calls:
                    tool_calls.append({
                        "id": tc.id,
                        "name": tc.function.name,
                        "input": json.loads(tc.function.arguments)
                    })
                # 保存原始 assistant 消息（API 要求 tool 消息前必须有此消息）
                raw_assistant_message = {
                    "role": "assistant",
                    "content": message.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in message.tool_calls
                    ],
                }

            stop_reason = response.choices[0].finish_reason
            if tool_calls:
                stop_reason = "tool_use"
            
            # 计算各阶段时间
            server_process_ms = total_ms - ttfb_ms  # 服务器处理 + 网络传输
            
            logger.info(f"📥 AI 响应完成")
            logger.info(f"   TTFB (首字节时间): {ttfb_ms:.0f} ms")
            logger.info(f"   总耗时: {total_ms:.0f} ms")
            logger.info(f"   服务器处理+传输: {server_process_ms:.0f} ms")
            logger.info(f"   响应大小: {response_size_kb:.2f} KB")
            logger.info(f"   生成 tokens: ~{len(text)} 字符")
            logger.info(f"   停止原因: {stop_reason}")
            logger.info("=" * 60)
            
            return LLMResponse(
                text=text,
                tool_calls=tool_calls,
                stop_reason=stop_reason,
                raw_assistant_message=raw_assistant_message,
            )
            
        except Exception as e:
            total_end = time.time()
            total_ms = (total_end - total_start) * 1000
            
            if ttfb_start and not ttfb_end:
                # 请求发出但没有收到响应
                logger.error(f"❌ AI 请求超时或失败 (已等待 {total_ms:.0f} ms)")
            else:
                logger.error(f"❌ AI API 调用失败: {e}")
            
            return LLMResponse(
                text=f"抱歉，我遇到了一些问题: {str(e)}",
                tool_calls=[],
                stop_reason="error"
            )
    
    async def chat_with_tool_result(self,
                                    user_message: str,
                                    tool_results: List[Dict],
                                    system_prompt: str = "",
                                    history: List[Dict[str, str]] = None,
                                    assistant_message: Optional[Dict] = None,
                                    max_tokens: int = 4096) -> LLMResponse:
        """发送工具执行结果，继续对话 - 带计时"""
        if history is None:
            history = []

        messages = []

        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        messages.extend(history[-20:])

        # 当前用户消息
        messages.append({"role": "user", "content": user_message})

        # assistant 原始消息（含 tool_calls），API 强制要求在 tool 消息之前
        if assistant_message:
            messages.append(assistant_message)

        for result in tool_results:
            messages.append({
                "role": "tool",
                "tool_call_id": result["tool_use_id"],
                "content": result["content"]
            })
        
        total_start = time.time()
        
        try:
            kwargs = {
                "model": self.model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": self._get_temperature(),
            }
            
            extra_body = self._get_extra_body()
            if extra_body:
                kwargs["extra_body"] = extra_body
            
            response = await self.client.chat.completions.create(**kwargs)
            
            total_end = time.time()
            total_ms = (total_end - total_start) * 1000
            
            message = response.choices[0].message
            text = message.content or ""
            
            logger.info(f"🛠️ 工具结果处理完成: {total_ms:.0f} ms")
            
            return LLMResponse(
                text=text,
                tool_calls=[],
                stop_reason=response.choices[0].finish_reason
            )
            
        except Exception as e:
            logger.error(f"AI API 调用失败: {e}")
            return LLMResponse(
                text=f"抱歉，我遇到了一些问题: {str(e)}",
                tool_calls=[],
                stop_reason="error"
            )
