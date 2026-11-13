"""可选的 Cross-Encoder 重排器；模型按需加载，缺失时由调用方回退原排序。"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
import math
import hashlib
from pathlib import Path
from threading import Lock
from typing import Sequence

from runtime_config import load_settings, setting


class RerankerUnavailable(RuntimeError):
    """Cross-Encoder 未安装、模型不可用或返回分数无效。"""


@dataclass
class CrossEncoderReranker:
    """按查询与候选案例成对预测相关度分数。"""

    model_name: str
    backend: str = "torch"
    onnx_path: str = ""
    provider: str = "CPUExecutionProvider"
    threads: int = 8
    candidate_count: int = 0
    _model: object | None = field(default=None, init=False, repr=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)
    _inference_lock: Lock = field(default_factory=Lock, init=False, repr=False)

    @classmethod
    def from_environment(cls, env_path: Path | None = None) -> CrossEncoderReranker | None:
        """读取 CROSS_ENCODER_MODEL；未配置时不创建重排器。"""
        settings = load_settings(env_path)
        value = lambda key, default="": str(setting(key, settings) or default).strip()
        name = value("CROSS_ENCODER_MODEL")
        if not name or name.lower() in {"off", "disabled"}:
            return None
        try:
            threads = int(value("CROSS_ENCODER_THREADS", "8"))
            count = int(value("CROSS_ENCODER_CANDIDATES", "0"))
        except ValueError as error:
            raise RerankerUnavailable("重排线程和候选数必须是整数") from error
        backend = value("CROSS_ENCODER_BACKEND", "torch").lower()
        if backend not in {"torch", "onnx"} or not 1 <= threads <= 32 or count not in {0, 5, 10}:
            raise RerankerUnavailable("重排后端、线程或候选数配置无效")
        return cls(name, backend, value("CROSS_ENCODER_ONNX_PATH"),
                   value("CROSS_ENCODER_PROVIDER", "CPUExecutionProvider"), threads, count)

    def artifact_identity(self) -> dict[str, str]:
        """冻结本地模型内容身份；不把大权重复制进每个任务。"""
        files = []
        directory = Path(self.model_name)
        if directory.is_dir():
            names = ("config.json", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json",
                     "sentencepiece.bpe.model", "vocab.txt", "vocab.json", "merges.txt")
            files.extend(directory / name for name in names if (directory / name).is_file())
        if self.backend == "onnx":
            files.append(Path(self.onnx_path))
        elif directory.is_dir():
            files.extend(directory.glob("*.safetensors"))
            files.extend(directory.glob("pytorch_model*.bin"))
        return {str(path.resolve()): _file_hash(str(path.resolve()), path.stat().st_size,
                                               path.stat().st_mtime_ns) for path in files}

    def _load_onnx(self):
        import onnxruntime as ort
        from transformers import AutoTokenizer
        if self.provider not in ort.get_available_providers():
            raise RerankerUnavailable("指定的 ONNX provider 不可用")
        if not Path(self.onnx_path).is_file() or not Path(self.model_name).is_dir():
            raise RerankerUnavailable("ONNX 重排需要本地权重和 tokenizer 目录")
        options = ort.SessionOptions()
        options.intra_op_num_threads = self.threads
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        if self.provider == "DmlExecutionProvider":
            options.enable_mem_pattern = False
        session = ort.InferenceSession(self.onnx_path, sess_options=options, providers=[self.provider])
        if session.get_providers()[0] != self.provider:
            raise RerankerUnavailable("ONNX 未使用指定 provider")
        tokenizer = AutoTokenizer.from_pretrained(self.model_name, local_files_only=True)
        # 每个进程首次加载后预热一次；冷启动耗时仍计入首个请求，不能当成消失。
        import numpy as np
        count = self.candidate_count or 10
        tokens = tokenizer(["Python 空输入异常修复"] * count, ["先检查输入是否为空，再读取元素。"] * count,
                           padding=True, truncation=True, max_length=512, return_tensors="np")
        session.run(None, {item.name: np.asarray(tokens[item.name], dtype=np.int64)
                           for item in session.get_inputs()})
        return session, tokenizer

    def warmup(self) -> None:
        """供长驻进程在接收查询前加载；一次性 worker 仍各有冷启动成本。"""
        self._load_model()

    def _load_model(self):
        """首次重排时加载模型，避免未启用功能时下载权重。"""
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is not None:
                return self._model
            try:
                if self.backend == "onnx":
                    self._model = self._load_onnx()
                else:
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
            with self._inference_lock:
                if self.backend == "onnx":
                    import numpy as np
                    session, tokenizer = model
                    scores = []
                    for start in range(0, len(documents), 16):
                        batch = list(documents[start:start + 16])
                        tokens = tokenizer([query] * len(batch), batch, padding=True, truncation=True,
                                           max_length=512, return_tensors="np")
                        feeds = {item.name: np.asarray(tokens[item.name], dtype=np.int64)
                                 for item in session.get_inputs()}
                        # 此适配器面向单输出回归重排模型，保留 Identity 原始 logits。
                        raw = np.asarray(session.run(None, feeds)[0])
                        if raw.shape not in {(len(batch),), (len(batch), 1)}:
                            raise RerankerUnavailable("ONNX 重排必须每对文本返回一个分数")
                        scores.extend(float(value) for value in raw.reshape(-1))
                else:
                    raw_scores = model.predict(
                        [(query, document) for document in documents], batch_size=16, show_progress_bar=False,
                    )
                    scores = [float(value) for value in raw_scores]
        except Exception as error:
            raise RerankerUnavailable("Cross-Encoder 预测失败") from error
        if len(scores) != len(documents) or any(not math.isfinite(value) for value in scores):
            raise RerankerUnavailable("Cross-Encoder 返回的分数数量或数值无效")
        return scores


@lru_cache(maxsize=16)
def _file_hash(path: str, size: int, modified: int) -> str:
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


@lru_cache(maxsize=4)
def _shared_reranker(name, backend, onnx_path, provider, threads, count, artifacts):
    return CrossEncoderReranker(name, backend, onnx_path, provider, threads, count)


def configured_reranker() -> CrossEncoderReranker | None:
    """返回当前进程共享的环境配置重排器；未配置时返回 None。"""
    config = CrossEncoderReranker.from_environment()
    if config is None:
        return None
    try:
        artifacts = tuple(sorted(config.artifact_identity().items()))
    except OSError:
        artifacts = (("unavailable", ""),)
    return _shared_reranker(config.model_name, config.backend, config.onnx_path,
                            config.provider, config.threads, config.candidate_count, artifacts)
