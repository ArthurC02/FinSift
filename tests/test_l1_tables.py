"""L1 characterization tests - TEST_DESIGN §4.1 / §4.2 / §4.3 (18 cases).

These three functions turn a .md file into the row shapes every extractor
downstream depends on. They take and return plain lists, so no fixture files
are needed: build_raw_lines()'s output shape is [(page_num, line), ...] with
page_num always None for markdown.
"""
from financialReports.statements import _split_dual_column_tables, group_rows_by_code, parse_pipe_tables
from core.numbers import nth_value
from core.tables import percent_stride_map


def md(*lines):
    """Wrap raw markdown lines in build_raw_lines()' (page_num, line) shape."""
    return [(None, line) for line in lines]


# --------------------------------------------------------------------------
# §4.1 parse_pipe_tables (7)
# --------------------------------------------------------------------------

def test_T1_single_table():
    tables = parse_pipe_tables(md("| A | B |", "|---|---|", "| 1 | 2 |", "| 3 | 4 |"))
    assert len(tables) == 1
    assert tables[0]["header"] == ["A", "B"]
    assert tables[0]["rows"] == [["1", "2"], ["3", "4"]]


def test_T2_two_tables_split_on_the_next_headers_divider():
    # build_raw_lines drops blank lines, so two tables separated only by a
    # blank line arrive back-to-back. The "next line is a divider" guard is
    # the only thing that stops them merging into one.
    tables = parse_pipe_tables(md("| A | B |", "|---|---|", "| 1 | 2 |",
                                  "| C | D |", "|---|---|", "| 3 | 4 |"))
    assert len(tables) == 2
    assert tables[0]["rows"] == [["1", "2"]]
    assert tables[1]["header"] == ["C", "D"]


def test_T3_bare_horizontal_rule_does_not_eat_the_last_row():
    # Real 4Q25 regression: a bare '---' section separator right below a
    # table's last row used to trigger the T2 guard and drop that row. Since
    # decks list periods oldest-first, the dropped row was the newest quarter.
    tables = parse_pipe_tables(md("| A | B |", "|---|---|", "| 1 | 2 |", "| 3 | 4 |", "---"))
    assert tables[0]["rows"] == [["1", "2"], ["3", "4"]]


def test_T4_no_divider_no_table():
    assert parse_pipe_tables(md("| A | B |", "| 1 | 2 |")) == []


def test_T5_header_and_divider_only():
    tables = parse_pipe_tables(md("| A | B |", "|---|---|"))
    assert len(tables) == 1 and tables[0]["rows"] == []


def test_T6_line_idx_points_at_the_header_row():
    # Callers use line_idx to walk backwards to the heading above a table, so
    # an off-by-one here silently attributes a table to the wrong entity.
    tables = parse_pipe_tables(md("## 前言", "| A | B |", "|---|---|", "| 1 | 2 |"))
    assert tables[0]["line_idx"] == 1


def test_T7_three_consecutive_tables():
    lines = md(*sum([[f"| H{i} |", "|---|", f"| {i} |"] for i in range(3)], []))
    assert len(parse_pipe_tables(lines)) == 3


# --------------------------------------------------------------------------
# §4.2 _split_dual_column_tables (5)
# --------------------------------------------------------------------------

DUAL_HEADER = "| 資產代碼 | 資產 | 金額 | 負債及權益代碼 | 負債及權益 | 金額 |"


def test_D1_dual_column_table_splits_into_two_contiguous_blocks():
    # Left half in full, THEN right half in full - not interleaved. Interleaving
    # would let a footnote-wrapped label on one side fold into the other side's
    # still-open entry during group_rows_by_code.
    out = _split_dual_column_tables(md(
        DUAL_HEADER, "|---|---|---|---|---|---|",
        "| 10000 | 資產 | 100 | 20000 | 負債 | 60 |",
        "| 11000 | 現金 | 40 | 30000 | 權益 | 40 |",
    ))
    assert [line for _, line in out[2:]] == [
        "| 10000 | 資產 | 100 |",
        "| 11000 | 現金 | 40 |",
        "| 20000 | 負債 | 60 |",
        "| 30000 | 權益 | 40 |",
    ]


def test_D2_single_code_column_passes_through_unchanged():
    lines = md("| 代碼 | 科目 | 金額 |", "|---|---|---|", "| 10000 | 資產 | 100 |")
    assert _split_dual_column_tables(lines) == lines


def test_D3_row_with_an_empty_right_half_yields_only_the_left():
    out = _split_dual_column_tables(md(
        DUAL_HEADER, "|---|---|---|---|---|---|",
        "| 10000 | 資產 | 100 | 20000 | 負債 | 60 |",
        "| 11000 | 現金 | 40 |  |  |  |",
    ))
    assert [line for _, line in out[2:]] == [
        "| 10000 | 資產 | 100 |",
        "| 11000 | 現金 | 40 |",
        "| 20000 | 負債 | 60 |",
    ]


def test_D4_two_dual_tables_in_one_file_keep_their_indices():
    # Replacements are spliced back-to-front precisely so the first table's
    # rewrite doesn't shift the second one's recorded span. Each split grows
    # the list, so a front-to-back splice lands the second replacement one
    # line early and swallows that table's divider - which is why this asserts
    # the whole line sequence rather than just counting the rows.
    divider = "|---|---|---|---|---|---|"
    out = [line for _, line in _split_dual_column_tables(md(
        DUAL_HEADER, divider, "| 10000 | 資產 | 100 | 20000 | 負債 | 60 |",
        DUAL_HEADER, divider, "| 40000 | 收益 | 30 | 50000 | 費用 | 20 |",
    ))]
    assert out == [
        DUAL_HEADER, divider, "| 10000 | 資產 | 100 |", "| 20000 | 負債 | 60 |",
        DUAL_HEADER, divider, "| 40000 | 收益 | 30 |", "| 50000 | 費用 | 20 |",
    ]


def test_D5_dual_header_without_a_divider_is_not_split():
    lines = md(DUAL_HEADER, "| 10000 | 資產 | 100 | 20000 | 負債 | 60 |")
    assert _split_dual_column_tables(lines) == lines


# --------------------------------------------------------------------------
# §4.3 group_rows_by_code (6)
# --------------------------------------------------------------------------

CODES = {"10000": "資產", "20000": "負債"}


def test_G1_known_code_starts_a_new_entry():
    entries = group_rows_by_code(md("| 10000 | 資產 | 100 |", "| 20000 | 負債 | 60 |"), CODES)
    assert [e[0] for e in entries] == ["10000", "20000"]


def test_G2_blank_leading_cell_is_a_continuation():
    entries = group_rows_by_code(md("| 10000 | 資產 | 100 |", "|  | 續 | 200 |"), CODES)
    assert len(entries) == 1
    assert entries[0][2] == ["10000", "資產", "100", "", "續", "200"]


def test_G3_untracked_but_code_shaped_cell_ends_the_entry_without_starting_one():
    # find_code_value() calls this with a code_dict holding a SINGLE code. Every
    # other row in the table then has an untracked code - if those counted as
    # continuations, their numbers would contaminate nth_value()'s scan.
    entries = group_rows_by_code(md("| 10000 | 資產 | 100 |", "| 99999 | 其他 | 999 |"), CODES)
    assert len(entries) == 1
    assert entries[0][2] == ["10000", "資產", "100"]


def test_G4_footnote_text_is_a_continuation():
    entries = group_rows_by_code(md("| 10000 | 資產 | 100 |", "| （附註四） |  | 200 |"), CODES)
    assert len(entries) == 1 and "200" in entries[0][2]


def test_G5_divider_rows_are_skipped():
    entries = group_rows_by_code(md("| 10000 | 資產 | 100 |", "|---|---|---|",
                                    "| 20000 | 負債 | 60 |"), CODES)
    assert len(entries) == 2


def test_G6_same_code_twice_yields_two_entries():
    # De-duplication happens later, in extract_statement - not here.
    entries = group_rows_by_code(md("| 10000 | 資產 | 100 |", "| 10000 | 資產 | 200 |"), CODES)
    assert [e[0] for e in entries] == ["10000", "10000"]


# --------------------------------------------------------------------------
# percent_stride_map - the header signal behind the #1 fix
# --------------------------------------------------------------------------

def test_stride_map_reads_the_header_for_share_columns():
    lines = md("| 代碼 | 科目 | 金額 | % |", "|---|---|---|---|",
               "| 10000 | 資產總計 | 6,120,884 | 100.0 |")
    assert percent_stride_map(lines) == [2, 2, 2]


def test_stride_map_drops_to_1_for_a_table_with_no_share_column():
    lines = md("| 代碼 | 科目 | 本期 | 前期 |", "|---|---|---|---|",
               "| 10000 | 資產總計 | 6,120,884 | 5,900,000 |")
    assert percent_stride_map(lines) == [1, 1, 1]


def test_stride_map_tracks_each_table_separately():
    lines = md("| 代碼 | 科目 | 本期 | 前期 |", "|---|---|---|---|",
               "| 10000 | 資產總計 | 100 | 90 |",
               "| 代碼 | 科目 | 金額 | % |", "|---|---|---|---|",
               "| 20000 | 負債合計 | 60 | 65.0 |")
    assert percent_stride_map(lines) == [1, 1, 1, 2, 2, 2]


def test_stride_map_defaults_to_2_before_any_header():
    # Anything whose enclosing table can't be identified keeps the historical
    # behaviour rather than guessing.
    assert percent_stride_map(md("封面", "| 10000 | 資產 | 100 |")) == [2, 2]


def test_group_rows_by_code_carries_its_table_stride(tmp_path):
    lines = md("| 代碼 | 科目 | 本期 | 前期 |", "|---|---|---|---|",
               "| 10000 | 資產總計 | 100 | 90 |")
    (code, _page, cells, stride), = group_rows_by_code(lines, {"10000": "資產"})
    assert code == "10000" and stride == 1
    assert nth_value(cells, 2, stride) == 90
