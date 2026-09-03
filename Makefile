.PHONY: up down logs check smoke lint

up:
	docker compose up -d
	@echo "Neo4j browser: http://localhost:7474  (user neo4j / see .env)"

down:
	docker compose down

logs:
	docker compose logs -f neo4j

lint:
	uv run ruff check . && uv run ruff format --check .

check: lint
	uv run pytest -q

smoke:
	uv run brain doctor
	uv run pytest -q -m live
