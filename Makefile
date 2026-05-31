PYTHON := .venv/bin/python3

.venv:
	python3 -m venv .venv
	.venv/bin/pip3 install -e ".[dev]"

setup: .venv

test: .venv
	$(PYTHON) -m pytest -q -p no:rerunfailures -m "not network"

lint: .venv
	$(PYTHON) -m ruff check chat_downloader tests

fmt: .venv
	$(PYTHON) -m ruff format chat_downloader tests

fmt-check: .venv
	$(PYTHON) -m ruff format --check chat_downloader tests

typecheck: .venv
	$(PYTHON) -m mypy .

check: lint fmt-check typecheck test

clean:
	find . -type d -name '__pycache__' -exec rm -rf {} +
	rm -rf .mypy_cache .pytest_cache .ruff_cache .coverage htmlcov *.egg-info

.PHONY: setup test lint fmt fmt-check typecheck check clean
