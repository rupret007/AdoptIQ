"""Round 50 / F-COMP-CONSIST-PULSE-THREAD analyze-page dark-theme contract.

The user reported a "white part on the right" of the Analysis Time
Range and CSOne Excel File input groups on the otherwise-dark analyze
page (screenshot Screenshot_2026-04-29_at_10.27.19_AM).

Root cause: the right-hand ``<span class="input-group-text bg-light">``
adornments in ``templates/analyze.html`` (the "days" suffix and the
upload-icon adornment) were pulling Bootstrap's
``.bg-light { background-color: #f8f9fa !important; }`` rule, which
overrode the dark ``.input-group-text { background:
var(--bg-surface-raised); ... }`` rule defined in
``templates/base.html``.  The ``!important`` flag is what makes the
override stick despite the more-specific custom selector.

R50 fix: drop ``bg-light`` from the two analyze-page input-group
adornments so the shared dark-theme rule paints them correctly.

This test pins the contract: no ``<span class="input-group-text ...">``
in the analyze form may carry the ``bg-light`` token.  If somebody
re-introduces it (e.g. via a future Bootstrap-theme migration), the
white artifact comes back -- catch it here.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

ANALYZE_HTML = PROJECT_ROOT / "templates" / "analyze.html"


# Match any <span ...class="..."...> whose class list contains both
# "input-group-text" and "bg-light" (in either order, with arbitrary
# whitespace between class tokens).  We match across the entire opening
# span tag rather than line-by-line so a multi-line attribute set
# (e.g. an aria-label after class) cannot hide the offence.
_BG_LIGHT_INPUT_GROUP_PATTERN = re.compile(
    r"<span\b[^>]*\bclass\s*=\s*\"[^\"]*\binput-group-text\b[^\"]*\bbg-light\b[^\"]*\"",
    re.IGNORECASE | re.DOTALL,
)
_BG_LIGHT_INPUT_GROUP_PATTERN_REVERSED = re.compile(
    r"<span\b[^>]*\bclass\s*=\s*\"[^\"]*\bbg-light\b[^\"]*\binput-group-text\b[^\"]*\"",
    re.IGNORECASE | re.DOTALL,
)


def test_analyze_input_group_text_does_not_carry_bg_light() -> None:
    """No ``<span class="input-group-text ...">`` in the analyze form
    may carry ``bg-light`` -- the Bootstrap utility class has
    ``!important`` and overrides the dark-theme ``.input-group-text``
    rule in base.html, producing the bright-white artifact the user
    reported.
    """
    src = ANALYZE_HTML.read_text()
    bg_light_offenders = [
        match.group(0)
        for match in _BG_LIGHT_INPUT_GROUP_PATTERN.finditer(src)
    ]
    bg_light_offenders.extend(
        match.group(0)
        for match in _BG_LIGHT_INPUT_GROUP_PATTERN_REVERSED.finditer(src)
    )
    assert not bg_light_offenders, (
        "Round 50 / F-COMP-CONSIST-PULSE-THREAD analyze dark-theme "
        "contract: input-group-text spans must NOT carry the Bootstrap "
        "`bg-light` utility -- it has `!important` and overrides the "
        "shared dark-theme rule in base.html, producing the white "
        "artifact the user reported on 2026-04-29.  Found offenders: "
        f"{bg_light_offenders!r}"
    )


def test_analyze_form_still_uses_input_group_text_adornments() -> None:
    """Sanity guard: the Round 50 fix removes ``bg-light`` but MUST
    leave the ``input-group-text`` adornments themselves intact (the
    "days" suffix and the upload-icon are still part of the visual
    contract).  If somebody deletes the spans entirely instead of
    just dropping the class, this catches it.
    """
    src = ANALYZE_HTML.read_text()
    n_spans = len(re.findall(r"<span\b[^>]*\binput-group-text\b", src))
    assert n_spans >= 2, (
        "Round 50: the analyze form must still carry at least two "
        "input-group-text adornment spans (Analysis Time Range "
        "'days' suffix and CSOne file upload icon).  found: "
        f"{n_spans}"
    )
