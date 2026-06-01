UV ?= uv

.PHONY: setup lock test lint fmt fmt-check typecheck coverage build check clean

setup:
	$(UV) sync

lock:
	$(UV) lock

test:
	$(UV) run pytest -q -p no:rerunfailures -m "not network"

lint:
	$(UV) run ruff check chat_downloader tests

fmt:
	$(UV) run ruff format chat_downloader tests

fmt-check:
	$(UV) run ruff format --check chat_downloader tests

typecheck:
	$(UV) run mypy .

coverage:
	$(UV) run coverage erase
	PYTHONHASHSEED=0 $(UV) run coverage run --source chat_downloader \
		-m pytest -q -m "not network"
	$(UV) run coverage report -m --precision=2

build:
	$(UV) build

check: lint fmt-check typecheck test

clean:
	find . -type d -name '__pycache__' -exec rm -rf {} +
	rm -rf .mypy_cache .pytest_cache .ruff_cache .coverage htmlcov dist *.egg-info
