"""
验证脚本：打印微信读书 bookmarklist 原始响应
用途：确认书签（书签类型）的字段结构，特别是无 markText 的条目
运行：python3 test_weread_raw.py
"""
import asyncio
import json
import sys
from pathlib import Path


async def main():
    # 读取配置
    config_path = Path(__file__).parent / "config.json"
    if not config_path.exists():
        print("❌ 找不到 config.json")
        sys.exit(1)

    with open(config_path) as f:
        cfg = json.load(f)

    weread_cfg = cfg.get("weread", {})
    if not weread_cfg.get("enabled"):
        print("❌ config.json 中 weread.enabled 未开启")
        sys.exit(1)

    cookie = weread_cfg.get("cookie_string", "")
    if not cookie:
        print("❌ config.json 中 weread.cookie_string 为空")
        sys.exit(1)

    from weread.client import WeReadClient

    client = WeReadClient(cookie)
    await client.initialize()

    # 先验证 Cookie 是否有效
    print("🔑 验证 Cookie...")
    auth_ok = await client.check_auth()
    if not auth_ok:
        print("❌ Cookie 已过期或无效，请更新 config.json 中的 weread.cookie_string")
        await client.close()
        return
    print("✅ Cookie 有效\n")

    # 直接拿原始书架响应，便于诊断
    print("📚 正在获取书架（原始响应）...")
    raw_shelf = await client._get("/web/shelf/sync")
    if raw_shelf is None:
        print("❌ /web/shelf/sync 返回 None（网络错误或 Cookie 问题）")
        await client.close()
        return
    print(f"   响应 keys: {list(raw_shelf.keys())}")
    raw_books = raw_shelf.get("books", [])
    print(f"   books 数量: {len(raw_books)}")

    shelf = await client.get_shelf()
    books = shelf.get("books", [])
    if not books:
        print("❌ 书架解析后为空，raw_books 示例:")
        print(json.dumps(raw_books[:1], ensure_ascii=False, indent=2))
        await client.close()
        return

    print(f"书架共 {len(books)} 本书：")
    for i, b in enumerate(books[:10]):
        print(f"  [{i}] 《{b.title}》 book_id={b.book_id}")

    # 取第一本书（或按序号选择）
    idx = 0
    if len(sys.argv) > 1:
        idx = int(sys.argv[1])
    book = books[idx]
    print(f"\n🔍 检查《{book.title}》（book_id={book.book_id}）的 bookmarklist 原始响应\n")

    # 直接调用 _get 拿原始数据
    book_referer = f"https://weread.qq.com/web/reader/{book.book_id}"
    raw = await client._get(
        "/web/book/bookmarklist",
        {"bookId": book.book_id, "synckey": 0},
        extra_headers={"Referer": book_referer},
    )

    if not raw:
        print("❌ API 返回为空（Cookie 可能过期）")
        await client.close()
        return

    updated = raw.get("updated", [])
    chapters = {c["chapterUid"]: c.get("title", "") for c in raw.get("chapters", [])}

    print(f"共 {len(updated)} 条记录\n")
    print("=" * 60)

    has_text = []
    no_text = []
    for item in updated:
        mark_text = item.get("markText", "").strip()
        if mark_text:
            has_text.append(item)
        else:
            no_text.append(item)

    print(f"【有文字（划线）】{len(has_text)} 条")
    for item in has_text[:3]:  # 只打印前3条
        uid = item.get("chapterUid", 0)
        print(f"  章节: {chapters.get(uid, uid)}")
        print(f"  markText: {item.get('markText', '')[:60]}")
        print(f"  style: {item.get('style')}  type: {item.get('type')}")
        print(f"  全字段: {list(item.keys())}")
        print()

    print(f"【无文字（可能是书签）】{len(no_text)} 条")
    for item in no_text:
        uid = item.get("chapterUid", 0)
        print(f"  章节: {chapters.get(uid, uid)}")
        print(f"  style: {item.get('style')}  type: {item.get('type')}")
        print(f"  全字段: {json.dumps(item, ensure_ascii=False, indent=4)}")
        print()

    if not no_text:
        print("  （没有无文字条目。可能：① 该书无书签 ② 书签走其他接口）")
        print("\n  提示：在微信读书 App 给这本书添加一个书签，再重新运行此脚本验证。")

    # 顺便试一下 bestbookmarks
    print("\n" + "=" * 60)
    print("🌟 尝试获取热门划线（/web/book/bestbookmarks）...")
    best_raw = await client._get(
        "/web/book/bestbookmarks",
        {"bookId": book.book_id},
    )
    if best_raw:
        # 响应套在 bestBookMarks 子键下
        payload = best_raw.get("bestBookMarks", best_raw)
        print(f"   payload keys: {list(payload.keys())}")
        best_chapters = {c["chapterUid"]: c.get("title", "") for c in payload.get("chapters", [])}
        items = payload.get("items") or payload.get("updated") or []
        if items:
            print(f"✅ 热门划线 {len(items)} 条（totalCount={payload.get('totalCount')}）")
            for item in items[:5]:
                uid = item.get("chapterUid", 0)
                print(f"  [{best_chapters.get(uid, uid)}] {item.get('markText', '')[:80]}")
        else:
            print(f"⚠️  接口可用，totalCount={payload.get('totalCount')}，但 items/updated 均为空")
            print(f"   payload keys 详情: {json.dumps({k: type(v).__name__ for k, v in payload.items()})}")
    else:
        print("❌ /web/book/bestbookmarks 返回空（Cookie 问题或路径不对）")

    # 测试想法/点评：/web/review/list
    print("\n" + "=" * 60)
    print("💭 测试想法/点评（/web/review/list）...")
    review_raw = await client._get(
        "/web/review/list",
        {"bookId": book.book_id, "listType": 11, "mine": 1, "synckey": 0},
    )
    if review_raw:
        reviews = review_raw.get("reviews", [])
        thoughts = [r for r in reviews if r.get("review", r).get("abstract", "").strip()]
        comments = [r for r in reviews if not r.get("review", r).get("abstract", "").strip()]
        print(f"✅ 想法 {len(thoughts)} 条、点评 {len(comments)} 条")
        for r in thoughts[:2]:
            rv = r.get("review", r)
            print(f"  [想法] 原文: {rv.get('abstract','')[:40]}...")
            print(f"         想法: {rv.get('content','')[:60]}")
        for r in comments[:2]:
            rv = r.get("review", r)
            print(f"  [点评] {rv.get('content','')[:80]}")
    else:
        print("❌ review/list 返回空")

    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
