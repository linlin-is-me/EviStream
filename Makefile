.PHONY: doctor dev-infra dev-infra-down migrate verify-stage1 verify-stage2 verify-stage3

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

verify-stage2:
	alembic upgrade head
	evistream policy-validate configs/policies/violence-weapon-v1.yaml
	evistream policy-validate configs/policies/dangerous-behavior-v1.yaml
	evistream policy-validate configs/policies/tobacco-alcohol-v1.yaml
	evistream seed-demo --check
	pytest

verify-stage3:
	alembic upgrade head
	$(PYTHON) scripts/verify_stage3_migrations.py
	evistream embedding-smoke --profile mock
	pytest
