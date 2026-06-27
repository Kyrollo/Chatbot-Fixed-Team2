from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import numpy as np

# ---------------------------------------------------------------------------
# ONNX embedding client using onnxruntime and transformers.AutoTokenizer.
# Eliminates sentence-transformers and PyTorch imports from the active path.
# ---------------------------------------------------------------------------

class ONNXEmbeddingClient:
    """
    Client for running sentence embeddings using ONNX Runtime.
    Specially optimized for CPU execution with support for quantized models.
    """
    def __init__(self, model_dir: str):
        import onnxruntime as ort
        from transformers import AutoTokenizer

        self.model_dir = Path(model_dir)

        # Check if the folder contains a nested "onnx" folder
        onnx_dir = self.model_dir / "onnx"
        if onnx_dir.exists():
            resolved_dir = onnx_dir
        else:
            resolved_dir = self.model_dir

        # Prioritize quantized model for speed/low CPU footprint
        model_file = None
        for filename in ["model_qint8_avx512_vnni.onnx", "model_O4.onnx", "model.onnx"]:
            candidate = resolved_dir / filename
            if candidate.exists():
                model_file = candidate
                break

        if not model_file:
            raise FileNotFoundError(
                f"No ONNX model file found in {resolved_dir}. "
                "Ensure E5 model has ONNX files exported."
            )

        print(f"ONNX Client: loading tokenizer from {self.model_dir}...")
        self.tokenizer = AutoTokenizer.from_pretrained(
            str(self.model_dir), 
            local_files_only=True
        )

        print(f"ONNX Client: initializing session for {model_file}...")
        self.session = ort.InferenceSession(
            str(model_file),
            providers=["CPUExecutionProvider"]
        )
        self.expected_inputs = {node.name for node in self.session.get_inputs()}

    def encode(
        self, 
        texts: list[str] | str, 
        normalize_embeddings: bool = True, 
        batch_size: int = 32, 
        **kwargs
    ) -> np.ndarray:
        if isinstance(texts, str):
            texts = [texts]

        all_embeddings = []

        for i in range(0, len(texts), batch_size):
            batch_texts = texts[i : i + batch_size]
            encoded = self.tokenizer(
                batch_texts,
                padding=True,
                truncation=True,
                max_length=512,
                return_tensors="np"
            )

            # E5 models require input_ids and attention_mask.
            # Some also require token_type_ids (generate if missing but expected).
            ort_inputs = {
                "input_ids": encoded["input_ids"],
                "attention_mask": encoded["attention_mask"]
            }
            if "token_type_ids" in self.expected_inputs:
                if "token_type_ids" in encoded:
                    ort_inputs["token_type_ids"] = encoded["token_type_ids"]
                else:
                    ort_inputs["token_type_ids"] = np.zeros_like(encoded["input_ids"])

            # Run inference
            outputs = self.session.run(None, ort_inputs)
            token_embeddings = outputs[0]  # shape: (batch_size, seq_len, hidden_dim)

            # Mean Pooling
            attention_mask = encoded["attention_mask"]
            input_mask_expanded = np.expand_dims(attention_mask, axis=-1)
            input_mask_expanded = np.broadcast_to(input_mask_expanded, token_embeddings.shape)

            sum_embeddings = np.sum(token_embeddings * input_mask_expanded, axis=1)
            sum_mask = np.sum(input_mask_expanded, axis=1)
            sum_mask = np.clip(sum_mask, a_min=1e-9, a_max=None)

            batch_embeddings = sum_embeddings / sum_mask

            # L2 Normalization (required by E5 models)
            if normalize_embeddings:
                norms = np.linalg.norm(batch_embeddings, axis=1, keepdims=True)
                batch_embeddings = batch_embeddings / np.clip(norms, a_min=1e-9, a_max=None)

            all_embeddings.append(batch_embeddings)

        return np.vstack(all_embeddings)
