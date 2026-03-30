import pytest
from mcp_server_qdrant.embeddings.types import EmbeddingProviderType
from mcp_server_qdrant.settings import (
    DEFAULT_TOOL_FIND_DESCRIPTION,
    DEFAULT_TOOL_STORE_DESCRIPTION,
    CollectionConfig,
    EmbeddingProviderSettings,
    QdrantSettings,
    ToolSettings,
)


class TestQdrantSettings:
    def test_default_values(self):
        """Test that required fields raise errors when not provided."""

        # Should not raise error because there are no required fields
        QdrantSettings()

    def test_minimal_config(self, monkeypatch):
        """Test loading minimal configuration from environment variables."""
        monkeypatch.setenv("QDRANT_URL", "http://localhost:6333")
        monkeypatch.setenv("COLLECTION_NAME", "test_collection")

        settings = QdrantSettings()
        assert settings.location == "http://localhost:6333"
        assert settings.collection_name == "test_collection"
        assert settings.api_key is None
        assert settings.local_path is None

    def test_full_config(self, monkeypatch):
        """Test loading full configuration from environment variables."""
        monkeypatch.setenv("QDRANT_URL", "http://qdrant.example.com:6333")
        monkeypatch.setenv("QDRANT_API_KEY", "test_api_key")
        monkeypatch.setenv("COLLECTION_NAME", "my_memories")
        monkeypatch.setenv("QDRANT_SEARCH_LIMIT", "15")
        monkeypatch.setenv("QDRANT_READ_ONLY", "1")

        settings = QdrantSettings()
        assert settings.location == "http://qdrant.example.com:6333"
        assert settings.api_key == "test_api_key"
        assert settings.collection_name == "my_memories"
        assert settings.search_limit == 15
        assert settings.read_only is True

    def test_local_path_config(self, monkeypatch):
        """Test loading local path configuration from environment variables."""
        monkeypatch.setenv("QDRANT_LOCAL_PATH", "/path/to/local/qdrant")

        settings = QdrantSettings()
        assert settings.local_path == "/path/to/local/qdrant"

    def test_local_path_is_exclusive_with_url(self, monkeypatch):
        """Test that local path cannot be set if Qdrant URL is provided."""
        monkeypatch.setenv("QDRANT_URL", "http://localhost:6333")
        monkeypatch.setenv("QDRANT_LOCAL_PATH", "/path/to/local/qdrant")

        with pytest.raises(ValueError):
            QdrantSettings()

        monkeypatch.delenv("QDRANT_URL", raising=False)
        monkeypatch.setenv("QDRANT_API_KEY", "test_api_key")
        with pytest.raises(ValueError):
            QdrantSettings()


class TestCollectionConfig:
    def test_collection_config_required_fields(self):
        col = CollectionConfig(
            name="vista", model="nomic/text", vector_name="nomic-v1.5", vector_size=768
        )
        assert col.name == "vista"
        assert col.model == "nomic/text"
        assert col.vector_name == "nomic-v1.5"
        assert col.vector_size == 768
        assert col.index_prefix == ""
        assert col.query_prefix == ""

    def test_collection_config_with_prefixes(self):
        col = CollectionConfig(
            name="docs",
            model="nomic/text",
            vector_name="nomic-v1.5",
            vector_size=768,
            index_prefix="search_document: ",
            query_prefix="search_query: ",
        )
        assert col.index_prefix == "search_document: "
        assert col.query_prefix == "search_query: "

    def test_collection_config_vector_size_must_be_positive(self):
        with pytest.raises(ValueError):
            CollectionConfig(name="bad", model="m", vector_name="v", vector_size=0)

    def test_qdrant_settings_with_collections(self):
        settings = QdrantSettings.model_validate({
            "collections": [
                {
                    "name": "vista",
                    "model": "nomic-ai/nomic-embed-text-v1.5",
                    "vector_name": "nomic-v1.5",
                    "vector_size": 768,
                    "query_prefix": "search_query: ",
                },
                {
                    "name": "vista-source",
                    "model": "jinaai/jina-embeddings-v2-base-code",
                    "vector_name": "jina-code-v2",
                    "vector_size": 768,
                },
            ]
        })
        assert len(settings.collections) == 2
        assert settings.collections[0].name == "vista"
        assert settings.collections[1].name == "vista-source"
        assert settings.collections[0].query_prefix == "search_query: "
        assert settings.collections[1].query_prefix == ""

    def test_default_collection_from_env(self, monkeypatch):
        monkeypatch.setenv("DEFAULT_COLLECTION", "vista")
        settings = QdrantSettings()
        assert settings.default_collection == "vista"

    def test_empty_collections_by_default(self):
        settings = QdrantSettings()
        assert settings.collections == []
        assert settings.default_collection == ""


class TestEmbeddingProviderSettings:
    def test_default_values(self):
        """Test default values are set correctly."""
        settings = EmbeddingProviderSettings()
        assert settings.provider_type == EmbeddingProviderType.FASTEMBED
        assert settings.model_name == "sentence-transformers/all-MiniLM-L6-v2"

    def test_custom_values(self, monkeypatch):
        """Test loading custom values from environment variables."""
        monkeypatch.setenv("EMBEDDING_MODEL", "custom_model")
        settings = EmbeddingProviderSettings()
        assert settings.provider_type == EmbeddingProviderType.FASTEMBED
        assert settings.model_name == "custom_model"


class TestToolSettings:
    def test_default_values(self):
        """Test that default values are set correctly when no env vars are provided."""
        settings = ToolSettings()
        assert settings.tool_store_description == DEFAULT_TOOL_STORE_DESCRIPTION
        assert settings.tool_find_description == DEFAULT_TOOL_FIND_DESCRIPTION

    def test_custom_store_description(self, monkeypatch):
        """Test loading custom store description from environment variable."""
        monkeypatch.setenv("TOOL_STORE_DESCRIPTION", "Custom store description")
        settings = ToolSettings()
        assert settings.tool_store_description == "Custom store description"
        assert settings.tool_find_description == DEFAULT_TOOL_FIND_DESCRIPTION

    def test_custom_find_description(self, monkeypatch):
        """Test loading custom find description from environment variable."""
        monkeypatch.setenv("TOOL_FIND_DESCRIPTION", "Custom find description")
        settings = ToolSettings()
        assert settings.tool_store_description == DEFAULT_TOOL_STORE_DESCRIPTION
        assert settings.tool_find_description == "Custom find description"

    def test_all_custom_values(self, monkeypatch):
        """Test loading all custom values from environment variables."""
        monkeypatch.setenv("TOOL_STORE_DESCRIPTION", "Custom store description")
        monkeypatch.setenv("TOOL_FIND_DESCRIPTION", "Custom find description")
        settings = ToolSettings()
        assert settings.tool_store_description == "Custom store description"
        assert settings.tool_find_description == "Custom find description"


class TestSearchLimitMax:
    def test_default_is_none(self):
        """search_limit_max defaults to None (uncapped)."""
        settings = QdrantSettings()
        assert settings.search_limit_max is None

    def test_set_via_env_var(self, monkeypatch):
        """QDRANT_SEARCH_LIMIT_MAX is parsed as an int."""
        monkeypatch.setenv("QDRANT_SEARCH_LIMIT_MAX", "50")
        settings = QdrantSettings()
        assert settings.search_limit_max == 50

    def test_capping_logic(self, monkeypatch):
        """num_results > search_limit_max should be capped."""
        monkeypatch.setenv("QDRANT_SEARCH_LIMIT_MAX", "20")
        settings = QdrantSettings()
        # Simulate the capping expression used in find()
        num_results = 100
        max_limit = settings.search_limit_max
        effective = min(num_results, max_limit) if max_limit is not None else num_results
        assert effective == 20

    def test_no_cap_when_none(self):
        """When search_limit_max is None, num_results is used as-is."""
        settings = QdrantSettings()
        num_results = 100
        max_limit = settings.search_limit_max
        effective = min(num_results, max_limit) if max_limit is not None else num_results
        assert effective == 100
