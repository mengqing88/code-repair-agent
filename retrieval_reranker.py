"""可选的 Cross-Encoder 重排器；模型按需加载，缺失时由调用方回退原排序。"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
import math
import os
from pathlib import Path
from threading import Lock
from typing import Sequence

from dotenv import dotenv_values


class RerankerUnavailable(RuntimeError):
    """Cross-Encoder 未安装、模型不可用或返回分数无效。"""


@dataclass
class CrossEncoderReranker:
    """按查询与候选案例成对预测相关度分数。"""

    model_name: str
    _model: object | None = field(default=None, init=False, repr=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    @classmethod
    def from_environment(cls, env_path: Path | None = None) -> CrossEncoderReranker | None:
        """读取 CROSS_ENCODER_MODEL；未配置时不创建重排器。"""
        config_path = env_path if env_path is not None else Path(__file__).resolve().with_name(".env")
        settings = dotenv_values(config_path, encoding="utf-8-sig", interpolate=False)
        model_name = os.getenv("CROSS_ENCODER_MODEL") or settings.get("CROSS_ENCODER_MODEL")
        return cls(model_name.strip()) if isinstance(model_name, str) and model_name.strip() else None

    def _load_model(self):
        """首次重排时加载模型，避免未启用功能时下载权重。"""
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is not None:
                return self._model
            try:
                from sentence_transformers import CrossEncoder
                self._model = CrossEncoder(self.model_name)
            except Exception as error:
                raise RerankerUnavailable("Cross-Encoder 模型未安装或无法加载") from error
        return self._model

    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        """返回与 documents 顺序一一对应的有限相关度分数。"""
        if not documents:
            return []
        model = self._load_model()
        try:
            raw_scores = model.predict(
                [(query, document) for document in documents], batch_size=16, show_progress_bar=False,
            )
            scores = [float(value) for value in raw_scores]
        except Exception as error:
            raise RerankerUnavailable("Cross-Encoder 预测失败") from error
        if len(scores) != len(documents) or any(not math.isfinite(value) for value in scores):
            raise RerankerUnavailable("Cross-Encoder 返回的分数数量或数值无效")
        return scores


@lru_cache(maxsize=1)
def configured_reranker() -> CrossEncoderReranker | None:
    """返回当前进程共享的环境配置重排器；未配置时返回 None。"""
    return CrossEncoderReranker.from_environment()
