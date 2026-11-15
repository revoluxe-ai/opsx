# Adapter: poetry-python

## Purpose

Project-local TARGET adapter for **stroy-system** when the product uses Poetry
(`pyproject.toml` + `poetry.lock`) as the sole package manager.

Portable OPSX kernel commands may still contain historical donor literals;
authority for this repository resolves through `opsx/project/capabilities.yaml`
and this adapter.

## Canonical sources

- Root `pyproject.toml` (Poetry build backend)
- `poetry.lock`
- `opsx/project/capabilities.yaml` → `product.package_manager`, `quality.install`

## Install (product)

```bash
poetry install --with dev --no-interaction
```

OPSX verify ``quality_gates`` node expects the optional **dev** group
(pytest, pyright, mypy, langgraph for ``opsx/opsx_runtime``).

## Run tests / tooling (product roots)

```bash
poetry run python -m pytest -q
poetry run alembic upgrade head
poetry run alembic check
```

## Detect signals

- `[tool.poetry]` in root `pyproject.toml`
- `poetry.lock` present
- `capabilities.yaml` → `package_manager: poetry`

## Out of scope

- Control-plane subprojects under `tools/cursor_executor`, `tools/temporal`
- Mandatory Poetry for other repositories or portable command transfer bodies
