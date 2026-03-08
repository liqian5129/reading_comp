"""
rendering 模块完整测试
覆盖：金句卡片、摘要卡片、即梦客户端、PPT生成、Markdown导出、ShareTools集成

运行方式:
  cd /Users/qliau/playground/text_2_image
  python -m pytest tests/test_rendering.py -v
  # 或单独运行
  python tests/test_rendering.py
"""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

# 确保项目根目录在 path 中
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ================================================================
# 1. CardRenderer 测试
# ================================================================

class TestCardRenderer(unittest.TestCase):
    """测试金句卡片和摘要卡片渲染"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_cards_")

    def test_render_quote_card_classic(self):
        """经典黑白模板金句卡片"""
        from rendering.card_renderer import CardRenderer
        renderer = CardRenderer(output_dir=self.tmpdir)
        path = renderer.render_quote_card(
            text="为了消除自己进攻拜占庭时的外部威胁，他趁此机会又和匈牙利人以及塞尔维亚人达成了一项为期三年的双边中立协议。",
            book_title="人类群星闪耀时",
            author="[奥]斯蒂芬·茨威格",
            mood="neutral",
        )
        self.assertTrue(Path(path).exists())
        self.assertTrue(Path(path).stat().st_size > 1000)
        print(f"  [OK] 经典卡片: {path}")

    def test_render_quote_card_all_templates(self):
        """遍历所有模板生成卡片"""
        from rendering.card_renderer import CardRenderer, TEMPLATES
        renderer = CardRenderer(output_dir=self.tmpdir)
        for tpl_name in TEMPLATES:
            path = renderer.render_quote_card(
                text="读书之法，在循序而渐进，熟读而精思。",
                book_title="朱子语类",
                author="朱熹",
                template_name=tpl_name,
            )
            self.assertTrue(Path(path).exists(), f"模板 {tpl_name} 生成失败")
        print(f"  [OK] 全部 {len(TEMPLATES)} 套模板渲染成功")

    def test_render_quote_card_mood_mapping(self):
        """情绪标签自动选模板"""
        from rendering.card_renderer import get_template_by_mood
        tpl = get_template_by_mood("warm")
        self.assertEqual(tpl.name, "暖棕")
        tpl2 = get_template_by_mood("tech")
        self.assertEqual(tpl2.name, "冷蓝")
        tpl3 = get_template_by_mood("unknown_mood")
        self.assertEqual(tpl3.name, "经典黑白")
        print("  [OK] mood 映射正确")

    def test_render_quote_card_long_text(self):
        """长文本自动换行和画布扩展"""
        from rendering.card_renderer import CardRenderer
        renderer = CardRenderer(output_dir=self.tmpdir, height=600)
        long_text = "这是一段很长的测试文本。" * 30
        path = renderer.render_quote_card(text=long_text, book_title="测试书")
        self.assertTrue(Path(path).exists())
        # 画布应该自动扩展
        from PIL import Image
        img = Image.open(path)
        self.assertGreater(img.height, 600)
        print(f"  [OK] 长文本卡片高度自动扩展至 {img.height}px")

    def test_render_quote_card_custom_date(self):
        """自定义日期"""
        from rendering.card_renderer import CardRenderer
        renderer = CardRenderer(output_dir=self.tmpdir)
        dt = datetime(2026, 1, 15)
        path = renderer.render_quote_card(
            text="新年快乐", date=dt, mood="warm",
        )
        self.assertTrue(Path(path).exists())
        self.assertIn("20260115", path)
        print(f"  [OK] 自定义日期卡片")

    def test_render_summary_card_no_illustration(self):
        """无插图的摘要卡片"""
        from rendering.card_renderer import CardRenderer
        renderer = CardRenderer(output_dir=self.tmpdir)
        path = renderer.render_summary_card(
            title="今日阅读摘要",
            summary_text="今天读了《人类群星闪耀时》的第三章，讲述了拜占庭的陷落。穆罕默德二世精心策划了对君士坦丁堡的围攻。",
            mood="warm",
        )
        self.assertTrue(Path(path).exists())
        print(f"  [OK] 摘要卡片(无插图): {path}")

    def test_render_summary_card_with_illustration(self):
        """带插图的摘要卡片（用临时图片模拟）"""
        from rendering.card_renderer import CardRenderer
        from PIL import Image

        # 创建模拟插图
        mock_img = Image.new("RGB", (1024, 576), "#87CEEB")
        mock_path = os.path.join(self.tmpdir, "mock_illustration.png")
        mock_img.save(mock_path)

        renderer = CardRenderer(output_dir=self.tmpdir)
        path = renderer.render_summary_card(
            title="阅读摘要",
            summary_text="精彩的内容摘要文本。",
            illustration_path=mock_path,
        )
        self.assertTrue(Path(path).exists())
        print(f"  [OK] 摘要卡片(含插图): {path}")

    def test_render_empty_text(self):
        """空文本不崩溃"""
        from rendering.card_renderer import CardRenderer
        renderer = CardRenderer(output_dir=self.tmpdir)
        path = renderer.render_quote_card(text="", book_title="")
        self.assertTrue(Path(path).exists())
        print("  [OK] 空文本处理正常")


# ================================================================
# 2. JimengClient 测试（mock API 调用）
# ================================================================

class TestJimengClient(unittest.TestCase):
    """测试即梦客户端（不实际调用 API）"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_jimeng_")

    def test_init(self):
        from rendering.jimeng_client import JimengClient
        client = JimengClient(api_key="test_key", model="ep-test")
        self.assertEqual(client.api_key, "test_key")
        self.assertEqual(client.model, "ep-test")
        print("  [OK] JimengClient 初始化")

    def test_no_config_returns_none(self):
        """未配置时返回 None"""
        from rendering.jimeng_client import JimengClient
        client = JimengClient(api_key="", model="")
        result = asyncio.get_event_loop().run_until_complete(
            client.text_to_image("test prompt", output_dir=self.tmpdir)
        )
        self.assertIsNone(result)
        print("  [OK] 未配置时安全返回 None")

    def test_text_to_image_mock(self):
        """模拟文生图 API 成功响应"""
        import base64
        from rendering.jimeng_client import JimengClient

        # 创建一个最小的 PNG
        from PIL import Image
        import io
        buf = io.BytesIO()
        Image.new("RGB", (64, 64), "red").save(buf, "PNG")
        fake_b64 = base64.b64encode(buf.getvalue()).decode()

        mock_response = {
            "data": [{"b64_json": fake_b64}]
        }

        client = JimengClient(api_key="test", model="ep-test")

        async def run():
            with patch("aiohttp.ClientSession") as MockSession:
                mock_resp = AsyncMock()
                mock_resp.status = 200
                mock_resp.json = AsyncMock(return_value=mock_response)
                mock_ctx = AsyncMock()
                mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
                mock_ctx.__aexit__ = AsyncMock(return_value=False)
                mock_session = AsyncMock()
                mock_session.post = MagicMock(return_value=mock_ctx)
                mock_session_ctx = AsyncMock()
                mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
                mock_session_ctx.__aexit__ = AsyncMock(return_value=False)
                MockSession.return_value = mock_session_ctx

                result = await client.text_to_image(
                    "beautiful landscape", output_dir=self.tmpdir,
                )
                return result

        result = asyncio.get_event_loop().run_until_complete(run())
        self.assertIsNotNone(result)
        self.assertTrue(Path(result).exists())
        print(f"  [OK] 文生图 mock 成功: {result}")

    def test_image_to_image_no_source(self):
        """原图不存在时返回 None"""
        from rendering.jimeng_client import JimengClient
        client = JimengClient(api_key="test", model="ep-test")
        result = asyncio.get_event_loop().run_until_complete(
            client.image_to_image(
                "/nonexistent/image.png", "watercolor style",
                output_dir=self.tmpdir,
            )
        )
        self.assertIsNone(result)
        print("  [OK] 原图不存在时安全返回 None")

    def test_size_presets(self):
        """尺寸预设映射 — 所有预设均满足 API 最低 3,686,400 像素要求"""
        from rendering.jimeng_client import SIZE_PRESETS
        for name, (w, h) in SIZE_PRESETS.items():
            self.assertGreaterEqual(w * h, 3_686_400, f"预设 {name} 像素数不足")
        print("  [OK] 尺寸预设正确")


# ================================================================
# 3. PPTGenerator 测试
# ================================================================

class TestPPTGenerator(unittest.TestCase):
    """测试 PPT 生成"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_ppt_")

    def test_generate_basic(self):
        """基本 PPT 生成"""
        try:
            from rendering.ppt_generator import PPTGenerator, SlideContent
        except ImportError:
            self.skipTest("python-pptx 未安装")

        gen = PPTGenerator(output_dir=self.tmpdir)
        slides = [
            SlideContent(layout="quote", body="读书破万卷，下笔如有神。", source="《奉赠韦左丞丈二十二韵》"),
            SlideContent(layout="bullets", title="今日收获", bullets=["理解了异步编程", "学会了装饰器模式", "掌握了上下文管理器"]),
            SlideContent(layout="quote", body="学而不思则罔，思而不学则殆。", source="《论语》"),
        ]
        path = gen.generate(slides, title="阅读笔记", subtitle="2026年3月8日")
        self.assertTrue(Path(path).exists())
        self.assertTrue(path.endswith(".pptx"))
        print(f"  [OK] PPT 生成: {path} ({Path(path).stat().st_size} bytes)")

    def test_generate_all_themes(self):
        """所有主题"""
        try:
            from rendering.ppt_generator import PPTGenerator, SlideContent, THEMES
        except ImportError:
            self.skipTest("python-pptx 未安装")

        slides = [SlideContent(layout="quote", body="测试内容")]
        for theme_name in THEMES:
            gen = PPTGenerator(output_dir=self.tmpdir, theme=theme_name)
            path = gen.generate(slides, title=f"主题测试-{theme_name}")
            self.assertTrue(Path(path).exists())
        print(f"  [OK] 全部 {len(THEMES)} 套 PPT 主题生成成功")

    def test_generate_from_notes(self):
        """从笔记列表快速生成"""
        try:
            from rendering.ppt_generator import PPTGenerator
        except ImportError:
            self.skipTest("python-pptx 未安装")

        gen = PPTGenerator(output_dir=self.tmpdir)
        notes = [
            {"content": "短金句内容", "tags": ["哲学"], "book_name": "苏菲的世界"},
            {"content": "这是一段较长的笔记内容\n包含多个段落\n每段都有独立的要点", "tags": ["笔记"], "book_name": "苏菲的世界"},
            {"content": "另一条简短笔记", "tags": [], "book_name": "活着"},
        ]
        path = gen.generate_from_notes(notes, book_title="苏菲的世界")
        self.assertTrue(Path(path).exists())
        print(f"  [OK] 从笔记生成 PPT: {path}")

    def test_generate_with_image(self):
        """带图片的幻灯片"""
        try:
            from rendering.ppt_generator import PPTGenerator, SlideContent
        except ImportError:
            self.skipTest("python-pptx 未安装")

        # 创建模拟图片
        from PIL import Image
        mock_path = os.path.join(self.tmpdir, "slide_img.png")
        Image.new("RGB", (800, 600), "#4CAF50").save(mock_path)

        gen = PPTGenerator(output_dir=self.tmpdir)
        slides = [
            SlideContent(
                layout="image_text",
                title="图文并茂",
                body="这是一段带配图的内容说明。",
                image_path=mock_path,
            ),
        ]
        path = gen.generate(slides, title="图文 PPT")
        self.assertTrue(Path(path).exists())
        print(f"  [OK] 图文 PPT 生成: {path}")


# ================================================================
# 4. MarkdownExporter 测试
# ================================================================

class TestMarkdownExporter(unittest.TestCase):
    """测试 Markdown 导出"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_md_")

    def test_export_reading_notes(self):
        """导出读书笔记"""
        from rendering.markdown_exporter import MarkdownExporter
        exporter = MarkdownExporter(output_dir=self.tmpdir)
        notes = [
            {"content": "人生而自由，却无往不在枷锁之中。", "tags": ["哲学", "自由"], "book_name": "社会契约论", "created_at": "2026-03-07"},
            {"content": "知识就是力量。", "tags": ["名言"], "book_name": "新工具", "created_at": "2026-03-08"},
        ]
        path = exporter.export_reading_notes(notes, book_title="")
        self.assertTrue(Path(path).exists())
        content = Path(path).read_text(encoding="utf-8")
        self.assertIn("人生而自由", content)
        self.assertIn("社会契约论", content)
        self.assertIn("`哲学`", content)
        print(f"  [OK] 读书笔记 MD: {path}")

    def test_export_daily_summary(self):
        """导出每日摘要"""
        from rendering.markdown_exporter import MarkdownExporter
        exporter = MarkdownExporter(output_dir=self.tmpdir)
        path = exporter.export_daily_summary(
            summary_text="今天阅读了《人类群星闪耀时》第三章，主要讲述了拜占庭的陷落过程。",
            highlights=["历史的关键时刻往往取决于一个微小的细节。"],
            notes=[{"content": "穆罕默德二世的战略眼光", "book_name": "人类群星闪耀时"}],
            stats={"pages": 42, "duration": "1.5h", "books": ["人类群星闪耀时"], "notes_count": 3},
        )
        self.assertTrue(Path(path).exists())
        content = Path(path).read_text(encoding="utf-8")
        self.assertIn("42", content)
        self.assertIn("1.5h", content)
        self.assertIn("阅读摘要", content)
        print(f"  [OK] 每日摘要 MD: {path}")

    def test_export_quote_collection(self):
        """导出金句集锦"""
        from rendering.markdown_exporter import MarkdownExporter
        exporter = MarkdownExporter(output_dir=self.tmpdir)
        quotes = [
            {"text": "未经审视的人生不值得过。", "book": "申辩篇", "author": "苏格拉底", "tags": ["哲学"]},
            {"text": "凡是过往，皆为序章。", "book": "暴风雨", "author": "莎士比亚", "tags": ["文学"]},
            {"text": "生活不止眼前的苟且。", "book": "", "tags": []},
        ]
        path = exporter.export_quote_collection(quotes, title="我的金句本")
        self.assertTrue(Path(path).exists())
        content = Path(path).read_text(encoding="utf-8")
        self.assertIn("未经审视", content)
        self.assertIn("苏格拉底", content)
        self.assertIn("3 条", content)
        print(f"  [OK] 金句集锦 MD: {path}")

    def test_export_notes_grouped_by_book(self):
        """多书笔记分组"""
        from rendering.markdown_exporter import MarkdownExporter
        exporter = MarkdownExporter(output_dir=self.tmpdir)
        notes = [
            {"content": "笔记A", "tags": [], "book_name": "书1"},
            {"content": "笔记B", "tags": [], "book_name": "书2"},
            {"content": "笔记C", "tags": [], "book_name": "书1"},
        ]
        path = exporter.export_reading_notes(notes)
        content = Path(path).read_text(encoding="utf-8")
        self.assertIn("书1", content)
        self.assertIn("书2", content)
        print("  [OK] 多书分组正确")


# ================================================================
# 5. ShareTools 集成测试（mock deps）
# ================================================================

def _make_mock_deps():
    """创建模拟的 deps 对象"""
    deps = SimpleNamespace(
        memory=SimpleNamespace(
            current_page_ocr="这是一段模拟的书页OCR文本，包含了一些有意义的内容。读书破万卷，下笔如有神。",
            current_book_context={"book_title": "测试书籍", "author": "测试作者"},
            session_reading_digest="今天读了测试书籍的第一章，讲述了基础概念。",
            last_snapshot_path=None,
        ),
        llm=AsyncMock(),
        feishu_pusher=None,
        feishu_chat_id="",
        session_manager=AsyncMock(),
        scanner=None,
        timer_manager=None,
        weread_client=None,
        weread_storage=None,
        embedder=None,
        storage=AsyncMock(),
        knowledge_linker=None,
        jimeng_client=None,
    )
    # 模拟 llm.chat 返回
    deps.llm.chat = AsyncMock(return_value=SimpleNamespace(text="这是AI生成的内容"))
    # 模拟 session_manager.get_recent_notes
    mock_note = SimpleNamespace(
        content="读书破万卷，下笔如有神。",
        book_name="测试书籍",
        tags=["诗词"],
        created_at="2026-03-08",
    )
    deps.session_manager.get_recent_notes = AsyncMock(return_value=[mock_note, mock_note])
    # 模拟 storage
    deps.storage.get_recent_digests = AsyncMock(return_value=[])
    deps.storage.get_reading_pages = AsyncMock(return_value=[])
    return deps


class TestShareToolsIntegration(unittest.TestCase):
    """ShareTools 集成测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_share_")
        # Patch config paths
        self.config_patcher = patch("config.config")
        self.mock_config = self.config_patcher.start()
        self.mock_config.CARDS_DIR = Path(self.tmpdir) / "cards"
        self.mock_config.JIMENG_DIR = Path(self.tmpdir) / "jimeng"
        self.mock_config.PPT_DIR = Path(self.tmpdir) / "ppt"
        self.mock_config.MARKDOWN_DIR = Path(self.tmpdir) / "markdown"
        for d in [self.mock_config.CARDS_DIR, self.mock_config.JIMENG_DIR,
                   self.mock_config.PPT_DIR, self.mock_config.MARKDOWN_DIR]:
            d.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.config_patcher.stop()

    def test_generate_quote_image(self):
        """金句图片卡片工具"""
        from agent.tools.share_tools import ShareTools
        deps = _make_mock_deps()
        tools = ShareTools(deps)

        async def run():
            result = await tools.exec_generate_quote_image({
                "text": "历史的关键时刻往往取决于一个微小的细节。",
                "book_title": "人类群星闪耀时",
                "author": "茨威格",
                "mood": "warm",
            })
            return result

        result = asyncio.get_event_loop().run_until_complete(run())
        self.assertTrue(result["success"])
        self.assertTrue(Path(result["image_path"]).exists())
        print(f"  [OK] generate_quote_image: {result['image_path']}")

    def test_generate_quote_image_auto_extract(self):
        """金句自动从 OCR 提炼"""
        from agent.tools.share_tools import ShareTools
        deps = _make_mock_deps()
        tools = ShareTools(deps)

        async def run():
            result = await tools.exec_generate_quote_image({})
            return result

        result = asyncio.get_event_loop().run_until_complete(run())
        self.assertTrue(result["success"])
        print(f"  [OK] 自动提炼金句: mood={result.get('mood')}")

    def test_generate_summary_image_no_jimeng(self):
        """无即梦配置时生成纯文字摘要卡"""
        from agent.tools.share_tools import ShareTools
        deps = _make_mock_deps()
        tools = ShareTools(deps)

        async def run():
            result = await tools.exec_generate_summary_image({
                "summary_text": "今天阅读了第三章，核心内容是关于拜占庭的陷落。",
                "title": "阅读摘要",
            })
            return result

        result = asyncio.get_event_loop().run_until_complete(run())
        self.assertTrue(result["success"])
        self.assertIsNone(result.get("illustration_path"))
        print(f"  [OK] 摘要卡片(无即梦): {result['image_path']}")

    def test_stylize_page_no_jimeng(self):
        """无即梦配置时提示错误"""
        from agent.tools.share_tools import ShareTools
        deps = _make_mock_deps()
        tools = ShareTools(deps)

        async def run():
            return await tools.exec_stylize_page({"style": "watercolor"})

        result = asyncio.get_event_loop().run_until_complete(run())
        self.assertFalse(result["success"])
        self.assertIn("未配置", result["error"])
        print("  [OK] stylize_page 无配置时正确报错")

    def test_stylize_page_no_snapshot(self):
        """有即梦但无书页照片"""
        from agent.tools.share_tools import ShareTools
        deps = _make_mock_deps()
        deps.jimeng_client = MagicMock()
        tools = ShareTools(deps)

        async def run():
            return await tools.exec_stylize_page({"style": "sketch"})

        result = asyncio.get_event_loop().run_until_complete(run())
        self.assertFalse(result["success"])
        self.assertIn("书页照片", result["error"])
        print("  [OK] stylize_page 无照片时正确报错")

    def test_export_ppt(self):
        """PPT 导出工具"""
        try:
            import pptx
        except ImportError:
            self.skipTest("python-pptx 未安装")

        from agent.tools.share_tools import ShareTools
        deps = _make_mock_deps()
        tools = ShareTools(deps)

        async def run():
            return await tools.exec_export_ppt({
                "days": 7,
                "theme": "warm",
                "include_summary": True,
            })

        result = asyncio.get_event_loop().run_until_complete(run())
        self.assertTrue(result["success"])
        self.assertTrue(Path(result["file_path"]).exists())
        self.assertGreater(result["slide_count"], 0)
        print(f"  [OK] PPT 导出: {result['file_path']} ({result['slide_count']}页)")

    def test_export_markdown_notes(self):
        """Markdown 笔记导出"""
        from agent.tools.share_tools import ShareTools
        deps = _make_mock_deps()
        tools = ShareTools(deps)

        async def run():
            return await tools.exec_export_markdown({
                "template": "notes",
                "days": 7,
            })

        result = asyncio.get_event_loop().run_until_complete(run())
        self.assertTrue(result["success"])
        self.assertTrue(Path(result["file_path"]).exists())
        content = Path(result["file_path"]).read_text(encoding="utf-8")
        self.assertIn("读书破万卷", content)
        print(f"  [OK] MD 笔记导出: {result['file_path']}")

    def test_export_markdown_daily(self):
        """Markdown 每日摘要导出"""
        from agent.tools.share_tools import ShareTools
        deps = _make_mock_deps()
        tools = ShareTools(deps)

        async def run():
            return await tools.exec_export_markdown({"template": "daily"})

        result = asyncio.get_event_loop().run_until_complete(run())
        self.assertTrue(result["success"])
        print(f"  [OK] MD 每日摘要: {result['file_path']}")

    def test_export_markdown_quotes(self):
        """Markdown 金句集锦导出"""
        from agent.tools.share_tools import ShareTools
        deps = _make_mock_deps()
        tools = ShareTools(deps)

        async def run():
            return await tools.exec_export_markdown({"template": "quotes"})

        result = asyncio.get_event_loop().run_until_complete(run())
        self.assertTrue(result["success"])
        print(f"  [OK] MD 金句集锦: {result['file_path']}")

    def test_export_markdown_no_notes(self):
        """无笔记时正确报错"""
        from agent.tools.share_tools import ShareTools
        deps = _make_mock_deps()
        deps.session_manager.get_recent_notes = AsyncMock(return_value=[])
        tools = ShareTools(deps)

        async def run():
            return await tools.exec_export_markdown({"template": "notes"})

        result = asyncio.get_event_loop().run_until_complete(run())
        self.assertFalse(result["success"])
        print("  [OK] 无笔记时正确报错")


# ================================================================
# 6. ToolRegistry 新工具注册验证
# ================================================================

class TestToolRegistration(unittest.TestCase):
    """验证新工具已正确注册"""

    def test_new_tools_registered(self):
        from agent.tools.registry import ToolRegistry, ALL_TOOLS
        registry = ToolRegistry()
        new_tools = [
            "generate_quote_image",
            "generate_summary_image",
            "stylize_page",
            "export_ppt",
            "export_markdown",
        ]
        all_names = [t["name"] for t in ALL_TOOLS]
        for name in new_tools:
            self.assertIn(name, all_names, f"工具 {name} 未在 ALL_TOOLS 中")
            self.assertIsNotNone(registry.get_tool(name), f"工具 {name} 未注册")
        print(f"  [OK] 全部 {len(new_tools)} 个新工具已注册")

    def test_total_tool_count(self):
        from agent.tools.registry import ALL_TOOLS
        # 原有 19 个 + 新增 5 个 = 24 个
        self.assertEqual(len(ALL_TOOLS), 24)
        print(f"  [OK] 总工具数: {len(ALL_TOOLS)}")


# ================================================================
# 运行入口
# ================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("rendering 模块测试")
    print("=" * 60)
    unittest.main(verbosity=2)
