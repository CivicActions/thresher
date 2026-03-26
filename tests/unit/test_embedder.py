"""Tests for thresher.embedder."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np

from thresher.embedder import Embedder


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
