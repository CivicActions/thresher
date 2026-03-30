"""Tests for thresher.embedder."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np

from thresher.embedder import Embedder, MultiModelEmbedder
from thresher.types import EmbeddingModelConfig


class TestEmbedderInit:
    """Tests for Embedder initialization."""

    def test_default_model_name(self) -> None:
        embedder = Embedder()
        assert embedder.model_name == "sentence-transformers/all-MiniLM-L6-v2"

    def test_default_max_tokens(self) -> None:
        embedder = Embedder()
        assert embedder.max_tokens == 512

    def test_custom_parameters(self) -> None:
        embedder = Embedder(model_name="custom-model", max_tokens=256)
        assert embedder.model_name == "custom-model"
        assert embedder.max_tokens == 256

    def test_model_not_loaded_on_init(self) -> None:
        embedder = Embedder()
        assert embedder._model is None


class TestEmbedderPreload:
    """Tests for Embedder.preload."""

    @patch("thresher.embedder.TextEmbedding", create=True)
    def test_preload_triggers_model_loading(self, mock_cls: MagicMock) -> None:
        with patch("fastembed.TextEmbedding", mock_cls):
            embedder = Embedder()
            embedder.preload()
            mock_cls.assert_called_once_with(model_name="sentence-transformers/all-MiniLM-L6-v2")
            assert embedder._model is not None


class TestEmbedTexts:
    """Tests for Embedder.embed_texts."""

    def test_empty_list_returns_empty(self) -> None:
        embedder = Embedder()
        result = embedder.embed_texts([])
        assert result == []

    @patch("fastembed.TextEmbedding")
    def test_returns_correct_number_of_vectors(self, mock_cls: MagicMock) -> None:
        mock_model = MagicMock()
        mock_model.embed.return_value = [
            np.array([0.1, 0.2, 0.3]),
            np.array([0.4, 0.5, 0.6]),
        ]
        mock_cls.return_value = mock_model

        embedder = Embedder()
        results = embedder.embed_texts(["hello", "world"])
        assert len(results) == 2
        assert results[0] == [0.1, 0.2, 0.3]
        assert results[1] == [0.4, 0.5, 0.6]

    @patch("fastembed.TextEmbedding")
    def test_embed_text_returns_single_vector(self, mock_cls: MagicMock) -> None:
        mock_model = MagicMock()
        mock_model.embed.return_value = [np.array([0.1, 0.2, 0.3])]
        mock_cls.return_value = mock_model

        embedder = Embedder()
        result = embedder.embed_text("hello")
        assert result == [0.1, 0.2, 0.3]


# ---------------------------------------------------------------------------
# MultiModelEmbedder tests (T011, T021)
# ---------------------------------------------------------------------------


def _make_model_config(
    model: str = "m1",
    vector_size: int = 3,
    vector_name: str = "vec1",
    max_tokens: int = 512,
    index_prefix: str = "",
    query_prefix: str = "",
) -> EmbeddingModelConfig:
    return EmbeddingModelConfig(
        model=model,
        vector_size=vector_size,
        vector_name=vector_name,
        max_tokens=max_tokens,
        index_prefix=index_prefix,
        query_prefix=query_prefix,
    )


def _two_model_configs() -> dict[str, EmbeddingModelConfig]:
    return {
        "docs": _make_model_config(model="nomic/text", vector_name="nomic"),
        "code": _make_model_config(model="jina/code", vector_name="jina"),
    }


class TestMultiModelEmbedderInit:
    def test_raises_on_empty_models(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="at least one"):
            MultiModelEmbedder({})

    def test_no_model_loaded_on_init(self) -> None:
        embedder = MultiModelEmbedder(_two_model_configs())
        assert embedder._active_name is None
        assert embedder._active_model is None

    def test_get_model_config_returns_config(self) -> None:
        embedder = MultiModelEmbedder(_two_model_configs())
        cfg = embedder.get_model_config("docs")
        assert cfg.model == "nomic/text"
        assert cfg.vector_name == "nomic"

    def test_get_model_config_raises_on_unknown(self) -> None:
        import pytest

        embedder = MultiModelEmbedder(_two_model_configs())
        with pytest.raises(KeyError):
            embedder.get_model_config("nonexistent")


class TestMultiModelEmbedderEmbedTexts:
    def test_empty_texts_returns_empty(self) -> None:
        embedder = MultiModelEmbedder(_two_model_configs())
        result = embedder.embed_texts([], "docs")
        assert result == []

    def test_unknown_model_raises_key_error(self) -> None:
        import pytest

        embedder = MultiModelEmbedder(_two_model_configs())
        with pytest.raises(KeyError):
            embedder.embed_texts(["hello"], "nonexistent")

    @patch("fastembed.TextEmbedding")
    def test_lazy_loads_model_on_first_call(self, mock_cls: MagicMock) -> None:
        mock_model = MagicMock()
        mock_model.embed.return_value = [np.array([0.1, 0.2, 0.3])]
        mock_cls.return_value = mock_model

        embedder = MultiModelEmbedder(_two_model_configs())
        assert embedder._active_name is None

        result = embedder.embed_texts(["hello"], "docs")
        assert embedder._active_name == "docs"
        assert result == [[0.1, 0.2, 0.3]]

    @patch("fastembed.TextEmbedding")
    def test_no_reload_when_same_model(self, mock_cls: MagicMock) -> None:
        mock_model = MagicMock()
        mock_model.embed.return_value = [np.array([0.1, 0.2])]
        mock_cls.return_value = mock_model

        embedder = MultiModelEmbedder(_two_model_configs())
        embedder.embed_texts(["hello"], "docs")
        embedder.embed_texts(["world"], "docs")
        # TextEmbedding should only be instantiated once
        assert mock_cls.call_count == 1

    @patch("fastembed.TextEmbedding")
    def test_swaps_model_when_different_model_requested(self, mock_cls: MagicMock) -> None:
        mock_model = MagicMock()
        mock_model.embed.return_value = [np.array([0.1, 0.2])]
        mock_cls.return_value = mock_model

        embedder = MultiModelEmbedder(_two_model_configs())
        embedder.embed_texts(["hello"], "docs")
        assert embedder._active_name == "docs"
        assert mock_cls.call_count == 1

        embedder.embed_texts(["world"], "code")
        assert embedder._active_name == "code"
        assert mock_cls.call_count == 2

    @patch("fastembed.TextEmbedding")
    def test_index_prefix_prepended(self, mock_cls: MagicMock) -> None:
        mock_model = MagicMock()
        mock_model.embed.return_value = [np.array([0.1, 0.2])]
        mock_cls.return_value = mock_model

        models = {
            "docs": EmbeddingModelConfig(
                model="nomic/text",
                vector_size=2,
                vector_name="nomic",
                index_prefix="search_document: ",
            )
        }
        embedder = MultiModelEmbedder(models)
        embedder.embed_texts(["hello world"], "docs")

        mock_model.embed.assert_called_once()
        call_args = mock_model.embed.call_args[0][0]
        assert call_args == ["search_document: hello world"]

    @patch("fastembed.TextEmbedding")
    def test_empty_prefix_passes_text_unchanged(self, mock_cls: MagicMock) -> None:
        mock_model = MagicMock()
        mock_model.embed.return_value = [np.array([0.1, 0.2])]
        mock_cls.return_value = mock_model

        models = {
            "code": EmbeddingModelConfig(
                model="jina/code", vector_size=2, vector_name="jina", index_prefix=""
            )
        }
        embedder = MultiModelEmbedder(models)
        embedder.embed_texts(["def foo()"], "code")

        call_args = mock_model.embed.call_args[0][0]
        assert call_args == ["def foo()"]


class TestMultiModelEmbedderPreload:
    @patch("fastembed.TextEmbedding")
    def test_preload_loads_model(self, mock_cls: MagicMock) -> None:
        mock_model = MagicMock()
        mock_cls.return_value = mock_model

        embedder = MultiModelEmbedder(_two_model_configs())
        embedder.preload("docs")
        assert embedder._active_name == "docs"
        mock_cls.assert_called_once()

    def test_preload_unknown_model_raises_key_error(self) -> None:
        import pytest

        embedder = MultiModelEmbedder(_two_model_configs())
        with pytest.raises(KeyError):
            embedder.preload("nonexistent")
