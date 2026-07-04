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
## Default uses fake providers. Set LIVE=1 to run with azure + openai (secrets in .env).
smoke:
	$(COMPOSE) up -d --build --wait --wait-timeout $(WAIT_TIMEOUT)
ifeq ($(LIVE),1)
	$(COMPOSE) exec -T -e OCR_PROVIDER=azure -e LLM_PROVIDER=openai api python -m pipeline.smoke
else
	$(COMPOSE) exec -T api python -m pipeline.smoke
endif

## Regenerate fixture PDFs + expected.json (dev only).
fixtures:
	$(COMPOSE) run --rm --no-deps api python /app/fixtures/gen_fixtures.py /app/fixtures

## Watch a scanner inbox folder and upload PDFs to landing/ (see docs/scanner-epson-es580w.md).
landing-watch:
	python3 scripts/landing-watch.py $${SCAN_INBOX:-/srv/scan-inbox}

clean: down
	$(DOCKER) system prune -f
