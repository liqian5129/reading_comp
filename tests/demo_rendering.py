#!/usr/bin/env python3
"""
渲染功能交互式演示脚本

用法:
  # 1. 生成全部金句卡片模板预览
  python3 tests/demo_rendering.py cards

  # 2. 用指定图片生成插画风格（需即梦 API）
  python3 tests/demo_rendering.py stylize path/to/snapshot.jpg [watercolor|sketch|comic|ghibli|ink]

  # 3. 生成带 AI 插图的摘要卡片（需即梦 API）
  python3 tests/demo_rendering.py summary "这里填摘要文本"

  # 4. 从笔记生成 PPT
  python3 tests/demo_rendering.py ppt

  # 5. 导出 Markdown（notes / daily / quotes）
  python3 tests/demo_rendering.py markdown [notes|daily|quotes]

  # 6. 一键运行全部本地演示（不依赖 API）
  python3 tests/demo_rendering.py all
"""
import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUTPUT_DIR = ROOT / "data" / "demo_output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ── 示例数据 ──────────────────────────────────────────────────

SAMPLE_QUOTES = [
    {
        "text": "为了消除自己进攻拜占庭时的外部威胁，他趁此机会又和匈牙利人以及塞尔维亚人达成了一项为期三年的双边中立协议。",
        "book": "人类群星闪耀时",
        "author": "[奥]斯蒂芬·茨威格",
        "mood": "warm",
    },
    {
        "text": "凡是过往，皆为序章。",
        "book": "暴风雨",
        "author": "莎士比亚",
        "mood": "literary",
    },
    {
        "text": "未经审视的人生不值得过。",
        "book": "申辩篇",
        "author": "苏格拉底",
        "mood": "art",
    },
    {
        "text": "任何足够先进的技术，都与魔法无异。",
        "book": "2001太空漫游",
        "author": "阿瑟·克拉克",
        "mood": "cool",
    },
    {
        "text": "人生而自由，却无往不在枷锁之中。",
        "book": "社会契约论",
        "author": "卢梭",
        "mood": "neutral",
    },
]

SAMPLE_NOTES = [
    {"content": "读书破万卷，下笔如有神。", "tags": ["诗词", "杜甫"], "book_name": "唐诗三百首"},
    {"content": "穆罕默德二世在围城前做了极其缜密的准备——铸造了当时世界上最大的青铜巨炮，修建了博斯普鲁斯海峡上的堡垒切断拜占庭的粮道。", "tags": ["历史", "军事"], "book_name": "人类群星闪耀时"},
    {"content": "异步编程的核心在于事件循环，它让单线程也能处理并发任务。", "tags": ["编程", "Python"], "book_name": "流畅的Python"},
    {"content": "学而不思则罔，思而不学则殆。", "tags": ["哲学"], "book_name": "论语"},
    {"content": "所谓的自由，不是随心所欲，而是自我主宰。", "tags": ["哲学", "自由"], "book_name": "社会契约论"},
    {"content": "真正的勇气不是没有恐惧，而是在恐惧面前仍然前行。", "tags": ["勇气"], "book_name": "杀死一只知更鸟"},
]

SAMPLE_SUMMARY = (
    '今天阅读了《人类群星闪耀时》第三章"拜占庭的陷落"。穆罕默德二世用了三年时间'
    "精心准备对君士坦丁堡的围攻：铸造巨型攻城炮、在海峡修建堡垒切断补给线、"
    "与周边国家签订中立协议以消除后顾之忧。茨威格以其一贯的戏剧化笔法，"
    "将这场改变欧洲历史走向的围城战写得扣人心弦。最令人唏嘘的是，"
    "一扇被遗忘的小门（凯尔卡门）最终决定了千年帝国的命运——"
    "历史的转折往往取决于最微小的疏忽。"
)


async def _llm_make_illustration_prompt(summary: str) -> str:
    """
    调用 LLM 自由发挥，为摘要/金句生成高审美的插图 prompt。
    失败时降级到关键词匹配。
    """
    try:
        from config import config
        from openai import AsyncOpenAI
        client = AsyncOpenAI(
            api_key=config.ILLUSTRATION_LLM_API_KEY,
            base_url=config.ILLUSTRATION_LLM_BASE_URL,
        )
        resp = await client.chat.completions.create(
            model=config.ILLUSTRATION_LLM_MODEL,
            max_tokens=120,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "这里有一段读书摘要或金句：\n\n"
                        f"{summary[:600]}\n\n"
                        "我想为这张摘要卡/金句卡配一张有意境、审美高级的插图。\n"
                        "请你理解内容的核心意境，自由发挥，生成一段英文文生图提示词（Stable Diffusion / Midjourney 风格）。\n"
                        "要求：\n"
                        "- 画面构图有层次感，意境深远，与内容精神契合\n"
                        "- 色调和情绪与文字基调一致（不要刻板地复述文字，而是用视觉意象传达）\n"
                        "- 风格随机（可以是：水彩、油画、版画、插画、写实、超现实、中国山水等）\n"
                        "- 提示词长度 40-80 词，直接输出英文提示词，不要解释"
                    ),
                }
            ],
        )
        prompt = resp.choices[0].message.content.strip()
        print(f"  [LLM prompt] {prompt}")
        return prompt
    except Exception as e:
        print(f"  [WARN] LLM 生成 prompt 失败，降级关键词匹配: {e}")
        return _make_illustration_prompt(summary)


def _make_illustration_prompt(summary: str) -> str:
    """
    关键词匹配降级方案：根据摘要文字提取核心意象，生成适合文生图的英文 prompt。
    """
    text = summary.lower()

    # 关键词 → 意象场景映射（优先级从上到下）
    SCENE_MAP = [
        (["基因", "dna", "进化", "生物", "物种"],
         "Scientific illustration of DNA double helix with glowing strands, microscopic cell structures, bioluminescent blue tones, book illustration style, watercolor"),
        (["想象", "虚构", "故事", "神话", "宗教", "信仰"],
         "Surreal watercolor illustration of human silhouettes connected by glowing threads forming constellations, cosmic library floating in clouds, warm golden light"),
        (["协作", "网络", "社会", "货币", "法律", "制度"],
         "Watercolor illustration of a vast interconnected web of people from different cultures and eras, glowing golden threads between them, top-down aerial view"),
        (["农业", "土地", "田野", "粮食"],
         "Soft watercolor of ancient farmlands stretching to horizon, small villages, farmers working terraced hillsides at golden hour, peaceful atmosphere"),
        (["战争", "战役", "围城", "军队", "拜占庭", "帝国", "征服"],
         "Epic watercolor of ancient fortress walls at dusk, dramatic clouds, torchlight, historical atmosphere, book illustration style"),
        (["革命", "工业", "科技", "机器", "发明"],
         "Watercolor illustration of industrial revolution scene, gears and steam, factories with golden sunset, vintage book illustration style"),
        (["宇宙", "星球", "太空", "星空"],
         "Ethereal watercolor of galaxy nebula with cosmic dust clouds, deep purple and gold tones, tiny human silhouette gazing upward"),
        (["海洋", "航海", "探索", "地图"],
         "Watercolor map illustration of ancient ocean voyage, tall sailing ships, sea monsters and compass roses, vintage atlas style"),
        (["哲学", "思想", "智慧", "苏格拉底", "孔子"],
         "Watercolor of ancient thinkers gathered under olive trees at sunset, scrolls and lanterns, Mediterranean atmosphere"),
        (["未来", "ai", "人工智能", "数字", "数据"],
         "Futuristic watercolor of a human brain merging with luminous circuit patterns, soft blues and purples, ethereal digital landscape"),
        (["自然", "森林", "山水", "风景"],
         "Traditional Chinese ink-wash inspired watercolor of misty mountains and flowing rivers, morning light through pine trees, serene atmosphere"),
    ]

    for keywords, prompt in SCENE_MAP:
        if any(kw in text for kw in keywords):
            return prompt

    # 默认：从文字中提取前几个名词组成场景
    return (
        "Elegant watercolor book illustration, abstract conceptual scene with "
        "flowing colors and symbolic elements, warm tones, artistic and contemplative atmosphere"
    )


def _print_result(label: str, path: str):
    size = Path(path).stat().st_size
    if size > 1024 * 1024:
        size_str = f"{size / 1024 / 1024:.1f} MB"
    elif size > 1024:
        size_str = f"{size / 1024:.1f} KB"
    else:
        size_str = f"{size} B"
    print(f"  -> {label}: {path}  ({size_str})")


# ── 1. 金句卡片全模板预览 ────────────────────────────────────

def demo_cards():
    from rendering.card_renderer import CardRenderer, TEMPLATES

    print("\n=== 金句卡片 ===\n")
    cards_dir = OUTPUT_DIR / "cards"
    renderer = CardRenderer(output_dir=str(cards_dir))

    # 每个模板用对应 mood 的金句
    tpl_mood_map = {
        "classic": "neutral",
        "warm": "warm",
        "cool": "cool",
        "literary": "literary",
        "purple": "art",
    }

    for tpl_name, mood in tpl_mood_map.items():
        q = next((x for x in SAMPLE_QUOTES if x["mood"] == mood), SAMPLE_QUOTES[0])
        path = renderer.render_quote_card(
            text=q["text"],
            book_title=q["book"],
            author=q["author"],
            mood=mood,
            template_name=tpl_name,
        )
        _print_result(f"模板 [{tpl_name}]", path)

    # 摘要卡片（无插图）
    print("\n--- 摘要卡片 ---")
    path = renderer.render_summary_card(
        title="今日阅读摘要",
        summary_text=SAMPLE_SUMMARY,
        mood="warm",
    )
    _print_result("摘要卡片", path)

    print(f"\n全部卡片已保存到: {cards_dir}")


# ── 2. 书页转插画 ────────────────────────────────────────────

async def demo_stylize(image_path: str, style: str = "watercolor"):
    from rendering.jimeng_client import JimengClient
    from config import config

    print(f"\n=== 书页转插画 (style={style}) ===\n")

    if not Path(image_path).exists():
        print(f"  [ERROR] 图片不存在: {image_path}")
        return

    if not config.JIMENG_ENABLED:
        print("  [SKIP] 即梦 API 未启用 (config.json jimeng.enabled=false)")
        print("  提示: 在 config.json 中添加:")
        print('    "jimeng": {"enabled": true, "api_key": "...", "model": "ep-..."}')
        return

    style_prompts = {
        "watercolor": "Transform this book page into a beautiful watercolor illustration style, soft colors, artistic, keep the core visual elements",
        "sketch": "Transform into a detailed pencil sketch style, fine lines and shading, artistic",
        "comic": "Transform into Japanese manga/comic style, bold lines, dynamic composition",
        "ghibli": "Transform into Studio Ghibli anime style, warm soft colors, dreamy atmosphere",
        "ink": "Transform into traditional Chinese ink wash painting style, black and white with gray tones, elegant",
    }

    client = JimengClient(
        api_key=config.JIMENG_API_KEY,
        model=config.JIMENG_MODEL,
        base_url=config.JIMENG_BASE_URL,
        timeout=config.JIMENG_TIMEOUT,
    )

    out_dir = OUTPUT_DIR / "stylize"
    out_dir.mkdir(exist_ok=True)

    prompt = style_prompts.get(style, style_prompts["watercolor"])
    result = await client.image_to_image(
        image_path=image_path,
        prompt=prompt,
        size="portrait",
        output_dir=str(out_dir),
        filename=f"stylize_{style}",
    )

    if result:
        _print_result(f"插画({style})", result)
    else:
        print("  [FAILED] 图生图返回空，请检查 API 配置和网络")


# ── 3. AI 插图摘要卡片 ──────────────────────────────────────

async def demo_summary_image(text: str = ""):
    from rendering.jimeng_client import JimengClient
    from rendering.card_renderer import CardRenderer
    from config import config

    summary = text or SAMPLE_SUMMARY
    print("\n=== AI 插图摘要卡片 ===\n")

    cards_dir = OUTPUT_DIR / "summary_cards"
    renderer = CardRenderer(output_dir=str(cards_dir))

    illustration_path = None

    if config.JIMENG_ENABLED:
        print("  [1/3] 调用即梦文生图生成插图...")
        client = JimengClient(
            api_key=config.JIMENG_API_KEY,
            model=config.JIMENG_MODEL,
            base_url=config.JIMENG_BASE_URL,
        )
        img_dir = OUTPUT_DIR / "illustrations"
        img_dir.mkdir(exist_ok=True)

        # LLM 自由发挥生成插图 prompt，失败降级关键词匹配
        print("  [0/3] LLM 生成插图 prompt...")
        prompt = await _llm_make_illustration_prompt(summary)
        illustration_path = await client.text_to_image(
            prompt=prompt,
            size="landscape",
            output_dir=str(img_dir),
            filename="summary_illustration",
        )
        if illustration_path:
            _print_result("AI 插图", illustration_path)
        else:
            print("  [WARN] 文生图失败，将生成纯文字摘要卡片")
    else:
        print("  [INFO] 即梦 API 未启用，生成纯文字摘要卡片")

    print("  [2/3] 渲染摘要卡片...")
    path = renderer.render_summary_card(
        title="人类群星闪耀时 · 阅读摘要",
        summary_text=summary,
        illustration_path=illustration_path,
        mood="warm",
    )
    _print_result("摘要卡片", path)
    print(f"\n  结果: {cards_dir}")


# ── 4. PPT 生成 ──────────────────────────────────────────────

def demo_ppt():
    try:
        from rendering.ppt_generator import PPTGenerator, SlideContent
    except ImportError:
        print("\n  [ERROR] python-pptx 未安装: pip3 install python-pptx")
        return

    print("\n=== PPT 生成 ===\n")
    ppt_dir = OUTPUT_DIR / "ppt"

    # --- 读书笔记 PPT ---
    gen = PPTGenerator(output_dir=str(ppt_dir), theme="warm")
    slides = [
        SlideContent(
            layout="bullets",
            title="核心要点",
            bullets=[
                "穆罕默德二世用三年时间精心准备围攻",
                "铸造巨型攻城炮、修建海峡堡垒切断补给",
                "与匈牙利和塞尔维亚签订中立协议",
                "一扇被遗忘的凯尔卡门决定了帝国命运",
                "历史的转折往往取决于最微小的疏忽",
            ],
        ),
    ]
    for q in SAMPLE_QUOTES[:3]:
        slides.append(SlideContent(
            layout="quote",
            body=q["text"],
            source=f"《{q['book']}》{q['author']}",
        ))

    path1 = gen.generate(
        slides,
        title="《人类群星闪耀时》读书笔记",
        subtitle=datetime.now().strftime("%Y年%m月%d日"),
        filename="demo_reading_notes",
    )
    _print_result("读书笔记 PPT", path1)

    # --- 从笔记列表快速生成 ---
    gen2 = PPTGenerator(output_dir=str(ppt_dir), theme="light")
    path2 = gen2.generate_from_notes(SAMPLE_NOTES, book_title="综合笔记", filename="demo_from_notes")
    _print_result("笔记列表 PPT", path2)

    # --- 深色主题 ---
    gen3 = PPTGenerator(output_dir=str(ppt_dir), theme="dark")
    dark_slides = [
        SlideContent(layout="quote", body=q["text"], source=f"《{q['book']}》")
        for q in SAMPLE_QUOTES
    ]
    path3 = gen3.generate(dark_slides, title="金句集", filename="demo_dark_theme")
    _print_result("深色主题 PPT", path3)

    print(f"\n全部 PPT 已保存到: {ppt_dir}")


# ── 5. Markdown 导出 ─────────────────────────────────────────

def demo_markdown(template: str = "all"):
    from rendering.markdown_exporter import MarkdownExporter

    print("\n=== Markdown 导出 ===\n")
    md_dir = OUTPUT_DIR / "markdown"
    exporter = MarkdownExporter(output_dir=str(md_dir))

    if template in ("all", "notes"):
        note_dicts = [
            {"content": n["content"], "tags": n["tags"], "book_name": n["book_name"],
             "created_at": "2026-03-08 14:30"}
            for n in SAMPLE_NOTES
        ]
        path = exporter.export_reading_notes(note_dicts, book_title="", filename="demo_notes")
        _print_result("读书笔记 MD", path)

    if template in ("all", "daily"):
        highlights = [q["text"] for q in SAMPLE_QUOTES[:3]]
        path = exporter.export_daily_summary(
            summary_text=SAMPLE_SUMMARY,
            highlights=highlights,
            notes=[{"content": n["content"], "book_name": n["book_name"]} for n in SAMPLE_NOTES[:3]],
            stats={"pages": 42, "duration": "1.5h", "books": ["人类群星闪耀时", "论语"], "notes_count": 6},
            filename="demo_daily",
        )
        _print_result("每日摘要 MD", path)

    if template in ("all", "quotes"):
        quotes = [{"text": q["text"], "book": q["book"], "author": q["author"], "tags": []} for q in SAMPLE_QUOTES]
        path = exporter.export_quote_collection(quotes, title="我的金句本", filename="demo_quotes")
        _print_result("金句集锦 MD", path)

    print(f"\n全部 Markdown 已保存到: {md_dir}")


# ── 6. 全部本地演示 ──────────────────────────────────────────

def demo_all():
    demo_cards()
    demo_ppt()
    demo_markdown()
    # summary_image 中即梦部分会自动跳过
    asyncio.get_event_loop().run_until_complete(demo_summary_image())

    print("\n" + "=" * 60)
    print(f"全部演示输出目录: {OUTPUT_DIR}")
    print("=" * 60)

    # 列出生成的文件
    files = sorted(OUTPUT_DIR.rglob("*"), key=lambda p: p.suffix)
    by_type = {}
    for f in files:
        if f.is_file():
            by_type.setdefault(f.suffix, []).append(f)
    print(f"\n生成文件统计:")
    for ext, fs in sorted(by_type.items()):
        total_size = sum(f.stat().st_size for f in fs)
        print(f"  {ext or '(无扩展名)':8s} : {len(fs):3d} 个  ({total_size / 1024:.0f} KB)")


# ── 主入口 ───────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1].lower()

    if cmd == "cards":
        demo_cards()
    elif cmd == "stylize":
        if len(sys.argv) < 3:
            print("用法: python3 tests/demo_rendering.py stylize <image_path> [style]")
            print("  style: watercolor(默认) / sketch / comic / ghibli / ink")
            return
        img = sys.argv[2]
        style = sys.argv[3] if len(sys.argv) > 3 else "watercolor"
        asyncio.get_event_loop().run_until_complete(demo_stylize(img, style))
    elif cmd == "summary":
        text = sys.argv[2] if len(sys.argv) > 2 else ""
        asyncio.get_event_loop().run_until_complete(demo_summary_image(text))
    elif cmd == "ppt":
        demo_ppt()
    elif cmd == "markdown":
        tpl = sys.argv[2] if len(sys.argv) > 2 else "all"
        demo_markdown(tpl)
    elif cmd == "all":
        demo_all()
    else:
        print(f"未知命令: {cmd}")
        print(__doc__)


if __name__ == "__main__":
    main()
