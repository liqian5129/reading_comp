"""
微信读书 HTTP 客户端
使用 aiohttp，调用非官方 Web API（weread.qq.com）
Cookie 认证，bookmarklist 接口需要显式传 vid=wr_vid
"""
import logging
import time
from typing import Any, Dict, List, Optional

import aiohttp

from .models import WeReadBook, WeReadHighlight, WeReadNote, WeReadProgress

logger = logging.getLogger(__name__)

_BASE_URL = "https://weread.qq.com"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://weread.qq.com/",
    "Origin": "https://weread.qq.com",
    "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}

# errcode 表示 Cookie 过期
_AUTH_EXPIRED_CODES = {-2012, -2010}

# 章节名字段名（实测 bookmarklist 和 review/list 都用 chapterName）
_CHAPTER_NAME_FIELDS = ("chapterName", "chapterTitle")


def _chapter_name(item: dict) -> str:
    """兼容 chapterName / chapterTitle 两种字段名"""
    for field in _CHAPTER_NAME_FIELDS:
        v = item.get(field)
        if v:
            return v
    return ""


class WeReadClient:
    """微信读书异步 HTTP 客户端"""

    def __init__(self, cookie_string: str):
        self._cookie_string = cookie_string
        self._session: Optional[aiohttp.ClientSession] = None

    async def initialize(self):
        """创建 aiohttp session"""
        headers = dict(_HEADERS)
        headers["Cookie"] = self._cookie_string
        connector = aiohttp.TCPConnector(ssl=False)
        self._session = aiohttp.ClientSession(
            base_url=_BASE_URL,
            headers=headers,
            connector=connector,
        )
        logger.info("微信读书客户端已初始化")

    async def close(self):
        """关闭 session"""
        if self._session:
            await self._session.close()
            self._session = None

    def _ts(self) -> int:
        """毫秒时间戳，防缓存"""
        return int(time.time() * 1000)

    async def _get(self, path: str, params: Optional[Dict] = None,
                   extra_headers: Optional[Dict] = None) -> Optional[Dict]:
        """
        发送 GET 请求，返回 JSON dict 或 None（失败/鉴权过期）
        extra_headers 用于覆盖 session 默认头（如 book-specific Referer）
        """
        if not self._session:
            logger.error("WeReadClient 未初始化，请先调用 initialize()")
            return None
        p = dict(params or {})
        p["_"] = self._ts()
        try:
            async with self._session.get(path, params=p, headers=extra_headers) as resp:
                if resp.status == 401:
                    logger.warning("微信读书 Cookie 已过期（HTTP 401）")
                    return None
                data = await resp.json(content_type=None)
                if isinstance(data, dict):
                    errcode = data.get("errcode")
                    if errcode in _AUTH_EXPIRED_CODES:
                        logger.warning(f"微信读书 Cookie 已过期（errcode={errcode}）")
                        return None
                return data
        except Exception as e:
            logger.error(f"WeRead GET {path} 失败: {e}")
            return None

    async def _post(self, path: str, json_body: Dict) -> Optional[Dict]:
        """发送 POST 请求"""
        if not self._session:
            logger.error("WeReadClient 未初始化")
            return None
        try:
            async with self._session.post(path, json=json_body) as resp:
                if resp.status == 401:
                    logger.warning("微信读书 Cookie 已过期（HTTP 401）")
                    return None
                data = await resp.json(content_type=None)
                if isinstance(data, dict):
                    errcode = data.get("errcode")
                    if errcode in _AUTH_EXPIRED_CODES:
                        logger.warning(f"微信读书 Cookie 已过期（errcode={errcode}）")
                        return None
                return data
        except Exception as e:
            logger.error(f"WeRead POST {path} 失败: {e}")
            return None

    async def check_auth(self) -> bool:
        """验证 Cookie 是否有效"""
        data = await self._get("/api/user/notebook")
        return data is not None

    async def get_shelf(self) -> Dict[str, List]:
        """
        获取书架，同时返回 books 和 bookProgress 列表。
        /web/shelf/sync 不需要 wr_vid，通过 Cookie 自动识别用户。
        返回格式: {"books": [...], "progress": [...]}
        """
        data = await self._get("/web/shelf/sync")
        if not data:
            return {"books": [], "progress": []}

        raw_books = data.get("books", [])
        raw_progress = data.get("bookProgress", [])

        books: List[WeReadBook] = []
        for item in raw_books:
            book_info = item.get("bookInfo", item)
            reading_info = item.get("readingBookInfo", {})
            if not book_info:
                continue
            book_id = book_info.get("bookId", "")
            if not book_id:
                continue
            books.append(WeReadBook(
                book_id=book_id,
                title=book_info.get("title", ""),
                author=book_info.get("author", ""),
                translator=book_info.get("translator", ""),
                cover=book_info.get("cover", ""),
                category=book_info.get("category", ""),
                finish_reading=bool(reading_info.get("finishReading", 0)),
                reading_time_s=reading_info.get("readingTime", 0),
                progress=reading_info.get("progress", 0),
            ))

        progress_list: List[WeReadProgress] = []
        for item in raw_progress:
            book_id = item.get("bookId", "")
            if not book_id:
                continue
            # 从已解析的 books 找书名
            book_title = next((b.title for b in books if b.book_id == book_id), "")
            progress_list.append(WeReadProgress(
                book_id=book_id,
                book_title=book_title,
                progress=item.get("progress", 0),
                reading_time_s=item.get("readingTime", 0),
                chapter_uid=item.get("chapterUid", 0),
                chapter_offset=item.get("chapterOffset", 0),
            ))

        return {"books": books, "progress": progress_list}

    async def get_notebook_books(self) -> List[Dict]:
        """
        获取有笔记的书单（含 noteCount, reviewCount）
        GET /api/user/notebook
        """
        data = await self._get("/api/user/notebook")
        if not data:
            return []
        return data.get("books", [])

    async def get_highlights(self, book_id: str, book_title: str = "") -> List[WeReadHighlight]:
        """
        获取划线列表
        GET /web/book/bookmarklist?bookId=...&synckey=0

        关键：Referer 必须设置为 https://weread.qq.com/web/reader/{book_id}
        否则 API 返回空 {}，vid 参数无效
        章节名字段是 chapterName（不是 chapterTitle）
        """
        book_referer = f"https://weread.qq.com/web/reader/{book_id}"
        data = await self._get(
            "/web/book/bookmarklist",
            {"bookId": book_id, "synckey": 0},
            extra_headers={"Referer": book_referer},
        )
        if not data:
            return []

        raw_updated = data.get("updated", [])

        highlights = []
        for item in raw_updated:
            mark_text = item.get("markText", "").strip()
            chapter_uid = item.get("chapterUid", 0)
            if not mark_text or not chapter_uid:
                continue
            if item.get("type") == 0:  # type=0 是书签，type=1 是划线
                continue
            highlights.append(WeReadHighlight(
                bookmark_id=item.get("bookmarkId", ""),
                book_id=book_id,
                book_title=book_title or item.get("bookTitle", ""),
                content=mark_text,
                chapter_uid=chapter_uid,
                chapter_title=_chapter_name(item),
                chapter_idx=item.get("chapterIdx", 0),
                created_at=item.get("createTime", 0),
            ))
        return highlights

    async def get_notes(self, book_id: str) -> List[WeReadNote]:
        """
        获取用户在某本书的想法和点评
        GET /web/review/list?bookId=...&listType=11&mine=1&synckey=0

        响应中每条 review 结构：
          {"reviewId": "...", "review": {"content": "...", "abstract": "...", ...}}

        类型判断：
          - 想法 (note_type="想法"): abstract != "" — 附在某段划线上的评论
          - 点评 (note_type="点评"): abstract == "" — 独立的书评/感想，不附在划线上
        """
        data = await self._get(
            "/web/review/list",
            {"bookId": book_id, "listType": 11, "mine": 1, "synckey": 0}
        )
        if not data:
            return []

        reviews = data.get("reviews", [])
        notes = []
        for item in reviews:
            review = item.get("review", item)
            abstract = review.get("abstract", "").strip()   # 划线原文（想法有，点评没有）
            content = review.get("content", "").strip()     # 用户写的文字

            if not content:
                continue  # 没有用户文字，跳过

            note_type = "想法" if abstract else "点评"

            notes.append(WeReadNote(
                review_id=review.get("reviewId", ""),
                book_id=book_id,
                book_title=review.get("bookTitle", ""),
                abstract=abstract,
                content=content,
                note_type=note_type,
                chapter_uid=review.get("chapterUid", 0),
                chapter_title=_chapter_name(review),
                chapter_idx=review.get("chapterIdx", 0),
                created_at=review.get("createTime", 0),
            ))
        return notes

    async def get_progress(self, book_id: str) -> Optional[WeReadProgress]:
        """
        实时获取阅读进度
        GET /web/book/getProgress?bookId=...

        实测响应格式：
          {"bookId": "...", "book": {"chapterUid": N, "chapterOffset": N,
                                     "readingTime": N, "progress": N, ...}}
        进度数据嵌套在 "book" 子键中
        """
        data = await self._get("/web/book/getProgress", {"bookId": book_id})
        if not data:
            return None

        # 进度数据在 "book" 子键中
        book_data = data.get("book", {})
        if not book_data:
            return None

        return WeReadProgress(
            book_id=book_id,
            book_title=data.get("bookTitle", ""),
            progress=book_data.get("progress", 0),
            reading_time_s=book_data.get("readingTime", 0),
            chapter_uid=book_data.get("chapterUid", 0),
            chapter_offset=book_data.get("chapterOffset", 0),
        )

    async def get_bookmarks(self, book_id: str, book_title: str = "") -> List[WeReadHighlight]:
        """
        获取书签（纯位置标记）。
        与划线来自同一接口 /web/book/bookmarklist，
        区别：书签的 markText 为空字符串，划线有文字内容。
        """
        book_referer = f"https://weread.qq.com/web/reader/{book_id}"
        data = await self._get(
            "/web/book/bookmarklist",
            {"bookId": book_id, "synckey": 0},
            extra_headers={"Referer": book_referer},
        )
        if not data:
            return []

        chapters = {c["chapterUid"]: c.get("title", "") for c in data.get("chapters", [])}

        bookmarks = []
        for item in data.get("updated", []):
            if item.get("type") != 0:  # type=0 是书签，其他跳过
                continue
            chapter_uid = item.get("chapterUid", 0)
            if not chapter_uid:
                continue
            bookmarks.append(WeReadHighlight(
                bookmark_id=item.get("bookmarkId", ""),
                book_id=book_id,
                book_title=book_title,
                content=item.get("markText", "").strip(),  # 书签位置的文字（自动捕获）
                chapter_uid=chapter_uid,
                chapter_title=chapters.get(chapter_uid, _chapter_name(item)),
                chapter_idx=item.get("chapterIdx", 0),
                created_at=item.get("createTime", 0),
            ))
        return bookmarks

    async def get_best_highlights(self, book_id: str) -> List[WeReadHighlight]:
        """
        获取热门划线（所有读者的热门标注）。
        接口：/web/book/bestbookmarks?bookId=...
        返回 chapters（章节映射）和 items（热门划线条目）。
        """
        data = await self._get(
            "/web/book/bestbookmarks",
            {"bookId": book_id},
        )
        if not data:
            return []

        # 响应格式：{"bestBookMarks": {"chapters": [...], "items"/"updated": [...], ...}}
        payload = data.get("bestBookMarks", data)
        chapters = {c["chapterUid"]: c.get("title", "") for c in payload.get("chapters", [])}
        # items 字段名可能是 "items" 或 "updated"
        raw_items = payload.get("items") or payload.get("updated") or []

        results = []
        for item in raw_items:
            mark_text = item.get("markText", "").strip()
            if not mark_text:
                continue
            chapter_uid = item.get("chapterUid", 0)
            results.append(WeReadHighlight(
                bookmark_id=item.get("bookmarkId", f"best_{len(results)}"),
                book_id=book_id,
                book_title="",
                content=mark_text,
                chapter_uid=chapter_uid,
                chapter_title=chapters.get(chapter_uid, _chapter_name(item)),
                chapter_idx=item.get("chapterIdx", 0),
                created_at=item.get("createTime", 0),
            ))
        return results

    async def get_chapter_infos(self, book_id: str) -> Dict[int, str]:
        """
        获取章节信息（chapterUid → title 映射）
        POST /web/book/chapterInfos  body={"bookIds": ["..."]}
        """
        data = await self._post("/web/book/chapterInfos", {"bookIds": [book_id]})
        if not data:
            return {}

        chapter_map: Dict[int, str] = {}
        for book_data in data.get("data", []):
            if book_data.get("bookId") != book_id:
                continue
            for chapter in book_data.get("updated", []):
                uid = chapter.get("chapterUid")
                title = chapter.get("title", "")
                if uid is not None:
                    chapter_map[uid] = title
        return chapter_map
