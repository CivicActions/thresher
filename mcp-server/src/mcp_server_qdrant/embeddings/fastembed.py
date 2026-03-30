import asyncio

from fastembed import TextEmbedding
from fastembed.common.model_description import DenseModelDescription

from mcp_server_qdrant.embeddings.base import EmbeddingProvider


class FastEmbedProvider(EmbeddingProvider):
    """
    FastEmbed implementation of the embedding provider.
    :param model_name: The name of the FastEmbed model to use.
    :param index_prefix: Prefix prepended to texts during indexing (embed_documents).
    :param query_prefix: Prefix prepended to query text during search (embed_query).
    """

    def __init__(
        self,
        model_name: str,
        index_prefix: str = "",
        query_prefix: str = "",
        vector_name: str | None = None,
    ):
        self.model_name = model_name
        self.index_prefix = index_prefix
        self.query_prefix = query_prefix
        self._vector_name_override = vector_name
        self.embedding_model = TextEmbedding(model_name)

    async def embed_documents(self, documents: list[str]) -> list[list[float]]:
        """Embed a list of documents into vectors, prepending index_prefix if configured."""
        # Run in a thread pool since FastEmbed is synchronous
        if self.index_prefix:
            documents = [self.index_prefix + doc for doc in documents]
        loop = asyncio.get_event_loop()
        embeddings = await loop.run_in_executor(
            None, lambda: list(self.embedding_model.passage_embed(documents))
        )
        return [embedding.tolist() for embedding in embeddings]

    async def embed_query(self, query: str) -> list[float]:
        """Embed a query into a vector, prepending query_prefix if configured."""
        if self.query_prefix:
            query = self.query_prefix + query
        # Run in a thread pool since FastEmbed is synchronous
        loop = asyncio.get_event_loop()
        embeddings = await loop.run_in_executor(
            None, lambda: list(self.embedding_model.query_embed([query]))
        )
        return embeddings[0].tolist()

    def get_vector_name(self) -> str:
        """
        Return the name of the vector for the Qdrant collection.
        If a vector_name override was provided at construction, use it.
        Otherwise fall back to the FastEmbed convention (``fast-{model}``)
        for backward compatibility with collections created before 0.6.0.
        """
        if self._vector_name_override:
            return self._vector_name_override
        model_name = self.embedding_model.model_name.split("/")[-1].lower()
        return f"fast-{model_name}"

    def get_vector_size(self) -> int:
        """Get the size of the vector for the Qdrant collection."""
        model_description: DenseModelDescription = self.embedding_model._get_model_description(
            self.model_name
        )
        return model_description.dim
