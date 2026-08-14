PYTHON ?= python3
VENV_PYTHON ?= .venv/bin/python
RUN := $(PYTHON) -m

.PHONY: install install-dev format format-check lint type test test-foundation security audit-deps check migrate downgrade clean

install:
	$(PYTHON) -m pip install --requirement requirements.lock

install-dev: install
	$(PYTHON) -m pip install --editable .

format:
	$(RUN) ruff format .

format-check:
	$(RUN) ruff format --check .

lint:
	$(RUN) ruff check .

type:
	$(RUN) mypy src

test:
	$(RUN) pytest

test-foundation:
	$(RUN) pytest tests/unit/test_foundation.py tests/unit/test_config.py tests/unit/test_audit.py tests/integration/test_database.py

security:
	$(RUN) bandit -r src

audit-deps:
	$(RUN) pip_audit

check: format-check lint type test security audit-deps

migrate:
	$(RUN) alembic -c migrations/alembic.ini upgrade head

downgrade:
	$(RUN) alembic -c migrations/alembic.ini downgrade -1

clean:
	$(PYTHON) -c "from pathlib import Path; [p.unlink() for p in Path('.').rglob('*.py[co]') if p.is_file()]"
