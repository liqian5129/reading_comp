#!/usr/bin/env python3
"""
AI 读书搭子 - 主程序
"""
import asyncio
import logging
import signal
import sys
import time
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
from agent.ai_client import AIClient
from agent.memory import Memory
from agent.tools import ToolRegistry, ToolExecutor
from agent.timer_manager import ReadingTimerManager
from scanner.vision_analyzer import VisionAnalyzer
from scanner.auto_scanner import AutoScanner
from voice.asr import AliyunStreamASR, create_asr
from voice.recorder import VoiceRecorder
from feishu.bot import FeishuBot
from feishu.push import SummaryPusher


class ReadingCompanion:
    """
    AI 读书搭子主类
    """

    def __init__(self):
        # 调试模式下跳过 API key 检查
        if config.DEBUG_MODE:
            logger.info("⚠️  调试模式已启用，跳过 API 配置验证")
        else:
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
        self.llm: Optional[AIClient] = None
        self.memory: Optional[Memory] = None
        self.tool_registry: Optional[ToolRegistry] = None
        self.tool_executor: Optional[ToolExecutor] = None
        self.scanner: Optional[AutoScanner] = None
        self.vision_analyzer: Optional[VisionAnalyzer] = None
        self.timer_manager: Optional[ReadingTimerManager] = None
        self.asr: Optional[AliyunStreamASR] = None
        self.recorder: Optional[VoiceRecorder] = None
        self.tts_player = None
        self.feishu_bot: Optional[FeishuBot] = None
        self.summary_pusher: Optional[SummaryPusher] = None
        
        # 状态
        self._running = False
        self._last_valid_ocr_ts: float = 0.0  # 上次有效 OCR 的时间戳
        
    async def initialize(self):
        """初始化所有模块"""
        logger.info("正在初始化...")

        # 保存事件循环引用
        self.loop = asyncio.get_running_loop()

        # 1. 数据库
        self.storage = Storage(config.SESSIONS_DB, notes_dir=config.NOTES_DIR)
        await self.storage.initialize()

        # 2. 会话管理
        self.session_manager = SessionManager(self.storage)

        if config.DEBUG_MODE:
            # --- 调试模式：只启动摄像头+OCR，跳过 AI/ASR/TTS/飞书 ---
            logger.info("🔧 调试模式：跳过 AI / ASR / TTS / 飞书初始化")

            self.scanner = AutoScanner(self.session_manager)
            self.scanner.on_snapshot = self._on_snapshot
            if config.SCANNER_ENABLED:
                await self.scanner.start()
            else:
                logger.info("📷 摄像头/OCR 扫描已禁用（camera.scanner_enabled=false）")

            logger.info("初始化完成（调试模式）")
            return

        # --- 正常模式 ---

        # 3. AI 客户端（支持 Kimi 或豆包）
        if config.AI_PROVIDER == "kimi":
            self.llm = AIClient(
                provider="kimi",
                api_key=config.KIMI_API_KEY,
                model=config.KIMI_MODEL,
                base_url=config.KIMI_BASE_URL,
                enable_thinking=config.KIMI_ENABLE_THINKING
            )
        else:  # doubao
            self.llm = AIClient(
                provider="doubao",
                api_key=config.DOUBAO_API_KEY,
                model=config.DOUBAO_MODEL,
                base_url=config.DOUBAO_BASE_URL
            )

        self.memory = Memory(config.PERSONA_FILE, long_term_file=config.LONG_TERM_MEMORY_FILE)
        self.tool_registry = ToolRegistry()
        self.timer_manager = ReadingTimerManager()

        # 4. 扫描器
        self.scanner = AutoScanner(self.session_manager)
        self.scanner.on_snapshot = self._on_snapshot
        if config.SCANNER_ENABLED:
            await self.scanner.start()
        else:
            logger.info("📷 摄像头/OCR 扫描已禁用（camera.scanner_enabled=false）")

        # 4b. 视觉分析器（需要支持图片的模型，默认关闭）
        if config.VISION_ANALYZER_ENABLED:
            if config.VISION_MODEL == config.CURRENT_MODEL:
                vision_llm = self.llm  # 同一模型，复用客户端
            else:
                vision_llm = AIClient(
                    provider="kimi",
                    api_key=config.VISION_API_KEY,
                    model=config.VISION_MODEL,
                    base_url=config.VISION_BASE_URL,
                )
                logger.info(f"🔭 视觉分析器使用独立模型: {config.VISION_MODEL}")
            self.vision_analyzer = VisionAnalyzer(
                ai_client=vision_llm,
                on_book_detected=self._on_book_detected,
            )
            self.scanner.set_vision_analyzer(self.vision_analyzer)
            logger.info("🔭 视觉分析器已启用")
        else:
            logger.info("🔭 视觉分析器已禁用（vision.enabled=false，kimi-k2.5 不支持图片）")

        # 5. 工具执行器（依赖 scanner 和 session_manager）
        self.tool_executor = ToolExecutor(
            session_manager=self.session_manager,
            scanner=self.scanner,
            memory=self.memory,
            llm=self.llm,
            timer_manager=self.timer_manager,
        )

        # 6. 语音
        self.asr = create_asr(
            app_key=config.ALIYUN_NLS_APP_KEY,
            token=config.ALIYUN_NLS_TOKEN,
            access_key_id=config.ALIYUN_NLS_ACCESS_KEY_ID,
            access_key_secret=config.ALIYUN_NLS_ACCESS_KEY_SECRET,
        )
        self.recorder = VoiceRecorder(
            self.asr,
            loop=self.loop,
            sample_rate=16000,
            channels=1,
            min_duration=0.3
        )
        self.recorder.on_text = self._on_voice_text

        # 7. TTS（支持阿里云或 ElevenLabs）
        from tts import create_tts_player
        self.tts_player = create_tts_player(config)
        await self.tts_player.start()
        # 把 TTS 注入定时器（无论飞书是否启用都能播报）
        self.timer_manager.set_tts_player(self.tts_player)

        # 8. 飞书 Bot（可选）
        if config.FEISHU_ENABLED and config.FEISHU_APP_ID and config.FEISHU_APP_SECRET:
            self.feishu_bot = FeishuBot(
                app_id=config.FEISHU_APP_ID,
                app_secret=config.FEISHU_APP_SECRET,
                encrypt_key=config.FEISHU_ENCRYPT_KEY,
                verification_token=config.FEISHU_VERIFICATION_TOKEN,
                message_handler=self._handle_feishu_message,
                loop=self.loop
            )
            self.summary_pusher = SummaryPusher(self.feishu_bot)
            self.feishu_bot.start()
            logger.info("飞书 Bot 已启动")

            # 将飞书 pusher 注入 ToolExecutor 和 TimerManager
            feishu_chat_id = getattr(config, "FEISHU_DEFAULT_CHAT_ID", "")
            self.tool_executor.feishu_pusher = self.summary_pusher
            self.tool_executor.feishu_chat_id = feishu_chat_id
            self.timer_manager.set_tts_player(self.tts_player)
            self.timer_manager.set_feishu(self.summary_pusher, feishu_chat_id)

        logger.info("初始化完成")
    
    async def shutdown(self):
        """关闭所有模块"""
        logger.info("正在关闭...")

        self._running = False

        if self.timer_manager:
            self.timer_manager.cancel_all()
        if self.vision_analyzer:
            await self.vision_analyzer.cancel()
        if self.recorder:
            self.recorder.stop()
        if self.scanner and self.scanner.is_running():
            await self.scanner.stop()
        if self.tts_player:
            await self.tts_player.stop()
        if self.feishu_bot:
            self.feishu_bot.stop()
        if self.storage:
            await self.storage.close()

        logger.info("已关闭")
    
    async def run(self):
        """主运行循环"""
        await self.initialize()

        self._running = True

        if config.DEBUG_MODE:
            logger.info("=" * 60)
            logger.info("🔧 AI 读书搭子已启动（调试模式）")
            logger.info("   ASR / AI / TTS / 飞书 均已禁用")
            if self.scanner and self.scanner.is_running():
                logger.info(f"   摄像头+OCR 已启动，间隔 {config.AUTO_SCAN_INTERVAL}s")
            else:
                logger.info("   摄像头/OCR 未启动（scanner_enabled=false）")
            logger.info("=" * 60)
        else:
            # 启动录音监听
            self.recorder.start()

            logger.info("=" * 60)
            logger.info("🎉 AI 读书搭子已启动！")
            logger.info(f"🤖 AI 提供商: {config.AI_PROVIDER}")
            logger.info(f"🤖 AI 模型: {config.CURRENT_MODEL}")
            logger.info(f"🔊 TTS 提供商: {config.TTS_PROVIDER}")
            logger.info("按住 【右 Alt 键】说话与 AI 交流")
            logger.info("=" * 60)
        
        # 保持运行
        try:
            while self._running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        
        await self.shutdown()
    
    async def _on_voice_text(self, text: str):
        """处理语音识别结果（异步版本）"""
        logger.info(f"👤 用户: {text}")
        await self._process_user_message(text)
    
    async def _process_user_message(self, text: str, channel: str = "voice"):
        """
        处理用户消息 - 带完整链路计时
        """
        logger.info("=" * 60)
        logger.info("🚀 开始处理用户消息")
        logger.info(f"   输入: {text[:50]}...")
        logger.info("=" * 60)
        
        # 整体链路计时
        start_time = time.time()
        
        try:
            # 1. 调用 LLM
            logger.info("⏳ 1. 准备调用 LLM...")
            system_prompt = self.memory.build_system_prompt()
            history = self.memory.get_history()
            tools = self.tool_registry.get_tools()
            page_ctx_len = len(self.memory.current_page_ocr)
            logger.info(f"   历史消息数: {len(history)}, 工具数: {len(tools)}, "
                        f"书页上下文: {page_ctx_len}字"
                        + (" ✓" if page_ctx_len else " (无)"))
            
            response = await self.llm.chat(
                user_message=text,
                system_prompt=system_prompt,
                history=history,
                tools=tools
            )
            
            llm_done_time = time.time()
            
            # 2. 处理工具调用
            if response.tool_calls:
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

                final_response = await self.llm.chat_with_tool_result(
                    user_message=text,
                    tool_results=tool_results,
                    system_prompt=system_prompt,
                    history=history,
                    assistant_message=response.raw_assistant_message,
                )
                
                reply_text = final_response.text
            else:
                reply_text = response.text
            
            tool_done_time = time.time()
            
            # 打印 AI 回复内容
            logger.info("=" * 60)
            logger.info("🤖 AI 回复内容:")
            logger.info("-" * 60)
            # 多行显示，每行最多 58 字符
            for line in reply_text.split('\n'):
                while line:
                    chunk = line[:58]
                    line = line[58:]
                    logger.info(f"  {chunk}")
            logger.info("-" * 60)
            logger.info(f"📊 回复长度: {len(reply_text)} 字符, {len(reply_text.split())} 词")
            logger.info("=" * 60)
            
            # 3. 记录对话历史
            self.memory.add_message("user", text)
            self.memory.add_message("assistant", reply_text)
            
            # 4. 语音播报（带 TTS 时间计算）
            if channel == "voice" and reply_text:
                logger.info("🔊 开始 TTS 转换...")
                await self.tts_player.speak(reply_text, interrupt=True)
                # 等待合成完成（不等播放），获取真实合成耗时
                if hasattr(self.tts_player, 'wait_synthesized'):
                    tts_time = await self.tts_player.wait_synthesized(timeout=30.0)
                else:
                    tts_time = 0
                logger.info(f"✅ TTS 合成完成，耗时: {tts_time:.0f} ms")
            else:
                tts_time = 0
            
            end_time = time.time()
            
            # 打印完整链路分析
            total_time = (end_time - start_time) * 1000
            llm_time = (llm_done_time - start_time) * 1000
            tool_time = (tool_done_time - llm_done_time) * 1000 if response.tool_calls else 0
            
            logger.info("╔" + "=" * 58 + "╗")
            logger.info("║" + " 📊 完整链路耗时分析 ".center(54) + "║")
            logger.info("╠" + "=" * 58 + "╣")
            logger.info(f"║  LLM 推理:     {llm_time:>6.0f} ms                          ║")
            if response.tool_calls:
                logger.info(f"║  工具执行:     {tool_time:>6.0f} ms                          ║")
            if tts_time > 0:
                logger.info(f"║  TTS 转换:     {tts_time:>6.0f} ms                          ║")
            logger.info("╠" + "=" * 58 + "╣")
            logger.info(f"║  总耗时:       {total_time:>6.0f} ms                          ║")
            logger.info("╚" + "=" * 58 + "╝")
                
        except Exception as e:
            logger.error(f"处理消息失败: {e}")
            if channel == "voice":
                await self.tts_player.speak("抱歉，处理时出错了", interrupt=True)
            return ""

        return reply_text

    async def _handle_feishu_message(self, text: str, channel: str = "feishu", chat_id: str = "") -> str:
        """处理飞书消息"""
        # 动态更新 chat_id：首条消息即可获得真实会话 ID，后续定时器推送可用
        if chat_id and self.summary_pusher:
            if self.timer_manager and self.timer_manager._feishu_chat_id != chat_id:
                self.timer_manager.set_feishu(self.summary_pusher, chat_id)
                logger.debug(f"飞书 chat_id 已更新: {chat_id}")
            if self.tool_executor:
                self.tool_executor.feishu_chat_id = chat_id
        return await self._process_user_message(text, channel="feishu")
    
    def _on_book_detected(self, vision_result: dict):
        """视觉分析回调：更新书籍上下文"""
        book_title = vision_result.get("book_title", "")
        confidence = vision_result.get("confidence", 0)
        if book_title and confidence >= 0.7:
            self.memory.update_book_context(vision_result)
            logger.info(f"📚 书名已识别: 《{book_title}》（置信度 {confidence:.2f}）")

    # OCR 连续无内容超时：超过此秒数才清空上下文
    _OCR_CLEAR_TIMEOUT_S = 60

    def _on_snapshot(self, ocr_text: str, image_path: str):
        """快照回调：将 OCR 文字写入 AI 上下文"""
        MIN_OCR_LEN = 6  # 少于此字数视为无效内容
        if not ocr_text or len(ocr_text.strip()) < MIN_OCR_LEN:
            # 检查距上次有效 OCR 是否超过超时阈值
            elapsed = time.time() - self._last_valid_ocr_ts
            if elapsed >= self._OCR_CLEAR_TIMEOUT_S:
                self.memory.set_page_context("")
                logger.info(f"📖 OCR 持续 {elapsed:.0f}s 无内容，已清空书页上下文")
            else:
                logger.debug(f"📖 OCR 无内容（已 {elapsed:.0f}s），保留上次上下文")
            return
        self._last_valid_ocr_ts = time.time()
        self.memory.set_page_context(ocr_text, image_path)
        preview = ocr_text[:80].replace('\n', ' ')
        logger.info(f"📖 书页上下文已注入 ({len(ocr_text)}字) → 下次 AI 对话生效")
        logger.info(f"   预览: {preview}…")
    
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
