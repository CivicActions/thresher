"""Tests for task prefix handling in FastEmbedProvider (US5)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np


class TestFastEmbedProviderPrefixes:
    """Tests verifying prefix prepending behavior in FastEmbedProvider."""

    @patch("mcp_server_qdrant.embeddings.fastembed.TextEmbedding")
    def test_embed_query_prepends_query_prefix(self, mock_cls):
        import asyncio

        from mcp_server_qdrant.embeddings.fastembed import FastEmbedProvider

        mock_model = MagicMock()
        mock_model.query_embed.return_value = iter([np.array([0.1, 0.2, 0.3])])
        mock_cls.return_value = mock_model

        provider = FastEmbedProvider("nomic/text", query_prefix="search_query: ")
        result = asyncio.run(provider.embed_query("what is MUMPS?"))

        mock_model.query_embed.assert_called_once_with(["search_query: what is MUMPS?"])
        assert result == [0.1, 0.2, 0.3]

    @patch("mcp_server_qdrant.embeddings.fastembed.TextEmbedding")
    def test_embed_query_no_prefix_passes_unchanged(self, mock_cls):
        import asyncio

        from mcp_server_qdrant.embeddings.fastembed import FastEmbedProvider

        mock_model = MagicMock()
        mock_model.query_embed.return_value = iter([np.array([0.1, 0.2])])
        mock_cls.return_value = mock_model

        provider = FastEmbedProvider("jina/code")
        asyncio.run(provider.embed_query("def foo():"))

        mock_model.query_embed.assert_called_once_with(["def foo():"])

    @patch("mcp_server_qdrant.embeddings.fastembed.TextEmbedding")
    def test_embed_documents_prepends_index_prefix(self, mock_cls):
        import asyncio

        from mcp_server_qdrant.embeddings.fastembed import FastEmbedProvider

        mock_model = MagicMock()
        mock_model.passage_embed.return_value = iter([
            np.array([0.1, 0.2, 0.3]), np.array([0.4, 0.5, 0.6])
        ])
        mock_cls.return_value = mock_model

        provider = FastEmbedProvider("nomic/text", index_prefix="search_document: ")
        result = asyncio.run(provider.embed_documents(["hello world", "foo bar"]))

        mock_model.passage_embed.assert_called_once_with(
            ["search_document: hello world", "search_document: foo bar"]
        )
        assert len(result) == 2

    @patch("mcp_server_qdrant.embeddings.fastembed.TextEmbedding")
    def test_embed_documents_no_prefix_passes_unchanged(self, mock_cls):
        import asyncio

        from mcp_server_qdrant.embeddings.fastembed import FastEmbedProvider

        mock_model = MagicMock()
        mock_model.passage_embed.return_value = iter([np.array([0.1, 0.2])])
        mock_cls.return_value = mock_model

        provider = FastEmbedProvider("jina/code")
        asyncio.run(provider.embed_documents(["def foo():"]))

        mock_model.passage_embed.assert_called_once_with(["def foo():"])

    @patch("mcp_server_qdrant.embeddings.fastembed.TextEmbedding")
    def test_both_prefixes_independent(self, mock_cls):
        """index_prefix and query_prefix are applied to their respective methods only."""
        import asyncio

        from mcp_server_qdrant.embeddings.fastembed import FastEmbedProvider

        mock_model = MagicMock()
        mock_model.passage_embed.return_value = iter([np.array([0.1, 0.2])])
        mock_model.query_embed.return_value = iter([np.array([0.1, 0.2])])
        mock_cls.return_value = mock_model

        provider = FastEmbedProvider(
            "nomic/text",
            index_prefix="search_document: ",
            query_prefix="search_query: ",
        )

        asyncio.run(provider.embed_documents(["a document"]))
        asyncio.run(provider.embed_query("a query"))

        mock_model.passage_embed.assert_called_once_with(["search_document: a document"])
        mock_model.query_embed.assert_called_once_with(["search_query: a query"])
