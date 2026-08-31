.PHONY: doctor

PYTHON ?= python3.11

doctor:
	$(PYTHON) scripts/doctor.py
