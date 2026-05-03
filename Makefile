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
		test_e2e; do \
		echo "=== $$t ==="; python3 -m tests.$$t || exit 1; \
	done

.PHONY: seed
seed: ## Insert two synthetic demo CVs into the backend DB.
	cd backend && python3 -m scripts.seed_demo_data

.PHONY: eval
eval: ## Run extraction + matching evaluators on the synthetic gold set.
	cd evaluation && python3 evaluate_extraction.py
	cd evaluation && python3 evaluate_matching.py

# ---- Docker ----

.PHONY: docker-up
docker-up: ## Build and start all services on default ports (3000 / 8000).
	docker compose up --build -d
	@echo ""
	@echo "Backend  → http://localhost:8000  (docs at /docs)"
	@echo "Frontend → http://localhost:3000"

.PHONY: docker-up-auto
docker-up-auto: ## Auto-pick free host ports if 3000 / 8000 are taken.
	@bash scripts/pick-ports.sh --write
	@docker compose --env-file .env.ports up --build -d
	@echo ""
	@grep '^FRONTEND_PORT' .env.ports | sed 's|FRONTEND_PORT=|Frontend → http://localhost:|'
	@grep '^BACKEND_PORT'  .env.ports | sed 's|BACKEND_PORT=|Backend  → http://localhost:|'

.PHONY: docker-down
docker-down: ## Stop services and remove containers (volumes preserved).
	docker compose down

.PHONY: docker-logs
docker-logs: ## Tail combined backend + frontend logs.
	docker compose logs -f --tail=100

.PHONY: docker-clean
docker-clean: ## Stop services AND remove volumes (deletes DB, uploads, index).
	docker compose down -v

.PHONY: docker-rebuild
docker-rebuild: ## Force rebuild without cache.
	docker compose build --no-cache

# ---- Help ----

.PHONY: help
help: ## Show this help.
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'
