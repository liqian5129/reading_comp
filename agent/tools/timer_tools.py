"""
定时器工具：set_timer
"""
import logging
from typing import Dict

logger = logging.getLogger(__name__)


class TimerTools:
    def __init__(self, deps):
        self.deps = deps

    async def exec_set_timer(self, params: Dict) -> Dict:
        """设定定时提醒"""
        minutes = params.get("minutes", 0)
        if not minutes or minutes <= 0:
            return {"success": False, "error": "请指定正确的分钟数"}

        message = params.get("message", "")
        feishu_push = params.get("feishu_push", False)

        if not self.deps.timer_manager:
            return {"success": False, "error": "定时器模块未初始化"}

        # 构建"触发时发送书页内容"的闭包，捕获触发时刻最新 OCR 内容
        on_fire = None
        if params.get("send_current_page"):
            _pusher = self.deps.feishu_pusher
            _chat_id = self.deps.feishu_chat_id
            _memory = self.deps.memory
            if not _pusher or not _chat_id:
                return {"success": False, "error": "send_current_page 需要飞书配置，请先确认飞书已启用且 chat_id 有效"}

            async def _send_page_content():
                content = _memory.current_page_ocr
                if not content:
                    logger.warning("⏰ 定时器触发：书页 OCR 内容为空，跳过发送")
                    return
                await _pusher.push_text(_chat_id, content)
                logger.info("⏰ 书页内容已发送到飞书")

            on_fire = _send_page_content
            if not message:
                message = "书页内容已推送到飞书"

        timer_id = await self.deps.timer_manager.set_timer(
            minutes=minutes, message=message, feishu_push=feishu_push, on_fire=on_fire
        )

        desc = []
        if feishu_push:
            desc.append("飞书提醒卡")
        if on_fire:
            desc.append("发送当前书页内容到飞书")
        action_hint = f"（{', '.join(desc)}）" if desc else ""

        return {
            "success": True,
            "message": f"已设定 {minutes} 分钟后触发{action_hint}",
            "timer_id": timer_id,
            "minutes": minutes,
        }
