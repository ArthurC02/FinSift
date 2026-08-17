"""L3 tests - TEST_DESIGN §6.2, runfinder's folder pairing (6 cases).

main() is driven with classify_folder and both run_* helpers stubbed, so what
is under test is the pairing and sheet-assembly logic rather than extraction.
write_excel_merged is captured instead of executed - the workbook's contents
are §6.3's business, not this table's.
"""
import sys

import pytest

from userInteractions import runfinder as rf


def rows(tag):
    return [(tag, 1.0, "", "", "", False, True)]


@pytest.fixture
def harness(monkeypatch, tmp_path):
    """Returns (run, state). `run(*folders, export=...)` calls main() and
    leaves the sheets that would have been written in state["sheets"]."""
    state = {"sheets": None, "no_bank": False, "kinds": {}}

    monkeypatch.setattr(rf, "load_all_codes", lambda: {})
    monkeypatch.setattr(rf, "classify_folder",
                        lambda folder, codes: (state["kinds"][str(folder)], 6, 1))
    monkeypatch.setattr(rf, "run_fin_report",
                        lambda folder, export, verbose, **kw:
                            None if state["no_bank"] else (rows("fin") if export == "excel" else None))
    monkeypatch.setattr(rf, "run_con_call",
                        lambda folder, config_path, export, verbose:
                            rows("con") if export == "excel" else None)
    monkeypatch.setattr(rf, "write_excel_merged",
                        lambda sheets, out_path: state.__setitem__("sheets", sheets))
    monkeypatch.setattr(rf, "open_file", lambda path: None)

    def run(*folders, export=None):
        argv = ["runfinder.py", *[str(f) for f in folders]]
        if export:
            argv += ["--export", export]
        monkeypatch.setattr(sys, "argv", argv)
        rf.main()
        return state["sheets"]

    def folder(name, kind):
        path = tmp_path / name
        path.mkdir(exist_ok=True)
        state["kinds"][str(path)] = kind
        return path

    return run, folder, state


def test_U1_one_fin_and_one_con_merge_into_a_single_sheet(harness):
    run, folder, _ = harness
    fin, con = folder("fin_q4", "fin_report"), folder("deck_q4", "con_call")
    sheets = run(fin, con, export="excel")
    assert [name for name, _ in sheets] == ["fin_q4"]
    assert [r[0] for r in sheets[0][1]] == ["fin", "con"]


def test_U2_con_rows_survive_when_the_paired_fin_folder_is_skipped(harness):
    """FIXED (was PINNED BUG #3). run_fin_report returns None when detect_bank
    fails, so pending_fin_rows was never set and the merge never fired - but
    pending_con_rows had already been held back out of excel_sheets, so the
    entire earnings-call folder vanished from the workbook with nothing in the
    output mentioning it."""
    run, folder, state = harness
    fin, con = folder("fin_q4", "fin_report"), folder("deck_q4", "con_call")
    state["no_bank"] = True
    sheets = run(fin, con, export="excel")
    assert [name for name, _ in sheets] == ["deck_q4"]
    assert [r[0] for r in sheets[0][1]] == ["con"]


def test_U3_without_excel_export_each_folder_just_prints(harness):
    run, folder, _ = harness
    fin, con = folder("fin_q4", "fin_report"), folder("deck_q4", "con_call")
    assert run(fin, con) is None       # write_excel_merged never called


def test_U4_two_fin_folders_are_not_paired_and_get_their_own_sheets(harness):
    run, folder, _ = harness
    a, b = folder("fin_a", "fin_report"), folder("fin_b", "fin_report")
    sheets = run(a, b, export="excel")
    assert [name for name, _ in sheets] == ["fin_a", "fin_b"]


def test_U5_csv_export_writes_per_folder_and_produces_no_workbook(harness):
    run, folder, _ = harness
    fin, con = folder("fin_q4", "fin_report"), folder("deck_q4", "con_call")
    assert run(fin, con, export="csv") is None


def test_U6_the_same_folder_twice_is_deduplicated(harness):
    run, folder, _ = harness
    fin = folder("fin_q4", "fin_report")
    sheets = run(fin, fin, export="excel")
    assert [name for name, _ in sheets] == ["fin_q4"]
