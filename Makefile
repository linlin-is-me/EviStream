.PHONY: doctor dev-infra dev-infra-down migrate dev-api dev-worker demo-up demo-down verify-stage1 verify-stage2 verify-stage3 verify-stage4 verify-stage5 verify-stage6 verify-deploy

PYTHON ?= python3.11

doctor:
	$(PYTHON) scripts/doctor.py

dev-infra:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d postgres redis

dev-infra-down:
	docker compose down

migrate:
	alembic upgrade head

dev-api:
	uvicorn apps.api.main:app --host 127.0.0.1 --port 8000 --reload

dev-worker:
	evistream worker

demo-up:
	docker compose up -d --build

demo-down:
	docker compose down

verify-stage1:
	$(PYTHON) scripts/run_stage_verification.py stage1

verify-stage2:
	$(PYTHON) scripts/run_stage_verification.py stage2

verify-stage3:
	$(PYTHON) scripts/run_stage_verification.py stage3

verify-stage4:
	$(PYTHON) scripts/run_stage_verification.py stage4

verify-stage5:
	$(PYTHON) scripts/run_stage_verification.py stage5

verify-stage6:
	$(PYTHON) scripts/run_stage_verification.py stage6

verify-deploy:
	$(PYTHON) scripts/verify_deploy.py
