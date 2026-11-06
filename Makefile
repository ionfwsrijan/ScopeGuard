.PHONY: install demo test lint format typecheck serve verify clean

ifeq ($(OS),Windows_NT)
PY ?= py -3
BIN := $(VENV)/Scripts
else
PY ?= python3
BIN := $(VENV)/bin
endif

VENV ?= .venv

install:
	$(PY) -m venv $(VENV)
	$(BIN)/python -m pip install --upgrade pip
	$(BIN)/python -m pip install -e ".[dev]"

demo:
	$(BIN)/scopeguard demo --reset

test:
	$(BIN)/python -m pytest

lint:
	$(BIN)/ruff check src tests
	$(BIN)/ruff format --check src tests

format:
	$(BIN)/ruff format src tests
	$(BIN)/ruff check src tests --fix

typecheck:
	$(BIN)/mypy

serve:
	$(BIN)/scopeguard --root . --home .scopeguard/demo serve --port 8787

verify:
	$(BIN)/scopeguard --root . --home .scopeguard/demo verify --receipt latest

clean:
	$(BIN)/python -c "import shutil; [shutil.rmtree(p, ignore_errors=True) for p in ('.pytest_cache', '.ruff_cache', '.mypy_cache', 'htmlcov', 'build', 'dist')]"
