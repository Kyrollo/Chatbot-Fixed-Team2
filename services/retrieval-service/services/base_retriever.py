"""
BaseRetriever — common contract for all retrieval engines.

Every retriever (vector, BM25, graph, ...) implements this interface
so the pipeline can treat them uniformly.
"""


class BaseRetriever:
    async def search(self, query: str, domain_id: str, top_k: int) -> list:
        """
        Search for relevant chunks.

        Args:
            query:     Raw user query string.
            domain_id: Scopes the search to one domain's documents.
            top_k:     Maximum number of results to return.

        Returns:
            List of ChunkResult objects (may be empty on failure).
        """
        raise NotImplementedError
