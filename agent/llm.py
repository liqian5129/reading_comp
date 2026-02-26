"""
LLM 客户端
使用 Anthropic Claude API
支持视觉输入和工具调用
"""
import base64
import json
import logging
from typing import List, Dict, Any, Optional, Callable
from dataclasses import dataclass
from pathlib import Path

import anthropic

logger = logging.getLogger(__name__)


@dataclass
class LLMResponse:
    """LLM 响应"""
    text: str
    tool_calls: List[Dict[str, Any]]
    stop_reason: str


class ClaudeClient:
    """
    Claude API 客户端
    """
    
    def __init__(self, api_key: str, model: str = "claude-3-5-sonnet-20241022"):
        self.api_key = api_key
        self.model = model
        self.client = anthropic.AsyncAnthropic(api_key=api_key)
        
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
                       image_path: Optional[str] = None) -> tuple:
        """
        构建消息列表
        
        Returns:
            (system, messages)
        """
        messages = []
        
        # 添加历史消息
        for msg in history[-20:]:  # 保留最近 20 条
            messages.append(msg)
        
        # 添加当前用户消息
        content = []
        
        # 如果有图片，添加图片内容
        if image_path:
            image_data = self._encode_image(image_path)
            if image_data:
                content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": image_data.split(';')[0].split(':')[1],
                        "data": image_data.split(',')[1]
                    }
                })
        
        # 添加文本
        content.append({
            "type": "text",
            "text": user_message
        })
        
        messages.append({
            "role": "user",
            "content": content
        })
        
        return system_prompt, messages
    
    async def chat(self,
                   user_message: str,
                   system_prompt: str = "",
                   history: List[Dict[str, str]] = None,
                   image_path: Optional[str] = None,
                   tools: List[Dict] = None,
                   max_tokens: int = 4096) -> LLMResponse:
        """
        与 Claude 对话
        
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
        
        system, messages = self._build_messages(
            system_prompt, history, user_message, image_path
        )
        
        try:
            kwargs = {
                "model": self.model,
                "max_tokens": max_tokens,
                "messages": messages,
            }
            
            if system:
                kwargs["system"] = system
            
            if tools:
                kwargs["tools"] = tools
            
            response = await self.client.messages.create(**kwargs)
            
            # 解析响应
            text_parts = []
            tool_calls = []
            
            for block in response.content:
                if block.type == "text":
                    text_parts.append(block.text)
                elif block.type == "tool_use":
                    tool_calls.append({
                        "id": block.id,
                        "name": block.name,
                        "input": block.input
                    })
            
            return LLMResponse(
                text="\n".join(text_parts),
                tool_calls=tool_calls,
                stop_reason=response.stop_reason
            )
            
        except Exception as e:
            logger.error(f"Claude API 调用失败: {e}")
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
        messages = list(history[-20:])
        
        # 添加工具结果
        tool_result_content = []
        for result in tool_results:
            tool_result_content.append({
                "type": "tool_result",
                "tool_use_id": result["tool_use_id"],
                "content": result["content"]
            })
        
        messages.append({
            "role": "user",
            "content": tool_result_content
        })
        
        try:
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                messages=messages,
                system=system_prompt if system_prompt else anthropic.NOT_GIVEN,
            )
            
            text_parts = []
            for block in response.content:
                if block.type == "text":
                    text_parts.append(block.text)
            
            return LLMResponse(
                text="\n".join(text_parts),
                tool_calls=[],
                stop_reason=response.stop_reason
            )
            
        except Exception as e:
            logger.error(f"Claude API 调用失败: {e}")
            return LLMResponse(
                text=f"抱歉，我遇到了一些问题: {str(e)}",
                tool_calls=[],
                stop_reason="error"
            )
