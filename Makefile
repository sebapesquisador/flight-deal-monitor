.PHONY: help install init seed demo api worker scheduler test lint fmt docker-up docker-down

help:                       ## mostra os alvos
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n",$$1,$$2}'

install:                    ## instala dependências
	pip install -r requirements.txt

init:                       ## cria o schema
	python -m src.cli init-db

seed:                       ## cria um watch de exemplo (config.example.json)
	python -m src.cli seed

demo:                       ## pipeline completo com fontes sintéticas
	python -m src.cli demo

api:                        ## sobe API + painel em :8000
	python -m src.cli api --port 8000

worker:                     ## sobe um worker
	python -m src.cli worker --concurrency 8

scheduler:                  ## sobe o scheduler
	python -m src.cli scheduler

test:                       ## roda a suíte de testes
	pytest -q

lint:                       ## ruff
	ruff check src tests

fmt:                        ## ruff format
	ruff format src tests

docker-up:                  ## sobe o stack completo
	docker compose up -d --build

docker-down:
	docker compose down -v
