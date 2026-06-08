from sentence_transformers import SentenceTransformer
import numpy as np

# ------------------------------------------------------------------
# Model is loaded ONCE when the worker process starts.
# get_model() returns the same instance every call — no re-loading.
# Both embed.py and chunk.py use the same model instance.
# ------------------------------------------------------------------

_model: SentenceTransformer | None = None

EMBEDDING_DIM = 768
BATCH_SIZE    = 32


def get_model() -> SentenceTransformer:
    """
    Returns the shared model instance.
    Loads it on first call, then caches it for the lifetime of the worker.
    """
    global _model
    if _model is None:
        print("Loading multilingual-e5-base model...")
        _model = SentenceTransformer("intfloat/multilingual-e5-base")
        print("✓ Model loaded")
    return _model


def embed_chunks(chunks: list[dict]) -> list[dict]:
    """
    Takes chunks from chunk.py and adds an 'embedding' key to each.

    multilingual-e5-base requires a prefix:
    - Documents at ingest time → "passage: " + text
    - Queries   at query time  → "query: "   + text
    Both must use the same model for vectors to be in the same space.

    Returns the same list of chunks with 'embedding' added:
    [
        {
            "chunk_id":  "...",
            "text":      "...",
            "embedding": [0.12, -0.34, ...],   ← 768 floats
            ...
        }
    ]
    """
    model = get_model()
    texts = [f"passage: {chunk['text']}" for chunk in chunks]

    print(f"  Embedding {len(texts)} chunks in batches of {BATCH_SIZE}...")

    embeddings = model.encode(
        texts,
        batch_size=BATCH_SIZE,
        show_progress_bar=True,
        normalize_embeddings=True,
        convert_to_numpy=True,
    )

    for chunk, embedding in zip(chunks, embeddings):
        chunk["embedding"] = embedding.tolist()

    print(f"  ✓ Embedded {len(chunks)} chunks — dim={EMBEDDING_DIM}")
    return chunks