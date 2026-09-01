.PHONY: doctor dev-infra dev-infra-down migrate verify-stage1 verify-stage2 verify-stage3 verify-stage4

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
	$(PYTHON) scripts/run_stage_verification.py stage1

verify-stage2:
	$(PYTHON) scripts/run_stage_verification.py stage2

verify-stage3:
	$(PYTHON) scripts/run_stage_verification.py stage3

verify-stage4:
	$(PYTHON) scripts/run_stage_verification.py stage4
