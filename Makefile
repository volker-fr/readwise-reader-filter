# Via http://marmelab.com/blog/2016/02/29/auto-documented-makefile.html
.PHONY: help
help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-30s\033[0m %s\n", $$1, $$2}'

# Common flags for development
DEV_FLAGS = --dry-run --debug

.PHONY: install
install: ## Install dependencies with uv
	uv sync

.PHONY: run
run: ## Run the filter
	uv run readwise-reader-filter

.PHONY: dry-run
dry-run: ## Show what would be done (safe)
	uv run readwise-reader-filter --dry-run

.PHONY: run-dev
run-dev: ## Run with dry-run and debug
	uv run readwise-reader-filter $(DEV_FLAGS)

.PHONY: refresh
refresh: ## Re-download all documents from API
	uv run readwise-reader-filter --refresh

.PHONY: debug
debug: ## Run with debug logging
	uv run readwise-reader-filter --debug

.PHONY: clean
clean: ## Clean build artifacts and cache
	rm -rf .venv
	rm -rf build dist *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

.PHONY: lint
lint: ## Run linters
	uv run ruff check .

.PHONY: format
format: ## Format code
	uv run ruff format .

.PHONY: test
test: ## Run tests
	uv run pytest tests/
