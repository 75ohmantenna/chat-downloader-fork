UV ?= uv
UV_RUN ?= $(UV) run --locked

.PHONY: setup lock lock-check test lint fmt fmt-check typecheck complexity coverage build smoke ci check clean

setup:
	$(UV) sync

lock:
	$(UV) lock

lock-check:
	$(UV) lock --check

test:
	$(UV) run pytest -q -p no:rerunfailures -m "not network"

lint:
	$(UV_RUN) ruff check src/chat_downloader tests

fmt:
	$(UV) run ruff format src/chat_downloader tests

fmt-check:
	$(UV_RUN) ruff format --check src/chat_downloader tests

typecheck:
	$(UV_RUN) mypy .

complexity:
	$(UV_RUN) ruff check src/chat_downloader --select C901 --config "lint.mccabe.max-complexity=8"

coverage:
	$(UV_RUN) coverage erase
	PYTHONHASHSEED=0 $(UV_RUN) coverage run -m pytest -q -p no:rerunfailures -m "not network"
	$(UV_RUN) coverage report

build:
	rm -rf dist
	$(UV) build

smoke: build
	@whl=$$(ls dist/*.whl); \
	$(UV) run --isolated --no-project --with "$$whl" chat_downloader --version

ci: lock-check lint fmt-check typecheck coverage smoke

check: lint fmt-check typecheck test

clean:
	find . -type d -name '__pycache__' -exec rm -rf {} +
	rm -rf .mypy_cache .pytest_cache .ruff_cache .coverage htmlcov dist *.egg-info
