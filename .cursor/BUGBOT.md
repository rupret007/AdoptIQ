# Bugbot review standards

Review this repository as a strict correctness, reliability, security, and edge-case reviewer.

Flag issues involving:
- broken logic
- unhandled null/undefined/empty states
- invalid input handling
- missing authorization
- unsafe trust of client input
- race conditions
- async error handling bugs
- data loss
- time zone/date bugs
- pagination/limit bugs
- network failure handling
- missing tests for changed behavior
- broad try/catch blocks that hide failures
- weakening or deleting tests
- insecure dynamic execution
- secrets in code/logs
- dependency or build changes without verification

For backend/API changes:
- require tests for success, failure, validation, authorization, and edge cases.

For frontend changes:
- require loading, empty, error, disabled, and accessibility states where applicable.

For bug fixes:
- require a regression test when practical.

Do not accept changes that merely silence errors without fixing root cause.
