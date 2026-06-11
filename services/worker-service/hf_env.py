"""
HuggingFace offline-mode bootstrap — MUST be imported before any
sentence_transformers / transformers import happens.

On Windows, SentenceTransformer.__init__ calls find_adapter_config_file
which enters cached_file → cached_files in transformers/utils/hub.py.
That network-probing call stack is deep enough (C→Python callbacks) to
exceed Python's default 1 000-frame recursion limit, raising:
    RecursionError: maximum recursion depth exceeded while calling a Python object

Setting HF_HUB_OFFLINE=1 + TRANSFORMERS_OFFLINE=1 *before* the first
import of sentence_transformers/transformers makes those hub utilities
return immediately without touching the network, so the deep call stack
is never entered.

The model (intfloat/multilingual-e5-small) is already fully cached in
~/.cache/huggingface/hub, so offline mode is completely safe here.
"""

from __future__ import annotations

import os
import sys

# Must be set before any HuggingFace / transformers import.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

# Prevent PyTorch from probing/loading CUDA DLLs — saves ~200 MB commit charge.
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
# Skip TorchScript JIT compilation — reduces memory and avoids background threads.
os.environ.setdefault("PYTORCH_JIT", "0")

# Belt-and-suspenders: raise the limit well above the HF hub call depth.
# The HF hub stack on Windows can reach ~3 000 frames; 10 000 gives a
# generous buffer while still catching genuine infinite-recursion bugs.
sys.setrecursionlimit(10_000)

# ---------------------------------------------------------------------------
# Disable memory-mapping in safetensors on Windows.
#
# safetensors.safe_open() defaults to mmap, which requires Windows commit
# charge equal to the mapped file size. When multiple services are running,
# this exhausts the commit limit, causing:
#     OSError: The paging file is too small … (os error 1455)
#
# Strategy (three tiers):
#   1. Try safe_open(disable_mmap=True) — works on safetensors >= 0.4.5
#   2. Try safe_open() without disable_mmap — works if paging file has room
#   3. Fall back to _SafeOpenInMemory — reads the .safetensors file using
#      plain Python file I/O (struct + read), no mmap, no commit charge
# ---------------------------------------------------------------------------
if os.name == "nt":
    try:
        import safetensors as _st                          # lightweight Rust ext — no torch

        _orig_safe_open = _st.safe_open

        class _SafeOpenInMemory:
            """Drop-in for safe_open() — reads entire file into RAM (no mmap).

            The safetensors binary format is:
              [8 bytes: header_size as uint64 LE]
              [header_size bytes: JSON metadata]
              [remaining bytes: raw tensor data]

            This reader parses the header, seeks to each tensor's offsets,
            and builds torch tensors via torch.frombuffer — zero mmap.
            """

            _DTYPE_MAP = None  # populated lazily to avoid importing torch at patch time

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
                # safetensors build lacks disable_mmap — try without it
                kwargs.pop("disable_mmap", None)
                try:
                    return _orig_safe_open(*args, **kwargs)
                except OSError:
                    # Paging file exhausted — fall back to pure-Python reader
                    return _SafeOpenInMemory(*args, **kwargs)

        _st.safe_open = _safe_open_no_mmap
    except ImportError:
        pass

