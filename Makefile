.PHONY: install run build package-install lint fmt check fix

install:
	uv sync

run:
	uv run project

build:
	rm -rf dist
	uv build

package-install: build
	uv tool install --reinstall dist/*.whl

lint:
	uv run ruff check .

fmt:
	uv run ruff format .

check:
	uv run ruff check .
	uv run ruff format --check .

fix:
	uv run ruff check --fix .
	uv run ruff format .
