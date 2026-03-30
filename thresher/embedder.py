"""FastEmbed ONNX embedding wrapper."""

from __future__ import annotations

import logging
from typing import Sequence

from thresher.types import EmbeddingModelConfig

logger = logging.getLogger("thresher.embedder")


class Embedder:
    """Wrapper around fastembed for generating text embeddings.

    Uses ONNX runtime for fast CPU-based inference.
    """

    def __init__(
        self,
        model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
        max_tokens: int = 512,
    ):
        self.model_name = model_name
        self.max_tokens = max_tokens
        self._model = None

    def _ensure_model(self) -> None:
        """Lazy-load the embedding model."""
        if self._model is None:
            from fastembed import TextEmbedding

            logger.info("Loading embedding model: %s", self.model_name)
            self._model = TextEmbedding(model_name=self.model_name)
            logger.info("Embedding model loaded")

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """Generate embeddings for a list of texts.

        Args:
            texts: List of text strings to embed.

        Returns:
            List of embedding vectors (each is list[float] of size vector_size).
        """
        if not texts:
            return []
        self._ensure_model()
        assert self._model is not None
        embeddings = list(self._model.embed(list(texts)))
        return [emb.tolist() for emb in embeddings]

    def embed_text(self, text: str) -> list[float]:
        """Generate embedding for a single text."""
        results = self.embed_texts([text])
        return results[0]

    def preload(self) -> None:
        """Pre-load the embedding model (call before parallel workers start)."""
        self._ensure_model()


class MultiModelEmbedder:
    """Manages a registry of named embedding models with lazy loading.

    Only one model is loaded in memory at a time. When a different model is
    requested, the previous one is unloaded (dereferenced for GC) before the
    new one is loaded. This keeps peak RAM usage to a single model's footprint.
    """

    def __init__(self, models: dict[str, EmbeddingModelConfig]):
        """Create a MultiModelEmbedder from a map of named model configs.

        Args:
            models: Map of model name → EmbeddingModelConfig.

        Raises:
            ValueError: If models is empty.
        """
        if not models:
            raise ValueError("MultiModelEmbedder requires at least one model configuration")
        self._models = models
        self._active_name: str | None = None
        self._active_model = None

    def embed_texts(self, texts: Sequence[str], model_name: str) -> list[list[float]]:
        """Embed texts using the specified named model.

        Prepends ``index_prefix`` from the model's config if configured.
        Loads (or swaps to) the requested model lazily.

        Args:
            texts: Texts to embed.
            model_name: Name of the model config to use.

        Returns:
            List of embedding vectors.

        Raises:
            KeyError: If model_name is not in the registry.
        """
        if not texts:
            return []

        model_config = self._models[model_name]  # raises KeyError if missing
        self._ensure_model(model_name)
        assert self._active_model is not None

        prefix = model_config.index_prefix
        if prefix:
            prefixed = [prefix + t for t in texts]
        else:
            prefixed = list(texts)

        embeddings = list(self._active_model.embed(prefixed))
        return [emb.tolist() for emb in embeddings]

    def preload(self, model_name: str) -> None:
        """Pre-load a specific model into memory.

        Args:
            model_name: Name of the model to load.

        Raises:
            KeyError: If model_name is not in the registry.
        """
        _ = self._models[model_name]  # validate key exists
        self._ensure_model(model_name)

    def get_model_config(self, model_name: str) -> EmbeddingModelConfig:
        """Return the config for a named model.

        Raises:
            KeyError: If model_name is not in the registry.
        """
        return self._models[model_name]

    def _ensure_model(self, model_name: str) -> None:
        """Load the requested model, swapping out the previous one if needed."""
        if self._active_name == model_name:
            return

        from fastembed import TextEmbedding

        model_config = self._models[model_name]
        logger.info("Loading embedding model: %s (name=%s)", model_config.model, model_name)

        # Dereference previous model for GC before loading the new one
        self._active_model = None
        self._active_name = None

        self._active_model = TextEmbedding(model_name=model_config.model)
        self._active_name = model_name
        logger.info("Embedding model loaded: %s", model_name)
