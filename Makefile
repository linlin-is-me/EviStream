.PHONY: doctor dev-infra dev-infra-down migrate verify-stage1

PYTHON ?= python3.11

doctor:
	$(PYTHON) scripts/doctor.py

dev-infra:
	docker compose up -d postgres

dev-infra-down:
	docker compose down

migrate:
	alembic upgrade head

verify-stage1:
	alembic upgrade head
	pytest tests/unit tests/integration -m "not external"
