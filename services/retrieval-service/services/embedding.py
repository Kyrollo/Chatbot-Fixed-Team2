import logging
from functools import lru_cache

from sentence_transformers import SentenceTransformer

from config import settings

logger = logging.getLogger(__name__)


class EmbeddingService:
    """
    Loads intfloat/multilingual-e5-base once and exposes embed_query().

    Query prefix must be "query: " — the worker service indexes documents
    with "passage: " prefix. Both sides must stay consistent or retrieval
    quality degrades significantly.
    """

    def __init__(self) -> None:
        logger.info("Loading embedding model: %s", settings.EMBEDDING_MODEL)
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
