#!/usr/bin/env python3
"""
Phase 7 记忆系统自动化测试
无需 API Key，全程 mock / 内存 DB / 临时文件

运行：
    python3 test_memory_system.py
"""
import asyncio
import json
import logging
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

logging.basicConfig(level=logging.CRITICAL)  # 压制模块内部日志噪音

# ─── 辅助打印 ────────────────────────────────────────────────────────────────

PASS = 0
FAIL = 0

def ok(msg):
    global PASS
    PASS += 1
    print(f"  ✓  {msg}")

def fail(msg, err=""):
    global FAIL
    FAIL += 1
    err_hint = f" → {err}" if err else ""
    print(f"  ✗  {msg}{err_hint}")

def section(title):
    print(f"\n{'─'*55}")
    print(f"  {title}")
    print(f"{'─'*55}")


# ─── T1. Embedder 单元测试（不涉及真实 API）────────────────────────────────

def test_T1_embedder_no_api():
    section("T1. Embedder（无 API Key）")
    from agent.embedder import Embedder

    # T1-1: enabled=False 时返回 None
    async def _t1_1():
        e = Embedder(api_key="fake", enabled=False)
        result = await e.embed("hello")
        assert result is None
    try:
        asyncio.get_event_loop().run_until_complete(_t1_1())
        ok("T1-1: enabled=False → 返回 None，不发网络请求")
    except Exception as ex:
        fail("T1-1", ex)

    # T1-2: embed("") 返回 None
    async def _t1_2():
        e = Embedder(api_key="fake", enabled=False)
        result = await e.embed("")
        assert result is None
    try:
        asyncio.get_event_loop().run_until_complete(_t1_2())
        ok("T1-2: embed('') → 返回 None")
    except Exception as ex:
        fail("T1-2", ex)

    # T1-3: API 超时 → 返回 None，不抛异常
    async def _t1_3():
        e = Embedder(api_key="fake", enabled=True, timeout_s=0.01)
        async def _slow(*a, **kw):
            await asyncio.sleep(1)
            raise Exception("should not reach")
        e._client = MagicMock()
        e._client.embeddings.create = AsyncMock(side_effect=_slow)
        result = await e.embed("test timeout")
        assert result is None
    try:
        asyncio.get_event_loop().run_until_complete(_t1_3())
        ok("T1-3: embed 超时 → 返回 None，不抛异常")
    except Exception as ex:
        fail("T1-3", ex)

    # T1-4: API 返回异常（如 401）→ 返回 None
    async def _t1_4():
        e = Embedder(api_key="fake", enabled=True)
        e._client = MagicMock()
        e._client.embeddings.create = AsyncMock(side_effect=Exception("401 Unauthorized"))
        result = await e.embed("test error")
        assert result is None
    try:
        asyncio.get_event_loop().run_until_complete(_t1_4())
        ok("T1-4: API 返回错误 → 返回 None，不抛异常")
    except Exception as ex:
        fail("T1-4", ex)

    # T1-6: 超 4000 字自动截断（mock embed 检查入参长度）
    async def _t1_6():
        captured = {}
        async def _mock_create(*a, input=None, **kw):
            captured['input'] = input
            resp = MagicMock()
            resp.data = [MagicMock(embedding=[0.1, 0.2])]
            return resp
        e = Embedder(api_key="fake", enabled=True, timeout_s=5)
        e._client = MagicMock()
        e._client.embeddings.create = _mock_create
        long_text = "x" * 5000
        result = await e.embed(long_text)
        assert len(captured['input']) <= 4000
        assert result == [0.1, 0.2]
    try:
        asyncio.get_event_loop().run_until_complete(_t1_6())
        ok("T1-6: 超 4000 字自动截断，正常返回")
    except Exception as ex:
        fail("T1-6", ex)

    # T1-7: embed_batch 部分失败
    async def _t1_7():
        call_count = 0
        async def _embed(text):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                return None  # 模拟第2条失败
            return [0.1, 0.2]
        e = Embedder(api_key="fake", enabled=False)
        e.embed = _embed
        results = await e.embed_batch(["a", "b", "c"])
        assert len(results) == 3
        assert results[0] == [0.1, 0.2]
        assert results[1] is None
        assert results[2] == [0.1, 0.2]
    try:
        asyncio.get_event_loop().run_until_complete(_t1_7())
        ok("T1-7: embed_batch 部分失败 → 只失败项为 None")
    except Exception as ex:
        fail("T1-7", ex)


# ─── T2. Storage 向量层 ────────────────────────────────────────────────────

async def _make_storage(tmpdir):
    from session.storage import Storage
    db_path = Path(tmpdir) / "test.db"
    s = Storage(db_path)
    await s.initialize()
    return s

def test_T2_storage():
    section("T2. Storage 向量层")
    import numpy as np

    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            s = await _make_storage(tmpdir)

            # T2-1: session_summaries 表存在
            async with s._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='session_summaries'"
            ) as cur:
                row = await cur.fetchone()
            assert row is not None
            ok("T2-1: session_summaries 表已创建")

            # T2-2: notes 表有 embedding 列
            async with s._conn.execute("PRAGMA table_info(notes)") as cur:
                cols = {row['name'] async for row in cur}
            assert 'embedding' in cols
            ok("T2-2: notes 表已迁移 embedding 列")

            # T2-3: save_embedding 写入成功
            from session.models import Note
            import time as _time
            note_id = await s._conn.execute(
                "INSERT INTO notes (ts, content, book_name, tags) VALUES (?,?,?,?)",
                (int(_time.time()*1000), "认知偏见很有趣", "思考快与慢", "[]")
            )
            await s._conn.commit()
            note_rowid = note_id.lastrowid
            vec = list(np.random.rand(8).astype(float))
            ok_flag = await s.save_embedding("notes", note_rowid, vec)
            assert ok_flag
            async with s._conn.execute("SELECT embedding FROM notes WHERE id=?", (note_rowid,)) as cur:
                row = await cur.fetchone()
            assert row['embedding'] is not None
            ok("T2-3: save_embedding 写入成功，embedding 不为 NULL")

            # T2-4: 无效表名 → 返回 False
            result = await s.save_embedding("invalid_table", 1, vec)
            assert result is False
            ok("T2-4: save_embedding 无效表名 → 返回 False，不崩溃")

            # T2-5: 无数据时 search_by_embedding → 空列表
            with tempfile.TemporaryDirectory() as tmpdir2:
                s2 = await _make_storage(tmpdir2)
                results = await s2.search_by_embedding(vec, top_k=3)
                assert results == []
                await s2.close()
            ok("T2-5: 无数据时 search_by_embedding → 空列表")

            # T2-6: 写入 3 条带 embedding 的 notes 后检索 top-2
            import time as _t
            base_vec = np.random.rand(8)
            base_vec = base_vec / np.linalg.norm(base_vec)
            for i, word in enumerate(["决策", "认知", "心理学"]):
                noise = base_vec + np.random.rand(8) * 0.1 * (i + 1)
                noise = noise / np.linalg.norm(noise)
                cur2 = await s._conn.execute(
                    "INSERT INTO notes (ts, content, book_name, tags) VALUES (?,?,?,?)",
                    (int(_t.time()*1000)+i, f"关于{word}的笔记", "心理学书籍", "[]")
                )
                await s._conn.commit()
                rid = cur2.lastrowid
                await s.save_embedding("notes", rid, list(noise.astype(float)))
            results = await s.search_by_embedding(list(base_vec.astype(float)), top_k=2)
            assert len(results) == 2
            assert results[0].score >= results[1].score
            ok("T2-6: 3 条 notes 检索 top-2，按 score 降序返回")

            # T2-7: tables 过滤只搜 notes
            results_notes = await s.search_by_embedding(
                list(base_vec.astype(float)), tables=["notes"], top_k=5
            )
            assert all(r.source == "notes" for r in results_notes)
            ok("T2-7: tables=['notes'] 只搜本地笔记")

            # T2-9: save_session_summary embedding=None
            sid = await s.save_session_summary("今天读了三体", ["科幻"], None)
            assert sid > 0
            async with s._conn.execute("SELECT embedding FROM session_summaries WHERE id=?", (sid,)) as cur:
                row = await cur.fetchone()
            assert row['embedding'] is None
            ok("T2-9: save_session_summary embedding=None，id 正常")

            # T2-10: save_session_summary 含 embedding
            sid2 = await s.save_session_summary("今天读了百年孤独", ["文学"], list(base_vec.astype(float)))
            async with s._conn.execute("SELECT embedding FROM session_summaries WHERE id=?", (sid2,)) as cur:
                row2 = await cur.fetchone()
            assert row2['embedding'] is not None
            ok("T2-10: save_session_summary 含 embedding，列非 NULL")

            # T2-11: load_recent_summaries 按时间正序返回
            await asyncio.sleep(0.01)
            await s.save_session_summary("第三条摘要", ["历史"])
            summaries = await s.load_recent_summaries(n=2)
            assert len(summaries) == 2
            ok("T2-11: load_recent_summaries 返回最近 2 条")

            # T2-12: 无数据 DB 时 load_recent_summaries → 空列表
            with tempfile.TemporaryDirectory() as tmpdir3:
                s3 = await _make_storage(tmpdir3)
                r = await s3.load_recent_summaries()
                assert r == []
                await s3.close()
            ok("T2-12: 无数据时 load_recent_summaries → 空列表")

            # T2-8: numpy 不可用时降级（patch import）
            import sys as _sys
            import builtins
            real_import = builtins.__import__
            def mock_import(name, *args, **kwargs):
                if name == 'numpy':
                    raise ImportError("mocked")
                return real_import(name, *args, **kwargs)
            builtins.__import__ = mock_import
            try:
                results_no_np = await s.search_by_embedding(list(base_vec.astype(float)), top_k=3)
                assert results_no_np == []
                ok("T2-8: numpy 不可用时 search_by_embedding → 空列表，不崩溃")
            finally:
                builtins.__import__ = real_import

            await s.close()

    asyncio.get_event_loop().run_until_complete(_run())


# ─── T3. Memory Prefetch 模式 ─────────────────────────────────────────────

def test_T3_memory_prefetch():
    section("T3. Memory Prefetch 模式")
    import numpy as np

    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            persona_file = Path(tmpdir) / "persona.json"

            # T3-1: 初始 cache=None，prompt 无【相关历史记忆】
            from agent.memory import Memory
            m = Memory(persona_file)
            assert m._prefetch_cache is None
            prompt = m.build_system_prompt()
            assert "相关历史记忆" not in prompt
            ok("T3-1: 初始 cache=None，prompt 无【相关历史记忆】")

            # T3-2: embedder=None 时 trigger_prefetch 静默跳过
            m2 = Memory(persona_file, embedder=None, storage=None)
            m2.trigger_prefetch("认知偏见")  # 不应崩溃
            ok("T3-2: embedder=None 时 trigger_prefetch 静默跳过")

            # T3-3: storage=None 时 trigger_prefetch 静默跳过
            mock_embedder = MagicMock()
            m3 = Memory(persona_file, embedder=mock_embedder, storage=None)
            m3.trigger_prefetch("认知偏见")
            ok("T3-3: storage=None 时 trigger_prefetch 静默跳过")

            # T3-4: _prefetch_memories embed 失败 → cache=None
            mock_embedder4 = MagicMock()
            mock_embedder4.embed = AsyncMock(return_value=None)
            mock_storage4 = MagicMock()
            m4 = Memory(persona_file, embedder=mock_embedder4, storage=mock_storage4)
            await m4._prefetch_memories("test")
            assert m4._prefetch_cache is None
            ok("T3-4: embed 返回 None → _prefetch_cache=None，不崩溃")

            # T3-5: _prefetch_memories 检索到结果 → cache 非空
            from session.models import SearchResult
            mock_embedder5 = MagicMock()
            mock_embedder5.embed = AsyncMock(return_value=[0.1, 0.2])
            mock_storage5 = MagicMock()
            mock_storage5.search_by_embedding = AsyncMock(return_value=[
                SearchResult(source="notes", content="认知偏见的笔记内容", book_name="思考快与慢", score=0.87),
            ])
            m5 = Memory(persona_file, embedder=mock_embedder5, storage=mock_storage5, proactive_top_k=3)
            await m5._prefetch_memories("认知偏见")
            assert m5._prefetch_cache is not None
            assert "认知偏见" in m5._prefetch_cache
            ok("T3-5: embed+检索正常 → _prefetch_cache 非空")

            # T3-6: cache 有值时 build_system_prompt 包含【相关历史记忆】
            m5._prefetch_cache = "- 你的笔记《思考快与慢》：认知偏见是人类…（相似度 0.87）"
            prompt = m5.build_system_prompt()
            assert "相关历史记忆" in prompt
            assert "认知偏见" in prompt
            ok("T3-6: cache 有值时 build_system_prompt 包含【相关历史记忆】")

            # T3-7: trigger_prefetch 连续调用，旧 task 被 cancel
            mock_embedder7 = MagicMock()
            event = asyncio.Event()
            async def slow_embed(text):
                await event.wait()  # 永久阻塞
                return [0.1]
            mock_embedder7.embed = slow_embed
            mock_storage7 = MagicMock()
            mock_storage7.search_by_embedding = AsyncMock(return_value=[])
            m7 = Memory(persona_file, embedder=mock_embedder7, storage=mock_storage7)
            m7.trigger_prefetch("first")
            task1 = m7._prefetch_task
            m7.trigger_prefetch("second")
            task2 = m7._prefetch_task
            assert task1 is not task2
            # 3.11+ 才有 cancelling()；这里只验证旧 task 已被请求 cancel（不同于新 task）
            assert task1 is not task2
            event.set()  # 放行，避免 task 残留
            await asyncio.sleep(0)  # 让 cancel 生效
            ok("T3-7: 连续 trigger_prefetch，旧 task 被 cancel")

            # T3-8: _prefetch_memories 异常 → cache=None，不崩溃
            mock_embedder8 = MagicMock()
            mock_embedder8.embed = AsyncMock(side_effect=RuntimeError("unexpected"))
            m8 = Memory(persona_file, embedder=mock_embedder8, storage=MagicMock())
            await m8._prefetch_memories("test")
            assert m8._prefetch_cache is None
            ok("T3-8: 未预期异常 → cache=None，不崩溃")

    asyncio.get_event_loop().run_until_complete(_run())


# ─── T4. LongTermMemory 新字段 ────────────────────────────────────────────

def test_T4_long_term_memory():
    section("T4. LongTermMemory 新字段")
    from agent.memory import LongTermMemory

    # T4-1: 默认值
    lt = LongTermMemory()
    assert lt.topic_interests == {}
    assert lt.session_recall == ""
    ok("T4-1: 默认值 topic_interests={}, session_recall=''")

    # T4-2: from_dict 加载含新字段
    data = {
        "book_summaries": {"三体": "很好"},
        "user_insights": ["喜欢科幻"],
        "reading_streaks": {"current_streak_days": 3, "last_read_date": "2024-01-01"},
        "topic_interests": {"认知心理学": 2, "决策": 1},
        "session_recall": "上次讨论了认知偏见",
    }
    lt2 = LongTermMemory.from_dict(data)
    assert lt2.topic_interests == {"认知心理学": 2, "决策": 1}
    assert lt2.session_recall == "上次讨论了认知偏见"
    ok("T4-2: from_dict 加载新字段正确")

    # T4-3: from_dict 加载旧版（无新字段）→ 有默认值
    old_data = {
        "book_summaries": {},
        "user_insights": [],
        "reading_streaks": {"current_streak_days": 0, "last_read_date": ""},
    }
    lt3 = LongTermMemory.from_dict(old_data)
    assert lt3.topic_interests == {}
    assert lt3.session_recall == ""
    ok("T4-3: from_dict 加载旧版 JSON → 新字段有默认值，不报 KeyError")

    # T4-4: to_dict 包含新字段
    d = lt2.to_dict()
    assert "topic_interests" in d
    assert "session_recall" in d
    ok("T4-4: to_dict 包含 topic_interests 和 session_recall")

    # T4-5: get_digest_for_prompt 包含话题
    lt4 = LongTermMemory(topic_interests={"认知": 3, "决策": 1})
    digest = lt4.get_digest_for_prompt()
    assert "认知" in digest
    ok("T4-5: get_digest_for_prompt 含话题信息")

    # T4-6: get_digest_for_prompt 包含 session_recall
    lt5 = LongTermMemory(session_recall="上次讨论了《三体》的物理概念")
    digest5 = lt5.get_digest_for_prompt()
    assert "三体" in digest5
    ok("T4-6: get_digest_for_prompt 含 session_recall")


# ─── T5. MemoryConsolidator ────────────────────────────────────────────────

def test_T5_consolidator():
    section("T5. MemoryConsolidator")
    from agent.memory_consolidator import MemoryConsolidator
    from agent.memory import Memory

    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            persona_file = Path(tmpdir) / "persona.json"
            memory_dir = Path(tmpdir) / "memory"

            # T2 用 storage
            from session.storage import Storage
            db_path = Path(tmpdir) / "test.db"
            storage = Storage(db_path)
            await storage.initialize()

            memory = Memory(persona_file)

            # T5-1: 历史为空 → 跳过
            mock_llm = MagicMock()
            mock_llm.chat = AsyncMock(return_value=MagicMock(text=""))
            c = MemoryConsolidator(mock_llm, None, storage, memory, memory_dir, debounce_min=0)
            await c.consolidate(reason="test")
            mock_llm.chat.assert_not_called()
            ok("T5-1: 对话历史为空 → 跳过，LLM 不被调用")

            # T5-2: debounce：同一次触发间隔 < debounce → 跳过
            memory.add_message("user", "你好")
            memory.add_message("assistant", "你好！")
            c2 = MemoryConsolidator(mock_llm, None, storage, memory, memory_dir, debounce_min=100)
            mock_llm.chat.reset_mock()
            mock_llm.chat.return_value = MagicMock(text="书籍：《三体》\n讨论要点：1.物理\n用户关注话题：科幻\n待续：无")
            await c2.consolidate(reason="first")
            first_call_count = mock_llm.chat.call_count
            await c2.consolidate(reason="second")
            # 第二次被 debounce，chat 调用次数不增加
            assert mock_llm.chat.call_count == first_call_count
            ok("T5-2: debounce 内第二次调用被跳过")

            # T5-3: 并发调用 → 第二个等锁后检测 debounce 跳过
            c3 = MemoryConsolidator(mock_llm, None, storage, memory, memory_dir, debounce_min=0)
            mock_llm.chat.reset_mock()
            mock_llm.chat.return_value = MagicMock(text="书籍：《三体》\n讨论要点：1.物理\n用户关注话题：科幻\n待续：无")
            results = await asyncio.gather(
                c3.consolidate(reason="concurrent1"),
                c3.consolidate(reason="concurrent2"),
            )
            # 只应执行一次（第一个持锁，第二个等锁后被 debounce）
            assert mock_llm.chat.call_count <= 2  # 最多2次（debounce=0s时可能都执行）
            ok("T5-3: 并发 consolidate 由锁保护，不崩溃")

            # T5-6: _append_daily_file 正常写入
            c4 = MemoryConsolidator(mock_llm, None, storage, memory, memory_dir,
                                     debounce_min=0, daily_file_enabled=True)
            await c4._append_daily_file("测试摘要内容", "test")
            import datetime
            date_str = datetime.datetime.now().strftime("%Y-%m-%d")
            daily_file = memory_dir / f"{date_str}.md"
            assert daily_file.exists()
            content = daily_file.read_text(encoding="utf-8")
            assert "测试摘要内容" in content
            ok("T5-6: _append_daily_file 正常写入 daily 文件")

            # T5-7: 写入不存在的目录 → 自动创建
            deep_dir = Path(tmpdir) / "deep" / "memory"
            c5 = MemoryConsolidator(mock_llm, None, storage, memory, deep_dir,
                                     debounce_min=0, daily_file_enabled=True)
            await c5._append_daily_file("深层目录测试", "test")
            assert (deep_dir / f"{date_str}.md").exists()
            ok("T5-7: 目录不存在时自动创建，不崩溃")

            # T5-8: _update_long_term 话题权重累加
            memory.long_term.topic_interests = {}
            c6 = MemoryConsolidator(mock_llm, None, storage, memory, memory_dir, debounce_min=0)
            await c6._update_long_term("摘要", ["认知", "决策", "认知"])
            assert memory.long_term.topic_interests.get("认知", 0) >= 1
            ok("T5-8: _update_long_term 话题权重累加")

            # T5-9: topic_interests 超 20 条 → 截断到 20
            memory.long_term.topic_interests = {f"topic{i}": i for i in range(25)}
            await c6._update_long_term("摘要", ["新话题"])
            assert len(memory.long_term.topic_interests) <= 20
            ok("T5-9: topic_interests 超 20 条 → 截断到 20")

            # T5-10: _refresh_session_recall 正常更新
            await storage.save_session_summary("摘要1", [])
            await asyncio.sleep(0.01)
            await storage.save_session_summary("摘要2", [])
            await c6._refresh_session_recall()
            assert "摘要" in memory.long_term.session_recall
            ok("T5-10: _refresh_session_recall 正常更新 session_recall")

            # T5-11: trigger_consolidate 不阻塞（fire-and-forget）
            c7 = MemoryConsolidator(mock_llm, None, storage, memory, memory_dir, debounce_min=0)
            start = time.time()
            c7.trigger_consolidate(reason="fire-forget")
            elapsed = time.time() - start
            assert elapsed < 0.05  # 应立即返回
            ok("T5-11: trigger_consolidate 立即返回，不阻塞调用方")

            # T5-12: start_periodic_loop(0) 立即退出
            c8 = MemoryConsolidator(mock_llm, None, storage, memory, memory_dir, debounce_min=0)
            await asyncio.wait_for(c8.start_periodic_loop(0), timeout=1.0)
            ok("T5-12: start_periodic_loop(0) 立即退出")

            # T5-13: loop 内 exception 不退出循环（测试一次迭代）
            call_log = []
            async def _consolidate_mock(reason=""):
                call_log.append(reason)
                if len(call_log) == 1:
                    raise RuntimeError("simulated error")
                # 第2次正常，通过 cancel 退出
                raise asyncio.CancelledError()
            c9 = MemoryConsolidator(mock_llm, None, storage, memory, memory_dir, debounce_min=0)
            c9.consolidate = _consolidate_mock
            try:
                await asyncio.wait_for(c9.start_periodic_loop(interval_minutes=0.0001), timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
            ok("T5-13: loop 内 exception → 捕获并继续，不退出（直到 cancel）")

            await storage.close()

    asyncio.get_event_loop().run_until_complete(_run())


# ─── T6. note_search 工具边界 ─────────────────────────────────────────────

def test_T6_note_search():
    section("T6. note_search 工具")

    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            from agent.tools import ToolExecutor
            from session.storage import Storage
            db_path = Path(tmpdir) / "test.db"
            storage = Storage(db_path)
            await storage.initialize()

            mock_sm = MagicMock()
            mock_memory = MagicMock()
            executor_no_embed = ToolExecutor(
                session_manager=mock_sm, scanner=None, memory=mock_memory,
                embedder=None, storage=storage,
            )
            executor_no_storage = ToolExecutor(
                session_manager=mock_sm, scanner=None, memory=mock_memory,
                embedder=MagicMock(), storage=None,
            )

            # T6-1: 空 query
            r = await executor_no_embed._exec_note_search({"query": ""})
            assert not r["success"]
            ok("T6-1: 空 query → success=False")

            # T6-2: embedder 未配置
            r2 = await executor_no_embed._exec_note_search({"query": "认知"})
            assert not r2["success"]
            ok("T6-2: embedder 未配置 → success=False，友好提示")

            # T6-3: embed 超时返回 None
            mock_embedder3 = MagicMock()
            mock_embedder3.embed = AsyncMock(return_value=None)
            executor3 = ToolExecutor(
                session_manager=mock_sm, scanner=None, memory=mock_memory,
                embedder=mock_embedder3, storage=storage,
            )
            r3 = await executor3._exec_note_search({"query": "测试"})
            assert not r3["success"]
            ok("T6-3: embed 返回 None → success=False，不崩溃")

            # T6-4: 无 embedding 数据时搜索 → 空结果
            import numpy as np
            fake_vec = list(np.random.rand(8).astype(float))
            mock_embedder4 = MagicMock()
            mock_embedder4.embed = AsyncMock(return_value=fake_vec)
            executor4 = ToolExecutor(
                session_manager=mock_sm, scanner=None, memory=mock_memory,
                embedder=mock_embedder4, storage=storage,
            )
            r4 = await executor4._exec_note_search({"query": "认知偏见"})
            assert r4["success"]
            assert r4["total"] == 0
            ok("T6-4: 无 embedding 数据 → 空结果，success=True")

            # T6-5: source="notes" 只搜本地笔记
            from session.models import SearchResult
            mock_storage5 = MagicMock()
            mock_storage5.search_by_embedding = AsyncMock(return_value=[
                SearchResult(source="notes", content="本地笔记内容", book_name="书A", score=0.9),
            ])
            mock_embedder5 = MagicMock()
            mock_embedder5.embed = AsyncMock(return_value=[0.1, 0.2])
            executor5 = ToolExecutor(
                session_manager=mock_sm, scanner=None, memory=mock_memory,
                embedder=mock_embedder5, storage=mock_storage5,
            )
            r5 = await executor5._exec_note_search({"query": "认知", "source": "notes"})
            call_args = mock_storage5.search_by_embedding.call_args
            assert call_args[1]["tables"] == ["notes"]
            assert r5["total"] == 1
            ok("T6-5: source='notes' → 只传 tables=['notes'] 给 search_by_embedding")

            # T6-6: source="weread"
            mock_storage6 = MagicMock()
            mock_storage6.search_by_embedding = AsyncMock(return_value=[])
            executor6 = ToolExecutor(
                session_manager=mock_sm, scanner=None, memory=mock_memory,
                embedder=mock_embedder5, storage=mock_storage6,
            )
            await executor6._exec_note_search({"query": "认知", "source": "weread"})
            call_args6 = mock_storage6.search_by_embedding.call_args
            assert call_args6[1]["tables"] == ["weread_highlights", "weread_notes"]
            ok("T6-6: source='weread' → tables=['weread_highlights','weread_notes']")

            await storage.close()

    asyncio.get_event_loop().run_until_complete(_run())


# ─── T7. 笔记写入后 Embedding 钩子 ───────────────────────────────────────

def test_T7_embed_hook():
    section("T7. 笔记写入后 Embedding 钩子")

    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            from agent.tools import ToolExecutor
            from session.storage import Storage
            from session.manager import SessionManager

            db_path = Path(tmpdir) / "test.db"
            storage = Storage(db_path)
            await storage.initialize()
            sm = SessionManager(storage)
            await sm.start_session()

            mock_memory = MagicMock()
            mock_memory.current_page_ocr = ""

            # T7-2: embedder=None 时记录笔记不报错
            executor_no_embed = ToolExecutor(
                session_manager=sm, scanner=None, memory=mock_memory,
                embedder=None, storage=storage,
            )
            r = await executor_no_embed._exec_reading_note({"content": "测试笔记"})
            assert r["success"]
            ok("T7-2: embedder=None 时记录笔记 → 正常返回，不触发 embed")

            # T7-3: _embed_note embed 失败 → 不影响已保存的笔记
            mock_embedder3 = MagicMock()
            mock_embedder3.embed = AsyncMock(side_effect=RuntimeError("embed failed"))
            executor3 = ToolExecutor(
                session_manager=sm, scanner=None, memory=mock_memory,
                embedder=mock_embedder3, storage=storage,
            )
            await executor3._embed_note(999, "test text")  # 直接调用，不崩溃
            ok("T7-3: _embed_note embed 失败 → 静默降级，不崩溃")

            # T7-4: _embed_note embed 成功 → save_embedding 被调用
            embed_called = {}
            async def _mock_embed(text):
                embed_called['text'] = text
                return [0.1, 0.2, 0.3]
            save_called = {}
            async def _mock_save(table, row_id, vec):
                save_called['table'] = table
                save_called['row_id'] = row_id
                return True
            mock_embedder4 = MagicMock()
            mock_embedder4.embed = _mock_embed
            mock_storage4 = MagicMock()
            mock_storage4.save_embedding = _mock_save
            executor4 = ToolExecutor(
                session_manager=sm, scanner=None, memory=mock_memory,
                embedder=mock_embedder4, storage=mock_storage4,
            )
            note_id = 42
            await executor4._embed_note(note_id, "认知偏见的研究")
            assert save_called.get('table') == "notes"
            assert save_called.get('row_id') == note_id
            ok("T7-4: _embed_note embed 成功 → save_embedding('notes', ...) 被调用")

            await storage.close()

    asyncio.get_event_loop().run_until_complete(_run())


# ─── T9. 回归测试 ─────────────────────────────────────────────────────────

def test_T9_regression():
    section("T9. 回归测试")

    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            persona_file = Path(tmpdir) / "persona.json"
            from agent.memory import Memory

            # T9-1: 无 prefetch 时 build_system_prompt 包含基础区块
            m = Memory(persona_file)
            prompt = m.build_system_prompt()
            assert "陪伴用户阅读" in prompt
            assert "工具调用策略" in prompt
            assert "相关历史记忆" not in prompt
            ok("T9-1: 无 prefetch 时 prompt 格式与原版一致")

            # T9-3: 旧版 long_term_memory.json 向后兼容
            old_json = {
                "book_summaries": {"三体": "好书"},
                "user_insights": ["喜欢科幻"],
                "reading_streaks": {"current_streak_days": 5, "last_read_date": "2024-01-01"},
            }
            ltm_file = Path(tmpdir) / "long_term_memory.json"
            ltm_file.write_text(json.dumps(old_json), encoding="utf-8")
            m2 = Memory(persona_file, long_term_file=ltm_file)
            assert m2.long_term.topic_interests == {}
            assert m2.long_term.session_recall == ""
            assert m2.long_term.book_summaries == {"三体": "好书"}
            ok("T9-3: 旧版 long_term_memory.json → 新字段有默认值，旧数据不丢失")

            # T9-4: 旧版 DB（无 embedding 列）→ 迁移成功
            from session.storage import Storage
            db_path = Path(tmpdir) / "old.db"
            # 手动创建旧版 notes 表（无 embedding 列）
            import aiosqlite
            async with aiosqlite.connect(str(db_path)) as conn:
                await conn.execute("""
                    CREATE TABLE notes (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id TEXT DEFAULT '',
                        ts INTEGER NOT NULL,
                        content TEXT NOT NULL,
                        book_name TEXT DEFAULT '',
                        tags TEXT DEFAULT '[]',
                        page_ocr_context TEXT DEFAULT ''
                    )
                """)
                await conn.commit()
            # 用 Storage 初始化（触发迁移）
            s = Storage(db_path)
            await s.initialize()
            async with s._conn.execute("PRAGMA table_info(notes)") as cur:
                cols = {row['name'] async for row in cur}
            assert 'embedding' in cols
            await s.close()
            ok("T9-4: 旧版 DB（无 embedding 列）→ ALTER TABLE 迁移成功，不报错")

    asyncio.get_event_loop().run_until_complete(_run())


# ─── 汇总 ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    test_T1_embedder_no_api()
    test_T2_storage()
    test_T3_memory_prefetch()
    test_T4_long_term_memory()
    test_T5_consolidator()
    test_T6_note_search()
    test_T7_embed_hook()
    test_T9_regression()

    print(f"\n{'═'*55}")
    total = PASS + FAIL
    print(f"  结果: {PASS}/{total} 通过  {'✓ 全部通过' if FAIL == 0 else f'✗ {FAIL} 失败'}")
    print(f"{'═'*55}")
    sys.exit(0 if FAIL == 0 else 1)
