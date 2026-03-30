"""Tests for thresher_config.py — reading thresher YAML without importing thresher."""

import textwrap

import pytest

from mcp_server_qdrant.thresher_config import ThresherMCPConfig, read_thresher_config


class TestReadThresherConfig:
    """Tests for read_thresher_config()."""

    def test_multi_model_config(self, tmp_path):
        """Reads a multi-model config and produces correct collection mappings."""
        config = tmp_path / "config.yaml"
        config.write_text(
            textwrap.dedent("""\
            destination:
              qdrant:
                url: "https://qdrant.example.com:6333"
                api_key: "test-key"

            embedding:
              default: docs
              models:
                docs:
                  model: "nomic-ai/nomic-embed-text-v1.5"
                  vector_size: 768
                  vector_name: "nomic-v1.5"
                  max_tokens: 512
                  index_prefix: "search_document: "
                  query_prefix: "search_query: "
                code:
                  model: "jinaai/jina-embeddings-v2-base-code"
                  vector_size: 768
                  vector_name: "jina-code-v2"
                  max_tokens: 512

            routing:
              default_collection: "vista"
              rules:
                - name: rpms-source-code
                  collection: rpms-source
                  embedding: code
                - name: rpms-docs
                  collection: rpms
                - name: vista-source-code
                  collection: vista-source
                  embedding: code
            """)
        )

        result = read_thresher_config(config)

        assert isinstance(result, ThresherMCPConfig)
        assert result.qdrant_url == "https://qdrant.example.com:6333"
        assert result.qdrant_api_key == "test-key"
        assert result.default_collection == "vista"
        assert len(result.collections) == 4

        by_name = {c.name: c for c in result.collections}
        assert "rpms-source" in by_name
        assert "rpms" in by_name
        assert "vista-source" in by_name
        assert "vista" in by_name

        # Code collections use jina
        assert by_name["rpms-source"].model == "jinaai/jina-embeddings-v2-base-code"
        assert by_name["rpms-source"].vector_name == "jina-code-v2"
        assert by_name["vista-source"].model == "jinaai/jina-embeddings-v2-base-code"

        # Doc collections use nomic
        assert by_name["rpms"].model == "nomic-ai/nomic-embed-text-v1.5"
        assert by_name["rpms"].query_prefix == "search_query: "
        assert by_name["vista"].model == "nomic-ai/nomic-embed-text-v1.5"

    def test_legacy_single_model_config(self, tmp_path):
        """Legacy single-model config produces a single default collection."""
        config = tmp_path / "config.yaml"
        config.write_text(
            textwrap.dedent("""\
            embedding:
              model: "sentence-transformers/all-MiniLM-L6-v2"
              vector_size: 384
              vector_name: "fast-all-minilm-l6-v2"
              max_tokens: 512

            routing:
              default_collection: "my-collection"
            """)
        )

        result = read_thresher_config(config)

        assert result.default_collection == "my-collection"
        assert len(result.collections) == 1
        assert result.collections[0].name == "my-collection"
        assert result.collections[0].model == "sentence-transformers/all-MiniLM-L6-v2"
        assert result.collections[0].vector_size == 384

    def test_file_not_found(self):
        """Raises FileNotFoundError for missing config."""
        with pytest.raises(FileNotFoundError):
            read_thresher_config("/nonexistent/path/config.yaml")

    def test_default_collection_added_when_not_in_rules(self, tmp_path):
        """Default collection is included even if no routing rule targets it."""
        config = tmp_path / "config.yaml"
        config.write_text(
            textwrap.dedent("""\
            embedding:
              default: docs
              models:
                docs:
                  model: "nomic-ai/nomic-embed-text-v1.5"
                  vector_size: 768
                  vector_name: "nomic-v1.5"

            routing:
              default_collection: "fallback"
              rules:
                - name: specific
                  collection: other
            """)
        )

        result = read_thresher_config(config)

        names = {c.name for c in result.collections}
        assert "fallback" in names
        assert "other" in names

    def test_qdrant_defaults(self, tmp_path):
        """Qdrant URL defaults to localhost when not specified."""
        config = tmp_path / "config.yaml"
        config.write_text("routing:\n  default_collection: test\n")

        result = read_thresher_config(config)

        assert result.qdrant_url == "http://localhost:6333"
        assert result.qdrant_api_key == ""
