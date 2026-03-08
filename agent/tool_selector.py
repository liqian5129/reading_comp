"""
LLM 驱动的工具选择器

两阶段调用：
  Stage 0 — 用轻量工具目录让 LLM 返回需要哪些工具（tiny prompt，无完整 schema）
  Stage 1 — 只加载选中工具的完整 schema 进行正式调用

边界情况：
  - 多工具请求：LLM 可返回多个，全部加载
  - 无需工具：返回 []，Stage 1 以纯文本回答
  - 超时/解析失败：返回 None，调用方降级为 []（纯文本回答）
  - LLM 幻觉出未知工具名：过滤掉，不影响已知工具
"""
import asyncio
import json
import logging
import re
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# ── 轻量工具目录：name → 一句话说明 ──────────────────────────────────────────
TOOL_CATALOG: Dict[str, str] = {
    "reading_note":            "保存读书笔记（摘抄/感悟/联想）到本地，支持多句合并为一条",
    "reading_notes":           "查看/列出本地保存的历史笔记，可按书名或时间过滤",
    "note_search":             "用语义搜索查找本地笔记和微信读书划线中的相关内容",
    "bookmark_create":         "在本 App 创建书签，标记当前阅读位置（非微信读书书签）",
    "bookmark_list":           "查看本 App 保存的书签列表（仅限本 App 手动创建，非微信读书书签）",
    "reading_progress_update": "更新本地记录的阅读进度（页码/完成状态）",
    "reading_progress_query":  "查询本地记录的阅读进度",
    "reading_list_manage":     "管理个人书单（添加想读/查看/标记完成或在读/移除）",
    "reading_stats":           "查询阅读统计汇总（翻页数/时长/笔记数），可按今天/本周/本月/全部",
    "reading_history":         "查询每次阅读会话的详细记录（每条含具体时长和翻页数）",
    "set_timer":               "设置倒计时提醒；触发时可 TTS 播报、发飞书卡片或推送当前书页内容",
    "generate_reading_card":   "生成金句/知识点/摘要卡片推送飞书；可整理今天/这周/这个月阅读的内容摘要发飞书；可自动取微信读书划线或当前书页 OCR",
    "feishu_send_message":     "发送任意文本消息到飞书（卡片推送请用 generate_reading_card）",
    "weread_shelf":            "查看微信读书书架（书目列表和阅读进度概览）",
    "weread_notebook":         "列出微信读书中有笔记的书单（含划线数和想法数）",
    "weread_get_notes":        "获取某书的微信读书书签/划线/想法/点评（微信读书的书签在这里，不在 bookmark_list）",
    "weread_progress":         "查询微信读书某书的阅读进度（百分比和时长）",
    "weread_best_highlights":  "获取某书在微信读书上的热门划线（所有读者共同标注的精华句）",
    "weread_merge_notes":      "合并某书的微信读书划线/想法与本地笔记，生成综合摘要，可推飞书",
}

_CATALOG_TEXT = "\n".join(f"- {k}: {v}" for k, v in TOOL_CATALOG.items())

_SELECTION_SYSTEM = f"""你是工具路由器，只负责判断需要调用哪些工具。

可用工具：
{_CATALOG_TEXT}

规则：
- 分析用户请求，列出需要调用的工具名（可多个）
- 若用户只是提问/聊天/让你解释内容，无需任何工具，tools 返回空列表
- 只输出 JSON，不要有任何其他文字

输出格式：{{"tools": ["tool_name1", "tool_name2"]}}"""


# ── 简单工具执行后直接返回固定文案，跳过 Round 2 ─────────────────────────────
# None 表示从工具返回的 result["message"] 取文案
SIMPLE_TOOL_REPLIES: Dict[str, Optional[str]] = {
    # reading_note 不在此列：用户分享想法时需要 AI 温暖回应，走 Round 2
    "set_timer":               None,
    "bookmark_create":         "书签已保存。",
    "reading_progress_update": "进度已更新。",
    "reading_list_manage":     None,
}


# ── 超时兜底：仅用于 LLM 超时时的最后安全网 ─────────────────────────────────
# key: 工具名  value: 触发关键词列表（命中任意一个即选该工具）
_TIMEOUT_FALLBACK: Dict[str, List[str]] = {
    "generate_reading_card": ["整理今天", "总结发飞书", "阅读摘要", "摘要发飞书", "读的内容发飞书", "读的内容整理"],
    "reading_stats":   ["读了多久", "多长时间", "阅读时间", "阅读时长", "读了多少页", "翻了多少", "统计", "今天读了", "这周读", "本周读", "本月读"],
    "reading_history": ["阅读记录", "读书记录", "历史记录", "读了什么"],
    "reading_notes":   ["我的笔记", "查笔记", "看笔记", "列笔记"],
    "set_timer":       ["提醒", "定时", "分钟后", "倒计时"],
}


def _timeout_fallback(user_message: str) -> List[str]:
    """LLM 超时时按关键词匹配最多一个工具，完全匹配不到则返回 []。"""
    for tool, keywords in _TIMEOUT_FALLBACK.items():
        if any(kw in user_message for kw in keywords):
            logger.info(f"[ToolSelector] 超时兜底：关键词命中 → [{tool}]")
            return [tool]
    return []


class ToolSelector:
    """
    LLM 驱动的工具选择器（Stage 0）。

    select() 返回值：
      []    → 无需工具，纯文本回答
      [...]  → 需要这些工具，加载完整 schema
      None  → 选择失败，调用方降级为 []（纯文本回答）
    """

    TIMEOUT_S = 15.0

    def __init__(self, llm_client):
        self._llm = llm_client

    async def select(self, user_message: str) -> Optional[List[str]]:
        """发起 Stage 0 选择请求，失败时尝试关键词兜底。"""
        try:
            resp = await asyncio.wait_for(
                self._llm.chat(
                    user_message=user_message,
                    system_prompt=_SELECTION_SYSTEM,
                    max_tokens=100,
                ),
                timeout=self.TIMEOUT_S,
            )
            result = self._parse(resp.text)
            logger.info(f"[ToolSelector] '{user_message[:40]}' → {result}")
            return result
        except asyncio.TimeoutError:
            fallback = _timeout_fallback(user_message)
            logger.warning(f"[ToolSelector] 超时 ({self.TIMEOUT_S}s)，关键词兜底 → {fallback or '纯文本'}")
            return fallback if fallback else None
        except Exception as e:
            logger.warning(f"[ToolSelector] 异常，降级纯文本回答: {e}")
            return None

    def _parse(self, text: str) -> Optional[List[str]]:
        """
        解析 LLM 返回的 JSON。容错：
          - JSON 前后有多余文字 → 正则提取
          - 工具名不在目录 → 过滤（防幻觉）
          - 解析失败 → 返回 None 触发降级
        """
        if not text:
            return None
        try:
            match = re.search(r'\{[^{}]*\}', text, re.DOTALL)
            if not match:
                logger.warning(f"[ToolSelector] 未找到 JSON: {text[:80]}")
                return None
            data = json.loads(match.group())
            raw: List = data.get("tools", [])
            if not isinstance(raw, list):
                return None
            valid = [t for t in raw if t in TOOL_CATALOG]
            unknown = set(raw) - set(valid)
            if unknown:
                logger.warning(f"[ToolSelector] 过滤幻觉工具名: {unknown}")
            return valid
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"[ToolSelector] 解析失败: {e} | 原文: {text[:80]}")
            return None
