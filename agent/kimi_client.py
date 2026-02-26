"""
Kimi AI 客户端
使用 Moonshot API (OpenAI 兼容格式)
支持视觉输入和工具调用
"""
import base64
import json
import logging
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from pathlib import Path

import openai

logger = logging.getLogger(__name__)


@dataclass
class LLMResponse:
    """LLM 响应"""
    text: str
    tool_calls: List[Dict[str, Any]]
    stop_reason: str


class KimiClient:
    """
    Kimi API 客户端 (Moonshot)
    文档: https://platform.moonshot.cn/docs/api-reference
    """
    
    def __init__(self, api_key: str, model: str = "kimi-k2.5", 
                 base_url: str = "https://api.moonshot.cn/v1"):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        
        # 创建 OpenAI 客户端
        self.client = openai.AsyncOpenAI(
            api_key=api_key,
            base_url=base_url
        )
        
    def _encode_image(self, image_path: str) -> Optional[str]:
        """将图片转为 base64"""
        try:
            path = Path(image_path)
            if not path.exists():
                return None
            
            with open(path, "rb") as f:
                image_data = f.read()
            
            # 检测图片类型
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
        
        # 系统提示词
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
        
        # 如果有图片，添加图片内容
        if image_path:
            image_data = self._encode_image(image_path)
            if image_data:
                content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": image_data
                    }
                })
        
        # 添加文本
        if isinstance(content, list):
            content.append({
                "type": "text",
                "text": user_message
            })
            messages.append({
                "role": "user",
                "content": content
            })
        else:
            messages.append({
                "role": "user",
                "content": user_message
            })
        
        return messages
    
    def _convert_tools(self, tools: List[Dict]) -> List[Dict]:
        """
        转换工具格式为 OpenAI 格式
        """
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
        """
        与 Kimi 对话
        
        Args:
            user_message: 用户消息
            system_prompt: 系统提示词
            history: 历史消息
            image_path: 图片路径（可选）
            tools: 工具定义
            max_tokens: 最大生成 token 数
            
        Returns:
            LLMResponse
        """
        if history is None:
            history = []
        
        messages = self._build_messages(
            system_prompt, history, user_message, image_path
        )
        
        try:
            kwargs = {
                "model": self.model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": 1.0 if "k2" in self.model else 0.7,
            }
            
            if tools:
                kwargs["tools"] = self._convert_tools(tools)
                kwargs["tool_choice"] = "auto"
            
            response = await self.client.chat.completions.create(**kwargs)
            
            # 解析响应
            message = response.choices[0].message
            
            # 提取文本
            text = message.content or ""
            
            # 提取工具调用
            tool_calls = []
            if message.tool_calls:
                for tc in message.tool_calls:
                    tool_calls.append({
                        "id": tc.id,
                        "name": tc.function.name,
                        "input": json.loads(tc.function.arguments)
                    })
            
            # 判断停止原因
            stop_reason = response.choices[0].finish_reason
            if tool_calls:
                stop_reason = "tool_use"
            
            return LLMResponse(
                text=text,
                tool_calls=tool_calls,
                stop_reason=stop_reason
            )
            
        except Exception as e:
            logger.error(f"Kimi API 调用失败: {e}")
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
                                    max_tokens: int = 4096) -> LLMResponse:
        """
        发送工具执行结果，继续对话
        """
        if history is None:
            history = []
        
        # 构建包含工具结果的消息
        messages = []
        
        if system_prompt:
            messages.append({
                "role": "system",
                "content": system_prompt
            })
        
        messages.extend(history[-20:])
        
        # 添加工具结果
        for result in tool_results:
            messages.append({
                "role": "tool",
                "tool_call_id": result["tool_use_id"],
                "content": result["content"]
            })
        
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=1.0 if "k2" in self.model else 0.7,
            )
            
            message = response.choices[0].message
            text = message.content or ""
            
            return LLMResponse(
                text=text,
                tool_calls=[],
                stop_reason=response.choices[0].finish_reason
            )
            
        except Exception as e:
            logger.error(f"Kimi API 调用失败: {e}")
            return LLMResponse(
                text=f"抱歉，我遇到了一些问题: {str(e)}",
                tool_calls=[],
                stop_reason="error"
            )
