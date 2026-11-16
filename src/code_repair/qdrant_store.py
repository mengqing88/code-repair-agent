"""为代码修复知识库提供带命名空间和元数据过滤的 Qdrant 向量索引。"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
import hashlib
from pathlib import Path
from threading import Lock
from typing import Mapping, Sequence
from uuid import NAMESPACE_URL, uuid5

from code_repair.runtime_config import load_settings, setting


class QdrantIndexError(RuntimeError):
    """Qdrant collection 配置、写入或检索失败时抛出。"""


@dataclass
class QdrantVectorIndex:
    """管理一个 Qdrant collection，并用 namespace 隔离不同知识库。"""

    client: object
    collection_name: str = "repair_experiences"
    _collection_lock: Lock = field(default_factory=Lock, init=False, repr=False)
    _synced_namespaces: set[tuple[str, str]] = field(default_factory=set, init=False, repr=False)
    _sync_lock: Lock = field(default_factory=Lock, init=False, repr=False)

    @classmethod
    def from_environment(cls, env_path: Path | None = None) -> QdrantVectorIndex | None:
        """从 QDRANT_URL 和可选 QDRANT_API_KEY 创建远端索引；未配置 URL 时返回 None。"""
        settings = load_settings(env_path)
        url = setting("QDRANT_URL", settings)
        if not isinstance(url, str) or not url.strip() or url.strip().lower() in {"off", "disabled"}:
            return None
        return _shared_index(url.strip(), setting("QDRANT_API_KEY", settings) or None,
                             setting("QDRANT_COLLECTION", settings) or "repair_experiences",
                             float(setting("QDRANT_TIMEOUT_SECONDS", settings) or 3))

    @classmethod
    def connect(cls, url, api_key, collection, timeout):
        """连接参数由当前任务配置选择，单次网络请求有明确超时。"""
        try:
            from qdrant_client import QdrantClient
        except ImportError as error:
            raise QdrantIndexError("使用 Qdrant 需要安装 qdrant-client") from error
        try:
            if not 0 < timeout <= 30:
                raise ValueError("Qdrant timeout must be in (0, 30]")
            client = QdrantClient(url=url, api_key=api_key, timeout=timeout)
        except Exception as error:
            raise QdrantIndexError("无法初始化 Qdrant 客户端") from error
        return cls(client=client, collection_name=collection)

    @staticmethod
    def point_id(namespace: str, outcome: str, case_id: str) -> str:
        """为同一 namespace、结果类型和案例 ID 生成稳定 UUID。"""
        return str(uuid5(NAMESPACE_URL, f"{namespace}:{outcome}:{case_id}"))

    @staticmethod
    def namespace_for_path(path: Path) -> str:
        """用知识库文件路径的哈希隔离不同用户和数据库，避免把本地路径写入 Qdrant。"""
        resolved = path.expanduser().resolve().as_posix().casefold()
        return hashlib.sha256(resolved.encode("utf-8")).hexdigest()

    def _ensure_collection(self, dimension: int) -> None:
        """创建 collection；已存在时确认向量维度一致。"""
        from qdrant_client import models

        try:
            with self._collection_lock:
                exists = self.client.collection_exists(self.collection_name)
                if not exists:
                    self.client.create_collection(
                        collection_name=self.collection_name,
                        vectors_config=models.VectorParams(size=dimension, distance=models.Distance.COSINE),
                    )
                    return
                info = self.client.get_collection(self.collection_name)
                vectors = info.config.params.vectors
                if isinstance(vectors, dict) or vectors.size != dimension:
                    raise QdrantIndexError("Qdrant collection 的向量维度与当前 Embedding 模型不一致")
        except QdrantIndexError:
            raise
        except Exception as error:
            raise QdrantIndexError("准备 Qdrant collection 失败") from error

    def upsert(
        self,
        *,
        namespace: str,
        case_id: str,
        outcome: str,
        revision: int,
        vector: Sequence[float],
        retrieval_scope: Mapping[str, object],
    ) -> None:
        """写入或更新案例向量及过滤元数据，不在 Qdrant 中保存完整案例正文。"""
        from qdrant_client import models

        try:
            self._ensure_collection(len(vector))
            self.client.upsert(
                collection_name=self.collection_name,
                points=[self._point(models, namespace, case_id, outcome, revision, vector, retrieval_scope)],
                wait=True,
            )
        except Exception as error:
            with self._sync_lock:
                self._synced_namespaces.discard((namespace, outcome))
            if isinstance(error, QdrantIndexError):
                raise
            raise QdrantIndexError("写入 Qdrant 向量失败") from error

    def delete_case(self, *, namespace: str, case_id: str, outcome: str) -> None:
        """删除已并入主案例的向量点。"""
        from qdrant_client import models

        try:
            if not self.client.collection_exists(self.collection_name):
                return
            self.client.delete(
                collection_name=self.collection_name,
                points_selector=models.PointIdsList(
                    points=[QdrantVectorIndex.point_id(namespace, outcome, case_id)],
                ),
                wait=True,
            )
        except Exception as error:
            raise QdrantIndexError("删除已合并案例向量失败") from error

    @staticmethod
    def _point(models, namespace, case_id, outcome, revision, vector, retrieval_scope):
        """构造不包含案例正文的 Qdrant 点。"""
        return models.PointStruct(
            id=QdrantVectorIndex.point_id(namespace, outcome, case_id),
            vector=list(vector),
            payload={
                "namespace": namespace,
                "case_id": case_id,
                "outcome": outcome,
                "revision": revision,
                "retrieval_scope": dict(retrieval_scope),
            },
        )

    def sync_once(self, *, namespace: str, outcome: str, entries: Sequence[Mapping[str, object]]) -> None:
        """首次检索时批量补入旧知识库案例，之后由 save 操作增量更新。"""
        key = (namespace, outcome)
        if key in self._synced_namespaces:
            return
        from qdrant_client import models

        with self._sync_lock:
            if key in self._synced_namespaces:
                return
            if entries:
                dimensions = {len(entry["vector"]) for entry in entries}
                if len(dimensions) != 1:
                    raise QdrantIndexError("本地知识库包含不同维度的向量，无法同步到同一 Qdrant collection")
                self._ensure_collection(next(iter(dimensions)))
                points = [
                    self._point(
                        models, namespace, str(entry["case_id"]), outcome, int(entry["revision"]),
                        entry["vector"], entry["retrieval_scope"],
                    )
                    for entry in entries
                ]
                try:
                    for start in range(0, len(points), 128):
                        self.client.upsert(
                            collection_name=self.collection_name,
                            points=points[start:start + 128],
                            wait=True,
                        )
                except Exception as error:
                    raise QdrantIndexError("同步本地案例到 Qdrant 失败") from error
            self._synced_namespaces.add(key)

    def needs_sync(self, *, namespace: str, outcome: str) -> bool:
        """判断当前客户端进程是否仍需从主存储补齐该范围的案例。"""
        with self._sync_lock:
            return (namespace, outcome) not in self._synced_namespaces

    def search(
        self,
        *,
        namespace: str,
        vector: Sequence[float],
        limit: int,
        outcome: str,
        filters: Mapping[str, object],
    ) -> list[dict[str, object]]:
        """按结果类型、命名空间和严格元数据过滤返回向量近邻。"""
        from qdrant_client import models

        try:
            if not self.client.collection_exists(self.collection_name):
                return []
            conditions = [
                models.FieldCondition(key="namespace", match=models.MatchValue(value=namespace)),
                models.FieldCondition(key="outcome", match=models.MatchValue(value=outcome)),
            ]
            if filters.get("language") is not None:
                conditions.append(models.FieldCondition(
                    key="retrieval_scope.language", match=models.MatchValue(value=filters["language"]),
                ))
            repository_id = filters.get("repository_id")
            if repository_id is not None:
                conditions.append(models.FieldCondition(
                    key="retrieval_scope.repository_id", match=models.MatchValue(value=repository_id),
                ))
            file_paths = filters.get("file_paths")
            if file_paths:
                conditions.append(models.FieldCondition(
                    key="retrieval_scope.file_paths", match=models.MatchAny(any=list(file_paths)),
                ))
            dependency_versions = filters.get("dependency_versions")
            if isinstance(dependency_versions, Mapping):
                for name, version in dependency_versions.items():
                    conditions.append(models.FieldCondition(
                        key=f"retrieval_scope.dependency_versions.{name}",
                        match=models.MatchValue(value=version),
                    ))
            response = self.client.query_points(
                collection_name=self.collection_name,
                query=list(vector),
                query_filter=models.Filter(must=conditions),
                limit=limit,
                with_payload=True,
            )
        except Exception as error:
            raise QdrantIndexError("检索 Qdrant 向量失败") from error
        return [
            {
                "case_id": point.payload["case_id"],
                "revision": int(point.payload.get("revision", 1)),
                "outcome": point.payload["outcome"],
                "score": float(point.score),
            }
            for point in response.points
            if isinstance(point.payload, dict) and point.payload.get("case_id")
        ]

    def close(self) -> None:
        """关闭客户端连接；支持内存测试客户端和远端客户端。"""
        close = getattr(self.client, "close", None)
        if callable(close):
            close()


@lru_cache(maxsize=8)
def _shared_index(url, api_key, collection, timeout):
    return QdrantVectorIndex.connect(url, api_key, collection, timeout)


def configured_qdrant_index() -> QdrantVectorIndex | None:
    """返回当前进程共享的环境配置客户端，避免每个知识库请求重复建连。"""
    return QdrantVectorIndex.from_environment()
