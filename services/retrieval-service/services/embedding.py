import logging
import os
import sys
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
import network_bootstrap  # noqa: F401, E402

from config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Disable memory-mapping in safetensors on Windows (same patch as hf_env.py).
#
# safetensors.safe_open() defaults to mmap, which requires Windows commit
# charge equal to the mapped file size.  When multiple services are running,
# this exhausts the commit limit, causing:
#     OSError: The paging file is too small … (os error 1455)
# or  [WinError 1114] A dynamic link library (DLL) initialization routine failed.
#
# Strategy (three tiers):
#   1. Try safe_open(disable_mmap=True) — works on safetensors >= 0.4.5
#   2. Try safe_open() without disable_mmap — works if paging file has room
#   3. Fall back to _SafeOpenInMemory — reads the .safetensors file using
#      plain Python file I/O (struct + read), no mmap, no commit charge
# ---------------------------------------------------------------------------
if os.name == "nt":
    os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
    os.environ.setdefault("PYTORCH_JIT", "0")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    try:
        import safetensors as _st

        _orig_safe_open = _st.safe_open

        class _SafeOpenInMemory:
            """Drop-in for safe_open() — reads entire file into RAM (no mmap).

            The safetensors binary format is:
              [8 bytes: header_size as uint64 LE]
              [header_size bytes: JSON metadata]
              [remaining bytes: raw tensor data]
            """

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
                    # Paging file exhausted — fall back to pure-Python reader
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

    NOTE: SentenceTransformer (and therefore PyTorch) is imported lazily
    inside __init__, NOT at module level. This prevents ~3 GB of PyTorch
    DLLs from loading at uvicorn startup, avoiding WinError 1114 / 1455
    when multiple services compete for Windows commit charge.
    """

    def __init__(self) -> None:
        logger.info("Loading embedding model: %s", settings.EMBEDDING_MODEL)
        from sentence_transformers import SentenceTransformer  # noqa: PLC0415

        self._model = SentenceTransformer(settings.EMBEDDING_MODEL)
        logger.info("Embedding model ready.")

    def embed_query(self, query: str) -> list[float]:
        vector = self._model.encode(
            f"query: {query}",
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return vector.tolist()


@lru_cache(maxsize=1)
def get_embedding_service() -> EmbeddingService:
    """Singleton — model is loaded once, reused for every request."""
    return EmbeddingService()
