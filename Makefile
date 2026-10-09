PY ?= python3
VENV ?= .venv
BIN := $(VENV)/bin
COMPOSE ?= podman compose
TEST_DB_URL ?= postgresql+psycopg://bot:bot@localhost:5432/bot_test

install:
	$(PY) -m venv $(VENV)
	$(BIN)/pip install -e ".[dev]"

test:
	$(BIN)/pytest -q -m "not integration"

test-integration:
	TEST_POSTGRES_URL=$(TEST_DB_URL) $(BIN)/pytest -q -m integration

lint:
	$(BIN)/ruff check .

format:
	$(BIN)/ruff format .
	$(BIN)/ruff check --fix .

typecheck:
	$(BIN)/mypy bot

up:
	$(COMPOSE) up -d

down:
	$(COMPOSE) down -v

migrate:
	$(BIN)/alembic upgrade head

run:
	$(BIN)/python -m bot.app
