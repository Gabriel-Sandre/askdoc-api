.PHONY: help install dev test lint fmt up down logs demo clean

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-12s\033[0m %s\n", $$1, $$2}'

install:  ## Instala as dependências de runtime e de desenvolvimento
	pip install -e ".[dev]"

dev:  ## Sobe a API local com reload (SQLite, sem Docker)
	uvicorn app.main:app --reload --port 8000

test:  ## Roda a suíte de testes
	pytest -q

lint:  ## Checa estilo e imports
	ruff check app tests

fmt:  ## Formata e corrige o que dá para corrigir automaticamente
	ruff check --fix app tests
	ruff format app tests

up:  ## Sobe API + Postgres com pgvector via Docker
	docker compose up --build -d
	@echo "API em http://localhost:8000/docs"

down:  ## Derruba os containers
	docker compose down

logs:  ## Acompanha os logs da API
	docker compose logs -f api

demo:  ## Roda o fluxo completo ponta a ponta contra a API local
	./scripts/demo.sh

clean:  ## Remove artefatos locais
	rm -rf .pytest_cache .ruff_cache **/__pycache__ askdoc.db
