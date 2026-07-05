# AI Job-CV Matching Agent — convenience targets.
# Run `make help` for the list.

SHELL := /bin/bash
.DEFAULT_GOAL := help

# ---- Local (no Docker) ----

.PHONY: backend
backend: ## Run the FastAPI backend with hot reload (uvicorn).
	cd backend && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

.PHONY: frontend
frontend: ## Run the Next.js dev server.
	cd frontend && npm run dev

.PHONY: dev
dev: ## Run backend and frontend concurrently (Ctrl-C stops both).
	@echo "Starting backend on :8000 and frontend on :3000…"
	@trap 'kill 0' SIGINT; \
		( cd backend  && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 ) & \
		( cd frontend && npm run dev ) & \
		wait

.PHONY: install
install: ## Install Python + Node deps for local dev.
	cd backend && pip install -r requirements.txt
	cd frontend && npm install

# ---- Tests ----

.PHONY: test
test: ## Run all backend test suites.
	cd backend && for t in test_cv_parser test_job_parser test_cv_chunker \
		test_matching_engine test_job_scraper test_job_csv_importer \
		test_llm_extraction test_app_startup test_profile_service \
		test_query_builder test_job_discovery test_rank_jobs \
		test_tailor test_agent test_job_from_file test_generate \
		test_cv_renderer test_e2e; do \
		echo "=== $$t ==="; python3 -m tests.$$t || exit 1; \
	done

.PHONY: seed
seed: ## Insert two synthetic demo CVs into the backend DB.
	cd backend && python3 -m scripts.seed_demo_data

.PHONY: seed-cv
seed-cv: ## Seed the CV library (header, projects, publications, etc.) for the renderer.
	cd backend && python3 -m scripts.seed_cv_library

.PHONY: eval
eval: ## Run extraction + matching evaluators on the synthetic gold set.
	cd evaluation && python3 evaluate_extraction.py
	cd evaluation && python3 evaluate_matching.py

# ---- Docker ----

# `docker compose --env-file` REPLACES the default `.env` lookup, so the
# port-overrides file alone hides every other secret. Always pass both
# when one exists. `-f` files are merged in order; later files win.
COMPOSE_ENV_FILES := $(strip $(if $(wildcard .env),--env-file .env,) $(if $(wildcard .env.ports),--env-file .env.ports,))

# Shell-env pollution sink: `${VAR:-}` in compose YAML prefers the
# CALLING shell's value over .env, including when shell set it to
# empty. Unset every var compose interpolates so .env always wins.
COMPOSE_UNSET := unset OPENAI_API_KEY OPENAI_BASE_URL LLM_MODEL_NAME ANTHROPIC_API_KEY ANTHROPIC_BASE_URL ANTHROPIC_MODEL LLM_PROVIDER USE_LLM_EXTRACTION LLM_TIMEOUT_SECONDS NEXT_PUBLIC_API_URL BACKEND_CORS_ORIGINS APP_LOG_LEVEL APP_EMBEDDING_MODEL FRONTEND_PORT BACKEND_PORT 2>/dev/null;

.PHONY: docker-up
docker-up: ## Build and start all services on default ports (3000 / 8000).
	$(COMPOSE_UNSET) docker compose $(COMPOSE_ENV_FILES) up --build -d
	@echo ""
	@echo "Backend  → http://localhost:8000  (docs at /docs)"
	@echo "Frontend → http://localhost:3000"

.PHONY: docker-up-auto
docker-up-auto: ## Auto-pick free host ports if 3000 / 8000 are taken.
	@bash scripts/pick-ports.sh --write
	@$(COMPOSE_UNSET) docker compose $(COMPOSE_ENV_FILES) up --build -d
	@echo ""
	@grep '^FRONTEND_PORT' .env.ports | sed 's|FRONTEND_PORT=|Frontend → http://localhost:|'
	@grep '^BACKEND_PORT'  .env.ports | sed 's|BACKEND_PORT=|Backend  → http://localhost:|'

.PHONY: docker-down
docker-down: ## Stop services and remove containers (volumes preserved).
	$(COMPOSE_UNSET) docker compose $(COMPOSE_ENV_FILES) down

.PHONY: docker-logs
docker-logs: ## Tail combined backend + frontend logs.
	$(COMPOSE_UNSET) docker compose $(COMPOSE_ENV_FILES) logs -f --tail=100

.PHONY: docker-clean
docker-clean: ## Stop services AND remove volumes (deletes DB, uploads, index).
	$(COMPOSE_UNSET) docker compose $(COMPOSE_ENV_FILES) down -v

.PHONY: docker-rebuild
docker-rebuild: ## Force rebuild without cache.
	$(COMPOSE_UNSET) docker compose $(COMPOSE_ENV_FILES) build --no-cache

# ---- Claude-subscription stack (parallel, ports 3100 / 8100) ----
# Drives the LLM through your Claude Pro/Max subscription (no API
# credits) via headless `claude --print`. Isolated containers/volumes,
# so the API-key stack (3000/8000) is untouched.

CLAUDE_COMPOSE := docker compose -f docker-compose.claude.yml --env-file .env.claude

.PHONY: claude-token
claude-token: ## Snapshot the Pro OAuth token from the Keychain → .env.claude.
	@bash scripts/claude-token.sh

.PHONY: claude-up
claude-up: ## Refresh token, build + start the claude-subscription stack (3100 / 8100).
	@bash scripts/claude-token.sh
	$(COMPOSE_UNSET) $(CLAUDE_COMPOSE) up --build -d
	@echo ""
	@echo "Claude-sub Backend  → http://localhost:8100  (docs at /docs)"
	@echo "Claude-sub Frontend → http://localhost:3100"

.PHONY: claude-watch-install
claude-watch-install: ## Install hourly launchd token-refresher (no more 401s).
	@bash scripts/claude-watch.sh install

.PHONY: claude-watch-uninstall
claude-watch-uninstall: ## Remove the hourly token-refresher.
	@bash scripts/claude-watch.sh uninstall

.PHONY: claude-watch-status
claude-watch-status: ## Show the token-refresher state + recent log.
	@bash scripts/claude-watch.sh status

.PHONY: claude-down
claude-down: ## Stop the claude-subscription stack (volumes preserved).
	$(COMPOSE_UNSET) $(CLAUDE_COMPOSE) down

.PHONY: claude-logs
claude-logs: ## Tail the claude-subscription stack logs.
	$(COMPOSE_UNSET) $(CLAUDE_COMPOSE) logs -f --tail=100

.PHONY: claude-clean
claude-clean: ## Stop the claude stack AND remove its volumes (deletes its DB).
	$(COMPOSE_UNSET) $(CLAUDE_COMPOSE) down -v

.PHONY: claude-seed-from-main
claude-seed-from-main: ## (obsolete) The stacks now SHARE one DB volume — nothing to copy.
	@echo "Both stacks share the applyagentai_backend_data volume now."
	@echo "Data is always in sync; no seeding needed."

# ---- Batch-autopilot stack (parallel, ports 3200 / 8200) ----
# Isolated 3rd stack for the batch autopilot (section 7). Shares the DB
# volume so results land in the same tracker. Uses the API key from
# .env. The 3000/3100 stacks are untouched.

BATCH_COMPOSE := docker compose -f docker-compose.batch.yml $(COMPOSE_ENV_FILES)

.PHONY: batch-up
batch-up: ## Build + start the batch-autopilot stack (3200 / 8200).
	$(COMPOSE_UNSET) $(BATCH_COMPOSE) up --build -d
	@echo ""
	@echo "Batch Backend  → http://localhost:8200  (docs at /docs)"
	@echo "Batch Frontend → http://localhost:3200"

.PHONY: batch-down
batch-down: ## Stop the batch stack (shared DB volume preserved).
	$(COMPOSE_UNSET) $(BATCH_COMPOSE) down

.PHONY: batch-logs
batch-logs: ## Tail the batch stack logs.
	$(COMPOSE_UNSET) $(BATCH_COMPOSE) logs -f --tail=100

.PHONY: batch-clean
batch-clean: ## Stop the batch stack + remove ONLY its own caches (never the shared DB).
	$(COMPOSE_UNSET) $(BATCH_COMPOSE) down -v

# ---- Help ----

.PHONY: help
help: ## Show this help.
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'
