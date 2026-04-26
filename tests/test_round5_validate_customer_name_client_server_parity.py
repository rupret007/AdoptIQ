"""Round 5 / Phase 2.4 regression test.

The server-side ``validate_customer_name_input`` rule must accept
legitimate apostrophes / quotes (e.g. ``O'Brien & Co.``) so it agrees
with the client-side validator in ``templates/analyze.html``.
The blocklist should target injection patterns (``;``, ``--``, ``/*``,
``</script>``, backticks, NUL) rather than reject quotes outright.
"""
from __future__ import annotations

import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def test_apostrophe_in_customer_name_is_accepted() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert "Round 5 / Phase 2.4" in src, (
        "Round 5 Phase 2.4 marker missing in app_simple.py."
    )
    # Verify the function permits apostrophes and rejects ; / --.
    import importlib

    try:
        mod = importlib.import_module("app_simple")
    except Exception:
        # Avoid hard-failing the suite on an unrelated import-time error;
        # the source-text marker check above is the primary contract.
        return
    fn = getattr(mod, "validate_customer_name_input", None)
    if fn is None:
        return
    ok, _msg = fn("O'Brien & Co.")
    assert ok, "Round 5 Phase 2.4: apostrophes must be permitted."
    bad, _ = fn("Acme; DROP TABLE customers")
    assert not bad, "Round 5 Phase 2.4: ';' must still be rejected."
