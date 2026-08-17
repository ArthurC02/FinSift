"""L2 characterization tests - TEST_DESIGN §5.2, §5.3, §5.8.

collect_roa_roe's inner build() decides which of three sources supplies a
ratio, what gets shown as a cross-check, and which warnings appear. §5.3
isolates the sign handling in the divergence formula, which is where it goes
wrong. §5.8 covers the remaining decision helpers plus two crash paths.
"""
import pytest

import acctfinder as af
import callfinder as cf

# --------------------------------------------------------------------------
# §5.2 collect_roa_roe / build - priority table (11 rules)
# --------------------------------------------------------------------------

def entry(roa=None, roe=None, period="114年12月31日"):
    return {"entity": None, "period_label": period, "quarter_num": 4,
            "roa_posttax": roa, "roe_posttax": roe, "profit_margin": None,
            "source_file": "013_x.md"}


def build_roa(monkeypatch, tmp_path, disclosed=None, concall=None, manual=None):
    """Drive collect_roa_roe with all three sources under control.

    `manual` is the plain ratio compute_ratios returns (build multiplies by
    100), or None to simulate it raising - which is what happens whenever a
    required code or the quarter number can't be found.
    """
    entries = [entry(roa=disclosed)] if disclosed is not None else []
    monkeypatch.setattr(af, "find_profitability_entries", lambda folder, verbose=False: entries)

    def fake_compute(folder, bank, coding=None, verbose=False):
        if manual is None:
            raise RuntimeError("no manual formula available")
        return {"roa": manual, "roe": manual, "quarter_num": 4}

    monkeypatch.setattr(af, "compute_ratios", fake_compute)
    return af.collect_roa_roe(tmp_path, "中信", concall_roa=concall, concall_roe=concall)["roa"]


def test_B1_disclosure_only(monkeypatch, tmp_path):
    row = build_roa(monkeypatch, tmp_path, disclosed=1.0)
    assert row["value"] == 1.0 and row["crosscheck_value"] is None and row["note"] == ""
    assert row["matched_label"].startswith("ROA(年) 稅後 @")


def test_B2_disclosure_with_an_agreeing_manual_crosscheck(monkeypatch, tmp_path):
    row = build_roa(monkeypatch, tmp_path, disclosed=1.0, manual=0.009)
    # The cross-check is carried even when it agrees; print_summary_rows only
    # shows it when a note is also set.
    assert row["value"] == 1.0 and row["crosscheck_value"] == pytest.approx(0.9)
    assert row["note"] == ""


def test_B3_concall_only(monkeypatch, tmp_path):
    row = build_roa(monkeypatch, tmp_path, concall=1.0)
    assert row["value"] == 1.0 and row["matched_label"] == "ROA"
    assert row["crosscheck_value"] is None and row["source_file"] is None


def test_B4_concall_keeps_the_manual_formula_as_a_crosscheck(monkeypatch, tmp_path):
    # Easy branch to miss: concall winning does NOT null the cross-check.
    row = build_roa(monkeypatch, tmp_path, concall=1.0, manual=0.009)
    assert row["value"] == 1.0 and row["crosscheck_value"] == pytest.approx(0.9)


def test_B5_manual_as_last_resort_clears_its_own_crosscheck(monkeypatch, tmp_path):
    # Deliberate: the manual figure IS the value here, so it cannot cross-check
    # itself. This is also what makes the divergence note unreachable for
    # source=manual - see test_divergence_is_unreachable_for_manual_source.
    row = build_roa(monkeypatch, tmp_path, manual=0.01)
    assert row["value"] == pytest.approx(1.0) and row["crosscheck_value"] is None
    assert "approximated" in row["matched_label"] or row["matched_label"] == "ROA"


def test_B6_no_source_at_all_returns_None(monkeypatch, tmp_path):
    assert build_roa(monkeypatch, tmp_path) is None


def test_B7_divergence_note(monkeypatch, tmp_path):
    row = build_roa(monkeypatch, tmp_path, disclosed=1.0, manual=0.03)
    assert "cross-check diverges" in row["note"]
    assert "implausible" not in row["note"]


def test_B8_implausible_value_note(monkeypatch, tmp_path):
    row = build_roa(monkeypatch, tmp_path, disclosed=9.0)   # ROA bound is +-5%
    assert "implausible value" in row["note"]
    assert "cross-check diverges" not in row["note"]


def test_B9_both_notes_coexist_joined_by_semicolon(monkeypatch, tmp_path):
    row = build_roa(monkeypatch, tmp_path, disclosed=9.0, manual=0.03)
    assert "cross-check diverges" in row["note"] and "implausible value" in row["note"]
    assert "; " in row["note"]


def test_B10_divergence_note_also_fires_for_a_concall_primary(monkeypatch, tmp_path):
    # Both note checks sit OUTSIDE the if/elif source ladder, so they apply to
    # every source, not just the disclosure.
    row = build_roa(monkeypatch, tmp_path, concall=1.0, manual=0.03)
    assert "cross-check diverges" in row["note"]


def test_B11_implausible_note_also_fires_for_a_concall_primary(monkeypatch, tmp_path):
    row = build_roa(monkeypatch, tmp_path, concall=9.0)
    assert "implausible value" in row["note"] and row["crosscheck_value"] is None


def test_divergence_is_unreachable_for_manual_source(monkeypatch, tmp_path):
    """Infeasible rule, marked rather than tested as a positive case: when the
    manual formula is the primary value it nulls `crosscheck`, and the
    divergence check requires `crosscheck is not None`. A refactor that makes
    this reachable has changed behaviour, even though no assertion above would
    catch it - hence this one."""
    row = build_roa(monkeypatch, tmp_path, manual=0.01)
    assert row["crosscheck_value"] is None
    assert "cross-check diverges" not in row["note"]


# --------------------------------------------------------------------------
# §5.3 divergence check - sign equivalence classes (6)
#
# max(value, crosscheck) / min(abs(value), abs(crosscheck) or 1e-9) - the
# NUMERATOR is not wrapped in abs().
# --------------------------------------------------------------------------

DIVERGENCE_CASES = [
    ("X1", 1.0, 3.0, True),
    ("X2", 3.0, 1.0, True),
    # FIXED (was PINNED BUG #6): max(-3.0, 1.0) was 1.0, so the ratio came out
    # at 1.0 and a 3x disagreement went unreported. Loss quarters produce
    # negative ROA/ROE legitimately - the plausible range runs to -5%/-50% -
    # so these are ordinary inputs, not theoretical edges.
    ("X3", -3.0, 1.0, True),
    ("X4", -1.0, 3.0, True),
    # ...and max(-3.0, -1.0) was -1.0, a NEGATIVE ratio that can never exceed
    # the factor no matter how far apart the two figures are.
    ("X5", -3.0, -1.0, True),
    ("X6", 0.8, 0.9, False),
    # Same magnitude, opposite signs: one source says profit, the other says
    # loss. The largest disagreement possible, and taking abs() on both sides
    # alone would still have scored it 1.0 and stayed silent.
    ("X7", -1.0, 1.0, True),
]


@pytest.mark.parametrize("value,crosscheck,fires", [c[1:] for c in DIVERGENCE_CASES],
                         ids=[c[0] for c in DIVERGENCE_CASES])
def test_divergence_sign_handling(monkeypatch, tmp_path, value, crosscheck, fires):
    row = build_roa(monkeypatch, tmp_path, disclosed=value, manual=crosscheck / 100)
    assert ("cross-check diverges" in row["note"]) is fires


# --------------------------------------------------------------------------
# §5.8 remaining L2 helpers, plus two crash paths
# --------------------------------------------------------------------------

def test_select_profitability_entry_treats_entity_None_as_in_scope():
    # Layout 3 has exactly one entity - the filing's own - so there is nothing
    # else it could be.
    entries = [entry(roa=1.0)]
    assert af._select_profitability_entry(entries, "中信") is entries[0]


def test_select_profitability_entry_prefers_an_alias_match():
    other = dict(entry(roa=1.0), entity="國泰世華銀行")
    mine = dict(entry(roa=2.0), entity="中國信託商業銀行")
    assert af._select_profitability_entry([other, mine], "中信") is mine


def test_select_profitability_entry_prefers_an_entry_that_has_a_value():
    empty = entry(period="114年12月31日")                    # newer, but no value
    valued = entry(roa=1.0, period="114年9月30日")
    assert af._select_profitability_entry([empty, valued], "中信") is valued


def test_entity_tier_falls_back_to_the_older_behaviour_when_bank_is_unknown():
    # primary_aliases=None means the bank couldn't be detected. Rather than
    # rejecting every named entity, any bank-named one is treated as primary.
    assert cf.entity_tier("台北富邦銀行", None) == 2
    assert cf.entity_tier("富邦金控", None) == 0
    assert cf.entity_tier(None, None) == 1
    assert cf.entity_tier("台北富邦銀行", ["中國信託"]) == 0


def test_rank_key_has_three_levels_and_ignores_period_recency():
    # strength -> tier -> quarterly_bonus. The period's AGE is deliberately not
    # part of the key, so two equally-strong matches from different periods tie
    # and the winner is decided by filename order downstream.
    assert cf._rank_key(2, None) == (2, 1, 0)
    assert cf._rank_key(3, None) > cf._rank_key(2, None)
    assert cf._rank_key(2, None, "4Q25", prefer_quarterly=True) == (2, 1, 1)
    assert cf._rank_key(2, None, "FY25", prefer_quarterly=True) == (2, 1, 0)
    assert cf._rank_key(2, None, "4Q25") == cf._rank_key(2, None, "1Q20")


def write_md(folder, name, *lines):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text("\n".join(lines) + "\n", encoding="utf-8")


# A label-shaped row: no code in cells[0], the label in cells[1], and no
# figure for this period. The code channel never sees it; the label-fallback
# channel does.
VALUELESS_OPEX = "|  | 營業費用合計 |  |"
NET_REVENUE = "| 4xxxx | 淨收益 | 132450 |"


def test_label_fallback_skips_a_row_with_no_value(tmp_path):
    """FIXED (was PINNED BUG #4). The label-fallback channel used to record a
    matched row even when it had no figure for the requested period, and then
    stop looking. That both handed callers a None where a number was expected
    - CIR's abs() - and prevented a later file from supplying the real value,
    contradicting build_code_index's own documented
    "first file WITH A VALUE wins" contract that the code channel follows.
    """
    write_md(tmp_path, "007_a.md", VALUELESS_OPEX)
    index = af.build_code_index(tmp_path, ["58400"],
                                label_fallbacks=af.SUMMARY_LABEL_FALLBACKS)
    assert index["58400"] is None

    # And it keeps looking: a later file carrying the same label WITH a figure
    # now resolves it, which the old "first match wins" behaviour made
    # impossible.
    write_md(tmp_path, "008_b.md", "|  | 營業費用合計 | -68900 |")
    index = af.build_code_index(tmp_path, ["58400"],
                                label_fallbacks=af.SUMMARY_LABEL_FALLBACKS)
    assert index["58400"] == ("營業費用合計", -68900, "008_b.md")


def test_CIR_reports_N_A_instead_of_crashing_on_a_valueless_opex(monkeypatch, tmp_path):
    """FIXED (was PINNED BUG #4). Driven through the real lookup channel rather
    than a stubbed index, because the fix is in the channel - stubbing the
    index would test a shape the code no longer produces."""
    write_md(tmp_path, "007_a.md", NET_REVENUE, VALUELESS_OPEX)
    monkeypatch.setattr(af, "collect_roa_roe",
                        lambda folder, bank, **kw: {"roa": None, "roe": None})
    # industry passed explicitly: this fixture carries no entity legal name,
    # and summary mode now refuses a layout it can't tie to a scheme (see
    # INDUSTRY_SUMMARY_LAYOUTS). Nothing about CIR is affected.
    rows = {r["term"]: r for r in af.collect_summary_rows(tmp_path, "中信", industry="金融業")}
    assert rows["CIR"]["value"] is None
    assert rows["淨收益"]["value"] == 132450


def test_no_composite_component_has_a_label_fallback():
    """Guards the assumption behind fixing #4 in the channel rather than at
    CIR: a composite term does `sum(component_values)`, which would raise on a
    None exactly as abs(None) did. It cannot happen today only because no
    COMPOSITE_TERMS component code appears in SUMMARY_LABEL_FALLBACKS. If that
    ever stops being true, the crash comes back somewhere new - so assert it
    rather than leave it as an unstated coincidence."""
    components = {code for term in af.COMPOSITE_TERMS.values()
                  for codes in term.values() for code in codes}
    assert components.isdisjoint(af.SUMMARY_LABEL_FALLBACKS)


def test_ZERO_ASSETS_degrades_instead_of_crashing_the_whole_run(monkeypatch, tmp_path):
    """FIXED (was PINNED BUG #21). Two periods of zero assets divided by zero
    inside compute_ratios. ZeroDivisionError is not a subclass of RuntimeError,
    so collect_roa_roe's `except RuntimeError` never caught it and the whole
    run died - while every other way of failing to derive the manual formula
    quietly degrades to "cross-check unavailable".

    Driven through the real compute_ratios: stubbing it to raise would only
    restate the fix.
    """
    assert not issubclass(ZeroDivisionError, RuntimeError)   # why the guard is needed
    (tmp_path / "007_bs.md").write_text("114年12月31日\n", encoding="utf-8")
    values = {"10000": 0, "30000": 500, "64000": 100}
    monkeypatch.setattr(
        af, "find_code_value",
        lambda folder, code, period=1, verbose=False, label_fallback=None:
            ("label", values.get(code, 100), "007_bs.md"))

    with pytest.raises(RuntimeError, match="資產總計"):
        af.compute_ratios(tmp_path, "中信")

    # ...and via collect_roa_roe that now degrades rather than propagating.
    monkeypatch.setattr(af, "find_profitability_entries", lambda folder, verbose=False: [])
    assert af.collect_roa_roe(tmp_path, "中信") == {"roa": None, "roe": None}


def test_zero_equity_is_caught_too(monkeypatch, tmp_path):
    # Both denominators, not just the one the bug report named.
    (tmp_path / "007_bs.md").write_text("114年12月31日\n", encoding="utf-8")
    values = {"10000": 1000, "30000": 0, "64000": 100}
    monkeypatch.setattr(
        af, "find_code_value",
        lambda folder, code, period=1, verbose=False, label_fallback=None:
            ("label", values.get(code, 100), "007_bs.md"))
    with pytest.raises(RuntimeError, match="權益總計"):
        af.compute_ratios(tmp_path, "中信")


# --------------------------------------------------------------------------
# §7 #1 end to end - the failure chain the pinned V4 case described
# --------------------------------------------------------------------------

NOPCT_BALANCE_SHEET = [
    "# 資產負債表", "",
    "| 代碼 | 資產 | 114年12月31日 | 113年12月31日 |",
    "|---|---|---|---|",
    "| 10000 | 資產總計 | 6,120,884 | 5,900,000 |",
    "| 30000 | 權益總計 | 520,884 | 500,000 |",
]

PCT_BALANCE_SHEET = [
    "# 資產負債表", "",
    "| 代碼 | 資產 | 金額 | % |",
    "|---|---|---|---|",
    "| 10000 | 資產總計 | 6,120,884 | 100.0 |",
    "| 30000 | 權益總計 | 520,884 | 8.5 |",
]


def test_ONE_no_percent_table_now_resolves_its_prior_period(tmp_path):
    """FIXED (was PINNED BUG #1), end to end. On a two-period balance sheet
    with no share column, period 2 was permanently None - which is what made
    compute_ratios raise, collect_roa_roe swallow it, and the ROA/ROE
    cross-check silently vanish with nothing in the output explaining why."""
    write_md(tmp_path, "007_bs.md", *NOPCT_BALANCE_SHEET)
    assert af.find_code_value(tmp_path, "10000", period=1)[1] == 6120884
    assert af.find_code_value(tmp_path, "10000", period=2)[1] == 5900000


def test_ONE_a_share_column_table_is_read_exactly_as_before(tmp_path):
    """The other side. A table WITH a share column must be untouched: its
    second numeric is a percentage, not a prior period, and reading it as one
    would be a far worse bug than the one being fixed."""
    write_md(tmp_path, "007_bs.md", *PCT_BALANCE_SHEET)
    assert af.find_code_value(tmp_path, "10000", period=1)[1] == 6120884
    assert af.find_code_value(tmp_path, "10000", period=2) is None
