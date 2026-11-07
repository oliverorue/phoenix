# =============================================================================
# Makefile — Phoenix (Docker Compose)
# =============================================================================
# Uso común:
#   make build       → Construir imagen
#   make up          → Iniciar todos los servicios (desarrollo)
#   make up-prod     → Iniciar en producción (sin override)
#   make down        → Detener servicios
#   make logs        → Ver logs del bot
#   make test        → Ejecutar tests dentro del contenedor
#   make shell       → Shell interactivo en el contenedor
#   make clean       → Limpiar contenedores e imágenes
# =============================================================================

.PHONY: build up up-prod down logs test shell clean

# ─── Variables ─────────────────────────────────────────────────────
COMPOSE_FILE     := docker-compose.yml
SERVICE_BOT      := bot
SERVICE_DASH     := dashboard
IMAGE_TAG        := phoenix:latest

# ─── Build ─────────────────────────────────────────────────────────
build:
	docker build -t $(IMAGE_TAG) .

build-nc:
	docker build --no-cache -t $(IMAGE_TAG) .

# ─── Up ────────────────────────────────────────────────────────────
up: build
	docker compose -f $(COMPOSE_FILE) up -d

up-prod: build
	docker compose -f $(COMPOSE_FILE) up -d

up-bot: build
	docker compose -f $(COMPOSE_FILE) up -d $(SERVICE_BOT)

up-dash: build
	docker compose -f $(COMPOSE_FILE) up -d $(SERVICE_DASH)

# ─── Down ──────────────────────────────────────────────────────────
down:
	docker compose -f $(COMPOSE_FILE) down --remove-orphans

down-vol:
	docker compose -f $(COMPOSE_FILE) down -v --remove-orphans

# ─── Logs ──────────────────────────────────────────────────────────
logs:
	docker compose -f $(COMPOSE_FILE) logs -f $(SERVICE_BOT)

logs-dash:
	docker compose -f $(COMPOSE_FILE) logs -f $(SERVICE_DASH)

logs-all:
	docker compose -f $(COMPOSE_FILE) logs -f

# ─── Tests ─────────────────────────────────────────────────────────
test:
	docker compose -f $(COMPOSE_FILE) run --rm $(SERVICE_BOT) test

test-v:
	docker compose -f $(COMPOSE_FILE) run --rm $(SERVICE_BOT) test -v

test-specific:
	docker compose -f $(COMPOSE_FILE) run --rm $(SERVICE_BOT) test $(filter-out $@,$(MAKECMDGOALS))

# ─── Shell ─────────────────────────────────────────────────────────
shell:
	docker compose -f $(COMPOSE_FILE) run --rm $(SERVICE_BOT) shell

shell-dash:
	docker compose -f $(COMPOSE_FILE) run --rm $(SERVICE_DASH) shell

# ─── Mantenimiento ─────────────────────────────────────────────────
clean: down
	docker rmi -f $(IMAGE_TAG) 2>/dev/null || true
	docker system prune -f --volumes

restart: down up

ps:
	docker compose -f $(COMPOSE_FILE) ps

# ─── Para test-specific: capturar argumentos extra ────────────────
%:
	@:
