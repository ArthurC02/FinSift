"""A/B driver: run every entry point over the synthetic fixtures and dump a
stable text rendering of the results, for byte comparison against another
checkout of the same repo (verification V4 - see docs/VERIFICATION.md).

    git worktree add ../wt_head HEAD
    python tools/ab.py ../wt_head/src > before.txt
    python tools/ab.py > after.txt

Run BOTH sides with THIS copy of the harness. Only the source tree under test
varies; the fixtures always come from the current checkout, resolved from
__file__ rather than passed in. Letting each side supply its own fixtures
would compare two different inputs and the diff would mean nothing.

`data/` is deliberately resolved relative to the source tree under test, not
to the current checkout - the coding dictionaries and con_call_terms.json are
part of the system being compared, whereas the fixtures are the shared input.

Network is stubbed - see docs/VERIFICATION.md §3. If either side ever really
reaches banking.gov.tw the run is a harness failure, not a result.
"""
import sys, os, io, json, contextlib, importlib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "tests" / "fixtures"

SRC = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else REPO / "src"
sys.path.insert(0, str(SRC))
# ONE harness has to read every layout that has ever existed - A/B is the only
# check that a restructure changed no behaviour, and it can only say so if both
# sides run through the same code. See _resolve for how, and why not try/except.
def _resolve(role, candidates, needs):
    """First importable candidate that actually provides `needs`.

    Resolved by CAPABILITY, not by import success, and deliberately not as a
    chain of try/except branches. Two reasons, both learned the hard way:

    - `import earningsCalls` succeeds against a tree whose package is still an
      empty __init__, so an import-only probe binds an empty facade and every
      con-call section raises - while diffing clean against another broken run.
    - The layout has changed four times now (flat -> packages -> domain names
      -> split packages). A/B compares a tree against an OLDER one, so it has
      to read every layout that has existed, and a branch per layout does not
      scale. Adding a new one here is one more string in a list.
    """
    for name in candidates:
        try:
            mod = importlib.import_module(name)
        except ImportError:
            continue
        if all(hasattr(mod, a) for a in needs):
            return mod
    sys.exit(f"ab.py: nothing supplies {role} (tried {candidates}). "
             f"HARNESS FAILURE, not a result.")


npl_finder = _resolve("regulator", ["regulatorDatasets.disclosures",
                                    "regulatorDatasets.npl_finder", "npl_finder"],
                      ["_fetch_url"])
af = _resolve("fin_report", ["financialReports", "financialReports.acctfinder", "acctfinder"],
              ["collect_summary_rows", "detect_bank", "collect_statement_rows"])
cf = _resolve("con_call", ["earningsCalls", "earningsCalls.decks",
                           "earningsCalls.callfinder", "callfinder"],
              ["load_terms", "collect_con_call_summary"])
rf = _resolve("cli", ["userInteractions.cli", "userInteractions.runfinder", "runfinder"],
              ["classify_folder", "load_all_codes"])
npl_finder._fetch_url = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("network stubbed"))

FIX, DECK, DECK2 = FIXTURES / "fixture", FIXTURES / "deck", FIXTURES / "deck2"

# Every section a complete run emits. Checked at the end so a harness that
# half-ran cannot be mistaken for a tree that behaves identically.
EXPECTED_TAGS = [
    "detect_industry", "detect_bank",
    *[f"rows/{s}/p{n}" for s in ("balance_sheet", "income_statement", "cash_flow") for n in (1, 2)],
    "profitability", "summary", "roa_roe",
    "concall/deck", "concall_quarter/deck", "concall/deck2", "concall_quarter/deck2",
    "classify/fixture", "classify/deck", "classify/deck2",
]
# cash_flow is absent from the fixture, so those two sections legitimately come
# back empty rather than raising; nothing else should raise at all.
_MAX_EXPECTED_RAISES = 0

out = []
def rec(tag, fn):
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            val = fn()
        out.append(f"### {tag}\n{buf.getvalue()}RESULT={json.dumps(val, ensure_ascii=False, default=str, sort_keys=True)}")
    except Exception as e:
        out.append(f"### {tag}\n{buf.getvalue()}RAISED={type(e).__name__}: {e}")

rec("detect_industry", lambda: af.detect_industry_category(FIX))
rec("detect_bank", lambda: af.detect_bank(FIX))
for stmt in ("balance_sheet", "income_statement", "cash_flow"):
    for period in (1, 2):
        rec(f"rows/{stmt}/p{period}", lambda s=stmt, p=period: af.collect_statement_rows(FIX, None, s, p))
rec("profitability", lambda: af.find_profitability_entries(FIX))
rec("summary", lambda: af.collect_summary_rows(FIX, af.detect_bank(FIX)))
rec("roa_roe", lambda: af.collect_roa_roe(FIX, af.detect_bank(FIX)))
terms = cf.load_terms(f"{SRC}/../data/con_call_terms.json")
for d in (DECK, DECK2):
    rec(f"concall/{os.path.basename(d)}", lambda d=d: cf.collect_con_call_summary(d, terms))
    # detect_bank via af: it belongs to financialReports.entities and was only
    # reachable as cf.detect_bank because the old single-file con-call module
    # had imported it into its own namespace. Same function either way, so the
    # comparison against an older tree stays valid.
    rec(f"concall_quarter/{os.path.basename(d)}", lambda d=d: (cf.detect_con_call_quarter(d), cf.detect_con_call_year(d), af.detect_bank(d)))
codes = rf.load_all_codes()
for d in (FIX, DECK, DECK2):
    rec(f"classify/{os.path.basename(d)}", lambda d=d: rf.classify_folder(d, codes))

# An empty dump compared against another empty one diffs clean, which reads as
# "this change altered no behaviour" when in fact the harness never ran. That
# false green happened for real during the earningsCalls split: both sides came
# back 0 lines and the diff stat reported no difference. A/B is the check that
# catches silent failure, so it must not be able to fail silently itself.
_missing = [t for t in EXPECTED_TAGS if not any(o.startswith(f"### {t}\n") for o in out)]
if _missing:
    sys.exit(f"ab.py emitted {len(out)}/{len(EXPECTED_TAGS)} sections; missing {_missing}. "
             f"HARNESS FAILURE, not a result - do not compare this output against anything.")
_raised = [o.split("\n")[0][4:] for o in out if "\nRAISED=" in o]
if len(_raised) > _MAX_EXPECTED_RAISES:
    sys.exit(f"ab.py: {len(_raised)} section(s) raised, expected {_MAX_EXPECTED_RAISES}: {_raised}. "
             f"HARNESS FAILURE, not a result.")
print("\n".join(out))
