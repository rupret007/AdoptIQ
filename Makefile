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

.PHONY: help test lint lint-fix security audit verify eval-ask-ai preflight-acceptance decision-report-acceptance ai-feature-acceptance local-acceptance-lab local-acceptance-app local-acceptance-http snowflake-capability-profile csone-corpus-replay metamorphic-acceptance production-simulation synthetic-csone offline-sim offline-sim-pr offline-sim-ci-surface offline-sim-local hosted-actions-classify synthetic-csone-metrics source-contracts offline-pipeline-smoke jeff-only-stubs

help:
	@echo "Round 14 verification harness"
	@echo "  make test         - pytest (default suite, excludes eval marker)"
	@echo "  make lint         - ruff check"
	@echo "  make lint-fix     - ruff check --fix (safe fixes only)"
	@echo "  make security     - bandit (HIGH/MED gate)"
	@echo "  make audit        - pip-audit on requirements.txt"
	@echo "  make verify       - lint + security + audit + test + deterministic Ask AI eval"
	@echo "  make eval-ask-ai  - Round 66 / Pass 4 Ask AI eval framework"
	@echo "                       (offline replay; cassettes in tests/ask_ai_eval/cassettes/)"
	@echo "  make preflight-acceptance - Round 140 disk/soak preflight (not in verify)"
	@echo "  make decision-report-acceptance - Round 143 four-scope offline/live acceptance"
	@echo "  make ai-feature-acceptance - Round 144 two-pass live AI feature acceptance"
	@echo "  make local-acceptance-lab - Round 145 validate guarded synthetic scenarios"
	@echo "  make local-acceptance-app - Round 145 guarded loopback fixture application"
	@echo "  make local-acceptance-http - Round 145 all-scenario loopback HTTP acceptance"
	@echo "  make snowflake-capability-profile - metadata-only local Snowflake opportunity audit"
	@echo "  make csone-corpus-replay - production-loader + pseudonymous real-shape CSOne gate"
	@echo "  make metamorphic-acceptance - deterministic cross-report and Ask AI truth mutations"
	@echo "  make production-simulation - mandatory real-shape CSOne + offline report/AI/source gate"
	@echo "  make source-contracts - Round 145 Snowflake fetchers vs fixture DB-API"
	@echo "  make offline-pipeline-smoke - ingest/reports/UX fixture pipeline"
	@echo "  make jeff-only-stubs - fail-closed work-Mac stubs"
	@echo "  make synthetic-csone - regenerate testdata/synthetic_csone from Round 145 fixtures"
	@echo "  make offline-sim-pr - Cloud/Bob PR loop (verify + lab + pipeline + stubs + replay)"
	@echo "  make offline-sim    - Cloud/Bob full loop (adds production-simulation when a corpus exists)"
	@echo "  make offline-sim-ci-surface - prove workflow + make targets exist (no hosted runner required)"
	@echo "  make offline-sim-local - local/fixture proof (hosted job never started; stay PRIVATE)"
	@echo "  make hosted-actions-classify - classify empty-runner hosted job as never-started (not billing)"
	@echo "  make synthetic-csone-metrics - load synthetic CSOne through canonical_metrics"

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

local-acceptance-lab:
	$(PY) scripts/run_local_acceptance_lab.py \
		--enable-local-fixtures \
		--scenario "$(or $(SCENARIO),all)"

local-acceptance-app:
	$(PY) scripts/run_local_acceptance_app.py \
		--enable-local-fixtures \
		--scenario "$(or $(SCENARIO),healthy)"

local-acceptance-http:
	$(PY) scripts/run_local_acceptance_http.py

snowflake-capability-profile:
	$(PY) scripts/profile_snowflake_capabilities.py \
		--enable-local-fixtures \
		--scenario "$(or $(SCENARIO),multi_manager)"

csone-corpus-replay:
	@test -n "$(CSONE_CORPUS_DIR)" || (echo "CSONE_CORPUS_DIR is required" >&2; exit 2)
	$(PY) scripts/run_csone_corpus_replay.py \
		--input-dir "$(CSONE_CORPUS_DIR)" \
		--max-rows "$(or $(CSONE_REPLAY_MAX_ROWS),600)"

metamorphic-acceptance:
	$(PY) scripts/run_round169_metamorphic_acceptance.py \
		--max-seconds "$(or $(MAX_SECONDS),180)" \
		$(if $(OUTPUT),--output "$(OUTPUT)")

# Required: CSONE_CORPUS_DIR=/external/path/to/real/exports. The profiler
# retains aggregate schema/missingness only; generated artifacts stay ignored.
# Generic ``run_round146_acceptance.py local`` remains the explicit fixture-only
# lane; this named pre-build target must never silently omit the real-shape replay.
production-simulation:
	@test -n "$(strip $(CSONE_CORPUS_DIR))" || (echo "CSONE_CORPUS_DIR is required for production-simulation" >&2; exit 2)
	$(PY) scripts/run_round146_acceptance.py \
		--output-dir "$(or $(OUTPUT_DIR),.adoptiq-acceptance/prebuild-production-simulation)" \
		local \
		--days "$(or $(DAYS),90)" \
		--csone-corpus-dir "$(CSONE_CORPUS_DIR)" \
		--csone-replay-max-rows "$(or $(CSONE_REPLAY_MAX_ROWS),600)"

# Round 168 / Round 169.1 Cloud+Bob: additional offline-sim targets. Do not
# override the Round 169 metamorphic-acceptance recipe above.
synthetic-csone:
	$(PY) scripts/generate_synthetic_csone_corpus.py

offline-sim-ci-surface:
	$(PY) scripts/check_offline_sim_ci_surface.py

offline-sim-pr:
	OFFLINE_SIM_PROFILE=pr bash scripts/run_offline_bob_sim.sh --profile pr

offline-sim:
	OFFLINE_SIM_PROFILE=full bash scripts/run_offline_bob_sim.sh --profile full

# Round 169.3: local/fixture proof. Hosted job never started is not billing.
# Diagnose workflow/runner/config. Repo stays PRIVATE. Do not change visibility.
offline-sim-local:
	$(PY) scripts/run_offline_sim_local.py

hosted-actions-classify:
	$(PY) scripts/classify_hosted_actions_failure.py --input testdata/hosted_actions/empty_runner_billing.json --require-kind hosted_runner_not_assigned --require-reason job_never_started --require-not-missing-target --require-not-billing

synthetic-csone-metrics:
	$(PY) scripts/run_synthetic_csone_metrics.py

source-contracts:
	$(PY) scripts/run_local_source_contracts.py --enable-local-fixtures

offline-pipeline-smoke:
	$(PY) scripts/run_offline_pipeline_smoke.py

jeff-only-stubs:
	$(PY) scripts/run_jeff_only_stubs.py

test:
	$(PY) -m pytest -q -m 'not eval'

# Round 66 / Pass 4 - Ask AI golden-set eval. Included in ``verify`` so the
# deterministic evidence replay is a release gate. Cassettes are committed
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

verify: lint security audit test eval-ask-ai
	@echo "All Round 14 gates passed."
