# Round 14 verification harness.
# Targets:
#   make test       Run the full pytest suite.
#   make lint       Run ruff lint over the workspace.
#   make security   Run bandit over the source modules (HIGH/MED gate).
#   make audit      Run pip-audit against requirements.txt.
#   make verify     All of the above. Use as the final gate.
#
# These targets intentionally do not change CI. They are local-only quality
# gates that surface findings before they get committed.

PY ?= python3

.PHONY: help test lint lint-fix security audit verify

help:
	@echo "Round 14 verification harness"
	@echo "  make test      - pytest"
	@echo "  make lint      - ruff check"
	@echo "  make lint-fix  - ruff check --fix (safe fixes only)"
	@echo "  make security  - bandit (HIGH/MED gate)"
	@echo "  make audit     - pip-audit on requirements.txt"
	@echo "  make verify    - lint + security + audit + test"

test:
	$(PY) -m pytest -q

lint:
	$(PY) -m ruff check .

lint-fix:
	$(PY) -m ruff check --fix .

# Bandit returns non-zero when issues remain. We gate on HIGH/MED severity by
# default; LOW findings are documented in QUALITY_AUDIT.md instead.
security:
	$(PY) -m bandit -c bandit.yaml -r . -x _bundled_secrets.py -ll -q

audit:
	$(PY) -m pip_audit -r requirements.txt --strict

verify: lint security audit test
	@echo "All Round 14 gates passed."
