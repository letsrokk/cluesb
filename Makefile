.DEFAULT_GOAL := help

UV ?= uv
PYTHON_VERSION ?= 3.11
ARGS ?=
INSTALL_DIR ?= $(HOME)/.local/bin

UV_RUN = $(UV) run --python $(PYTHON_VERSION)

.PHONY: help test test-integration lint run standalone build-standalone install check

help:
	@printf '%s\n' \
		'cluesb development targets:' \
		'  make test              Run unit and Textual tests' \
		'  make test-integration  Run live macOS collector tests' \
		'  make lint              Run Ruff checks' \
		'  make run               Run cluesb from source' \
		'  make run ARGS="..."    Run cluesb with CLI arguments' \
		'  make standalone        Build dist/standalone/cluesb' \
		'  make install           Build and install cluesb to ~/.local/bin' \
		'  make check             Run lint and unit/Textual tests'

test:
	$(UV_RUN) --extra test pytest -m "not integration"

test-integration:
	$(UV_RUN) --extra test pytest -m integration

lint:
	$(UV_RUN) --extra lint ruff check src tests scripts

run:
	$(UV_RUN) cluesb $(ARGS)

standalone:
	$(UV_RUN) --extra standalone python scripts/build_standalone.py

build-standalone: standalone

install: standalone
	install -d "$(INSTALL_DIR)"
	install -m 755 dist/standalone/cluesb "$(INSTALL_DIR)/cluesb"

check: lint test
