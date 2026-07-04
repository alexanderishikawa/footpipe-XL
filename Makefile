# footpipe-XL harness — the only agent entrypoints (AGENTS.md).
# Override DOCKER on hosts where docker needs sudo, e.g.:
#   make up DOCKER="sudo docker"

DOCKER ?= docker
COMPOSE ?= $(DOCKER) compose
WAIT_TIMEOUT ?= 600

.PHONY: up down logs ps test smoke fixtures clean

## Build + start the full stack and block until every service is healthy.
up:
	$(COMPOSE) up -d --build --wait --wait-timeout $(WAIT_TIMEOUT)
	$(COMPOSE) ps

## Stop the stack and remove volumes.
down:
	$(COMPOSE) down -v

logs:
	$(COMPOSE) logs -f --tail=100

ps:
	$(COMPOSE) ps

## Unit/contract tests with fake providers — no network, no other services.
test:
	$(COMPOSE) build api
	$(COMPOSE) run --rm --no-deps -e DATABASE_URL=sqlite:// api pytest -q

## End-to-end golden smoke over fixtures (requires the stack up).
smoke:
	$(COMPOSE) up -d --build --wait --wait-timeout $(WAIT_TIMEOUT)
	$(COMPOSE) exec -T api python -m pipeline.smoke

## Regenerate fixture PDFs + expected.json (dev only).
fixtures:
	$(COMPOSE) run --rm --no-deps api python /app/fixtures/gen_fixtures.py /app/fixtures

clean: down
	$(DOCKER) system prune -f
