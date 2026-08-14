# AdoptIQ macOS Build 115 candidate — invalidated

`candidate.json` preserves the authoritative historical identity for these immutable
bytes. Local packaging, integrity checks, signatures, and frozen-runtime smoke passed
for that exact Build 115 artifact when it was created.

**Status: INVALIDATED / NO-GO.** Round 168 changes product/runtime behavior after
Build 115 was packaged. These bytes therefore cannot represent current source and
must not be accepted, installed, promoted, published, or deployed. This is a release
identity decision, not a judgment that the historical hashes or build record changed.

The DMG is excluded from Git and must travel only through an approved encrypted
channel. Build 116 is source-only and pending: it requires an exact clean source
commit, a new package, fresh immutable candidate evidence, frozen smoke, authorized
live validation, and manual review before it can become eligible.
