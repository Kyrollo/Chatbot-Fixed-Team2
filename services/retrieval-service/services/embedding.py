import asyncio
import logging
import os
import sys
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
import network_bootstrap  # noqa: F401, E402

from config import settings

logger = logging.getLogger(__name__)


def _validate_model_path(model_name: str) -> Path:
    model_path = Path(model_name)
    if not model_path.exists():
        raise FileNotFoundError(
            f"Embedding model path does not exist: {model_name}. "
            "Set EMBEDDING_MODEL to a downloaded local model directory."
        )
    return model_path

# ---------------------------------------------------------------------------
# Disable memory-mapping in safetensors on Windows (same patch as hf_env.py).
# ---------------------------------------------------------------------------
if os.name == "nt":
    os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
    os.environ.setdefault("PYTORCH_JIT", "0")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    try:
        import safetensors as _st

        _orig_safe_open = _st.safe_open

        class _SafeOpenInMemory:
            _DTYPE_MAP = None

            def __init__(self, filename, framework="pt", device="cpu"):
                import json
                import struct
                import torch

                if _SafeOpenInMemory._DTYPE_MAP is None:
                    _SafeOpenInMemory._DTYPE_MAP = {
                        "F64": torch.float64, "F32": torch.float32,
                        "F16": torch.float16, "BF16": torch.bfloat16,
                        "I64": torch.int64, "I32": torch.int32,
                        "I16": torch.int16, "I8": torch.int8,
                        "U8": torch.uint8, "BOOL": torch.bool,
                    }

                self._tensors = {}
                self._metadata = {}

                with open(str(filename), "rb") as f:
                    header_size = struct.unpack("<Q", f.read(8))[0]
                    header = json.loads(f.read(header_size))
                    data_base = 8 + header_size

                    if "__metadata__" in header:
                        self._metadata = header.pop("__metadata__")

                    for name, info in header.items():
                        start, end = info["data_offsets"]
                        f.seek(data_base + start)
                        raw = bytearray(f.read(end - start))
                        dtype = self._DTYPE_MAP.get(info["dtype"], torch.float32)
                        tensor = torch.frombuffer(raw, dtype=dtype).reshape(info["shape"])
                        if device != "cpu":
                            tensor = tensor.to(device)
                        self._tensors[name] = tensor

            def keys(self):
                return self._tensors.keys()

            def get_tensor(self, name):
                return self._tensors[name]

            def metadata(self):
                return self._metadata

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        def _safe_open_no_mmap(*args, **kwargs):
            kwargs.setdefault("disable_mmap", True)
            try:
                return _orig_safe_open(*args, **kwargs)
            except TypeError:
                kwargs.pop("disable_mmap", None)
                try:
                    return _orig_safe_open(*args, **kwargs)
                except OSError:
                    return _SafeOpenInMemory(*args, **kwargs)

        _st.safe_open = _safe_open_no_mmap
    except ImportError:
        pass


class EmbeddingService:
    """
    Loads intfloat/multilingual-e5-small once and exposes embed_query().

    Query prefix must be "query: " — the worker service indexes documents
    with "passage: " prefix. Both sides must stay consistent or retrieval
    quality degrades significantly.
    """

    def __init__(self) -> None:
        self._model = None
        self._lock = asyncio.Lock()

    @property
    def is_ready(self) -> bool:
        return self._model is not None

    def _load_model(self) -> None:
        import time
        t0 = time.perf_counter()
        model_path = _validate_model_path(settings.EMBEDDING_MODEL)
        logger.info("Loading embedding model in ONNX Runtime: %s (exists=%s)", model_path, model_path.exists())
        from services.onnx_client import ONNXEmbeddingClient
        self._model = ONNXEmbeddingClient(str(model_path))
        logger.info("Embedding model ready (ONNX). (took %.2fs)", time.perf_counter() - t0)

    async def warmup(self) -> None:
        if self._model is None:
            async with self._lock:
                if self._model is None:
                    await asyncio.to_thread(self._load_model)

    async def embed_query(self, query: str) -> list[float]:
        if self._model is None:
            await self.warmup()

        vector = await asyncio.to_thread(
            self._model.encode,
            f"query: {query}",
            normalize_embeddings=True,
        )
        return vector.tolist()


@lru_cache(maxsize=1)
def get_embedding_service() -> EmbeddingService:
    """Singleton — model is loaded once, reused for every request."""
    return EmbeddingService()
