from mcp_server_qdrant.embeddings.base import EmbeddingProvider
from mcp_server_qdrant.embeddings.types import EmbeddingProviderType
from mcp_server_qdrant.settings import CollectionConfig, EmbeddingProviderSettings


def create_embedding_provider(
    settings: EmbeddingProviderSettings,
    index_prefix: str = "",
    query_prefix: str = "",
) -> EmbeddingProvider:
    """Create an embedding provider based on the specified type.

    :param settings: The settings for the embedding provider.
    :param index_prefix: Optional prefix to prepend to documents during indexing.
    :param query_prefix: Optional prefix to prepend to queries during search.
    :return: An instance of the specified embedding provider.
    """
    if settings.provider_type == EmbeddingProviderType.FASTEMBED:
        from mcp_server_qdrant.embeddings.fastembed import FastEmbedProvider

        return FastEmbedProvider(
            settings.model_name, index_prefix=index_prefix, query_prefix=query_prefix
        )
    else:
        raise ValueError(f"Unsupported embedding provider: {settings.provider_type}")


def create_collection_providers(
    collections: list[CollectionConfig],
) -> dict[str, EmbeddingProvider]:
    """Create a mapping of collection name to EmbeddingProvider.

    Re-uses provider instances when multiple collections share the same model and prefixes.

    :param collections: List of per-collection embedding configurations.
    :return: Dict mapping collection name to its EmbeddingProvider.
    """
    from mcp_server_qdrant.embeddings.fastembed import FastEmbedProvider

    # Cache providers by (model, index_prefix, query_prefix, vector_name) to avoid loading twice
    provider_cache: dict[tuple[str, str, str, str], EmbeddingProvider] = {}
    providers: dict[str, EmbeddingProvider] = {}

    for col in collections:
        vector_name = col.vector_name if col.vector_name else None
        key = (col.model, col.index_prefix, col.query_prefix, vector_name or "")
        if key not in provider_cache:
            provider_cache[key] = FastEmbedProvider(
                col.model,
                index_prefix=col.index_prefix,
                query_prefix=col.query_prefix,
                vector_name=vector_name,
            )
        providers[col.name] = provider_cache[key]

    return providers
