"""Round 71 / Phase 7 (#34) -- PC spec hidden_imports parity with Mac.

Pre-R71 ``adoptiq_pc.spec`` was missing several lazy-imported modules
that ``adoptiq_mac.spec`` already pinned:

* ``error_classifier``, ``connectivity_diagnostics`` (R55)
* ``ai_narrative_validator`` (R16/R27)
* ``report_source_injector``, ``report_iteration_loop`` (R57/R59)
* ``knowledge_schema``, ``corpus_crypto``, ``corpus_indexer``,
  ``corpus_retriever``, ``corpus_bootstrap`` (R35)
* ``ask_ai_corpus``, ``report_corpus_context``, ``adoptiq_settings``
* ``model_resolver`` (R69)

PyInstaller's static analyser misses imports inside function bodies,
so the Windows .exe silently degraded (no source citations, no
narrative validation, no corpus on first launch).

Round 71 / Phase 7 (#34) aligns the PC spec with the Mac spec.
"""

from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def _read_pc_spec() -> str:
    return (REPO_ROOT / "adoptiq_pc.spec").read_text(encoding="utf-8", errors="replace")


def _read_mac_spec() -> str:
    return (REPO_ROOT / "adoptiq_mac.spec").read_text(encoding="utf-8", errors="replace")


def _extract_hidden_imports(spec_src: str) -> set:
    """Return the set of hidden_imports literals from a spec source.

    Strips Python-style comments from the source before scanning so
    multi-line comment blocks (which both the PC and Mac specs use to
    document each import group) don't pollute the literal set with
    apostrophe-quoted prose.
    """
    m = re.search(r"hidden_imports\s*=\s*\[(.*?)\]", spec_src, flags=re.DOTALL)
    if not m:
        return set()
    block = m.group(1)
    # Strip comment lines (everything from '#' to end-of-line).
    cleaned_lines = []
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        # Inline comment: cut at '#' but only outside of quotes.
        if "#" in line:
            # Conservative strip: cut at first '#' that isn't inside a string.
            in_string = False
            quote_ch = ""
            cut_idx = -1
            i = 0
            while i < len(line):
                ch = line[i]
                if in_string:
                    if ch == quote_ch and (i == 0 or line[i-1] != "\\"):
                        in_string = False
                else:
                    if ch in ("'", '"'):
                        in_string = True
                        quote_ch = ch
                    elif ch == "#":
                        cut_idx = i
                        break
                i += 1
            if cut_idx >= 0:
                line = line[:cut_idx]
        cleaned_lines.append(line)
    cleaned = "\n".join(cleaned_lines)
    # Names are typically single-quoted but tolerate double quotes.
    single = set(re.findall(r"'([A-Za-z_][A-Za-z0-9_.]*)'", cleaned))
    double = set(re.findall(r'"([A-Za-z_][A-Za-z0-9_.]*)"', cleaned))
    return single | double


REQUIRED_LAZY_MODULES = {
    "error_classifier",
    "connectivity_diagnostics",
    "ai_narrative_validator",
    "report_source_injector",
    "report_iteration_loop",
    "knowledge_schema",
    "corpus_crypto",
    "corpus_indexer",
    "corpus_retriever",
    "corpus_bootstrap",
    "ask_ai_corpus",
    "report_corpus_context",
    "adoptiq_settings",
    "model_resolver",
}


def test_round71_pc_spec_pins_every_required_lazy_module() -> None:
    """Every lazy-imported module the Mac spec pins MUST also be in
    the PC spec's hidden_imports."""
    pc_imports = _extract_hidden_imports(_read_pc_spec())
    missing = REQUIRED_LAZY_MODULES - pc_imports
    assert not missing, (
        f"Round 71 / Phase 7 (#34): adoptiq_pc.spec.hidden_imports "
        f"is missing the following lazy-imported modules: {sorted(missing)}.  "
        f"PyInstaller's static analyser misses these imports because "
        f"they are inside function bodies; without explicit pins the "
        f"Windows .exe silently degrades."
    )


def test_round71_pc_spec_carries_round71_marker() -> None:
    """The PC spec MUST carry a Round 71 marker so the audit grep
    finds the change point."""
    src = _read_pc_spec()
    assert "Round 71 / Phase 7 (#34)" in src, (
        "adoptiq_pc.spec must carry a ``Round 71 / Phase 7 (#34)`` "
        "marker comment near the hidden_imports block."
    )


def test_round71_mac_spec_also_pins_model_resolver() -> None:
    """The Mac spec MUST also explicitly pin ``model_resolver`` (R69)
    because it is imported lazily on both Ask AI and report-narrative
    paths."""
    mac_imports = _extract_hidden_imports(_read_mac_spec())
    assert "model_resolver" in mac_imports, (
        "Round 71 / Phase 7 (#34): adoptiq_mac.spec must explicitly "
        "pin ``model_resolver`` so the Mac frozen build honours the "
        "operator's per-call-site model override."
    )


def test_round71_pc_spec_pins_fastembed_for_hybrid_retrieval() -> None:
    """The PC spec MUST pin the fastembed lazy chain (fastembed +
    onnxruntime + tokenizers) so the frozen Windows build's hybrid
    retrieval doesn't silently fall back to lexical-only mode."""
    pc_imports = _extract_hidden_imports(_read_pc_spec())
    for required in ("ask_ai_embeddings", "fastembed", "onnxruntime", "tokenizers"):
        assert required in pc_imports, (
            f"Round 71 / Phase 7 (#34): adoptiq_pc.spec must pin "
            f"{required!r} so hybrid retrieval works in frozen builds."
        )
