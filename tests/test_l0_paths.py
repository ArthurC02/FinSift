"""L0 characterization tests - TEST_DESIGN §3.9, path resolution (4 cases).

Failure mode F5: every bundled data path is built from
`Path(__file__).parent.parent`. Moving a module one directory deeper (Phase 5
moves main() into src/cli/) silently changes what that resolves to. These are
the only tests that notice.
"""
import sys
from pathlib import Path

import pytest

import acctfinder
import callfinder
import npl_finder

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_PA1_industry_coding_files_exist():
    assert set(acctfinder.INDUSTRY_CODING_FILES) == {"金控業", "金融業", "保險業"}
    for category, path in acctfinder.INDUSTRY_CODING_FILES.items():
        assert Path(path).is_file(), f"{category}: {path}"


def _config_default(monkeypatch, tmp_path):
    """The --config default is built inline inside callfinder.main(), so the
    only way to read the real value is to run the parser. Stub load_terms as a
    tripwire and let main() hand us the resolved path."""
    seen = {}

    def capture(config_path):
        seen["path"] = config_path
        raise SystemExit(0)

    monkeypatch.setattr(callfinder, "load_terms", capture)
    monkeypatch.setattr(sys, "argv", ["callfinder.py", "--folder", str(tmp_path)])
    with pytest.raises(SystemExit):
        callfinder.main()
    return seen["path"]


def test_PA2_config_default_exists(monkeypatch, tmp_path):
    assert Path(_config_default(monkeypatch, tmp_path)).is_file()


def test_PA3_paths_survive_a_different_cwd(monkeypatch, tmp_path):
    # If any of these ever becomes a cwd-relative path, it still passes PA1/PA2
    # (which run from the repo root) and only fails here.
    monkeypatch.chdir(tmp_path)
    for path in acctfinder.INDUSTRY_CODING_FILES.values():
        assert Path(path).is_absolute() and Path(path).is_file()
    config = _config_default(monkeypatch, tmp_path)
    assert Path(config).is_absolute() and Path(config).is_file()


def test_PA4_npl_cache_dir_resolves_to_repo_root():
    # The oracle has to be the resolved path string: npl_cache/ is gitignored
    # and usually absent, so "the directory exists" would prove nothing.
    assert npl_finder._CACHE_DIR == REPO_ROOT / "npl_cache"
    assert npl_finder._CACHE_DIR.parent.name != "src"
