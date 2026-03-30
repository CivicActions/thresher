"""Tests for per-collection provider routing in QdrantConnector."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp_server_qdrant.embeddings.factory import create_collection_providers
from mcp_server_qdrant.settings import CollectionConfig


def _make_collection_config(name: str, model: str, vector_name: str, **kwargs) -> CollectionConfig:
    return CollectionConfig(
        name=name, model=model, vector_name=vector_name, vector_size=768, **kwargs
    )


class TestCreateCollectionProviders:
    @patch("mcp_server_qdrant.embeddings.fastembed.TextEmbedding")
    def test_creates_provider_per_collection(self, mock_cls):
        mock_cls.return_value = MagicMock()
        collections = [
            _make_collection_config("vista", "nomic/text", "nomic-v1.5"),
            _make_collection_config("vista-source", "jina/code", "jina-code-v2"),
        ]
        providers = create_collection_providers(collections)
        assert set(providers.keys()) == {"vista", "vista-source"}

    @patch("mcp_server_qdrant.embeddings.fastembed.TextEmbedding")
    def test_reuses_provider_for_same_model_and_prefixes(self, mock_cls):
        mock_cls.return_value = MagicMock()
        collections = [
            _make_collection_config("col1", "nomic/text", "nomic-v1.5"),
            _make_collection_config("col2", "nomic/text", "nomic-v1.5"),
        ]
        providers = create_collection_providers(collections)
        # Same model + prefix → same provider instance
        assert providers["col1"] is providers["col2"]
        # TextEmbedding should only be loaded once
        assert mock_cls.call_count == 1

    @patch("mcp_server_qdrant.embeddings.fastembed.TextEmbedding")
    def test_creates_separate_provider_for_different_prefixes(self, mock_cls):
        mock_cls.return_value = MagicMock()
        collections = [
            _make_collection_config("col1", "nomic/text", "nomic-v1.5", query_prefix="q: "),
            _make_collection_config("col2", "nomic/text", "nomic-v1.5"),
        ]
        providers = create_collection_providers(collections)
        # Different prefix → different provider instance
        assert providers["col1"] is not providers["col2"]
        assert mock_cls.call_count == 2

    @patch("mcp_server_qdrant.embeddings.fastembed.TextEmbedding")
    def test_empty_collections_returns_empty_dict(self, mock_cls):
        providers = create_collection_providers([])
        assert providers == {}
        mock_cls.assert_not_called()


class TestQdrantConnectorProviderRouting:
    """Tests that QdrantConnector routes to the correct provider per collection."""

    def _make_connector(self, collection_providers=None):
        from mcp_server_qdrant.qdrant import QdrantConnector

        default_provider = MagicMock()
        default_provider.get_vector_name.return_value = "default-vec"
        default_provider.get_vector_size.return_value = 384

        with patch("mcp_server_qdrant.qdrant.AsyncQdrantClient"):
            connector = QdrantConnector(
                qdrant_url=None,
                qdrant_api_key=None,
                collection_name="default",
                embedding_provider=default_provider,
                embedding_providers=collection_providers,
            )
        return connector, default_provider

    def test_uses_default_provider_when_no_per_collection_provider(self):
        connector, default_prov = self._make_connector()
        provider = connector._get_provider("any-collection")
        assert provider is default_prov

    def test_uses_per_collection_provider_when_configured(self):
        per_col_provider = MagicMock()
        connector, default_prov = self._make_connector({"vista": per_col_provider})
        assert connector._get_provider("vista") is per_col_provider

    def test_falls_back_to_default_for_unknown_collection(self):
        per_col_provider = MagicMock()
        connector, default_prov = self._make_connector({"vista": per_col_provider})
        assert connector._get_provider("unknown") is default_prov

    def test_uses_default_for_none_collection_name(self):
        per_col_provider = MagicMock()
        connector, default_prov = self._make_connector({"vista": per_col_provider})
        assert connector._get_provider(None) is default_prov

    @pytest.mark.asyncio
    async def test_search_uses_per_collection_provider(self):
        per_col_provider = AsyncMock()
        per_col_provider.embed_query = AsyncMock(return_value=[0.1, 0.2, 0.3])
        per_col_provider.get_vector_name = MagicMock(return_value="nomic-v1.5")

        connector, _ = self._make_connector({"vista": per_col_provider})

        mock_client = AsyncMock()
        mock_client.collection_exists = AsyncMock(return_value=True)
        mock_client.query_points = AsyncMock(return_value=MagicMock(points=[]))
        connector._client = mock_client

        await connector.search("test query", collection_name="vista")

        per_col_provider.embed_query.assert_called_once_with("test query")

    @pytest.mark.asyncio
    async def test_store_uses_per_collection_provider(self):
        from mcp_server_qdrant.qdrant import Entry

        per_col_provider = AsyncMock()
        per_col_provider.embed_documents = AsyncMock(return_value=[[0.1, 0.2, 0.3]])
        per_col_provider.get_vector_name = MagicMock(return_value="nomic-v1.5")

        connector, _ = self._make_connector({"vista": per_col_provider})

        mock_client = AsyncMock()
        mock_client.collection_exists = AsyncMock(return_value=True)
        connector._client = mock_client

        entry = Entry(content="test content")
        await connector.store(entry, collection_name="vista")

        per_col_provider.embed_documents.assert_called_once_with(["test content"])
