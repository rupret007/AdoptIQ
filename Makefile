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

.PHONY: help test lint lint-fix security audit verify eval-ask-ai preflight-acceptance decision-report-acceptance ai-feature-acceptance

help:
	@echo "Round 14 verification harness"
	@echo "  make test         - pytest (default suite, excludes eval marker)"
	@echo "  make lint         - ruff check"
	@echo "  make lint-fix     - ruff check --fix (safe fixes only)"
	@echo "  make security     - bandit (HIGH/MED gate)"
	@echo "  make audit        - pip-audit on requirements.txt"
	@echo "  make verify       - lint + security + audit + test"
	@echo "  make eval-ask-ai  - Round 66 / Pass 4 Ask AI eval framework"
	@echo "                       (offline replay; cassettes in tests/ask_ai_eval/cassettes/)"
	@echo "  make preflight-acceptance - Round 140 disk/soak preflight (not in verify)"
	@echo "  make decision-report-acceptance - Round 143 four-scope offline/live acceptance"
	@echo "  make ai-feature-acceptance - Round 144 two-pass live AI feature acceptance"

preflight-acceptance:
	bash scripts/preflight_acceptance.sh

# Required variables: MANAGER, AS_OF. Optional: MODE, DAYS, OUTPUT_DIR,
# BASE_URL, MEMBER_EMAIL, CUSTOMER_NAME, CSONE_FILE.
decision-report-acceptance:
	@test -n "$(MANAGER)" || (echo "MANAGER is required" >&2; exit 2)
	@test -n "$(AS_OF)" || (echo "AS_OF is required" >&2; exit 2)
	$(PY) scripts/run_decision_report_acceptance.py \
		--mode "$(or $(MODE),auto)" \
		--manager "$(MANAGER)" \
		--days "$(or $(DAYS),90)" \
		--as-of "$(AS_OF)" \
		--output-dir "$(or $(OUTPUT_DIR),.adoptiq-acceptance)" \
		--base-url "$(or $(BASE_URL),http://127.0.0.1:5151)" \
		$(if $(MEMBER_EMAIL),--member-email "$(MEMBER_EMAIL)") \
		$(if $(CUSTOMER_NAME),--customer-name "$(CUSTOMER_NAME)") \
		$(if $(CSONE_FILE),--csone-file "$(CSONE_FILE)")

# Required variables: MANAGER, CUSTOMER_NAME. Optional: TECHNOLOGY, DAYS,
# OUTPUT_DIR, BASE_URL, PACE_SECONDS.
ai-feature-acceptance:
	@test -n "$(MANAGER)" || (echo "MANAGER is required" >&2; exit 2)
	@test -n "$(CUSTOMER_NAME)" || (echo "CUSTOMER_NAME is required" >&2; exit 2)
	$(PY) scripts/run_ai_feature_acceptance.py \
		--manager "$(MANAGER)" \
		--technology "$(or $(TECHNOLOGY),All)" \
		--customer-name "$(CUSTOMER_NAME)" \
		--days "$(or $(DAYS),90)" \
		--output-dir "$(or $(OUTPUT_DIR),.adoptiq-acceptance/ai-features)" \
		--base-url "$(or $(BASE_URL),http://127.0.0.1:5151)" \
		--pace-seconds "$(or $(PACE_SECONDS),7.0)"

test:
	$(PY) -m pytest -q -m 'not eval'

# Round 66 / Pass 4 - Ask AI golden-set eval. NOT included in 'verify'
# to keep CI fast; runs nightly or on-demand. Cassettes are committed
# alongside the fixtures so this target works fully offline.
eval-ask-ai:
	$(PY) -m pytest tests/ask_ai_eval/ -v -m eval

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
