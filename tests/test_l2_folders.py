"""L2 characterization tests - TEST_DESIGN §5.4, §5.6, §5.7 (19 cases).

Folder classification decides which extractor runs at all, so a wrong answer
here means an entire folder is silently skipped or run through the wrong
pipeline.
"""
import pytest

from financialReports import acctfinder as af
from earningsCalls import callfinder as cf
from regulatorDatasets import npl_finder as npl
from userInteractions import runfinder as rf

CODES = {"10000": "資產", "20000": "負債", "30000": "權益",
         "40000": "收益", "50000": "費用", "60000": "淨利"}
FINSUM = "活期性存款比率"


def code_rows(n):
    codes = list(CODES)
    return "\n".join(f"| {codes[i % len(codes)]} | 科目 | 100 |" for i in range(n))


def make_folder(tmp_path, name, files):
    folder = tmp_path / name
    folder.mkdir(parents=True)
    for filename, text in files.items():
        path = folder / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return folder


# --------------------------------------------------------------------------
# §5.4 classify_folder - Decision Table + BVT (10)
# --------------------------------------------------------------------------

def test_F1_enough_coded_rows_is_a_fin_report(tmp_path):
    folder = make_folder(tmp_path, "f1", {"001.md": code_rows(6)})
    assert rf.classify_folder(folder, CODES)[0] == "fin_report"


def test_F2_finsum_marker_and_few_files_routes_to_the_summary_extractor(tmp_path):
    folder = make_folder(tmp_path, "f2", {"001.md": code_rows(6) + f"\n{FINSUM}\n"})
    assert rf.classify_folder(folder, CODES)[0] == "fin_report_summary"


def test_F3_finsum_marker_but_too_many_files_is_a_full_filing(tmp_path):
    files = {f"{i:03d}.md": "" for i in range(rf._FINSUM_MAX_FILES + 1)}
    files["001.md"] = code_rows(6) + f"\n{FINSUM}\n"
    folder = make_folder(tmp_path, "f3", files)
    assert rf.classify_folder(folder, CODES)[0] == "fin_report"


def test_F4_con_call_markers_without_coded_rows(tmp_path):
    folder = make_folder(tmp_path, "f4", {"001.md": "2025年第四季法人說明會\n"})
    assert rf.classify_folder(folder, CODES)[0] == "con_call"


def test_F5_neither_signal(tmp_path):
    folder = make_folder(tmp_path, "f5", {"001.md": "封面\n"})
    assert rf.classify_folder(folder, CODES)[0] is None


@pytest.mark.parametrize("hits,expected", [(4, None), (5, "fin_report"), (6, "fin_report")])
def test_code_hit_threshold_boundary(tmp_path, hits, expected):
    folder = make_folder(tmp_path, f"bvt{hits}", {"001.md": code_rows(hits)})
    assert rf.classify_folder(folder, CODES)[0] == expected


@pytest.mark.parametrize("n_files,expected", [(30, "fin_report_summary"), (31, "fin_report")])
def test_finsum_file_count_boundary(tmp_path, n_files, expected):
    files = {f"{i:03d}.md": "" for i in range(n_files)}
    files["000.md"] = code_rows(6) + f"\n{FINSUM}\n"
    folder = make_folder(tmp_path, f"files{n_files}", files)
    assert len(list(folder.glob("*.md"))) == n_files
    assert rf.classify_folder(folder, CODES)[0] == expected


def test_classify_folder_scans_subdirectories_like_every_extractor(tmp_path):
    """FIXED (was PINNED BUG #11). classify_folder scanned with glob('*.md'),
    while detect_bank, find_code_value, collect_statement_rows and
    find_term_value all use rglob. A folder whose .md files sat in a
    subdirectory was classified None and skipped by runfinder, while acctfinder
    run directly on the very same folder worked fine."""
    folder = make_folder(tmp_path, "nested", {"sub/001.md": code_rows(6)})
    assert rf.classify_folder(folder, CODES)[0] == "fin_report"


# --------------------------------------------------------------------------
# §5.6 npl_finder.resolve_period - Decision Table (5)
# --------------------------------------------------------------------------

LINKS = {(114, 6): "u6", (114, 9): "u9", (114, 12): "u12"}


def test_Q1_no_request_takes_the_newest():
    assert npl.resolve_period(LINKS) == (114, 12, "u12", True)


def test_Q2_requested_month_is_published():
    assert npl.resolve_period(LINKS, 114, 9) == (114, 9, "u9", True)


def test_Q3_falls_back_to_the_newest_earlier_month():
    # These datasets lag by a month or two, so asking for a just-ended quarter
    # routinely lands here rather than being an error.
    assert npl.resolve_period(LINKS, 114, 11) == (114, 9, "u9", False)


def test_Q4_fallback_never_jumps_forward_when_an_earlier_month_exists():
    assert npl.resolve_period(LINKS, 115, 3) == (114, 12, "u12", False)


def test_Q5_request_older_than_everything_falls_forward_to_the_oldest():
    """FIXED (was PINNED BUG #16) - by correcting the contract, not the
    behaviour. With nothing at or before the request there is no earlier month
    to fall back to, so the oldest available is the best datum there is and
    returning it is right. What was wrong was the docstring flatly promising
    "never a later one", and the note explaining it as "isn't published yet"
    when the month is in fact no longer published at all.
    """
    assert npl.resolve_period(LINKS, 113, 1) == (114, 6, "u6", False)


def test_the_fallback_note_explains_the_right_direction():
    forward = npl._result("逾期放款總額", 114, 6, False, "u", "千元", {}, (113, 1))
    assert "no longer in the published dataset" in forward["note"]
    assert "used 114年6月 instead" in forward["note"]

    backward = npl._result("逾期放款總額", 114, 9, False, "u", "千元", {}, (114, 11))
    assert "isn't published yet" in backward["note"]


# --------------------------------------------------------------------------
# §5.7 merge_fin_and_con_rows (4)
#
# Rows are excel_rows tuples: (term, value, label, page, note, is_pct, is_amount)
# --------------------------------------------------------------------------

def row(term, value=1):
    return (term, value, "", "", "", False, True)


def test_M1_disjoint_terms_come_back_in_MERGED_TERM_ORDER():
    fin = [row("稅後淨利"), row("總資產")]
    con = [row("NIM")]
    assert [r[0] for r in rf.merge_fin_and_con_rows(fin, con)] == ["總資產", "稅後淨利", "NIM"]


def test_M2_terms_outside_the_order_list_are_appended():
    fin = [row("總資產"), row("未知項目")]
    assert [r[0] for r in rf.merge_fin_and_con_rows(fin, [])] == ["總資產", "未知項目"]


def test_M3_a_term_present_on_both_sides_is_emitted_once():
    """FIXED (was PINNED BUG #5). fin won the ordered pass but con's copy was
    never marked used, so the trailing `merged.extend(con_rows if not used)`
    appended it again - two rows for one term, contradicting the docstring's
    "each term appearing exactly once". fin still wins, as documented (活存比
    and CIR only exist there)."""
    fin = [row("總資產", 100)]
    con = [row("總資產", 200)]
    merged = rf.merge_fin_and_con_rows(fin, con)
    assert [r[0] for r in merged] == ["總資產"]
    assert [r[1] for r in merged] == [100]


def test_M4_one_empty_side_passes_the_other_through():
    con = [row("NIM"), row("存放利差")]
    assert [r[0] for r in rf.merge_fin_and_con_rows([], con)] == ["NIM", "存放利差"]


def test_E6_detect_bank_reads_the_same_file_window_as_detect_industry(tmp_path):
    """FIXED (was PINNED BUG #22). detect_bank read only paths[0], while
    detect_industry_category reads the first five - it was widened precisely
    because one real filing's balance-sheet page didn't carry the entity's full
    name. A folder whose bank name appears on the second page therefore
    detected its industry fine and failed on the bank, and runfinder skipped
    the whole folder with 'Couldn't auto-detect the bank'."""
    folder = make_folder(tmp_path, "late_name", {
        "001.md": "封面\n",
        "002.md": "玉山商業銀行股份有限公司\n",
    })
    assert af.detect_bank(folder) == "玉山"
    assert af.detect_industry_category(folder) == "金融業"

# --------------------------------------------------------------------------
# detect_bank ambiguity - the sector-scale defence
# --------------------------------------------------------------------------

def test_B1_a_single_named_bank_is_detected(tmp_path):
    folder = make_folder(tmp_path, "b1", {"001.md": "玉山商業銀行股份有限公司\n"})
    assert af.bank_candidates(folder) == ["玉山"]
    assert af.detect_bank(folder) == "玉山"


def test_B2_two_named_banks_refuse_to_resolve(tmp_path):
    """detect_bank used to return the first match in BANK_NAME_ALIASES order,
    so a玉山 filing that names a peer in a related-party or interbank note
    resolved to 國泰 - and then ran with 國泰's COMPOSITE_TERMS and code
    overrides, producing a full set of plausible-looking WRONG numbers rather
    than an N/A. Ambiguity has to be refused, not ranked."""
    folder = make_folder(tmp_path, "b2", {
        "001.md": "玉山商業銀行股份有限公司\n",
        "002.md": "關係人交易：與國泰世華商業銀行之拆款\n",
    })
    assert af.bank_candidates(folder) == ["國泰", "玉山"]
    assert af.detect_bank(folder) is None


def test_B3_no_bank_name_at_all_is_still_none(tmp_path):
    folder = make_folder(tmp_path, "b3", {"001.md": "某控股股份有限公司\n"})
    assert af.bank_candidates(folder) == []
    assert af.detect_bank(folder) is None


def test_B4_the_message_separates_ambiguous_from_unsupported(tmp_path):
    """'Couldn't detect' alone doesn't say whether the entity is unsupported
    or merely ambiguous, and only the second is fixable with --bank."""
    ambiguous = make_folder(tmp_path, "b4a", {
        "001.md": "玉山商業銀行股份有限公司\n中國信託商業銀行股份有限公司\n"})
    unknown = make_folder(tmp_path, "b4b", {"001.md": "某某企業股份有限公司\n"})
    assert "Several banks" in af.bank_detection_message(ambiguous)
    assert "玉山" in af.bank_detection_message(ambiguous)
    assert "中信" in af.bank_detection_message(ambiguous)
    assert "Couldn't auto-detect" in af.bank_detection_message(unknown)


def test_B5_an_empty_folder_has_no_candidates(tmp_path):
    folder = make_folder(tmp_path, "b5", {})
    assert af.bank_candidates(folder) == []
    assert af.detect_bank(folder) is None

# --------------------------------------------------------------------------
# Industry axis - summary mode must not apply the bank layout to another scheme
# --------------------------------------------------------------------------

LIFE_FILING = {
    "001.md": "# 國泰人壽保險股份有限公司\n\n114年12月31日 個體財務報告\n",
    "008.md": ("| 代碼 | 科目 | 金額 | % |\n|---|---|---|---|\n"
               "| 10000 | 資產總計 | 7,000,000 | 100.0 |\n"
               "| 58200 | 保險成本 | 250,000 | 3.5 |\n"),
}


def test_I1_an_insurance_filing_is_refused_not_relabelled(tmp_path):
    """FIXED. summary mode matches SUMMARY_LAYOUT's codes RAW - it loads no
    coding dictionary - so nothing tied a code to the scheme it came from.
    58200 is 呆帳提存 under 金融業 but an insurance cost line under 保險業
    (INDUSTRY_CODING_FILES documents exactly this), and '國泰人壽保險股份有限
    公司' contains '國泰', so detect_bank resolved it happily. The filing then
    reported 保險成本 under the canonical term 呆帳提存(收回), sign-flipped by
    apply_cost_sign - a correctly-parsed number under a term that does not
    describe it, and term is what the CSV/Excel exports key on."""
    folder = make_folder(tmp_path, "life", LIFE_FILING)
    assert af.detect_industry_category(folder) == "保險業"
    # Two independent defences, and the identity one now fires first: no
    # profile lists 保險業 among its industries, so the 國泰 alias no longer
    # matches at all. This assertion was `== "國泰"` when only the layout
    # guard existed - flipped in the same commit that added the industry
    # field to BANK_PROFILES.
    assert af.bank_candidates(folder) == []
    assert af.detect_bank(folder) is None
    with pytest.raises(ValueError) as excinfo:
        af.collect_summary_rows(folder, "國泰")
    assert "保險業" in str(excinfo.value)


def test_I2_a_bank_filing_still_resolves_its_layout(tmp_path):
    folder = make_folder(tmp_path, "bank", {
        "001.md": "# 玉山商業銀行股份有限公司\n",
        "008.md": "| 代碼 | 科目 | 金額 | % |\n|---|---|---|---|\n| 10000 | 資產總計 | 100 | 100.0 |\n",
    })
    assert af.detect_industry_category(folder) == "金融業"
    rows = af.collect_summary_rows(folder, "玉山")
    assert [r["term"] for r in rows][:1] == ["總資產"]


def test_I3_an_unidentifiable_industry_is_refused_too(tmp_path):
    """The raw-code layout is most dangerous precisely when the scheme is
    unknown, and every other mode already hard-errors here (see
    resolve_coding_path). --industry is the documented escape hatch."""
    folder = make_folder(tmp_path, "noindustry", {"001.md": "封面\n"})
    assert af.detect_industry_category(folder) is None
    with pytest.raises(ValueError):
        af.collect_summary_rows(folder, "玉山")
    assert af.collect_summary_rows(folder, "玉山", industry="金融業") is not None


def test_I4_the_finsum_wrapper_carries_the_industry_through(tmp_path):
    """collect_summary_rows_finsum delegates, so the guard must not be
    bypassable by taking the summarized-disclosure route."""
    folder = make_folder(tmp_path, "life_finsum", LIFE_FILING)
    with pytest.raises(ValueError):
        af.collect_summary_rows_finsum(folder, "國泰")


def test_I5_every_supported_industry_maps_to_a_real_layout():
    """A layout keyed to an industry that isn't a real INDUSTRY_CODING_FILES
    key would silently never be selected."""
    assert set(af.INDUSTRY_SUMMARY_LAYOUTS) <= set(af.INDUSTRY_CODING_FILES)
    for industry, layout in af.INDUSTRY_SUMMARY_LAYOUTS.items():
        assert layout, industry

# --------------------------------------------------------------------------
# BANK_PROFILES - one source per entity, and an import-time completeness check
# --------------------------------------------------------------------------

def test_P1_every_derived_view_matches_its_profile():
    """The five old tables are now views. If a view drifts from the profile,
    an entity would behave differently depending on which name read it."""
    assert af.BANKS == list(af.BANK_PROFILES)
    for name, profile in af.BANK_PROFILES.items():
        assert af.BANK_NAME_ALIASES[name] == profile["aliases"]
        assert af.SUMMARY_CODE_OVERRIDES[name] == profile["code_overrides"]
        assert af.SUMMARY_CODE_OVERRIDES_FINSUM[name] == profile["code_overrides_finsum"]
        assert cf.PRIMARY_BANK_ENTITIES[name] == profile["primary_entities"]
        for term, codes in profile["composites"].items():
            assert af.COMPOSITE_TERMS[term][name] == codes


def test_P2_an_incomplete_profile_is_rejected_at_import_time():
    """The reason for consolidating six tables: adding an entity can no longer
    half-happen. A missing composites entry used to surface as one N/A row in
    one bank's output - indistinguishable from an undisclosed line."""
    good = af.BANK_PROFILES["玉山"]
    with pytest.raises(ValueError, match="missing"):
        af._validate_profiles({"新銀行": {k: v for k, v in good.items() if k != "composites"}})
    with pytest.raises(ValueError, match="unexpected"):
        af._validate_profiles({"新銀行": {**good, "typo_field": 1}})


def test_P3_a_profile_missing_a_composite_its_layout_needs_is_rejected():
    good = af.BANK_PROFILES["玉山"]
    with pytest.raises(ValueError, match="composite item"):
        af._validate_profiles({"新銀行": {**good, "composites": {"評價及已實現": ["49200"]}}})


def test_P4_a_profile_naming_an_unknown_industry_is_rejected():
    good = af.BANK_PROFILES["玉山"]
    with pytest.raises(ValueError, match="not one of|which is not"):
        af._validate_profiles({"新銀行": {**good, "industries": ["製造業"]}})
    with pytest.raises(ValueError, match="no aliases"):
        af._validate_profiles({"新銀行": {**good, "aliases": []}})


def test_P5_industry_scoping_is_what_separates_a_group_s_bank_from_its_insurer(tmp_path):
    """The same short alias identifies both 國泰世華銀行 and 國泰人壽. Only the
    filing's own industry tells them apart, which is why `industries` exists."""
    bank = make_folder(tmp_path, "p5bank", {"001.md": "國泰世華商業銀行股份有限公司\n"})
    life = make_folder(tmp_path, "p5life", {"001.md": "國泰人壽保險股份有限公司\n"})
    assert af.bank_candidates(bank) == ["國泰"]
    assert af.bank_candidates(life) == []
    # Without the industry signal both look the same - that is exactly the
    # state the con-call side stays in, and why None must not narrow.
    assert af.bank_candidates(life, industry=None) == []
    assert af.bank_candidates(life, industry="金融業") == ["國泰"]


def test_P6_an_earnings_call_deck_still_resolves_with_no_industry(tmp_path):
    """Decks carry no registered name, so detect_industry_category returns
    None for them. Narrowing on None would break con-call detection outright."""
    deck = make_folder(tmp_path, "p6", {"001.md": "# 玉山金控 2025年第四季法人說明會\n"})
    assert af.detect_industry_category(deck) is None
    assert af.detect_bank(deck) == "玉山"

def test_P6b_no_alias_is_an_ordinary_word_that_appears_in_every_filing():
    """The reason 第一銀行's aliases are the long forms. '第一' on its own is a
    substring of ordinary prose (第一階段, 第一季) in every other bank's
    filing, which would make that entity a candidate everywhere and turn
    detect_bank ambiguous - i.e. every folder skipped - for all of them."""
    prose = "民國114年度第一季，本行於第一階段採用預期信用損失模式。"
    for name, profile in af.BANK_PROFILES.items():
        for alias in profile["aliases"]:
            assert alias not in prose, f"{name}'s alias {alias!r} matches ordinary prose"


def test_P6c_no_alias_makes_another_entity_a_candidate(tmp_path):
    """Aliases are matched as substrings, so one being contained in another
    resolves both and detect_bank refuses. Cheap to pin, expensive to notice:
    the symptom is a folder silently skipped, not an error."""
    for name, profile in af.BANK_PROFILES.items():
        folder = make_folder(tmp_path, f"alias_{name}",
                             {"001.md": f"{profile['aliases'][0]}商業銀行股份有限公司\n"})
        assert af.bank_candidates(folder) == [name]


def test_P7_stating_an_empty_override_is_the_same_as_omitting_the_entity():
    """The two override tables used to omit entities with no overrides
    entirely; the profiles state them as empty instead. Both consumers read
    them with .get(bank, {}) (acctfinder:785 and collect_summary_rows), so
    absent and empty behave identically - this pins WHICH entities actually
    carry an override, so the difference stays cosmetic."""
    assert {n: o for n, o in af.SUMMARY_CODE_OVERRIDES.items() if o} == {"國泰": {"64000": "63000"}}
    assert {n: o for n, o in af.SUMMARY_CODE_OVERRIDES_FINSUM.items() if o} == {"國泰": {"64000": "61000"}}

def test_P8_an_unsupported_industry_says_so_instead_of_asking_for_bank():
    """--bank can't rescue a filing whose scheme no profile covers - every
    accepted value would be refused again by the layout guard, so pointing at
    --bank sends the reader down a dead end."""
    import pathlib, tempfile
    d = pathlib.Path(tempfile.mkdtemp())
    (d / "001.md").write_text("國泰人壽保險股份有限公司\n", encoding="utf-8")
    msg = af.bank_detection_message(d)
    assert "保險業" in msg and "no supported entity" in msg
    assert "--bank" not in msg
