"""L1 characterization tests - TEST_DESIGN §4.7 (6 cases).

The profitability layouts and the entity-row grouper. Four of these six pin
known-wrong behaviour: each one produces a plausible number rather than an
error, so nothing downstream notices.
"""
import acctfinder as af
from callfinder import _row_sections


def md(*lines):
    return [(None, line) for line in lines]


def write_md(tmp_path, name, *lines):
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_O1_row_sections_maps_rows_to_the_nearest_bare_header_row():
    # 中信's 存放利差 table repeats 放款利率/存款利率 once per currency section,
    # with no currency wording in the metric row itself. The section header is
    # the only thing negative_terms can veto on.
    rows = [["台幣", "", ""], ["放款利率", "1.5", "2.0"],
            ["外幣", "", ""], ["放款利率", "3.0", "4.0"]]
    # A header row's own entry is None - it is not inside itself.
    assert _row_sections(rows) == {0: None, 1: "台幣", 2: None, 3: "外幣"}


def test_O2_metric_label_split_across_two_cells_is_still_classified():
    assert af.classify_metric_row(["資產報酬率", "稅後", "1.2"]) == "roa_posttax"
    assert af.classify_metric_row(["資產報酬率(稅後)", "", "1.2"]) == "roa_posttax"
    assert af.classify_metric_row(["純益率", "", "30"]) == "profit_margin"
    # ROE is recognised only as 淨值報酬率. 權益報酬率 / 股東權益報酬率 are not
    # in the vocabulary - pinned because widening it is an easy accidental
    # "improvement" during a refactor, and it would change which row wins.
    assert af.classify_metric_row(["淨值報酬率", "稅後", "10"]) == "roe_posttax"
    assert af.classify_metric_row(["權益報酬率", "稅前", "10"]) is None


def test_O3_a_full_width_dash_holds_its_metric_position():
    """FIXED (was PINNED BUG #15). _METRIC_TOKEN_RE's placeholder alternative
    was an ASCII '-' only, so a full-width '—' matched nothing, held no
    position, and every metric after it was read one column early - five
    plausible-looking numbers, all attributed to the wrong metric."""
    expected = {"roa_pretax": 0.5, "roa_posttax": None, "roe_pretax": 5,
                "roe_posttax": 6, "profit_margin": 30}
    for dash in ("-", "—", "–"):
        assert af.extract_metrics(["本公司", "0.5", dash, "5.0", "6.0", "30.0"]) == expected


def test_O4_a_section_heading_no_longer_disables_layout_3(tmp_path):
    """FIXED (was PINNED BUG #9). _has_entity_heading_before matched ANY
    numbered line, so an ordinary '1. 前言' on page one made every layout-3
    table in the file look like layout 2's and the extractor skipped them all.
    The figures still came out - layout 2 picked the table up - but attributed
    to an entity called 前言."""
    path = write_md(tmp_path, "001_x.md",
                    "1. 前言", "",
                    "| 項目 | 114年12月31日 |", "|---|---|",
                    "| 資產報酬率(稅後) | 0.8 |")
    assert af.extract_single_entity_profitability_tables(path)[0]["roa_posttax"] == 0.8

    # A heading that really does name an entity still hands the table to
    # layout 2, so the same table is never counted under both layouts.
    claimed = write_md(tmp_path, "002_x.md",
                       "1. 玉山金控及子公司", "",
                       "| 項目 | 114年12月31日 |", "|---|---|",
                       "| 資產報酬率(稅後) | 0.8 |")
    assert af.extract_single_entity_profitability_tables(claimed) == []
    assert af.extract_transposed_entity_tables(claimed)[0]["entity"] == "玉山金控及子公司"


def test_O4b_selection_prefers_the_correctly_attributed_copy(tmp_path):
    # When the two layouts disagree about a heading the table is extracted
    # twice, with identical figures - once as entity 前言 (layout 2) and once
    # as entity None (layout 3). entity=None is always in scope, so that is
    # the copy that wins.
    write_md(tmp_path, "010_prof.md",
             "1. 前言", "", "獲利能力", "",
             "| 項目 | 114年12月31日 |", "|---|---|",
             "| 資產報酬率(稅後) | 0.8 |", "| 淨值報酬率(稅後) | 9.9 |")
    entries = af.find_profitability_entries(tmp_path)
    assert sorted(str(e["entity"]) for e in entries) == ["None", "前言"]
    chosen = af._select_profitability_entry(entries, "玉山")
    assert chosen["entity"] is None and chosen["roa_posttax"] == 0.8


def test_O5_layout_2_labels_a_period_range_by_its_end_date(tmp_path):
    """FIXED (was PINNED BUG #14). Layout 2 called parse_single_date, which is
    unanchored and so matched the START date embedded in a range header. The
    period was labelled by its first day instead of its quarter end. Layout 3
    already used parse_period_header_date; this extractor now does too."""
    path = write_md(tmp_path, "003_x.md",
                    "1. 測試銀行", "",
                    "| 項目 | 115年1月1日至3月31日 |", "|---|---|",
                    "| 資產報酬率(稅後) | 0.8 |")
    row = af.extract_transposed_entity_tables(path)[0]
    assert row["period_label"] == "115年3月31日"
    assert row["quarter_num"] == 1
    # The two parsers, side by side - why the wrong one looked plausible.
    assert af.parse_period_header_date("115年1月1日至3月31日") == (115, 3, 31)
    assert af.parse_single_date("115年1月1日至3月31日") == (115, 1, 1)


def test_O5b_layout_2_still_reads_a_plain_single_date_header(tmp_path):
    # parse_period_header_date is a superset, but pin the common shape so the
    # widening didn't cost anything.
    path = write_md(tmp_path, "004_x.md",
                    "1. 測試銀行", "",
                    "| 項目 | 114年12月31日 |", "|---|---|",
                    "| 資產報酬率(稅後) | 0.8 |")
    row = af.extract_transposed_entity_tables(path)[0]
    assert row["period_label"] == "114年12月31日" and row["quarter_num"] == 4


def test_O6_an_account_name_containing_銀行_is_not_an_entity_row():
    """FIXED (was PINNED BUG #18). The company-keyword branch was a prefix
    match, so an ordinary balance-sheet line like 存放銀行同業 was read as an
    entity and swallowed the rows beneath it as continuations. That branch is
    now anchored - anything after the keyword must be a company suffix."""
    assert af.group_rows_by_entity(md("| 存放銀行同業 | 100 |", "| 現金 | 50 |")) == []


def test_O6b_real_entity_labels_still_match():
    """The other side of the anchoring. The first three alternatives stay
    PREFIX matches deliberately: anchoring them too would reject 本公司及子公司
    and fold a real entity's row into the previous one - trading a rare false
    positive for a false negative on the primary path."""
    for label in ["合併", "本公司", "本公司及子公司", "國泰世華銀行",
                  "玉山商業銀行", "中國信託商業銀行股份有限公司",
                  "國泰人壽保險股份有限公司"]:
        entries = af.group_rows_by_entity(md(f"| {label} | 1.0 |"))
        assert [e[0] for e in entries] == [label], label
