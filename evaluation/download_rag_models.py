"""Download only pinned public model artifacts for the local RAG acceptance."""
import os
from pathlib import Path

MODELS = [
    ("cross-encoder/mmarco-mMiniLMv2-L12-H384-v1", "1427fd652930e4ba29e8149678df786c240d8825", "mmarco-mMiniLMv2"),
    ("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", "e8f8c211226b894fcb81acc59f3b34ba3efd5f42", "paraphrase-multilingual-MiniLM"),
]


def main():
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    from huggingface_hub import snapshot_download
    root = Path(__file__).resolve().parents[1] / "work/models"
    for repository, revision, name in MODELS:
        snapshot_download(repository, revision=revision, local_dir=root / name,
                          allow_patterns=["*.json", "*.safetensors", "*.model", "README.md"],
                          ignore_patterns=["onnx/*", "openvino/*"])
        print(f"Ready: {repository}@{revision}")


if __name__ == "__main__":
    main()
