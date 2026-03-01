#!/usr/bin/env python3
"""
新功能测试脚本（Phase 1-4）

无需摄像头/API KEY，全部在内存/临时 DB 上运行。

运行：
    python3 test_new_features.py
"""
import asyncio
import logging
import sys
import tempfile
from pathlib import Path

logging.basicConfig(level=logging.WARNING)  # 只显示警告和错误
logger = logging.getLogger("test")


# ─────────────────────────────────────────────────────────────────────────────
# 辅助

def ok(msg):
    print(f"  ✓  {msg}")

def fail(msg, err=""):
    print(f"  ✗  {msg}", f"→ {err}" if err else "")

def section(title):
    print(f"\n{'─'*50}")
    print(f"  {title}")
    print(f"{'─'*50}")


# ─────────────────────────────────────────────────────────────────────────────
# 1. 模型层

def test_models():
    section("1. 新数据模型")
    try:
        from session.models import Book, BookProgress, Bookmark, ReadingListItem

        b = Book(id=1, title="三体", author="刘慈欣", genre="科幻", created_at=0)
        assert b.title == "三体"
        ok("Book dataclass 创建正常")

        bp = BookProgress(id=1, book_id=1, book_title="三体", last_page_num=100, status="reading")
        assert bp.status_str == "阅读中"
        ok("BookProgress.status_str 正常")

        bm = Bookmark(id=1, book_id=1, book_title="三体", session_id="s1",
                      page_num=88, page_ocr_excerpt="这是一段摘录", ts=1700000000000)
        assert "2023" in bm.created_at_str or "2024" in bm.created_at_str or bm.created_at_str
        ok("Bookmark.created_at_str 正常")

        item = ReadingListItem(id=1, title="百年孤独", status="want")
        assert item.status_str == "想读"
        ok("ReadingListItem.status_str 正常")

        return True
    except Exception as e:
        fail("模型层", e)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# 2. 存储层（临时数据库）

async def test_storage():
    section("2. 存储层 — 新表和 CRUD")
    try:
        from session.storage import Storage

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            storage = Storage(db_path)
            await storage.initialize()

            # ── books ──
            book = await storage.get_or_create_book("三体", "刘慈欣")
            assert book.title == "三体" and book.id > 0
            ok("get_or_create_book 创建正常")

            book2 = await storage.get_or_create_book("三体")  # 重复，应返回已有
            assert book2.id == book.id
            ok("get_or_create_book 去重正常")

            # ── reading_progress ──
            progress = await storage.upsert_book_progress(
                book_id=book.id, book_title="三体", page_num=50, page_ocr="第五十页内容"
            )
            assert progress.last_page_num == 50
            ok("upsert_book_progress 插入正常")

            progress2 = await storage.upsert_book_progress(
                book_id=book.id, book_title="三体", page_num=80, status="reading"
            )
            assert progress2.last_page_num == 80
            ok("upsert_book_progress 更新正常")

            fetched = await storage.get_book_progress("三体")
            assert fetched and fetched.last_page_num == 80
            ok("get_book_progress 查询正常")

            # ── bookmarks ──
            bm = await storage.create_bookmark(
                book_id=book.id, book_title="三体", session_id="s1",
                page_num=80, page_ocr_excerpt="这是书签摘录", note="精彩片段"
            )
            assert bm.id > 0 and bm.page_num == 80
            ok("create_bookmark 正常")

            bms = await storage.list_bookmarks(book_title="三体")
            assert len(bms) == 1
            ok("list_bookmarks 查询正常")

            # ── reading_list ──
            item = await storage.reading_list_add("百年孤独", "马尔克斯")
            assert item.title == "百年孤独" and item.status == "want"
            ok("reading_list_add 正常")

            item2 = await storage.reading_list_add("百年孤独")  # 重复
            assert item2.id == item.id
            ok("reading_list_add 去重正常")

            await storage.reading_list_update_status("百年孤独", "reading")
            items = await storage.reading_list_get_all(status="reading")
            assert any(i.title == "百年孤独" for i in items)
            ok("reading_list_update_status / get_all 正常")

            await storage.reading_list_remove("百年孤独")
            items_after = await storage.reading_list_get_all()
            assert not any(i.title == "百年孤独" for i in items_after)
            ok("reading_list_remove 正常")

            # ── reading_stats ──
            stats = await storage.get_reading_stats(period="all")
            assert isinstance(stats["total_pages"], int)
            ok(f"get_reading_stats 正常（全部翻页数: {stats['total_pages']}）")

            await storage.close()
        return True
    except Exception as e:
        import traceback; traceback.print_exc()
        fail("存储层", e)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# 3. SessionManager 高层方法

async def test_session_manager():
    section("3. SessionManager — 新方法")
    try:
        from session.storage import Storage
        from session.manager import SessionManager

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            storage = Storage(db_path)
            await storage.initialize()
            mgr = SessionManager(storage)

            # 模拟启动会话
            session = await mgr.start_session("三体", camera_device=0)

            # create_bookmark
            bm = await mgr.create_bookmark(
                book_title="三体", page_num=42, page_ocr_excerpt="这是OCR内容"
            )
            assert bm.id > 0 and bm.book_title == "三体"
            ok("create_bookmark 通过 manager 正常")

            # upsert_book_progress
            prog = await mgr.upsert_book_progress("三体", page_num=42)
            assert prog.last_page_num == 42
            ok("upsert_book_progress 通过 manager 正常")

            # get_reading_stats
            stats = await mgr.get_reading_stats(period="today")
            assert "total_pages" in stats
            ok(f"get_reading_stats 正常")

            # manage_reading_list — add
            result = await mgr.manage_reading_list(action="add", title="沙丘", author="赫伯特")
            assert result["success"]
            ok("manage_reading_list add 正常")

            # manage_reading_list — list
            result = await mgr.manage_reading_list(action="list")
            assert result["success"] and result["total"] >= 1
            ok(f"manage_reading_list list 正常（{result['total']} 项）")

            # manage_reading_list — mark_done
            result = await mgr.manage_reading_list(action="mark_done", title="沙丘")
            assert result["success"] and result["status"] == "done"
            ok("manage_reading_list mark_done 正常")

            # manage_reading_list — remove
            result = await mgr.manage_reading_list(action="remove", title="沙丘")
            assert result["success"]
            ok("manage_reading_list remove 正常")

            await storage.close()
        return True
    except Exception as e:
        import traceback; traceback.print_exc()
        fail("SessionManager", e)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# 4. 记忆系统

def test_memory():
    section("4. Memory — 长期记忆 & 书籍上下文")
    try:
        from agent.memory import Memory, LongTermMemory

        with tempfile.TemporaryDirectory() as tmpdir:
            persona_file = Path(tmpdir) / "persona.json"
            lt_file = Path(tmpdir) / "long_term_memory.json"

            mem = Memory(persona_file, long_term_file=lt_file)

            # LongTermMemory digest
            lt = LongTermMemory(
                book_summaries={"三体": "讲述地球文明与三体文明的接触故事"},
                user_insights=["用户喜欢睡前阅读科幻"],
                reading_streaks={"current_streak_days": 5, "last_read_date": "2026-02-28"},
            )
            digest = lt.get_digest_for_prompt()
            assert "三体" in digest and "5" in digest
            ok("LongTermMemory.get_digest_for_prompt 正常")

            # update_book_context
            mem.update_book_context({
                "book_title": "三体",
                "current_page_num": 99,
                "content_type": "正文",
                "confidence": 0.9,
            })
            assert mem.current_book_context["book_title"] == "三体"
            ok("update_book_context 正常")

            # build_system_prompt 含书名和长期记忆
            mem.long_term = lt
            prompt = mem.build_system_prompt()
            assert "三体" in prompt
            ok("build_system_prompt 包含书名上下文")

            # OCR 上下文也注入
            mem.set_page_context("这是第99页的内容...")
            prompt2 = mem.build_system_prompt()
            assert "第99页" in prompt2
            ok("build_system_prompt 包含 OCR 上下文")

        return True
    except Exception as e:
        import traceback; traceback.print_exc()
        fail("Memory", e)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# 5. 工具执行器（Mock LLM）

async def test_tool_executor():
    section("5. ToolExecutor — 新工具（Mock）")
    try:
        from session.storage import Storage
        from session.manager import SessionManager
        from agent.tools import ToolExecutor
        from agent.memory import Memory
        from agent.timer_manager import ReadingTimerManager

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            storage = Storage(db_path)
            await storage.initialize()
            mgr = SessionManager(storage)
            await mgr.start_session("三体")

            persona_file = Path(tmpdir) / "persona.json"
            mem = Memory(persona_file)
            mem.set_page_context("这是书页内容，充满了深刻的哲学思考。")
            mem.update_book_context({"book_title": "三体", "current_page_num": 42,
                                     "content_type": "正文", "confidence": 0.95})

            timer_mgr = ReadingTimerManager()

            executor = ToolExecutor(
                session_manager=mgr,
                scanner=None,
                memory=mem,
                llm=None,  # 不需要真实 LLM
                timer_manager=timer_mgr,
            )

            # bookmark_create
            result = await executor.execute("bookmark_create", {"book_title": "三体", "page_num": 42})
            assert result["success"], result
            ok(f"bookmark_create: {result['message']}")

            # bookmark_list
            result = await executor.execute("bookmark_list", {"book_title": "三体"})
            assert result["success"] and result["total"] == 1
            ok(f"bookmark_list: {result['message']}")

            # reading_progress_update
            result = await executor.execute("reading_progress_update",
                                            {"book_title": "三体", "page_num": 50})
            assert result["success"]
            ok(f"reading_progress_update: {result['message']}")

            # reading_progress_query
            result = await executor.execute("reading_progress_query", {"book_title": "三体"})
            assert result["success"] and result["progress"]["last_page_num"] == 50
            ok(f"reading_progress_query: 第 {result['progress']['last_page_num']} 页")

            # reading_list_manage add
            result = await executor.execute("reading_list_manage",
                                            {"action": "add", "book_title": "沙丘"})
            assert result["success"]
            ok("reading_list_manage add 正常")

            # reading_list_manage list
            result = await executor.execute("reading_list_manage", {"action": "list"})
            assert result["success"] and result["total"] >= 1
            ok(f"reading_list_manage list: {result['total']} 本")

            # reading_stats
            result = await executor.execute("reading_stats", {"period": "today"})
            assert result["success"]
            ok(f"reading_stats: {result['message']}")

            # set_timer（1分钟，不真正等待）
            result = await executor.execute("set_timer", {"minutes": 1, "message": "测试提醒"})
            assert result["success"]
            timer_mgr.cancel_all()  # 立即取消，不真正等
            ok(f"set_timer: {result['message']}")

            # generate_reading_card（无 LLM 时，直接用 OCR 内容）
            result = await executor.execute("generate_reading_card",
                                            {"card_type": "quote", "book_title": "三体"})
            assert result["success"]
            ok(f"generate_reading_card: content={result['card_content'][:30]}...")

            await storage.close()
        return True
    except Exception as e:
        import traceback; traceback.print_exc()
        fail("ToolExecutor", e)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# 6. 定时器管理器

async def test_timer_manager():
    section("6. ReadingTimerManager")
    try:
        from agent.timer_manager import ReadingTimerManager

        mgr = ReadingTimerManager()

        # 设置一个极短的测试定时器（0.01 分钟 = 0.6s）——不实际等待
        timer_id = await mgr.set_timer(minutes=999, message="这不会真的触发")
        assert timer_id > 0
        ok(f"set_timer 返回 timer_id={timer_id}")

        timers = mgr.list_timers()
        assert timer_id in timers
        ok(f"list_timers 返回 {timers}")

        cancelled = mgr.cancel_timer(timer_id)
        assert cancelled
        ok("cancel_timer 正常")

        mgr.cancel_all()
        ok("cancel_all 正常")

        return True
    except Exception as e:
        import traceback; traceback.print_exc()
        fail("TimerManager", e)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# 7. 新模块导入检查

def test_new_imports():
    section("7. 新模块导入检查")
    try:
        from session.models import Book, BookProgress, Bookmark, ReadingListItem
        ok("session.models 新模型")

        from agent.memory import LongTermMemory, Memory
        ok("agent.memory LongTermMemory")

        from agent.timer_manager import ReadingTimerManager
        ok("agent.timer_manager")

        from scanner.vision_analyzer import VisionAnalyzer
        ok("scanner.vision_analyzer")

        from agent.tools import (
            BOOKMARK_CREATE_TOOL, BOOKMARK_LIST_TOOL,
            READING_PROGRESS_UPDATE_TOOL, READING_PROGRESS_QUERY_TOOL,
            READING_LIST_MANAGE_TOOL, READING_STATS_TOOL,
            SET_TIMER_TOOL, GENERATE_READING_CARD_TOOL, ALL_TOOLS,
        )
        assert len(ALL_TOOLS) == 11, f"期望11个工具，实际 {len(ALL_TOOLS)}"
        ok(f"agent.tools 共 {len(ALL_TOOLS)} 个工具定义")

        from config import config
        assert hasattr(config, "LONG_TERM_MEMORY_FILE")
        ok(f"config.LONG_TERM_MEMORY_FILE = {config.LONG_TERM_MEMORY_FILE}")

        return True
    except Exception as e:
        import traceback; traceback.print_exc()
        fail("新模块导入", e)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# 主入口

async def main():
    print("=" * 50)
    print("  AI 读书搭子 — 新功能自动化测试")
    print("=" * 50)

    results = []
    results.append(("新数据模型",          test_models()))
    results.append(("存储层 CRUD",         await test_storage()))
    results.append(("SessionManager",      await test_session_manager()))
    results.append(("Memory 增强",         test_memory()))
    results.append(("ToolExecutor 新工具", await test_tool_executor()))
    results.append(("TimerManager",        await test_timer_manager()))
    results.append(("新模块导入",          test_new_imports()))

    print(f"\n{'='*50}")
    passed = sum(1 for _, r in results if r)
    total = len(results)
    for name, result in results:
        print(f"  {'✓' if result else '✗'}  {name}")
    print(f"\n  {passed}/{total} 项通过")
    if passed == total:
        print("  🎉 全部通过！")
    else:
        print("  ⚠  有失败项，请查看上方报错")
    print("=" * 50)

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
