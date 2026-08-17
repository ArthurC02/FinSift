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
import sys, os, io, json, contextlib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "tests" / "fixtures"

SRC = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else REPO / "src"
sys.path.insert(0, str(SRC))
# Imported so this ONE harness runs against both the flat layout (modules
# directly under src/) and the package layout (src/financialReports/... etc).
# That is not tidiness: A/B is the only check that a restructure changed no
# behaviour, and it can only say so if the same harness reads both trees.
# Three layouts have existed and A/B must read all of them, because its whole
# job is to compare a tree against an older one. Newest first:
#   1. packages, domain names      src/financialReports/statements.py
#   2. packages, legacy names      src/financialReports/acctfinder.py
#   3. flat                        src/acctfinder.py
try:
    from regulatorDatasets import disclosures as npl_finder
    import financialReports as af
    from earningsCalls import decks as cf
    from userInteractions import cli as rf
except ImportError:   # NOT ModuleNotFoundError: `from pkg import missing_submodule`
                      # raises plain ImportError when the package itself exists.
    try:
        from regulatorDatasets import npl_finder
        from financialReports import acctfinder as af
        from earningsCalls import callfinder as cf
        from userInteractions import runfinder as rf
    except ImportError:   # NOT ModuleNotFoundError: `from pkg import missing_submodule`
                      # raises plain ImportError when the package itself exists.
        import npl_finder
        import acctfinder as af, callfinder as cf, runfinder as rf
npl_finder._fetch_url = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("network stubbed"))

FIX, DECK, DECK2 = FIXTURES / "fixture", FIXTURES / "deck", FIXTURES / "deck2"
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
    rec(f"concall_quarter/{os.path.basename(d)}", lambda d=d: (cf.detect_con_call_quarter(d), cf.detect_con_call_year(d), cf.detect_bank(d)))
codes = rf.load_all_codes()
for d in (FIX, DECK, DECK2):
    rec(f"classify/{os.path.basename(d)}", lambda d=d: rf.classify_folder(d, codes))
print("\n".join(out))
