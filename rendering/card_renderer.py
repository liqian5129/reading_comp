"""
金句卡片渲染器
使用 Pillow 本地生成日历风格的阅读卡片（参考 book_abstract.jpeg）
支持多套配色模板，根据内容情景自动选择
"""
import logging
import textwrap
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# 字体搜索路径（macOS 优先，兼容 Linux）
_FONT_SEARCH_PATHS = [
    # macOS
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STSong.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    # Linux
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
]

_SERIF_FONT_PATHS = [
    "/System/Library/Fonts/STSong.ttc",
    "/System/Library/Fonts/Songti.ttc",
    "/Library/Fonts/Songti.ttc",
    "/usr/share/fonts/truetype/noto/NotoSerifCJK-Regular.ttc",
]

_BOLD_FONT_PATHS = [
    "/System/Library/AssetsV2/com_apple_MobileAsset_Font7/PingFang.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
]


def _find_font(paths: list, fallback_name: str = "Arial") -> str:
    for p in paths:
        if Path(p).exists():
            return p
    return fallback_name


@dataclass
class CardTemplate:
    """卡片配色模板"""
    name: str
    bg_color: str  # 背景色 hex
    text_color: str  # 正文颜色
    accent_color: str  # 日期/分割线颜色
    source_color: str  # 来源文字颜色
    moods: list = field(default_factory=list)  # 适用的情绪标签


# 预设模板
TEMPLATES = {
    "classic": CardTemplate(
        name="经典黑白",
        bg_color="#FAFAFA",
        text_color="#2C2C2C",
        accent_color="#1A1A1A",
        source_color="#888888",
        moods=["neutral", "general"],
    ),
    "warm": CardTemplate(
        name="暖棕",
        bg_color="#FDF6EC",
        text_color="#5D4037",
        accent_color="#795548",
        source_color="#A1887F",
        moods=["warm", "history", "philosophy"],
    ),
    "cool": CardTemplate(
        name="冷蓝",
        bg_color="#EFF6FC",
        text_color="#1565C0",
        accent_color="#0D47A1",
        source_color="#90A4AE",
        moods=["cool", "tech", "science"],
    ),
    "literary": CardTemplate(
        name="墨绿",
        bg_color="#F1F8E9",
        text_color="#33691E",
        accent_color="#2E7D32",
        source_color="#81C784",
        moods=["literary", "nature", "poetry"],
    ),
    "purple": CardTemplate(
        name="深紫",
        bg_color="#F3E5F5",
        text_color="#4A148C",
        accent_color="#6A1B9A",
        source_color="#AB47BC",
        moods=["art", "psychology", "emotion"],
    ),
}

# mood -> template 映射
_MOOD_MAP = {}
for tpl_key, tpl in TEMPLATES.items():
    for mood in tpl.moods:
        _MOOD_MAP[mood] = tpl_key


def get_template_by_mood(mood: str) -> CardTemplate:
    key = _MOOD_MAP.get(mood, "classic")
    return TEMPLATES[key]


class CardRenderer:
    """金句卡片渲染器"""

    # 基准宽度 800px，所有尺寸/字体按 scale 等比放大
    _BASE_WIDTH = 800

    def __init__(self, output_dir: str = "./data/cards", width: int = 1600, height: int = 1200):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.width = width
        self.height = height
        self._scale = width / self._BASE_WIDTH  # e.g. 1600/800 = 2.0

        # 加载字体
        sans_path = _find_font(_FONT_SEARCH_PATHS)
        serif_path = _find_font(_SERIF_FONT_PATHS, sans_path)
        bold_path = _find_font(_BOLD_FONT_PATHS, sans_path)

        s = self._scale
        self._font_date_big = ImageFont.truetype(bold_path, int(120 * s))
        self._font_date_sub = ImageFont.truetype(sans_path, int(28 * s))
        self._font_weekday = ImageFont.truetype(sans_path, int(22 * s))
        self._font_body = ImageFont.truetype(serif_path, int(36 * s))
        self._font_source = ImageFont.truetype(sans_path, int(22 * s))

    def render_quote_card(
        self,
        text: str,
        book_title: str = "",
        author: str = "",
        mood: str = "neutral",
        template_name: Optional[str] = None,
        date: Optional[datetime] = None,
    ) -> str:
        """
        渲染金句卡片，返回图片文件路径。

        Args:
            text: 金句/摘要内容
            book_title: 书名
            author: 作者
            mood: 情绪标签（warm/cool/literary/art/neutral 等）
            template_name: 强制指定模板名（优先于 mood）
            date: 日期，默认今天
        """
        if template_name and template_name in TEMPLATES:
            tpl = TEMPLATES[template_name]
        else:
            tpl = get_template_by_mood(mood)

        dt = date or datetime.now()
        s = self._scale
        img = Image.new("RGB", (self.width, self.height), tpl.bg_color)
        draw = ImageDraw.Draw(img)

        margin_x = int(80 * s)
        content_width = self.width - margin_x * 2
        y = int(80 * s)

        # === 日期区域 ===
        day_str = str(dt.day)
        day_bbox = draw.textbbox((0, 0), day_str, font=self._font_date_big)
        day_w = day_bbox[2] - day_bbox[0]
        draw.text(
            ((self.width - day_w) / 2, y),
            day_str, fill=tpl.accent_color, font=self._font_date_big,
        )
        y += day_bbox[3] - day_bbox[1] + int(15 * s)

        # 月份年份
        weekday_names = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        month_names = [
            "JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE",
            "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER",
        ]
        month_str = f"{month_names[dt.month - 1]} {dt.year}"
        m_bbox = draw.textbbox((0, 0), month_str, font=self._font_date_sub)
        draw.text(
            ((self.width - (m_bbox[2] - m_bbox[0])) / 2, y),
            month_str, fill=tpl.accent_color, font=self._font_date_sub,
        )
        y += m_bbox[3] - m_bbox[1] + int(10 * s)

        # 星期
        wd_str = weekday_names[dt.weekday()]
        wd_bbox = draw.textbbox((0, 0), wd_str, font=self._font_weekday)
        draw.text(
            ((self.width - (wd_bbox[2] - wd_bbox[0])) / 2, y),
            wd_str, fill=tpl.source_color, font=self._font_weekday,
        )
        y += wd_bbox[3] - wd_bbox[1] + int(30 * s)

        # 分割线
        div_w = int(60 * s)
        line_x = (self.width - div_w) / 2
        draw.line([(line_x, y), (line_x + div_w, y)], fill=tpl.accent_color, width=max(2, int(2 * s)))
        y += int(50 * s)

        # === 正文区域 ===
        wrapped = self._wrap_text(text, self._font_body, content_width, draw)
        for line in wrapped:
            line_bbox = draw.textbbox((0, 0), line, font=self._font_body)
            line_h = line_bbox[3] - line_bbox[1]
            line_w_px = line_bbox[2] - line_bbox[0]
            draw.text(
                ((self.width - line_w_px) / 2, y),
                line, fill=tpl.text_color, font=self._font_body,
            )
            y += line_h + int(16 * s)

        y += int(30 * s)

        # === 来源区域 ===
        if book_title:
            src_line = f"《{book_title}》" if not book_title.startswith("《") else book_title
            if author:
                src_line += f"\n{author}"
            for part in src_line.split("\n"):
                s_bbox = draw.textbbox((0, 0), part, font=self._font_source)
                draw.text(
                    ((self.width - (s_bbox[2] - s_bbox[0])) / 2, y),
                    part, fill=tpl.source_color, font=self._font_source,
                )
                y += s_bbox[3] - s_bbox[1] + int(8 * s)

        # 动态裁剪/扩展画布到实际内容高度
        final_h = y + int(100 * s)
        if final_h != self.height:
            new_img = Image.new("RGB", (self.width, final_h), tpl.bg_color)
            new_img.paste(img, (0, 0))
            img = new_img

        # 保存
        ts = dt.strftime("%Y%m%d_%H%M%S")
        filename = f"card_{tpl.name}_{ts}.png"
        filepath = self.output_dir / filename
        img.save(str(filepath), "PNG", quality=95)
        logger.info(f"卡片已渲染: {filepath}")
        return str(filepath)

    def render_summary_card(
        self,
        title: str,
        summary_text: str,
        illustration_path: Optional[str] = None,
        mood: str = "neutral",
        template_name: Optional[str] = None,
        date: Optional[datetime] = None,
    ) -> str:
        """
        渲染摘要卡片（上图下文），返回图片文件路径。

        Args:
            title: 卡片标题
            summary_text: 摘要内容
            illustration_path: AI 生成的插图路径（可选）
            mood: 情绪标签
            template_name: 模板名
            date: 日期
        """
        if template_name and template_name in TEMPLATES:
            tpl = TEMPLATES[template_name]
        else:
            tpl = get_template_by_mood(mood)

        dt = date or datetime.now()
        sc = self._scale
        margin_x = int(60 * sc)
        content_width = self.width - margin_x * 2

        # 计算总高度
        parts_height = int(60 * sc)  # top padding

        # 插图区域
        illustration_h = 0
        if illustration_path and Path(illustration_path).exists():
            illustration_h = int(self.width * 0.5625)  # 16:9
            parts_height += illustration_h + int(30 * sc)

        # 标题
        parts_height += int(70 * sc)

        # 正文预估
        body_lines = len(summary_text) // 18 + 3
        parts_height += body_lines * int(52 * sc)

        # 底部日期
        parts_height += int(80 * sc)

        total_h = max(self.height, parts_height)
        img = Image.new("RGB", (self.width, total_h), tpl.bg_color)
        draw = ImageDraw.Draw(img)
        y = int(60 * sc)

        # 插图
        if illustration_path and Path(illustration_path).exists():
            try:
                illust = Image.open(illustration_path)
                illust = illust.resize((self.width, illustration_h), Image.LANCZOS)
                img.paste(illust, (0, y))
                y += illustration_h + int(30 * sc)
            except Exception as e:
                logger.warning(f"加载插图失败: {e}")

        # 标题
        title_font = self._font_date_sub
        t_bbox = draw.textbbox((0, 0), title, font=title_font)
        draw.text(
            ((self.width - (t_bbox[2] - t_bbox[0])) / 2, y),
            title, fill=tpl.accent_color, font=title_font,
        )
        y += t_bbox[3] - t_bbox[1] + int(10 * sc)

        # 分割线
        div_w = int(60 * sc)
        draw.line(
            [((self.width - div_w) / 2, y), ((self.width + div_w) / 2, y)],
            fill=tpl.accent_color, width=max(2, int(2 * sc)),
        )
        y += int(30 * sc)

        # 正文
        wrapped = self._wrap_text(summary_text, self._font_body, content_width, draw)
        for line in wrapped:
            draw.text((margin_x, y), line, fill=tpl.text_color, font=self._font_body)
            line_bbox = draw.textbbox((0, 0), line, font=self._font_body)
            y += line_bbox[3] - line_bbox[1] + int(16 * sc)

        y += int(20 * sc)

        # 底部日期
        date_str = dt.strftime("%Y.%m.%d")
        d_bbox = draw.textbbox((0, 0), date_str, font=self._font_source)
        draw.text(
            ((self.width - (d_bbox[2] - d_bbox[0])) / 2, y),
            date_str, fill=tpl.source_color, font=self._font_source,
        )

        # 裁剪到实际内容高度
        final_h = y + int(60 * sc)
        if final_h < total_h:
            img = img.crop((0, 0, self.width, final_h))

        ts = dt.strftime("%Y%m%d_%H%M%S")
        filename = f"summary_{tpl.name}_{ts}.png"
        filepath = self.output_dir / filename
        img.save(str(filepath), "PNG", quality=95)
        logger.info(f"摘要卡片已渲染: {filepath}")
        return str(filepath)

    @staticmethod
    def _wrap_text(text: str, font, max_width: int, draw: ImageDraw.Draw) -> list:
        """中文友好的文本换行"""
        lines = []
        for paragraph in text.split("\n"):
            if not paragraph.strip():
                lines.append("")
                continue
            current = ""
            for char in paragraph:
                test = current + char
                bbox = draw.textbbox((0, 0), test, font=font)
                if bbox[2] - bbox[0] > max_width:
                    lines.append(current)
                    current = char
                else:
                    current = test
            if current:
                lines.append(current)
        return lines
