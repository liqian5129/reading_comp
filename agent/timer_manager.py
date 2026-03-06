"""
阅读定时器管理器
支持设定 N 分钟后的提醒，通过 TTS 播报 + 可选飞书推送
"""
import asyncio
import logging
from typing import Optional, Callable, Awaitable, Dict

logger = logging.getLogger(__name__)


class ReadingTimerManager:
    """
    管理阅读提醒定时器
    """

    def __init__(self):
        self._tasks: Dict[int, asyncio.Task] = {}  # timer_id -> Task
        self._next_id = 1
        self._tts_player = None        # 由外部注入
        self._recorder = None          # 由外部注入（VoiceRecorder）
        self._feishu_pusher = None     # 由外部注入
        self._feishu_chat_id: str = ""

    def set_tts_player(self, player):
        self._tts_player = player

    def set_recorder(self, recorder):
        self._recorder = recorder

    def set_feishu(self, pusher, chat_id: str):
        self._feishu_pusher = pusher
        self._feishu_chat_id = chat_id

    async def set_timer(
        self,
        minutes: int,
        message: str = "",
        feishu_push: bool = False,
        on_fire: Optional[Callable[[], Awaitable]] = None,
    ) -> int:
        """
        设定定时提醒

        Args:
            minutes: 多少分钟后触发
            message: 提醒内容（为空时使用默认文案）
            feishu_push: 是否同步推送飞书提醒卡
            on_fire: 触发时额外执行的 async 回调（可读取触发时刻的最新状态）

        Returns:
            timer_id（可用于取消）
        """
        if not message and not on_fire:
            message = f"已经过去 {minutes} 分钟了，是时候活动一下，保护眼睛！"

        timer_id = self._next_id
        self._next_id += 1

        task = asyncio.create_task(
            self._run_timer(timer_id, minutes, message, feishu_push, on_fire)
        )
        self._tasks[timer_id] = task
        logger.info(f"⏰ 定时器已设置: {timer_id}，{minutes} 分钟后触发")
        return timer_id

    async def _run_timer(
        self,
        timer_id: int,
        minutes: int,
        message: str,
        feishu_push: bool,
        on_fire: Optional[Callable[[], Awaitable]] = None,
    ):
        """等待指定时间后执行提醒"""
        try:
            await asyncio.sleep(minutes * 60)

            logger.info(f"⏰ 定时器触发: {timer_id} - {message}")

            # TTS 播报（等待 TTS 播放结束 & 用户未在录音时再播）
            if self._tts_player and message:
                try:
                    wait_timeout = 120  # 最多等 120s
                    waited = 0.0
                    interval = 0.3
                    while waited < wait_timeout:
                        tts_busy = self._tts_player.is_playing() if hasattr(self._tts_player, "is_playing") else False
                        rec_busy = self._recorder.is_busy() if self._recorder and hasattr(self._recorder, "is_busy") else False
                        if not tts_busy and not rec_busy:
                            break
                        await asyncio.sleep(interval)
                        waited += interval
                    if waited >= wait_timeout:
                        logger.warning(f"⏰ 定时器 {timer_id}: 等待播放窗口超时，强制播报")
                    await self._tts_player.speak(message, interrupt=False)
                except Exception as e:
                    logger.error(f"TTS 播报失败: {e}")

            # 飞书提醒卡
            if feishu_push:
                if self._feishu_pusher and self._feishu_chat_id:
                    try:
                        await self._feishu_pusher.push_timer_alert(
                            self._feishu_chat_id, message, minutes
                        )
                        logger.info(f"⏰ 飞书推送成功: {timer_id}")
                    except Exception as e:
                        logger.error(f"飞书推送失败: {e}")
                else:
                    logger.warning(
                        f"⏰ 定时器 {timer_id}: feishu_push=True 但飞书未配置或 chat_id 为空，跳过推送"
                    )

            # 自定义动作（读取触发时刻最新状态）
            if on_fire:
                try:
                    await on_fire()
                except Exception as e:
                    logger.error(f"⏰ 定时器 {timer_id} on_fire 执行失败: {e}")

        except asyncio.CancelledError:
            logger.info(f"⏰ 定时器已取消: {timer_id}")
        finally:
            self._tasks.pop(timer_id, None)

    def cancel_timer(self, timer_id: int) -> bool:
        """取消指定定时器"""
        task = self._tasks.get(timer_id)
        if task and not task.done():
            task.cancel()
            return True
        return False

    def cancel_all(self):
        """取消所有定时器（程序退出时调用）"""
        for task in list(self._tasks.values()):
            if not task.done():
                task.cancel()
        self._tasks.clear()
        logger.info("所有定时器已取消")

    def list_timers(self) -> list:
        """返回当前活跃定时器列表"""
        return [tid for tid, t in self._tasks.items() if not t.done()]
