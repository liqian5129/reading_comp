"""
知识链接器 - 笔记关联图谱
笔记保存后异步发现关联笔记并建立双向链接
"""
import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

SIMILARITY_THRESHOLD = 0.80  # cosine 相似度阈值


class KnowledgeLinker:
    """
    笔记保存后 fire-and-forget：embed → 检索相似笔记 → 双向写入 note_links 表。
    任何异常静默降级，不影响主流程。
    """

    def __init__(self, embedder, storage):
        self.embedder = embedder
        self.storage = storage

    async def link_note(self, note) -> None:
        """为新笔记建立知识链接（应通过 asyncio.create_task 调用）"""
        if not self.embedder or not self.storage:
            return
        if not note or not note.id:
            return

        try:
            embed_text = f"{note.book_name or ''} {' '.join(note.tags or [])} {note.content}"
            embedding = await self.embedder.embed(embed_text)
            if not embedding:
                return

            # 仅在 notes 表中检索相似笔记
            results = await self.storage.search_by_embedding(
                embedding,
                tables=["notes"],
                top_k=6,
            )
            if not results:
                return

            # 过滤：排除自身 + 低于阈值
            related = []
            for r in results:
                if r.score < SIMILARITY_THRESHOLD:
                    continue
                # search_by_embedding 不返回 id，需要通过内容匹配——
                # 此处简化：直接用 score 和 content 前缀做双重过滤
                # 真实 id 从 note_links 已有记录中推断无法得到，
                # 故在 storage.search_by_embedding 结果中追加 id 支持
                # 当前 SearchResult 无 id 字段，跳过此条（待 storage 扩展后补充）
                pass

            # 若 SearchResult 缺少 id，尝试从 storage 直接 query 已有 embedding
            related_with_ids = await self._find_related_by_embedding(note.id, embedding)
            if related_with_ids:
                await self.storage.save_note_links(note.id, related_with_ids)
                logger.info(
                    f"知识链接：笔记 #{note.id} 与 {len(related_with_ids)} 条笔记建立关联"
                )

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug(f"link_note 失败（已降级）: {e}")

    async def _find_related_by_embedding(self, note_id: int, query_vec: list) -> list:
        """
        直接查询 notes 表中有 embedding 的行，计算相似度，返回 [(related_id, score), ...]。
        """
        try:
            import numpy as np
        except ImportError:
            return []

        try:
            q = np.array(query_vec, dtype=np.float32)
            q_norm = np.linalg.norm(q)
            if q_norm == 0:
                return []
            q = q / q_norm

            import json
            rows = await self.storage._fetch_embedding_rows("notes")
            related = []
            for row in rows:
                row_id = row.get("id")
                if row_id is None or row_id == note_id:
                    continue
                emb_blob = row.get("embedding")
                if not emb_blob:
                    continue
                try:
                    emb = np.array(json.loads(emb_blob), dtype=np.float32)
                    norm = np.linalg.norm(emb)
                    if norm == 0:
                        continue
                    score = float(np.dot(q, emb / norm))
                    if score >= SIMILARITY_THRESHOLD:
                        related.append((row_id, score))
                except Exception:
                    continue

            # 按相似度降序，最多取前 5
            related.sort(key=lambda x: x[1], reverse=True)
            return related[:5]

        except Exception as e:
            logger.debug(f"_find_related_by_embedding 失败: {e}")
            return []
