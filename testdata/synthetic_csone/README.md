# Synthetic CSOne corpus (Round 168)

This directory is a **fixture-projected** CSOne-shaped workbook set for
Cursor Cloud / Bob. It is **not** a Cisco export.

- Source: Round 145 `local_acceptance_lab` healthy TAC rows
- Customers: Acme Corporation, Beta Industries, Gamma Public Sector
- Emails: `fixture.contact1@example.invalid` only
- Honesty: `live_validation_performed=false`, `production_accuracy_claimed=false`

Regenerate (does not need Jeff’s Mac):

```bash
make synthetic-csone
```

`scripts/bake_corpus.py` / `scripts/mint_corpus_sentinel.py` cannot produce
these workbooks. Do not replace this generator with bake/mint.

Do **not** drop real CSOne exports here. Real exports stay in an external
`CSONE_CORPUS_DIR` on the work Mac and must never enter Git.
