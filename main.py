#!/usr/bin/env python3
"""
AI 读书搭子 - 主程序

核心功能：
1. 语音输入（按住右 Alt 说话）
2. AI 对话（Kimi 2.5 + 工具调用）
3. TTS 播报
4. 自动扫描书页（2秒间隔，OCR 识别）
5. 飞书集成（Bot + 推送）

启动：
    python main.py

依赖：
    - 阿里云 NLS（ASR + TTS）
    - Moonshot Kimi（AI）
    - 飞书开放平台（可选）
"""
import asyncio
import logging
import signal
import sys
from pathlib import Path
from typing import Optional

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("main")

# 导入模块
from config import config, Config
from session.storage import Storage
from session.manager import SessionManager
from agent.kimi_client import KimiClient
from agent.memory import Memory
from agent.tools import ToolRegistry, ToolExecutor
from scanner.auto_scanner import AutoScanner
from voice.asr import AliyunStreamASR, create_asr
from voice.recorder import VoiceRecorder
from tts.speaker import AliyunTTS, TTSPlayer, detect_player
from feishu.bot import FeishuBot
from feishu.push import SummaryPusher


class ReadingCompanion:
    """
    AI 读书搭子主类
    整合所有模块，协调工作
    """
    
    def __init__(self):
        # 配置检查
        missing = config.validate()
        if missing:
            logger.error(f"缺少配置项: {', '.join(missing)}")
            logger.error("请运行: python setup.py 生成配置文件")
            sys.exit(1)
        
        # 确保目录存在
        config.ensure_dirs()
        
        # 保存事件循环引用（用于跨线程调度）
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        
        # 初始化各模块
        self.storage: Optional[Storage] = None
        self.session_manager: Optional[SessionManager] = None
        self.llm: Optional[KimiClient] = None
        self.memory: Optional[Memory] = None
        self.tool_registry: Optional[ToolRegistry] = None
        self.tool_executor: Optional[ToolExecutor] = None
        self.scanner: Optional[AutoScanner] = None
        self.asr: Optional[AliyunStreamASR] = None
        self.recorder: Optional[VoiceRecorder] = None
        self.tts: Optional[AliyunTTS] = None
        self.tts_player: Optional[TTSPlayer] = None
        self.feishu_bot: Optional[FeishuBot] = None
        self.summary_pusher: Optional[SummaryPusher] = None
        
        # 状态
        self._running = False
        
    async def initialize(self):
        """初始化所有模块"""
        logger.info("正在初始化...")
        
        # 保存事件循环引用
        self.loop = asyncio.get_running_loop()
        
        # 1. 数据库
        self.storage = Storage(config.SESSIONS_DB)
        await self.storage.initialize()
        
        # 2. 会话管理
        self.session_manager = SessionManager(self.storage)
        
        # 3. AI 相关 (Kimi)
        self.llm = KimiClient(
            api_key=config.KIMI_API_KEY,
            model=config.KIMI_MODEL,
            base_url=config.KIMI_BASE_URL
        )
        self.memory = Memory(config.PERSONA_FILE)
        self.tool_registry = ToolRegistry()
        
        # 4. 扫描器（先创建，但稍后启动）
        self.scanner = AutoScanner(self.session_manager)
        self.scanner.on_page_turn = self._on_page_turn
        self.scanner.on_snapshot = self._on_snapshot
        
        # 5. 工具执行器（依赖 scanner 和 session_manager）
        self.tool_executor = ToolExecutor(
            session_manager=self.session_manager,
            scanner=self.scanner,
            memory=self.memory
        )
        
        # 6. 语音
        self.asr = create_asr(config.ALIYUN_NLS_APP_KEY, config.ALIYUN_NLS_TOKEN)
        self.recorder = VoiceRecorder(
            self.asr,
            loop=self.loop,  # 传入事件循环，用于跨线程调度
            sample_rate=16000,
            channels=1,
            min_duration=0.3
        )
        self.recorder.on_text = self._on_voice_text
        
        # 7. TTS
        self.tts = AliyunTTS(config.ALIYUN_NLS_APP_KEY, config.ALIYUN_NLS_TOKEN)
        player_cmd = config.TTS_PLAYER_CMD or detect_player()
        self.tts_player = TTSPlayer(self.tts, player_cmd=player_cmd)
        await self.tts_player.start()
        
        # 8. 飞书 Bot（可选）
        if config.FEISHU_ENABLED and config.FEISHU_APP_ID and config.FEISHU_APP_SECRET:
            self.feishu_bot = FeishuBot(
                app_id=config.FEISHU_APP_ID,
                app_secret=config.FEISHU_APP_SECRET,
                encrypt_key=config.FEISHU_ENCRYPT_KEY,
                verification_token=config.FEISHU_VERIFICATION_TOKEN,
                message_handler=self._handle_feishu_message
            )
            self.summary_pusher = SummaryPusher(self.feishu_bot)
            self.feishu_bot.start()
            logger.info("飞书 Bot 已启动")
        
        logger.info("初始化完成")
    
    async def shutdown(self):
        """关闭所有模块"""
        logger.info("正在关闭...")
        
        self._running = False
        
        # 停止录音
        if self.recorder:
            self.recorder.stop()
        
        # 停止扫描
        if self.scanner:
            await self.scanner.stop()
        
        # 停止 TTS
        if self.tts_player:
            await self.tts_player.stop()
        
        # 停止飞书
        if self.feishu_bot:
            self.feishu_bot.stop()
        
        # 关闭数据库
        if self.storage:
            await self.storage.close()
        
        logger.info("已关闭")
    
    async def run(self):
        """主运行循环"""
        await self.initialize()
        
        self._running = True
        
        # 启动录音监听
        self.recorder.start()
        
        logger.info("=" * 50)
        logger.info("🎉 AI 读书搭子已启动！")
        logger.info(f"🤖 AI 模型: {config.KIMI_MODEL}")
        logger.info("按住 【右 Alt 键】说话与 AI 交流")
        logger.info("指令：")
        logger.info("  - \"开始读书\" - 开始阅读会话")
        logger.info("  - \"看看这页\" - 拍摄当前页面")
        logger.info("  - \"记录一下...\" - 添加笔记")
        logger.info("  - \"读完了\" - 结束会话并推送总结")
        logger.info("  - \"今天读了什么\" - 查询历史")
        logger.info("=" * 50)
        
        # 保持运行
        try:
            while self._running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        
        await self.shutdown()
    
    # ==================== 回调处理 ====================
    
    async def _on_voice_text(self, text: str):
        """处理语音识别结果（异步版本）"""
        logger.info(f"👤 用户: {text}")
        await self._process_user_message(text)
    
    async def _process_user_message(self, text: str, channel: str = "voice"):
        """
        处理用户消息
        
        Args:
            text: 用户输入
            channel: 渠道（voice / feishu）
        """
        try:
            # 1. 调用 LLM
            system_prompt = self.memory.build_system_prompt()
            history = self.memory.get_history()
            tools = self.tool_registry.get_tools()
            
            response = await self.llm.chat(
                user_message=text,
                system_prompt=system_prompt,
                history=history,
                tools=tools
            )
            
            # 2. 处理工具调用
            if response.tool_calls:
                # 先记录 AI 的思考过程
                self.memory.add_message("assistant", f"[调用工具: {', '.join(tc['name'] for tc in response.tool_calls)}]")
                
                # 执行工具
                tool_results = []
                for tool_call in response.tool_calls:
                    result = await self.tool_executor.execute(
                        tool_call["name"],
                        tool_call["input"]
                    )
                    tool_results.append({
                        "tool_use_id": tool_call["id"],
                        "content": str(result)
                    })
                
                # 将工具结果发送给 LLM
                final_response = await self.llm.chat_with_tool_result(
                    user_message=text,
                    tool_results=tool_results,
                    system_prompt=system_prompt,
                    history=history
                )
                
                reply_text = final_response.text
            else:
                reply_text = response.text
            
            # 3. 记录对话历史
            self.memory.add_message("user", text)
            self.memory.add_message("assistant", reply_text)
            
            # 4. 输出回复
            logger.info(f"🤖 AI: {reply_text}")
            
            # 5. 语音播报（如果是语音渠道）
            if channel == "voice":
                await self.tts_player.speak(reply_text, interrupt=True)
            
            # 6. 检查是否需要推送飞书（会话结束）
            if "read" in text or "结束" in text:
                await self._check_and_push_feishu()
                
        except Exception as e:
            logger.error(f"处理消息失败: {e}")
            if channel == "voice":
                await self.tts_player.speak("抱歉，处理时出错了", interrupt=True)
    
    async def _handle_feishu_message(self, text: str, channel: str = "feishu") -> str:
        """处理飞书消息"""
        # 复用相同的处理逻辑
        await self._process_user_message(text, channel="feishu")
        # 返回空字符串，因为实际回复在 _process_user_message 中处理
        return ""
    
    def _on_page_turn(self, page_count: int):
        """翻页回调"""
        logger.info(f"📖 已翻到第 {page_count} 页")
    
    def _on_snapshot(self, ocr_text: str, image_path: str):
        """快照回调"""
        # 更新记忆
        self.memory.set_page_context(ocr_text, image_path)
        logger.debug(f"📸 快照已更新，文本长度: {len(ocr_text)}")
    
    async def _check_and_push_feishu(self):
        """检查并推送飞书总结"""
        if not self.feishu_bot or not self.summary_pusher:
            return


async def main():
    """入口函数"""
    app = ReadingCompanion()
    
    # 信号处理
    def signal_handler(sig, frame):
        logger.info("收到退出信号...")
        if app.loop:
            asyncio.run_coroutine_threadsafe(app.shutdown(), app.loop)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        await app.run()
    except Exception as e:
        logger.exception("程序异常退出")
        raise


if __name__ == "__main__":
    asyncio.run(main())
