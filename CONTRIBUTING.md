# Contributing to Thresher

## Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) (package manager)
- Docker (for functional tests and container builds)

## Development Setup

```bash
git clone https://github.com/your-org/thresher.git
cd thresher
uv sync --dev                # Install all dependencies
uv run pre-commit install    # Set up git hooks (ruff, ty)
```

## Running Tests

```bash
# Unit tests
uv run pytest tests/unit/ -v

# Functional tests (start services first)
docker compose -f docker-compose.functional.yaml up -d
uv run pytest tests/functional/ -v

# Full suite
uv run pytest tests/ -v
```

## Linting and Formatting

Pre-commit hooks run automatically on `git commit`. To run manually:

```bash
uv run prek                  # Run all pre-commit checks
uv run ruff check . --fix    # Lint with auto-fix
uv run ruff format .         # Format code
```

## Commit Conventions

We use [Conventional Commits](https://www.conventionalcommits.org/) enforced by [commitizen](https://commitizen-tools.github.io/commitizen/):

```
feat: add S3 source provider
fix: handle empty archive gracefully
refactor: simplify queue claiming logic
test: add chunker edge case tests
docs: update configuration reference
```

Use `uv run cz bump` to create version bumps with auto-generated changelog.

## Code Style

- **Formatter/linter**: Ruff (E, F, I, W rules)
- **Line length**: 100 characters
- **Type hints**: Required on all public functions
- **Docstrings**: Required on classes and public methods
- **Type checker**: ty (via pre-commit)

## Pull Request Workflow

1. Create a feature branch from `main`
2. Make changes with tests
3. Ensure `uv run pytest tests/unit/` passes
4. Ensure `uv run prek` passes (lint + format + type check)
5. Use conventional commit messages
6. Open a PR with a clear description of what and why

## Project Structure

- `thresher/` — Main package (see [architecture docs](docs/architecture.md))
- `tests/unit/` — Unit tests (mocked external services)
- `tests/functional/` — Functional tests (real Docker services)
- `specs/` — Design specifications and provider contracts
- `config.example.yaml` — Annotated configuration template
