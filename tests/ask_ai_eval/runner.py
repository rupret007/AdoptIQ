"""Round 66 / Pass 4 - Eval runner for the Ask AI golden set.

End-to-end loop per question:

1. Load the question YAML (id, portfolio, category, predicates).
2. Build the evidence-record set from the portfolio's CSV fixtures
   (mimics what ``prefetch_ask_ai_grounded`` would have returned, but
   from disk so the eval is fully offline).
3. Rank evidence (lexical default; hybrid when
   ``ASK_AI_RETRIEVAL_METHOD=hybrid`` and Pass 5 has wired the path).
4. Build allowed_ids from the kept evidence.
5. Call the mock CircuIT client to get the LLM payload.
6. Compose the grounded answer via the production
   ``compose_grounded_answer``.
7. Evaluate the question's predicates.
8. Emit a per-question row.

The runner returns a structured result dict; ``test_runner.py`` is the
pytest entrypoint that calls into it. ``write_scorecard`` formats the
results into a stable Markdown table.
"""

from __future__ import annotations

import csv
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# Ensure the project root is importable when this module is run directly
# (e.g. ``python -m tests.ask_ai_eval.runner``). The pytest harness already
# inserts the project root via tests/conftest.py.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pandas as pd  # noqa: E402

import ask_ai_grounded as _grounded  # noqa: E402

from . import predicates as _predicates  # noqa: E402
from .mock_circuit import MockCircuitClient, CassetteMissError  # noqa: E402


_FIXTURES_ROOT = Path(__file__).resolve().parent / "fixtures" / "portfolios"
_QUESTIONS_ROOT = Path(__file__).resolve().parent / "questions"
_SCORECARDS_ROOT = Path(__file__).resolve().parent / "scorecards"


# ---------------------------------------------------------------------------
# Question + portfolio loaders
# ---------------------------------------------------------------------------


def _load_yaml_lite(path: Path) -> Dict[str, Any]:
    """Tiny YAML subset reader (no PyYAML dep).

    Supports the exact shape we author for questions:
    - top-level scalars: ``key: value``
    - nested lists of dicts under ``predicates:``
    - integer / float / quoted-string / bool scalars
    - lists of scalars under any key (``- item``)
    """
    raw = path.read_text(encoding="utf-8")
    out: Dict[str, Any] = {}
    cur_list_key: Optional[str] = None
    cur_dict: Optional[Dict[str, Any]] = None
    for raw_line in raw.splitlines():
        line = raw_line.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if line.startswith("  - "):
            payload = line[4:].strip()
            if cur_list_key is None:
                continue
            if ":" in payload:
                cur_dict = {}
                k, v = payload.split(":", 1)
                cur_dict[k.strip()] = _parse_scalar(v.strip())
                if cur_list_key not in out:
                    out[cur_list_key] = []
                out[cur_list_key].append(cur_dict)
            else:
                if cur_list_key not in out:
                    out[cur_list_key] = []
                out[cur_list_key].append(_parse_scalar(payload))
                cur_dict = None
            continue
        if line.startswith("    "):
            payload = line.strip()
            if cur_dict is None:
                continue
            if ":" in payload:
                k, v = payload.split(":", 1)
                cur_dict[k.strip()] = _parse_scalar(v.strip())
            continue
        # top-level
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        key = k.strip()
        val = v.strip()
        if not val:
            cur_list_key = key
            cur_dict = None
            out[key] = []
            continue
        cur_list_key = None
        cur_dict = None
        out[key] = _parse_scalar(val)
    return out


def _parse_scalar(raw: str) -> Any:
    """Parse a YAML scalar - quoted strings, ints, floats, bools."""
    s = raw.strip()
    if not s:
        return ""
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        return s[1:-1]
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(part) for part in _split_top_level_commas(inner)]
    low = s.lower()
    if low in {"true", "yes", "on"}:
        return True
    if low in {"false", "no", "off"}:
        return False
    if low in {"null", "none", "~"}:
        return None
    s_no_underscore = s.replace("_", "")
    try:
        if "." in s_no_underscore or "e" in s_no_underscore.lower():
            return float(s_no_underscore)
        return int(s_no_underscore)
    except ValueError:
        return s


def _split_top_level_commas(text: str) -> List[str]:
    out: List[str] = []
    buf: List[str] = []
    depth = 0
    in_quote: Optional[str] = None
    for ch in text:
        if in_quote:
            buf.append(ch)
            if ch == in_quote:
                in_quote = None
            continue
        if ch in ('"', "'"):
            in_quote = ch
            buf.append(ch)
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(buf).strip())
            buf = []
            continue
        buf.append(ch)
    if buf:
        out.append("".join(buf).strip())
    return out


@dataclass(frozen=True)
class Question:
    id: str
    portfolio: str
    category: str
    question: str
    predicates: List[Dict[str, Any]] = field(default_factory=list)
    expected_evidence_ids: List[str] = field(default_factory=list)


def load_questions(root: Optional[Path] = None) -> List[Question]:
    base = root if root is not None else _QUESTIONS_ROOT
    if not base.is_dir():
        return []
    out: List[Question] = []
    for path in sorted(base.glob("*.yaml")):
        data = _load_yaml_lite(path)
        out.append(
            Question(
                id=str(data.get("id") or path.stem),
                portfolio=str(data.get("portfolio") or ""),
                category=str(data.get("category") or "uncategorized"),
                question=str(data.get("question") or ""),
                predicates=list(data.get("predicates") or []),
                expected_evidence_ids=list(data.get("expected_evidence_ids") or []),
            )
        )
    return out


@dataclass
class PortfolioBundle:
    portfolio_id: str
    adoption_barriers: pd.DataFrame
    support_cases: pd.DataFrame
    customer_pulse: pd.DataFrame
    success_priorities: pd.DataFrame
    action_plans: pd.DataFrame


def _read_csv_or_empty(path: Path) -> pd.DataFrame:
    if not path.is_file():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except (OSError, pd.errors.ParserError, pd.errors.EmptyDataError):
        return pd.DataFrame()


def load_portfolio(portfolio_id: str, root: Optional[Path] = None) -> PortfolioBundle:
    base = (root if root is not None else _FIXTURES_ROOT) / portfolio_id
    return PortfolioBundle(
        portfolio_id=portfolio_id,
        adoption_barriers=_read_csv_or_empty(base / "ab.csv"),
        support_cases=_read_csv_or_empty(base / "csone.csv"),
        customer_pulse=_read_csv_or_empty(base / "pulse.csv"),
        success_priorities=_read_csv_or_empty(base / "sp.csv"),
        action_plans=_read_csv_or_empty(base / "ap.csv"),
    )


# ---------------------------------------------------------------------------
# Evidence pipeline (mirrors run_portfolio_grounded_ask_ai's evidence path)
# ---------------------------------------------------------------------------


def build_evidence_records(bundle: PortfolioBundle) -> Tuple[List[_grounded.EvidenceRecord], set]:
    """Convert the fixture DataFrames into EvidenceRecords using the
    SAME ``_records_from_dataframe`` helper that production uses, so
    Pass 5's hybrid retrieval acts on identical inputs."""
    payload = {
        "adoption_barriers": bundle.adoption_barriers,
        "support_cases_snowflake": bundle.support_cases,
        "csconsole_customer_pulse": bundle.customer_pulse,
        "csconsole_success_priorities": bundle.success_priorities,
        "csconsole_action_plans": bundle.action_plans,
    }
    return _grounded._portfolio_records_from_payload(payload)


def render_evidence_context(
    records: Sequence[_grounded.EvidenceRecord],
    question: str,
    domains: Sequence[str],
) -> Tuple[str, set, int]:
    return _grounded.build_evidence_context(records, question, domains)


# ---------------------------------------------------------------------------
# Per-question runner
# ---------------------------------------------------------------------------


@dataclass
class QuestionResult:
    question_id: str
    portfolio: str
    category: str
    passed: bool
    predicate_results: List[Dict[str, Any]]
    rejected_count: int = 0
    allowed_ids_count: int = 0
    answer_excerpt: str = ""
    error: Optional[str] = None


def evaluate_question(
    question: Question,
    bundle: PortfolioBundle,
    client: MockCircuitClient,
) -> QuestionResult:
    try:
        records, _ = build_evidence_records(bundle)
        plan = _grounded.build_retrieval_plan(question.question)
        _ctx_text, allowed_ids, _used = render_evidence_context(
            records,
            question.question,
            plan.get("domains", ["core"]),
        )
        # Prompt is the evidence context + question + cassette header so
        # the prompt_hash sees prompt content drift (Pass 5 hybrid will
        # change the evidence ordering -> different ctx -> different
        # hash -> cassette miss; that's the operator's signal to
        # re-record).
        prompt = (
            f"Q: {question.question}\n"
            f"PORTFOLIO: {bundle.portfolio_id}\n"
            f"DOMAINS: {','.join(plan.get('domains', []))}\n"
            f"EVIDENCE_CONTEXT:\n{_ctx_text}"
        )
        try:
            payload = client.call(question.id, prompt)
        except CassetteMissError as e:
            return QuestionResult(
                question_id=question.id,
                portfolio=question.portfolio,
                category=question.category,
                passed=False,
                predicate_results=[],
                rejected_count=0,
                allowed_ids_count=len(allowed_ids),
                answer_excerpt="",
                error=f"cassette_miss: {e}",
            )
        # Round 66 / Pass 4 - canonical_numbers is empty for the eval
        # baseline; the production path computes them from the
        # canonical_headline block which the runner does not reproduce.
        # The predicates measure whether numbers ALSO carry citations,
        # which is the more conservative gate.
        answer, rejected = _grounded.compose_grounded_answer(
            payload,
            allowed_ids,
            canonical_numbers=set(),
        )
        results = _predicates.evaluate(
            answer,
            question.predicates,
            allowed_ids,
            portfolio_bundle=bundle,
        )
        all_passed = bool(results) and all(r.get("passed") for r in results)
        return QuestionResult(
            question_id=question.id,
            portfolio=question.portfolio,
            category=question.category,
            passed=all_passed,
            predicate_results=results,
            rejected_count=int(rejected),
            allowed_ids_count=len(allowed_ids),
            answer_excerpt=answer[:400],
        )
    except Exception as e:  # noqa: BLE001 - never poison whole scorecard
        return QuestionResult(
            question_id=question.id,
            portfolio=question.portfolio,
            category=question.category,
            passed=False,
            predicate_results=[],
            rejected_count=0,
            allowed_ids_count=0,
            answer_excerpt="",
            error=f"{type(e).__name__}: {e}",
        )


def run_all(
    *,
    questions_root: Optional[Path] = None,
    fixtures_root: Optional[Path] = None,
    cassette_dir: Optional[Path] = None,
    mode: Optional[str] = None,
) -> List[QuestionResult]:
    questions = load_questions(questions_root)
    if not questions:
        return []
    portfolios: Dict[str, PortfolioBundle] = {}
    out: List[QuestionResult] = []
    client = MockCircuitClient(mode=mode, cassette_dir=cassette_dir)
    for q in questions:
        if q.portfolio not in portfolios:
            portfolios[q.portfolio] = load_portfolio(q.portfolio, fixtures_root)
        out.append(evaluate_question(q, portfolios[q.portfolio], client))
    return out


# ---------------------------------------------------------------------------
# Scorecard renderer
# ---------------------------------------------------------------------------


def _retrieval_method_label() -> str:
    return os.environ.get("ASK_AI_RETRIEVAL_METHOD", "lexical").strip().lower() or "lexical"


def _category_rollup(results: Sequence[QuestionResult]) -> List[Tuple[str, int, int]]:
    by_cat: Dict[str, Tuple[int, int]] = {}
    for r in results:
        passed, total = by_cat.get(r.category, (0, 0))
        by_cat[r.category] = (passed + (1 if r.passed else 0), total + 1)
    return sorted([(cat, p, t) for cat, (p, t) in by_cat.items()])


def render_scorecard(
    results: Sequence[QuestionResult],
    *,
    git_sha: str = "(unrecorded)",
    generated_at: str = "(unrecorded)",
) -> str:
    method = _retrieval_method_label()
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    pass_rate = (100.0 * passed / total) if total else 0.0
    lines: List[str] = []
    lines.append(f"# Ask AI Eval Scorecard - {git_sha}")
    lines.append("")
    lines.append(f"- Generated: {generated_at}")
    lines.append(f"- Retrieval method: {method}")
    lines.append(f"- Total questions: {total}")
    lines.append(f"- Pass rate: {passed}/{total} ({pass_rate:.1f}%)")
    canonical_total = sum(
        1
        for r in results
        for pr in (r.predicate_results or [])
        if pr.get("type") == "must_match_canonical_metric"
    )
    canonical_passed = sum(
        1
        for r in results
        for pr in (r.predicate_results or [])
        if pr.get("type") == "must_match_canonical_metric" and pr.get("passed")
    )
    lines.append(f"- Canonical metric predicates: {canonical_passed}/{canonical_total}")
    lines.append("")
    lines.append("## Per-category")
    lines.append("")
    lines.append("| Category | Passed | Total | Rate |")
    lines.append("|---|---|---|---|")
    for cat, p, t in _category_rollup(results):
        rate = (100.0 * p / t) if t else 0.0
        lines.append(f"| {cat} | {p} | {t} | {rate:.1f}% |")
    lines.append("")
    lines.append("## Per-question")
    lines.append("")
    lines.append("| Question | Portfolio | Category | Passed | Failure reason |")
    lines.append("|---|---|---|---|---|")
    for r in results:
        if r.passed:
            reason = "-"
        elif r.error:
            reason = r.error
        else:
            failed = [pr for pr in r.predicate_results if not pr.get("passed")]
            reason = "; ".join(f"{pr.get('type')}: {pr.get('reason')}" for pr in failed) or "no predicates"
        # Markdown-escape pipes inside the reason to keep table shape stable.
        reason_clean = reason.replace("|", "\\|")
        lines.append(
            f"| {r.question_id} | {r.portfolio} | {r.category} | "
            f"{'PASS' if r.passed else 'FAIL'} | {reason_clean} |"
        )
    return "\n".join(lines) + "\n"


def write_scorecard(
    results: Sequence[QuestionResult],
    *,
    out_path: Path,
    git_sha: str = "(unrecorded)",
    generated_at: str = "(unrecorded)",
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        render_scorecard(results, git_sha=git_sha, generated_at=generated_at),
        encoding="utf-8",
    )


def category_pass_rates(results: Iterable[QuestionResult]) -> Dict[str, float]:
    by_cat: Dict[str, Tuple[int, int]] = {}
    for r in results:
        p, t = by_cat.get(r.category, (0, 0))
        by_cat[r.category] = (p + (1 if r.passed else 0), t + 1)
    return {cat: (100.0 * p / t) if t else 0.0 for cat, (p, t) in by_cat.items()}


def total_pass_rate(results: Iterable[QuestionResult]) -> float:
    items = list(results)
    if not items:
        return 0.0
    return 100.0 * sum(1 for r in items if r.passed) / len(items)


__all__ = [
    "Question",
    "PortfolioBundle",
    "QuestionResult",
    "load_questions",
    "load_portfolio",
    "build_evidence_records",
    "evaluate_question",
    "run_all",
    "render_scorecard",
    "write_scorecard",
    "category_pass_rates",
    "total_pass_rate",
    "_FIXTURES_ROOT",
    "_QUESTIONS_ROOT",
    "_SCORECARDS_ROOT",
]


if __name__ == "__main__":  # pragma: no cover
    import datetime as _dt

    results = run_all()
    sha = os.environ.get("GIT_SHA") or "local"
    ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = _SCORECARDS_ROOT / f"{sha}.md"
    write_scorecard(results, out_path=out, git_sha=sha, generated_at=ts)
    print(f"wrote {out}")
    print(f"pass rate: {total_pass_rate(results):.1f}%")
