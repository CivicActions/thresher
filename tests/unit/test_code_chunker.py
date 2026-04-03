"""Tests for the Chonkie CodeChunker wrapper (T047/T048)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from thresher.processing.chunkers.chonkie_code import (
    _compute_line_numbers,
    _fallback_line_chunks,
    chunk_code,
    detect_language,
)


class TestDetectLanguage:
    def test_python_extension(self) -> None:
        assert detect_language("src/main.py") == "python"

    def test_javascript_extension(self) -> None:
        assert detect_language("app/index.js") == "javascript"

    def test_typescript_extension(self) -> None:
        assert detect_language("src/utils.ts") == "typescript"

    def test_go_extension(self) -> None:
        assert detect_language("cmd/server.go") == "go"

    def test_rust_extension(self) -> None:
        assert detect_language("src/lib.rs") == "rust"

    def test_c_header(self) -> None:
        assert detect_language("include/api.h") == "c"

    def test_shell_extension(self) -> None:
        assert detect_language("scripts/deploy.sh") == "bash"

    def test_unknown_extension_defaults_to_python(self) -> None:
        assert detect_language("data/file.xyz") == "python"

    def test_explicit_hint_overrides_extension(self) -> None:
        assert detect_language("file.py", hint="javascript") == "javascript"

    def test_hint_auto_uses_extension(self) -> None:
        assert detect_language("file.go", hint="auto") == "go"

    def test_empty_hint_uses_extension(self) -> None:
        assert detect_language("file.rs", hint="") == "rust"

    def test_no_extension(self) -> None:
        assert detect_language("Makefile") == "python"

    def test_uppercase_extension(self) -> None:
        assert detect_language("Main.PY") == "python"


# ---------------------------------------------------------------------------
# _compute_line_numbers
# ---------------------------------------------------------------------------


class TestComputeLineNumbers:
    def test_first_line(self) -> None:
        text = "line1\nline2\nline3"
        assert _compute_line_numbers(text, "line1") == (1, 1)

    def test_middle_lines(self) -> None:
        text = "line1\nline2\nline3"
        assert _compute_line_numbers(text, "line2") == (2, 2)

    def test_multiline_chunk(self) -> None:
        text = "line1\nline2\nline3\nline4"
        assert _compute_line_numbers(text, "line2\nline3") == (2, 3)

    def test_entire_text(self) -> None:
        text = "line1\nline2\nline3"
        assert _compute_line_numbers(text, text) == (1, 3)

    def test_chunk_not_found(self) -> None:
        text = "line1\nline2\nline3"
        start, end = _compute_line_numbers(text, "not_in_text")
        assert start == 1
        assert end == 3  # total lines

    def test_single_line_text(self) -> None:
        text = "only one line"
        assert _compute_line_numbers(text, "only one line") == (1, 1)


# ---------------------------------------------------------------------------
# _fallback_line_chunks
# ---------------------------------------------------------------------------


class TestFallbackLineChunks:
    def test_empty_text(self) -> None:
        chunks = _fallback_line_chunks("", chunk_size=512)
        assert len(chunks) == 1
        assert chunks[0]["text"] == ""
        assert chunks[0]["start_line"] == 1

    def test_short_text_single_chunk(self) -> None:
        text = "line1\nline2\nline3"
        chunks = _fallback_line_chunks(text, chunk_size=512)
        assert len(chunks) == 1
        assert chunks[0]["text"] == text
        assert chunks[0]["start_line"] == 1
        assert chunks[0]["end_line"] == 3

    def test_splits_long_text(self) -> None:
        # 4 chars/token * 10 tokens = 40 chars per chunk
        lines = [f"line{i:04d}x" for i in range(20)]  # each ~10 chars
        text = "\n".join(lines)
        chunks = _fallback_line_chunks(text, chunk_size=10)
        assert len(chunks) > 1
        # All lines should be covered
        all_text = "\n".join(c["text"] for c in chunks)
        assert all_text == text

    def test_chunk_has_required_keys(self) -> None:
        text = "def foo():\n    pass"
        chunks = _fallback_line_chunks(text, chunk_size=512)
        assert len(chunks) >= 1
        for c in chunks:
            assert "text" in c
            assert "start_line" in c
            assert "end_line" in c
            assert "start_index" in c
            assert "end_index" in c
            assert "token_count" in c

    def test_line_numbers_are_contiguous(self) -> None:
        lines = [f"line{i}" for i in range(50)]
        text = "\n".join(lines)
        chunks = _fallback_line_chunks(text, chunk_size=10)
        assert chunks[0]["start_line"] == 1
        for i in range(1, len(chunks)):
            assert chunks[i]["start_line"] == chunks[i - 1]["end_line"] + 1


# ---------------------------------------------------------------------------
# chunk_code – with mocked CodeChunker
# ---------------------------------------------------------------------------


class TestChunkCode:
    def test_empty_text_returns_empty(self) -> None:
        assert chunk_code("") == []
        assert chunk_code("   \n  ") == []

    def test_chunks_python_code(self) -> None:
        """CodeChunker is called and results include line numbers."""
        from types import SimpleNamespace

        python_code = "def hello():\n    print('hi')\n\ndef world():\n    return 42\n"

        mock_chunk1 = SimpleNamespace(
            text="def hello():\n    print('hi')",
            start_index=0,
            end_index=28,
            token_count=10,
        )
        mock_chunk2 = SimpleNamespace(
            text="def world():\n    return 42",
            start_index=30,
            end_index=55,
            token_count=8,
        )

        mock_chunker = MagicMock()
        mock_chunker.chunk.return_value = [mock_chunk1, mock_chunk2]
        mock_class = MagicMock(return_value=mock_chunker)

        with patch.dict(
            "sys.modules",
            {"chonkie": MagicMock(CodeChunker=mock_class)},
        ):
            result = chunk_code(python_code, chunk_size=256, language="python")

        assert len(result) == 2
        assert result[0]["text"] == "def hello():\n    print('hi')"
        assert result[0]["start_line"] == 1
        assert result[0]["end_line"] == 2
        assert result[0]["start_index"] == 0
        assert result[0]["token_count"] == 10

        assert result[1]["text"] == "def world():\n    return 42"
        assert result[1]["start_line"] == 4
        assert result[1]["end_line"] == 5

    def test_chunks_javascript(self) -> None:
        from types import SimpleNamespace

        js_code = "function greet() {\n  console.log('hi');\n}\n"

        mock_chunk = SimpleNamespace(
            text=js_code.strip(),
            start_index=0,
            end_index=len(js_code.strip()),
            token_count=15,
        )

        mock_chunker = MagicMock()
        mock_chunker.chunk.return_value = [mock_chunk]
        mock_class = MagicMock(return_value=mock_chunker)

        with patch.dict(
            "sys.modules",
            {"chonkie": MagicMock(CodeChunker=mock_class)},
        ):
            result = chunk_code(js_code, chunk_size=512, language="javascript")

        assert len(result) == 1
        assert result[0]["start_line"] == 1
        assert result[0]["end_line"] == 3

    def test_import_error_falls_back(self) -> None:
        """When chonkie is not importable, fallback is used."""
        with patch.dict("sys.modules", {"chonkie": None}):
            result = chunk_code("def foo(): pass\n", chunk_size=512)

        assert len(result) >= 1
        assert result[0]["start_line"] == 1

    def test_chunker_exception_falls_back(self) -> None:
        """When CodeChunker raises, fallback is used."""
        mock_class = MagicMock(side_effect=RuntimeError("tree-sitter fail"))

        with patch.dict(
            "sys.modules",
            {"chonkie": MagicMock(CodeChunker=mock_class)},
        ):
            result = chunk_code("def foo(): pass\n", chunk_size=512, language="python")

        assert len(result) >= 1
        assert result[0]["start_line"] == 1


# ---------------------------------------------------------------------------
# dispatch_chunker integration
# ---------------------------------------------------------------------------


class TestDispatchChunkerCodeRoute:
    def test_dispatch_routes_to_chonkie_code(self) -> None:
        from thresher.types import ChunkerConfig, FileTypeGroup

        group = FileTypeGroup(
            name="general-source",
            extensions=[".py"],
            chunker=ChunkerConfig(strategy="chonkie-code", language="python"),
        )

        with (
            patch(
                "thresher.processing.chunkers.chonkie_code.chunk_code",
                return_value=[{"text": "chunk1"}],
            ) as mock_cc,
            patch(
                "thresher.processing.chunkers.chonkie_code.detect_language",
                return_value="python",
            ) as mock_dl,
        ):
            from thresher.runner.processor import dispatch_chunker

            result = dispatch_chunker("code", group, file_path="test.py")

        mock_dl.assert_called_once_with("test.py", "python")
        mock_cc.assert_called_once_with(
            "code",
            chunk_size=512,
            language="python",
            file_path="test.py",
            tokenizer="sentence-transformers/all-MiniLM-L6-v2",
        )
        assert result == [{"text": "chunk1"}]

    def test_dispatch_auto_language_detection(self) -> None:
        from thresher.types import ChunkerConfig, FileTypeGroup

        group = FileTypeGroup(
            name="general-source",
            extensions=[".go"],
            chunker=ChunkerConfig(strategy="chonkie-code", language="auto"),
        )

        with (
            patch(
                "thresher.processing.chunkers.chonkie_code.chunk_code",
                return_value=[{"text": "chunk1"}],
            ),
            patch(
                "thresher.processing.chunkers.chonkie_code.detect_language",
                return_value="go",
            ) as mock_dl,
        ):
            from thresher.runner.processor import dispatch_chunker

            result = dispatch_chunker("code", group, file_path="main.go")

        mock_dl.assert_called_once_with("main.go", "auto")
        assert result == [{"text": "chunk1"}]

    def test_dispatch_file_path_defaults_to_empty(self) -> None:
        from thresher.types import ChunkerConfig, FileTypeGroup

        group = FileTypeGroup(
            name="general-source",
            extensions=[".py"],
            chunker=ChunkerConfig(strategy="chonkie-code"),
        )

        with (
            patch(
                "thresher.processing.chunkers.chonkie_code.chunk_code",
                return_value=[],
            ),
            patch(
                "thresher.processing.chunkers.chonkie_code.detect_language",
                return_value="python",
            ) as mock_dl,
        ):
            from thresher.runner.processor import dispatch_chunker

            dispatch_chunker("code", group)

        mock_dl.assert_called_once_with("", "auto")
