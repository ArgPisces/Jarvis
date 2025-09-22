# Makefile for Jarvis (PyO3 + Rust default)
# Quick commands for development, build and publish
# Requirements:
#   - Rust toolchain via rustup
#   - maturin (used via pyproject build-backend; can also be installed globally)
#
# Environment:
#   - MATURIN_PYPI_TOKEN: when running `make publish` to PyPI
#
# Common usage:
#   make develop    # build and install extension into current venv
#   make build      # build release wheels
#   make install    # pip install current project
#   make clean      # cleanup artifacts
#   make publish    # publish wheels to PyPI via maturin
#

CARGO_MANIFEST := rust/jarvis_native/Cargo.toml

.PHONY: help
help:
	@echo "Targets:"
	@echo "  develop   - Build and install native extension into current environment (maturin develop)"
	@echo "  build     - Build release wheels (maturin build)"
	@echo "  install   - Install the package into current environment (pip install .)"
	@echo "  clean     - Clean build artifacts"
	@echo "  publish   - Publish wheels to PyPI (requires MATURIN_PYPI_TOKEN)"

.PHONY: develop
develop:
	maturin develop -m $(CARGO_MANIFEST) --release

.PHONY: build
build:
	maturin build -m $(CARGO_MANIFEST) --release

.PHONY: install
install:
	pip install .

.PHONY: clean
clean:
	@echo "Cleaning build artifacts..."
	rm -rf target build dist *.egg-info **/*.egg-info
	find . -name "__pycache__" -type d -prune -exec rm -rf {} +

.PHONY: publish
publish:
	@if [ -z "$$MATURIN_PYPI_TOKEN" ]; then echo "Error: MATURIN_PYPI_TOKEN is not set"; exit 1; fi
	MATURIN_PYPI_TOKEN=$$MATURIN_PYPI_TOKEN maturin publish -m $(CARGO_MANIFEST) --release
