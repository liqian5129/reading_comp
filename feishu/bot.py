"""
飞书 Bot
WebSocket 长连接接收消息
"""
import asyncio
import json
import logging
from typing import Callable, Optional, Dict, Any

import lark_oapi as lark
from lark_oapi.api.im.v1 import *

from config import config

logger = logging.getLogger(__name__)


class FeishuBot:
    """
    飞书机器人
    
    - WebSocket 长连接接收消息
    - 调用 AI Agent 处理
    - 回复文本或卡片消息
    """
    
    def __init__(self, app_id: str, app_secret: str,
                 encrypt_key: str = "",
                 verification_token: str = "",
                 message_handler: Optional[Callable] = None,
                 loop=None):
        self.app_id = app_id
        self.app_secret = app_secret
        self.encrypt_key = encrypt_key
        self.verification_token = verification_token
        self.message_handler = message_handler
        self._loop = loop  # 主事件循环，用于跨线程派发协程
        
        # 飞书客户端
        self.client = lark.Client.builder() \
            .app_id(app_id) \
            .app_secret(app_secret) \
            .log_level(lark.LogLevel.WARNING) \
            .build()
        
        # WS 客户端
        self.ws_client: Optional[lark.ws.Client] = None
        self._running = False
        
    def _handle_p2_message(self, data) -> None:
        """处理收到的消息（data 为 P2ImMessageReceiveV1 对象）"""
        try:
            message = data.event.message

            msg_type = message.message_type
            content = message.content or "{}"
            chat_id = message.chat_id
            msg_id = message.message_id

            # 只处理文本消息
            if msg_type == "text":
                content_obj = json.loads(content)
                text = content_obj.get("text", "").strip()

                logger.info(f"收到飞书消息: {text}")

                if self.message_handler and self._loop:
                    asyncio.run_coroutine_threadsafe(
                        self._handle_message_async(chat_id, msg_id, text),
                        self._loop
                    )

        except Exception as e:
            logger.error(f"处理飞书消息失败: {e}")
    
    async def _handle_message_async(self, chat_id: str, msg_id: str, text: str):
        """异步处理消息"""
        try:
            response = await self.message_handler(text, channel="feishu")
            await self.send_text(chat_id, response)
        except Exception as e:
            logger.error(f"处理消息失败: {e}")
            await self.send_text(chat_id, f"抱歉，处理消息时出错: {str(e)}")
    
    def start(self):
        """启动 WebSocket 连接（同步方式，在后台线程运行）"""
        import threading
        
        def run_ws():
            # 创建事件处理器
            handler = lark.EventDispatcherHandler.builder(
                self.encrypt_key,
                self.verification_token,
                lark.LogLevel.INFO
            ).register_p2_im_message_receive_v1(self._handle_p2_message) \
             .build()
            
            # 创建 WS 客户端
            self.ws_client = lark.ws.Client(
                self.app_id,
                self.app_secret,
                log_level=lark.LogLevel.INFO,
                event_handler=handler,
            )
            
            self._running = True
            logger.info("飞书 Bot WebSocket 启动...")
            self.ws_client.start()
        
        # 在后台线程启动
        self._ws_thread = threading.Thread(target=run_ws, daemon=True)
        self._ws_thread.start()
    
    def stop(self):
        """停止 WebSocket 连接"""
        self._running = False
        if self.ws_client:
            self.ws_client.stop()
    
    async def send_text(self, chat_id: str, text: str):
        """
        发送文本消息
        
        Args:
            chat_id: 会话 ID
            text: 消息内容
        """
        try:
            request = CreateMessageRequest.builder() \
                .receive_id_type("chat_id") \
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(chat_id)
                    .msg_type("text")
                    .content(json.dumps({"text": text}))
                    .build()
                ) \
                .build()

            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.client.im.v1.message.create(request)
            )

            if response.success():
                logger.debug(f"消息发送成功: {chat_id}")
            else:
                logger.error(f"消息发送失败: {response.code} - {response.msg}")

        except Exception as e:
            logger.error(f"发送消息失败: {e}")

    async def send_interactive_card(self, chat_id: str, card: Dict):
        """
        发送交互卡片

        Args:
            chat_id: 会话 ID
            card: 卡片内容
        """
        try:
            request = CreateMessageRequest.builder() \
                .receive_id_type("chat_id") \
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(chat_id)
                    .msg_type("interactive")
                    .content(json.dumps(card))
                    .build()
                ) \
                .build()

            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.client.im.v1.message.create(request)
            )

            if response.success():
                logger.debug(f"卡片发送成功: {chat_id}")
            else:
                logger.error(f"卡片发送失败: {response.code} - {response.msg}")

        except Exception as e:
            logger.error(f"发送卡片失败: {e}")

    async def send_to_user(self, user_id: str, text: str):
        """
        给用户发送消息（Open ID）

        Args:
            user_id: 用户 Open ID
            text: 消息内容
        """
        try:
            request = CreateMessageRequest.builder() \
                .receive_id_type("open_id") \
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(user_id)
                    .msg_type("text")
                    .content(json.dumps({"text": text}))
                    .build()
                ) \
                .build()

            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.client.im.v1.message.create(request)
            )

            if response.success():
                logger.debug(f"消息发送给用户成功: {user_id}")
            else:
                logger.error(f"消息发送失败: {response.code} - {response.msg}")

        except Exception as e:
            logger.error(f"发送消息给用户失败: {e}")
