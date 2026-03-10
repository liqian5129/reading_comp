#!/usr/bin/env python3
"""
AI 读书搭子 - 主程序
"""
import asyncio
import datetime
import logging
import os
import re
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

# 文件日志：每次启动创建一个带时间戳的日志文件
_LOG_DIR = Path(__file__).parent / "logs"
_LOG_DIR.mkdir(exist_ok=True)
_LOG_FILE = _LOG_DIR / datetime.datetime.now().strftime("%Y%m%d_%H%M%S.log")
_fh = logging.FileHandler(_LOG_FILE, encoding="utf-8")
_fh.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logging.getLogger().addHandler(_fh)
logger.info(f"📝 日志文件: {_LOG_FILE}")

# 显式锁定我们自己的模块级别，防止第三方库修改 root logger 后被连带压制
for _n in ["main", "config", "session", "agent", "scanner",
           "voice", "tts", "feishu", "camera", "ocr"]:
    logging.getLogger(_n).setLevel(logging.INFO)

# 压制第三方噪音日志（propagate=False 防止它们冒泡到 root handler）
for _n in ["jieba", "Lark", "httpx", "urllib3"]:
    _l = logging.getLogger(_n)
    _l.setLevel(logging.WARNING)
    _l.propagate = False

# 压制 urllib3 的 NotOpenSSLWarning（Python warnings 模块层面）
import warnings
warnings.filterwarnings("ignore", category=Warning, module="urllib3")

# ── TTS 分块参数 ──────────────────────────────────────────────────────────────
# TTS 切割阈值（有效字符 = 汉字+字母+数字，不含标点符号）
_SENT_END = re.compile(r'(?<=[。！？…!?\n])\s*')
_TTS_FIRST_MIN_CHARS = 8   # 首段门槛：遇到句子边界且有效字符 ≥8 即开口
_TTS_MIN_CHARS = 31        # 后续段门槛：有效字符 >30（即 ≥31）再发一批
_TTS_MAX_CHARS = 200       # 强制切割上限（仍用原始长度，作为安全兜底）

_EFFECTIVE_RE = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf\uF900-\uFAFFa-zA-Z0-9]')


def _count_effective(text: str) -> int:
    """统计有效字符数：汉字 + 字母 + 数字，不含标点和其他符号"""
    return len(_EFFECTIVE_RE.findall(text))


def _extract_tts_chunk(buf: str, force: bool = False, min_chars: int = _TTS_MIN_CHARS):
    """
    从缓冲区提取一个 TTS 片段，返回 (片段或None, 剩余缓冲)。

    策略：
    - 找到句子边界 AND 有效字符数 >= min_chars → 发出
    - 原始长度 >= _TTS_MAX_CHARS → 强制切割（安全兜底）
    - force=True（流结束）→ 发出所有剩余
    """
    if force and buf.strip():
        return buf.strip(), ""

    if len(buf) >= _TTS_MAX_CHARS:
        return buf[:_TTS_MAX_CHARS].strip(), buf[_TTS_MAX_CHARS:]

    parts = _SENT_END.split(buf)
    if len(parts) <= 1:
        return None, buf  # 没找到句子边界

    accumulated = ""
    for i, part in enumerate(parts[:-1]):
        accumulated += part
        if _count_effective(accumulated) >= min_chars:
            remainder = "".join(parts[i + 1:])
            return accumulated.strip(), remainder

    return None, buf  # 有边界但有效字符不足 min_chars，继续等


def _format_tool_results(results: dict) -> str:
    """将 Sub Agent 工具结果格式化为 system prompt 注入文本。
    保留完整结构化数据，避免 LLM 因数据缺失而编造内容。
    """
    import json
    lines = []
    for name, result in results.items():
        if not isinstance(result, dict):
            lines.append(f"[{name}]: {result}")
            continue
        if not result.get("success", True):
            lines.append(f"[{name}]: 执行失败 - {result.get('error', '未知错误')}")
            continue
        skip_keys = {"success", "book_title"}
        data = {k: v for k, v in result.items() if k not in skip_keys and v}
        lines.append(f"[{name}]:\n{json.dumps(data, ensure_ascii=False, indent=2)}")
    return "\n\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────

# 导入模块
from config import config, Config
from session.storage import Storage
from session.manager import SessionManager
from agent.ai_client import AIClient
from agent.memory import Memory
from agent.embedder import Embedder
from agent.memory_consolidator import MemoryConsolidator
from agent.tools import ToolRegistry, ToolDispatcher
from agent.sub_agent import SubAgent
from agent.timer_manager import ReadingTimerManager
from agent.knowledge_linker import KnowledgeLinker
from scanner.auto_scanner import AutoScanner
from voice.asr import create_asr
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
        self.tool_dispatcher: Optional[ToolDispatcher] = None
        self.knowledge_linker: Optional[KnowledgeLinker] = None
        self.scanner: Optional[AutoScanner] = None
        self._kimi_ocr = None
        self.timer_manager: Optional[ReadingTimerManager] = None
        self.asr: Optional[Any] = None
        self.recorder: Optional[VoiceRecorder] = None
        self.tts_player = None
        self.feishu_bot: Optional[FeishuBot] = None
        self.summary_pusher: Optional[SummaryPusher] = None
        self.weread_client = None
        self.weread_storage = None
        self.jimeng_client = None
        self.embedder: Optional[Embedder] = None
        self.consolidator: Optional[MemoryConsolidator] = None
        self._periodic_task: Optional[asyncio.Task] = None
        self.sub_agent_llm: Optional[AIClient] = None
        self.sub_agent: Optional[SubAgent] = None

        # 状态
        self._running = False
        self._shutting_down = False  # 防止 shutdown 重入
        self._last_valid_ocr_ts: float = time.time()  # 上次有效 OCR 的时间戳
        self._msg_lock = asyncio.Lock()  # 防止并发处理用户消息
        self._ai_task: Optional[asyncio.Task] = None
        self._last_page_text: str = ""  # 上一次 OCR 文本（用于内容比对翻页检测）
        
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
                enable_thinking=config.KIMI_ENABLE_THINKING,
                max_retries=2,
            )
        else:  # doubao
            self.llm = AIClient(
                provider="doubao",
                api_key=config.DOUBAO_API_KEY,
                model=config.DOUBAO_MODEL,
                base_url=config.DOUBAO_BASE_URL,
                max_retries=2,
            )

        # Sub Agent LLM（独立于 Main LLM，负责工具选择和执行，不共享 rate limit）
        self.sub_agent_llm = AIClient(
            provider=config.SUB_AGENT_PROVIDER,
            api_key=config.SUB_AGENT_API_KEY,
            model=config.SUB_AGENT_MODEL,
            base_url=config.SUB_AGENT_BASE_URL,
            max_retries=1,
        )
        self.sub_agent = SubAgent()
        logger.info(
            f"Sub Agent LLM: provider={config.SUB_AGENT_PROVIDER}, model={config.SUB_AGENT_MODEL}"
        )

        # Embedding 服务（阿里云百炼，独立 key/url）
        if config.EMBEDDING_ENABLED and config.EMBEDDING_API_KEY:
            self.embedder = Embedder(
                api_key=config.EMBEDDING_API_KEY,
                model=config.EMBEDDING_MODEL,
                base_url=config.EMBEDDING_BASE_URL,
                timeout_s=config.EMBEDDING_TIMEOUT_S,
                enabled=True,
            )
        else:
            logger.info("Embedding 已禁用（embedding.enabled=false 或 API key 未配置）")

        self.memory = Memory(
            config.PERSONA_FILE,
            long_term_file=config.LONG_TERM_MEMORY_FILE,
            embedder=self.embedder,
            storage=self.storage,
            proactive_top_k=config.MEMORY_PROACTIVE_INJECT_TOP_K,
        )

        # 启动时加载最近会话摘要到 session_recall
        if config.MEMORY_CONSOLIDATION_ENABLED:
            recent_summaries = await self.storage.load_recent_summaries(
                n=config.MEMORY_SESSION_RECALL_COUNT
            )
            if recent_summaries:
                self.memory.long_term.session_recall = "\n---\n".join(recent_summaries)
                logger.info(f"已加载 {len(recent_summaries)} 条历史会话摘要到 session_recall")

        # 启动时恢复今日阅读摘要（跨会话连续）
        today_digests = await self.storage.get_recent_digests(days=1)
        if today_digests:
            self.memory.session_reading_digest = today_digests[0]["digest_text"]
            logger.info(f"已恢复今日阅读摘要（{len(self.memory.session_reading_digest)}字）")

        self.tool_registry = ToolRegistry()
        self.timer_manager = ReadingTimerManager()

        # KnowledgeLinker（依赖 embedder/storage）
        self.knowledge_linker = KnowledgeLinker(self.embedder, self.storage)

        # 4. 扫描器
        self.scanner = AutoScanner(self.session_manager)
        self.scanner.on_snapshot = self._on_snapshot
        if config.SCANNER_ENABLED:
            await self.scanner.start()
        else:
            logger.info("📷 摄像头/OCR 扫描已禁用（camera.scanner_enabled=false）")

        # 4b. KimiOCR（用 Kimi vision API 替代本地 PaddleOCR，默认关闭）
        if config.KIMI_OCR_ENABLED:
            from scanner.kimi_ocr import KimiOCR, OcrChain

            def _make_kimi_ocr_client():
                return AIClient(
                    provider="kimi",
                    api_key=config.KIMI_OCR_API_KEY,
                    model=config.KIMI_MODEL,
                    base_url=config.KIMI_BASE_URL,
                    max_retries=0,
                )

            def _make_doubao_ocr_client():
                return AIClient(
                    provider="doubao",
                    api_key=config.DOUBAO_OCR_API_KEY,
                    model=config.DOUBAO_OCR_MODEL,
                    base_url=config.DOUBAO_BASE_URL,
                    max_retries=0,
                    timeout=90.0,  # 比 KimiOCR.TIMEOUT_S(120s) 小，确保 SDK 先报错而非被 asyncio 取消
                    reasoning_effort="minimal",  # 不开启思考，避免 OCR 任务超时
                )

            kimi_ocr = KimiOCR(
                _make_kimi_ocr_client(),
                min_interval_s=config.KIMI_OCR_INTERVAL,
                results_dir=config.KIMI_OCR_RESULTS_DIR if config.KIMI_OCR_SAVE_RESULTS else None,
            )
            doubao_ocr = KimiOCR(
                _make_doubao_ocr_client(),
                min_interval_s=config.KIMI_OCR_INTERVAL,
                results_dir=config.DOUBAO_OCR_RESULTS_DIR if config.KIMI_OCR_SAVE_RESULTS else None,
            )

            if config.VISION_OCR_PRIMARY == "doubao":
                primary, fallback = doubao_ocr, kimi_ocr
                logger.info("🔍 OCR 主链路: 豆包，备用: Kimi")
            else:
                primary, fallback = kimi_ocr, doubao_ocr
                logger.info("🔍 OCR 主链路: Kimi，备用: 豆包")

            self._kimi_ocr = OcrChain(primary, fallback)
            self._kimi_ocr.on_text_ready = self._on_snapshot
            self.scanner.set_kimi_ocr(self._kimi_ocr)
            self.scanner.on_book_info = self._on_book_detected
        else:
            logger.info("🔍 KimiOCR 未启用（使用本地 PaddleOCR）")

        # 4b-2. 即梦文生图客户端（可选）
        if config.JIMENG_ENABLED and config.JIMENG_MODEL:
            from rendering.jimeng_client import JimengClient
            self.jimeng_client = JimengClient(
                api_key=config.JIMENG_API_KEY,
                model=config.JIMENG_MODEL,
                base_url=config.JIMENG_BASE_URL,
                timeout=config.JIMENG_TIMEOUT,
            )
            logger.info(f"🎨 即梦文生图已启用 (model={config.JIMENG_MODEL})")
        else:
            logger.info("🎨 即梦文生图未启用（jimeng.enabled=false 或未配置 model）")

        # 4c. 微信读书客户端（可选）
        if config.WEREAD_ENABLED and config.WEREAD_COOKIE:
            from weread import WeReadClient
            from weread.storage import WeReadStorage
            self.weread_client = WeReadClient(config.WEREAD_COOKIE)
            await self.weread_client.initialize()
            self.weread_storage = WeReadStorage(self.storage._conn)
            auth_ok = await self.weread_client.check_auth()
            if auth_ok:
                logger.info("📱 微信读书集成已启用（Cookie 有效）")
            else:
                logger.warning("⚠️  微信读书 Cookie 已过期，请更新 config.json 中的 weread.cookie_string")
        else:
            logger.info("📱 微信读书集成未启用（weread.enabled=false 或未配置 cookie_string）")

        # 5. 工具调度器（依赖 scanner 和 session_manager）
        from types import SimpleNamespace
        _deps = SimpleNamespace(
            session_manager=self.session_manager,
            scanner=self.scanner,
            memory=self.memory,
            llm=self.llm,
            timer_manager=self.timer_manager,
            feishu_pusher=None,
            feishu_chat_id="",
            weread_client=self.weread_client,
            weread_storage=self.weread_storage,
            embedder=self.embedder,
            storage=self.storage,
            knowledge_linker=self.knowledge_linker,
            jimeng_client=self.jimeng_client,
            tts_player=None,
        )
        self.tool_dispatcher = ToolDispatcher(_deps)

        # 5b. 记忆巩固器（可选）
        if config.MEMORY_CONSOLIDATION_ENABLED and self.llm:
            self.consolidator = MemoryConsolidator(
                llm=self.llm,
                embedder=self.embedder,
                storage=self.storage,
                memory=self.memory,
                memory_dir=config.MEMORY_DIR,
                debounce_min=config.MEMORY_CONSOLIDATION_DEBOUNCE_MIN,
                daily_file_enabled=config.MEMORY_DAILY_FILE_ENABLED,
                session_recall_count=config.MEMORY_SESSION_RECALL_COUNT,
            )
            # 启动定时巩固 loop
            if config.MEMORY_CONSOLIDATION_INTERVAL_MIN > 0:
                self._periodic_task = asyncio.create_task(
                    self.consolidator.start_periodic_loop(
                        config.MEMORY_CONSOLIDATION_INTERVAL_MIN
                    )
                )
            logger.info(
                f"记忆巩固器已启动（间隔 {config.MEMORY_CONSOLIDATION_INTERVAL_MIN} 分钟）"
            )
        else:
            logger.info("记忆巩固已禁用（consolidation_enabled=false 或 AI 未初始化）")

        # 6. 语音
        if config.ASR_PROVIDER == "funasr":
            from voice.funasr_asr import create_local_asr
            logger.info(f"🎙️ 使用本地 FunASR (device={config.FUNASR_DEVICE}, "
                        f"model={config.FUNASR_MODEL})，后台加载中...")
            self.asr = create_local_asr(
                device=config.FUNASR_DEVICE,
                model=config.FUNASR_MODEL,
                chunk_size_frames=config.FUNASR_CHUNK_SIZE_FRAMES,
                on_ready=self._on_asr_ready,
            )
        else:
            logger.info("🎙️ 使用阿里云 NLS ASR（云端）")
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
        self.recorder.on_interrupt = self.interrupt_ai_from_thread
        if self.scanner:
            self.scanner.set_voice_recorder(self.recorder)

        # 7. TTS（支持阿里云或 ElevenLabs）
        from tts import create_tts_player
        self.tts_player = create_tts_player(config)
        await self.tts_player.start()
        # 把 TTS 注入工具调度器（供异步工具完成后通知用户）
        self.tool_dispatcher._deps.tts_player = self.tts_player
        # 把 TTS 注入定时器（无论飞书是否启用都能播报）
        self.timer_manager.set_tts_player(self.tts_player)
        self.timer_manager.set_recorder(self.recorder)

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

            # 将飞书 pusher 注入 ToolDispatcher 和 TimerManager
            feishu_chat_id = getattr(config, "FEISHU_DEFAULT_CHAT_ID", "")
            self.tool_dispatcher.feishu_pusher = self.summary_pusher
            self.tool_dispatcher.feishu_chat_id = feishu_chat_id
            self.timer_manager.set_tts_player(self.tts_player)
            self.timer_manager.set_feishu(self.summary_pusher, feishu_chat_id)

        # 启动时异步补全历史 embedding（不阻塞启动）
        if self.embedder and self.storage:
            asyncio.create_task(self._backfill_embeddings())

        logger.info("初始化完成")

    async def _backfill_embeddings(self) -> None:
        """启动时为历史记录中缺失 embedding 的行补全（fire-and-forget）"""
        tables = ["notes", "weread_highlights", "weread_notes"]
        total = 0
        for table in tables:
            try:
                rows = await self.storage.get_rows_missing_embedding(table, limit=50)
                for row in rows:
                    try:
                        content = row.get("content", "")
                        book = row.get("book_name") or row.get("book_title", "")
                        text = f"{book} {content}".strip()
                        if not text:
                            continue
                        embedding = await self.embedder.embed(text)
                        if embedding:
                            await self.storage.save_embedding(table, row["id"], embedding)
                            total += 1
                        await asyncio.sleep(0.1)  # 避免 API 速率限制
                    except Exception as e:
                        logger.debug(f"backfill {table}#{row.get('id')}: {e}")
            except Exception as e:
                logger.warning(f"backfill 表 {table} 失败: {e}")
        if total:
            logger.info(f"✅ 启动 embedding 补全完成，共处理 {total} 条")
        else:
            logger.info("✅ 所有记录 embedding 已完整，无需补全")

    async def shutdown(self):
        """关闭所有模块"""
        if self._shutting_down:
            return
        self._shutting_down = True
        logger.info("正在关闭...")

        self._running = False

        # 取消定时巩固 task
        if self._periodic_task and not self._periodic_task.done():
            self._periodic_task.cancel()

        if self.timer_manager:
            self.timer_manager.cancel_all()
        if self._kimi_ocr:
            await self._kimi_ocr.cancel()
        if self.recorder:
            self.recorder.stop()
        if self.scanner and self.scanner.is_running():
            await self.scanner.stop()
        if self.tts_player:
            await self.tts_player.stop()
        if self.feishu_bot:
            self.feishu_bot.stop()
        if self.weread_client:
            await self.weread_client.close()

        # 进程终止时触发一次巩固（最多等待 15s，超时则跳过不影响退出）
        if self.consolidator:
            try:
                await asyncio.wait_for(
                    self.consolidator.consolidate(reason="shutdown"),
                    timeout=15.0,
                )
            except asyncio.TimeoutError:
                logger.warning("shutdown 巩固超时（15s），已跳过")
            except Exception as e:
                logger.error(f"shutdown 巩固失败: {e}")

        # 确保未满10页的 buffer 内容也被压缩保存
        if self.memory and self.memory._new_pages_buffer and self.llm and self.storage:
            logger.info("Shutdown: 压缩剩余阅读 buffer...")
            try:
                await asyncio.wait_for(self._compress_reading_digest(), timeout=8.0)
            except Exception:
                pass

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

            if config.ASR_PROVIDER == "funasr":
                # FunASR 后台加载中，就绪后由 _on_asr_ready 打印横幅
                logger.info("⏳ FunASR 模型加载中，加载完成后即可说话...")
            else:
                self._print_ready_banner()
        
        # 保持运行
        try:
            while self._running:
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            pass

        # 优雅关闭（硬超时 30 秒，超时则强制退出）
        logger.info("开始优雅关闭流程（最多等待 30 秒）...")
        try:
            await asyncio.wait_for(self.shutdown(), timeout=30.0)
        except asyncio.TimeoutError:
            logger.warning("⚠️  关闭超时（30s），强制退出")
            os._exit(1)
        except Exception as e:
            logger.error(f"关闭异常，强制退出: {e}")
            os._exit(1)
    
    async def _on_voice_text(self, text: str):
        """处理语音识别结果（异步版本）"""
        logger.info(f"👤 用户: {text}")
        # 取消旧任务（键盘中断已触发 cancel，这里做二次保险）
        if self._ai_task and not self._ai_task.done():
            self._ai_task.cancel()
        self._ai_task = asyncio.create_task(self._process_user_message(text))
    
    async def _process_user_message(self, text: str, channel: str = "voice", feishu_send_callback=None):
        """
        处理用户消息 - 流式 ReAct 多轮循环
        """
        async with self._msg_lock:
            return await self._process_user_message_inner(text, channel, feishu_send_callback)

    async def _process_user_message_inner(self, text: str, channel: str = "voice", feishu_send_callback=None):
        """
        处理用户消息核心逻辑（由 _process_user_message 持锁调用）
        """
        logger.info("=" * 60)
        logger.info("🚀 开始处理用户消息")
        logger.info(f"   输入: {text[:50]}...")
        logger.info("=" * 60)

        start_time = time.time()

        try:
            system_prompt = self.memory.build_system_prompt(user_text=text)
            history = self.memory.get_history()
            page_ctx_len = len(self.memory.current_page_ocr)
            logger.info(
                f"   书页上下文: {page_ctx_len}字" + (" ✓" if page_ctx_len else " (无)")
            )

            MAX_ROUNDS = 5
            round_count = 0
            reply_parts = []
            pending_tool_rounds = []  # 本轮工具调用暂存，用于轮结束后按序写入 history
            first_tts_enqueue_time: Optional[float] = None  # speak() 首次调用时间

            # 重置 TTS 时间戳（仅 voice 通道且播放器支持）
            if channel == "voice" and self.tts_player and hasattr(self.tts_player, "reset_timing"):
                self.tts_player.reset_timing()

            # ── Sub Agent 并行路径 ────────────────────────────────────────────
            # Sub Agent 始终启动，LLM 自己决定是否需要工具；正则仅在 LLM 超时时兜底
            t_sub_agent_start = time.time()
            all_tools = self.tool_registry.get_tools()
            sub_agent_task = asyncio.create_task(
                self.sub_agent.run(
                    user_text=text,
                    tools=all_tools,
                    history=history,
                    memory=self.memory,
                    llm=self.sub_agent_llm,
                    tool_dispatcher=self.tool_dispatcher,
                    page_context=self.memory.current_page_ocr or None,
                )
            )
            # Main LLM 立即开始流式（不携带 tool schemas，不阻塞于工具执行）
            system_prompt_r1 = (
                system_prompt
                + "\n\n## 当前情况"
                + "\n你的助手正在后台帮你执行操作，你先开口回应用户。"
                + "\n- 不管是操作类（定时器、书签、进度）还是查询类（书签列表、笔记、统计）："
                + "只说你在做什么，不要假装已经完成或假装已经拿到结果。"
                + "\n  例：「好的，我来定个时」「稍等，查一下书签」「帮你记一下」"
                + "\n- 不要复述用户刚说过的具体内容（书名、时间、数字等），越短越好。"
                + "\n- 不要编造结果：不说「已设置好」「已记录」「已查到」，结果由助手查完后告诉你。"
                + "\n- 纯聊天（无工具触发）：正常完整回复。"
            )
            stream_kwargs = dict(
                user_message=text,
                system_prompt=system_prompt_r1,
                history=history,
                tools=[],
            )

            # ── 流式 Main LLM Round 1 ─────────────────────────────────────
            round_count += 1
            tts_buf = ""
            t_r1_start = time.time()
            stream_start = t_r1_start
            logger.info(f"⏱️  [R1] 开始流式 (SubAgent已并行启动 +{(t_r1_start - t_sub_agent_start)*1000:.0f}ms)")
            first_token_time = None
            total_chars = 0
            chars_100_time = None
            first_tts_sent = False

            async for chunk in self.llm.chat_stream(**stream_kwargs):
                if chunk.type == "text_delta":
                    if first_token_time is None:
                        first_token_time = time.time()
                        logger.info(
                            f"🚀 [Main/R1] 流式首字: {(first_token_time - stream_start)*1000:.0f}ms"
                        )
                    total_chars += len(chunk.content)
                    if chars_100_time is None and total_chars >= 100:
                        chars_100_time = time.time()
                    tts_buf += chunk.content
                    min_c = _TTS_FIRST_MIN_CHARS if not first_tts_sent else _TTS_MIN_CHARS
                    chunk_to_send, tts_buf = _extract_tts_chunk(tts_buf, min_chars=min_c)
                    if chunk_to_send:
                        first_tts_sent = True
                        if first_tts_enqueue_time is None:
                            first_tts_enqueue_time = time.time()
                        reply_parts.append(chunk_to_send)
                        if channel == "voice":
                            await self.tts_player.speak(chunk_to_send, interrupt=False)
                # Sub Agent 路径下 Main LLM 不输出 tool_use，忽略其他 chunk 类型

            # flush Round 1 剩余
            tail, _ = _extract_tts_chunk(tts_buf, force=True)
            if tail:
                if first_tts_enqueue_time is None:
                    first_tts_enqueue_time = time.time()
                reply_parts.append(tail)
                if channel == "voice":
                    await self.tts_player.speak(tail, interrupt=False)
            tts_buf = ""
            r1_parts_count = len(reply_parts)  # R1 结束时的 reply_parts 边界
            r1_first_token_time = first_token_time  # 保存 R1 首字时间，避免被 R2 循环覆盖

            # 飞书通道：R1 完成后立即发出，不等工具结果
            if feishu_send_callback:
                r1_text_early = "".join(reply_parts)
                if r1_text_early.strip():
                    await feishu_send_callback(r1_text_early)

            _chars100_str = (
                f"{((chars_100_time - stream_start)*1000):.0f}ms"
                if chars_100_time else "N/A"
            )
            logger.info(
                f"📊 [Main/R1] 流式: 共{total_chars}字, "
                f"首字={(((first_token_time or 0) - stream_start)*1000):.0f}ms, "
                f"百字={_chars100_str}"
            )
            t_r1_end = time.time()
            logger.info(
                f"⏱️  [R1] LLM流式结束 耗时={(t_r1_end - t_r1_start)*1000:.0f}ms | "
                f"共{total_chars}字 | R1输出=「{(''.join(reply_parts))[:40]}...」"
            )
            logger.info(f"⏱️  [R1→R2] 等待 Sub Agent 结果... (SubAgent已运行 {(t_r1_end - t_sub_agent_start)*1000:.0f}ms)")

            # 等待 Sub Agent 完成
            sub_agent_results = await sub_agent_task
            t_sub_agent_done = time.time()
            logger.info(f"⏱️  [SubAgent] 完成，等待耗时={(t_sub_agent_done - t_r1_end)*1000:.0f}ms | 总耗时={(t_sub_agent_done - t_sub_agent_start)*1000:.0f}ms")

            # 超时标志检测
            _subagent_timed_out = isinstance(sub_agent_results, dict) and sub_agent_results.get("__timeout__")
            if _subagent_timed_out:
                sub_agent_results = None

            # ── 工具结果契约 ────────────────────────────────────────────────────
            # Sync 工具（数据已就绪）：返回 {"success": True/False, ...data...}
            # Async 工具（后台已启动）：返回 {"status": "ok", "message": "正在..."}
            #   - async 工具立即返回 ack，真正的产物（图片/推送）由后台 task 完成
            #   - R1 已经宣布了"我来做"，R2 对 async ack 没有任何新内容可说
            #   - 混合场景（sync + async）：R2 只拿到 sync 数据，async ack 不进 tool_ctx
            # ──────────────────────────────────────────────────────────────────
            def _is_async_ack(r: dict) -> bool:
                return isinstance(r, dict) and r.get("status") == "ok" and "success" not in r

            if sub_agent_results:
                executed_names = list(sub_agent_results.keys())
                sync_results = {k: v for k, v in sub_agent_results.items() if not _is_async_ack(v)}
                async_names  = [k for k, v in sub_agent_results.items() if _is_async_ack(v)]

            if sub_agent_results and sync_results:
                # ── 有 sync 数据 → R2 汇报（async ack 已被过滤，不进 tool_ctx）──
                tool_ctx = _format_tool_results(sync_results)
                round1_text = "".join(reply_parts)
                if async_names:
                    logger.info(f"[SubAgent] 执行完成: {executed_names} | async跳过={async_names} | tool_ctx={len(tool_ctx)}字")
                else:
                    logger.info(f"[SubAgent] 执行完成: {executed_names} | tool_ctx={len(tool_ctx)}字")
                logger.info(f"⏱️  [R2] 准备启动 | R1说了=「{round1_text[:40]}...」")
                enriched_prompt = (
                    system_prompt
                    + f"\n\n## 你刚才对用户说的话（R1）\n{round1_text}"
                    + "\n\n## 你的助手执行工具后的真实结果\n" + tool_ctx
                    + "\n\n## 静默规则（优先级最高）"
                    + "\n对比 R1 已说过的话和工具返回的结果："
                    + "\n- 若工具结果中有 R1 未提及的具体数据（书签内容、划线列表、统计数字等）→ 一句话说出新数据"
                    + "\n- 若工具结果仅是操作成功确认，且 R1 已经表达了承诺或确认（「定好了」「帮你记」「稍等查」等）→ 只输出 [SILENT]，不要任何其他内容"
                    + "\n- 不要说「好的」「已设置」「完成了」等对 R1 的语义重复"
                    + "\n\n请自然地接着你刚才说的话，把真实结果告诉用户。"
                    + "\n- 操作成功：「定好了」「书签记上了」等简短确认，一句话"
                    + "\n- 操作失败：告知失败原因"
                    + "\n- 查询结果：直接说数据内容"
                    + "\n- 若工具返回的是概览/书单但用户明显要某本书的具体内容：告诉用户找到了这本书但详情没拿到，建议重新问（如「你可以说帮我查《xxx》的划线」）"
                    + "\n- 不要说「这就查」「马上查」「接下来查」等暗示自己会再次查询的话，你没有再次执行工具的能力"
                    + "\n不要重新开口（不要说「好的」「以下是」），不要复述 R1 已说过的内容。"
                )
                stream_kwargs = dict(
                    user_message=text,
                    system_prompt=enriched_prompt,
                    history=history,
                    tools=[],
                )
            elif sub_agent_results and async_names:
                # ── 全为 async ack，R1 已覆盖，跳过 R2 ──
                logger.info(f"[SubAgent] 执行完成: {executed_names} | 全为异步启动任务，R1 已覆盖，跳过 R2")
                stream_kwargs = None
            elif _subagent_timed_out:
                # ── SubAgent 超时，R2 告知用户 ──
                logger.warning("[SubAgent] 超时，启动 R2 告知用户")
                round1_text = "".join(reply_parts)
                timeout_prompt = (
                    system_prompt
                    + f"\n\n## 你刚才对用户说的话（R1）\n{round1_text}"
                    + "\n\n## 情况说明\n后台工具查询超时，未能获取数据。"
                    + "\n\n请用一句话告诉用户查询超时，请他再试一次。不要说「好的」或重复 R1 已说的内容。"
                )
                stream_kwargs = dict(
                    user_message=text,
                    system_prompt=timeout_prompt,
                    history=history,
                    tools=[],
                )
            else:
                logger.info("[SubAgent] 未调用工具，R1 为最终回复")
                stream_kwargs = None

            while round_count < MAX_ROUNDS:
                if stream_kwargs is None:
                    break
                round_count += 1
                tts_buf = ""
                tool_calls = None
                raw_assistant_msg = None

                # 性能打点
                t_rN_start = time.time()
                stream_start = t_rN_start
                first_token_time = None
                total_chars = 0
                chars_100_time = None
                logger.info(f"⏱️  [R{round_count}] 开始流式")

                first_tts_sent = False  # 首段是否已发出（首段用低门槛快开口）

                async for chunk in self.llm.chat_stream(**stream_kwargs):
                    if chunk.type == "text_delta":
                        if first_token_time is None:
                            first_token_time = time.time()
                            logger.info(f"🚀 [R{round_count}] 流式首字: {(first_token_time - stream_start)*1000:.0f}ms")
                        total_chars += len(chunk.content)
                        if chars_100_time is None and total_chars >= 100:
                            chars_100_time = time.time()
                            logger.info(f"📊 流式100字: {(chars_100_time - stream_start)*1000:.0f}ms")

                        tts_buf += chunk.content
                        # 首段用低门槛（快开口），后续段用正常门槛
                        min_c = _TTS_FIRST_MIN_CHARS if not first_tts_sent else _TTS_MIN_CHARS
                        chunk_to_send, tts_buf = _extract_tts_chunk(tts_buf, min_chars=min_c)
                        if chunk_to_send:
                            first_tts_sent = True
                            if first_tts_enqueue_time is None:
                                first_tts_enqueue_time = time.time()
                            reply_parts.append(chunk_to_send)
                            if channel == "voice":
                                await self.tts_player.speak(chunk_to_send, interrupt=False)

                    elif chunk.type == "tool_use":
                        # flush 剩余文本（如"好的，我来查一下"）
                        if tts_buf.strip():
                            reply_parts.append(tts_buf)
                            if channel == "voice":
                                await self.tts_player.speak(tts_buf, interrupt=False)
                            tts_buf = ""
                        tool_calls = chunk.tool_calls
                        raw_assistant_msg = chunk.raw_assistant_msg

                    # "done" chunk 不需要处理

                # 流结束后 flush 剩余
                if not tool_calls:
                    # R2 静默机制：[SILENT] 哨兵 token → 跳过 TTS，不写入 reply_parts
                    if round_count >= 2 and tts_buf.strip() == "[SILENT]":
                        logger.info("[R2] 输出 [SILENT]，跳过 TTS 和 history 写入")
                        tts_buf = ""
                    else:
                        tail, _ = _extract_tts_chunk(tts_buf, force=True)
                        if tail:
                            reply_parts.append(tail)
                            if channel == "voice":
                                await self.tts_player.speak(tail, interrupt=False)

                _chars100_str = (
                    f"{((chars_100_time - stream_start)*1000):.0f}ms"
                    if chars_100_time else "N/A"
                )
                logger.info(
                    f"📊 第{round_count}轮流式: 共{total_chars}字, "
                    f"首字={(((first_token_time or 0) - stream_start)*1000):.0f}ms, "
                    f"百字={_chars100_str}"
                )

                # R2 传入 tools=[]，Main LLM 不会输出 tool_use，直接退出
                break

            reply_text = "".join(reply_parts)
            r1_text_final = "".join(reply_parts[:r1_parts_count])
            r2_text_final = "".join(reply_parts[r1_parts_count:])

            # 飞书通道：R2 完成后发出（若有内容）
            if feishu_send_callback and r2_text_final.strip():
                await feishu_send_callback(r2_text_final)

            # 打印 AI 回复内容（R1/R2 分开）
            logger.info("=" * 60)
            logger.info(f"🤖 [R1] 内容（{len(r1_text_final)}字）:")
            logger.info("-" * 60)
            for line in r1_text_final.split('\n'):
                while line:
                    logger.info(f"  {line[:58]}")
                    line = line[58:]
            if r2_text_final:
                logger.info("-" * 60)
                logger.info(f"🤖 [R2] 内容（{len(r2_text_final)}字）:")
                logger.info("-" * 60)
                for line in r2_text_final.split('\n'):
                    while line:
                        logger.info(f"  {line[:58]}")
                        line = line[58:]
            elif round_count >= 2:
                logger.info("-" * 60)
                logger.info("🤖 [R2] 静默（R2 无输出）")
            logger.info("-" * 60)
            logger.info(f"📊 回复长度: {len(reply_text)} 字符, 共 {round_count} 轮")
            logger.info("=" * 60)

            # 按正确顺序写入对话历史：user → tool轮... → assistant最终回复
            self.memory.add_message("user", text)
            for raw_msg, results in pending_tool_rounds:
                self.memory.add_tool_round(raw_msg, results)
            self.memory.add_message("assistant", reply_text)

            # fire-and-forget：预取下一轮相关记忆（零延迟，回答完毕后异步执行）
            self.memory.trigger_prefetch(text)

            end_time = time.time()

            # ── 全链路时间轴 summary ───────────────────────────────────────────
            ref = start_time

            def _ms(t: Optional[float]) -> str:
                return f"+{(t - ref) * 1000:6.0f} ms" if t else "  (待测) "

            # 从 TTS 播放器读取时间戳（仅 DoubaoTTSPlayer 支持）
            tp = self.tts_player if channel == "voice" else None
            tts_synth_start  = getattr(tp, "first_synth_start",  None)
            tts_synth_end    = getattr(tp, "first_synth_end",    None)
            tts_play_start   = getattr(tp, "first_play_start",   None)

            # 首字→开口 的端到端延迟
            e2e_ms = (
                f"{(tts_play_start - ref) * 1000:.0f} ms"
                if tts_play_start else "(待测)"
            )

            logger.info("=" * 60)
            logger.info("📊 全链路延迟（从收到用户消息开始）")
            logger.info("-" * 60)
            logger.info(f"  LLM 首字出现:   {_ms(r1_first_token_time)}  ← R1 开始生成")
            logger.info(f"  TTS 文本入队:   {_ms(first_tts_enqueue_time)}  ← 首段文字送出")
            logger.info(f"  TTS 开始合成:   {_ms(tts_synth_start)}  ← synth_worker 拾取")
            logger.info(f"  TTS 合成完成:   {_ms(tts_synth_end)}  ← 首段音频就绪")
            logger.info(f"  TTS 开始播放:   {_ms(tts_play_start)}  ← 用户听到首字")
            logger.info("-" * 60)
            logger.info(f"  首字→开口延迟:  {e2e_ms}")
            logger.info(f"  全程总耗时:    +{(end_time - ref) * 1000:6.0f} ms")
            logger.info("=" * 60)

        except asyncio.CancelledError:
            if 'sub_agent_task' in locals() and not sub_agent_task.done():
                sub_agent_task.cancel()
            logger.info("🛑 AI 处理被用户打断")
            raise  # 必须重新抛出，让 Task 正常结束
        except Exception as e:
            logger.error(f"处理消息失败: {e}")
            if channel == "voice":
                await self.tts_player.speak("抱歉，处理时出错了", interrupt=True)
            return ""

        return reply_text

    async def _handle_feishu_message(self, text: str, channel: str = "feishu", chat_id: str = "", send_callback=None) -> str:
        """处理飞书消息"""
        # 动态更新 chat_id：首条消息即可获得真实会话 ID，后续定时器推送可用
        if chat_id and self.summary_pusher:
            if self.timer_manager and self.timer_manager._feishu_chat_id != chat_id:
                self.timer_manager.set_feishu(self.summary_pusher, chat_id)
                logger.debug(f"飞书 chat_id 已更新: {chat_id}")
            if self.tool_dispatcher:
                self.tool_dispatcher.feishu_chat_id = chat_id
        return await self._process_user_message(text, channel="feishu", feishu_send_callback=send_callback)
    
    @staticmethod
    def _is_content_page_turn(old_text: str, new_text: str) -> bool:
        """基于 OCR 文本内容比对判断是否翻页（Jaccard 字符集相似度）"""
        if not old_text:
            return True  # 首次有内容，算第一页
        # 取前 150 字比较（页首最稳定，避免 OCR 尾部噪声）
        old_chars = set(old_text[:150])
        new_chars = set(new_text[:150])
        if not old_chars or not new_chars:
            return True
        jaccard = len(old_chars & new_chars) / len(old_chars | new_chars)
        return jaccard < 0.4  # 相似度 < 40% 视为新页

    def _on_book_detected(self, book_info: dict, image_path: str = ""):
        """OCR 元数据回调：记录阅读活动 + 更新书籍上下文"""
        import time as _time
        book_title = book_info.get("book_title", "")
        page_num = book_info.get("page_num", -1)
        if isinstance(page_num, list):
            page_num = page_num[0] if page_num else -1
        if page_num is None:
            page_num = -1
        ocr_chars = book_info.get("ocr_chars", 0)

        # is_reading=false 时不记录阅读活动（书合上/空桌面）
        is_reading = book_info.get("is_reading", True)
        if not is_reading:
            logger.debug("📖 画面非阅读状态（is_reading=false），跳过记录")
            return

        # 每帧可见页数（书摊开双页=2，单页=1）
        visible_pages = book_info.get("visible_pages", 1)
        if not isinstance(visible_pages, int) or visible_pages not in (1, 2):
            visible_pages = 1

        # 内容比对翻页检测：当前 OCR 文本 vs 上一次
        current_text = self.memory.current_page_ocr if self.memory else ""
        pages_turned = 0
        if current_text:
            if self._is_content_page_turn(self._last_page_text, current_text):
                pages_turned = visible_pages  # 双页拍摄翻一次 = 2 页
                self._last_page_text = current_text
                logger.info(f"📄 内容比对检测到翻页（+{visible_pages} 页）")
            elif not self._last_page_text:
                self._last_page_text = current_text

        now_ts = int(_time.time() * 1000)

        # 每次 is_reading=true 都记录到 reading_activity（用于计算阅读时长）
        if self.session_manager:
            asyncio.create_task(
                self.session_manager.record_reading_activity(
                    ts=now_ts,
                    page_num=page_num if page_num > 0 else None,
                    book_title=book_title,
                    ocr_chars=ocr_chars,
                    is_page_turn=pages_turned,
                )
            )

        # 翻页时：将完整书页内容持久化到 reading_pages
        if pages_turned > 0 and current_text and self.session_manager:
            chapter = book_info.get("chapter", "")
            asyncio.create_task(
                self.session_manager.record_reading_page(
                    ts=now_ts,
                    book_title=book_title,
                    page_num=page_num,
                    chapter=chapter,
                    visible_pages=visible_pages,
                    ocr_text=current_text,
                )
            )

        # 更新书籍上下文（仅供显示，不用于逻辑判断）
        confidence = book_info.get("confidence", 0)
        if book_title and confidence >= 0.7:
            self.memory.update_book_context(book_info)
            logger.info(f"📚 书名已识别: 《{book_title}》（置信度 {confidence:.2f}）")

    def _print_ready_banner(self):
        logger.info("=" * 60)
        logger.info("🎉 AI 读书搭子已启动！")
        logger.info(f"🤖 AI 提供商: {config.AI_PROVIDER}")
        logger.info(f"🤖 AI 模型: {config.CURRENT_MODEL}")
        logger.info(f"🔊 TTS 提供商: {config.TTS_PROVIDER}")
        logger.info("按住 【右 Alt 键】说话与 AI 交流")
        logger.info("=" * 60)

    def _on_asr_ready(self, elapsed: float):
        """FunASR 后台加载完成回调"""
        logger.info(f"🟢 FunASR 已就绪（加载耗时 {elapsed:.1f}s）")
        self._print_ready_banner()

    def _do_interrupt_in_loop(self):
        """在事件循环线程中执行打断（由 call_soon_threadsafe 调度）"""
        if self.tts_player:
            self.tts_player.interrupt()
        if self._ai_task and not self._ai_task.done():
            self._ai_task.cancel()
        logger.info("🛑 [打断] 已取消 AI 任务并打断 TTS")

    def interrupt_ai_from_thread(self):
        """从键盘监听线程安全触发打断"""
        if self.loop:
            self.loop.call_soon_threadsafe(self._do_interrupt_in_loop)

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

        # 阅读内容积累：去重 + 计数 + 触发压缩
        if self.memory and len(ocr_text.strip()) > 80:
            ocr_hash = hash(ocr_text[:200])
            if ocr_hash != self.memory._last_buffer_hash:
                self.memory._last_buffer_hash = ocr_hash
                page_num = self.memory.current_book_context.get("current_page_num", 0)
                preview = ocr_text[:300].replace('\n', ' ')
                self.memory._new_pages_buffer.append(f"第{page_num}页: {preview}")
                self.memory._valid_page_count += 1
                if self.memory._valid_page_count % 5 == 0:
                    asyncio.create_task(self._compress_reading_digest())
    
    async def _compress_reading_digest(self):
        """将 buffer 中的新页内容压缩融入 session_reading_digest（fire-and-forget）"""
        if not self.memory or not self.memory._new_pages_buffer:
            return
        new_pages = "\n".join(self.memory._new_pages_buffer)
        current = self.memory.session_reading_digest
        book_title = self.memory.current_book_context.get("book_title", "")

        prompt = (
            "请将以下阅读进度记录更新压缩（300字以内），保留内容脉络和重要概念，"
            "直接输出压缩后的记录，不要说明：\n"
            + (f"当前摘要：\n{current}\n\n" if current else "")
            + f"新读到的书页：\n{new_pages}"
        )
        try:
            resp = await asyncio.wait_for(
                self.llm.chat(user_message=prompt, max_tokens=400),
                timeout=8.0,
            )
            if resp and resp.text:
                self.memory.session_reading_digest = resp.text.strip()
                self.memory._new_pages_buffer = []
                await self.storage.save_reading_digest(
                    self.memory.session_reading_digest,
                    book_title=book_title,
                )
                logger.info(f"阅读摘要已更新并持久化（{len(self.memory.session_reading_digest)}字）")
        except Exception as e:
            logger.debug(f"阅读摘要压缩失败（已降级）: {e}")

    async def _check_and_push_feishu(self):
        """检查并推送飞书总结"""
        if not self.feishu_bot or not self.summary_pusher:
            return


async def main():
    """入口函数"""
    app = ReadingCompanion()
    loop = asyncio.get_running_loop()

    # asyncio 原生信号处理（在事件循环线程内执行，无竞争）
    _signal_count = 0

    def _on_signal():
        nonlocal _signal_count
        _signal_count += 1
        if _signal_count == 1:
            logger.info("收到退出信号，正在优雅关闭（再按 Ctrl+C 可强制退出）...")
            app._running = False  # 唤醒主循环，进入 shutdown 流程
        else:
            logger.warning("收到强制退出信号，立即终止！")
            os._exit(1)

    loop.add_signal_handler(signal.SIGINT, _on_signal)
    loop.add_signal_handler(signal.SIGTERM, _on_signal)

    try:
        await app.run()
    except Exception as e:
        logger.exception("程序异常退出")
        raise


if __name__ == "__main__":
    asyncio.run(main())
