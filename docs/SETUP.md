# 環境與執行

## 相依

repo **沒有** `pyproject.toml`／`requirements*.txt`，所以新環境不能靠套件管理檔自動還原。

- **Python 3.14.6** —— 最後驗證過的版本。這是「已驗證」不是「正式最低版本」，沒有測過更舊的。
- **`openpyxl`** —— 讀 `data/*.xlsx` 編碼字典、寫 Excel 匯出。
- **`pytest`** —— 測試。
- 其餘全部是標準函式庫。`tkinter`（資料夾選擇對話框）隨 CPython 安裝，Linux 上可能要另外裝發行版套件。

## 建立環境

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install openpyxl pytest
python -m pytest --collect-only -q
python -m pytest
```

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install openpyxl pytest
python -m pytest
```

既有環境已能 import `openpyxl` 與 `pytest` 就不必建 venv，但**交付時要說明實際使用的 Python 版本**。

> **不要相信任何文件裡硬編碼的測試條數**（含 `AGENTS.md` 與其他 `docs/`）。以 `--collect-only` 與實際執行結果為準。撰寫當下是 302 條。

## 執行

四個進入點都可以單獨跑，不帶資料夾參數時會開啟資料夾選擇對話框。

```bash
# 財報：單一報表 / 全部 / 比率 / 精選摘要
python src/userInteractions/runfinder.py acct <資料夾> balance_sheet
python src/userInteractions/runfinder.py acct <資料夾> summary --period 1 -v
python src/userInteractions/runfinder.py acct <資料夾> summary --bank 玉山 --industry 金融業
python src/userInteractions/runfinder.py acct <資料夾> all --export csv

# 法說會
python src/userInteractions/runfinder.py call --folder <資料夾> -v

# 自動判斷資料夾類型，兩個資料夾可合併輸出
python src/userInteractions/runfinder.py --export excel

# 金管會公開月報（會連外網）
python src/userInteractions/runfinder.py npl --banks 玉山 中信 --year 114
```

常用旗標：

| 旗標 | 用途 |
|---|---|
| `--period N` | 第 N 期，1 = 最新。依文件排列位置，不是日期標籤 |
| `--bank` | 略過機構自動偵測 |
| `--industry` | 略過產業自動偵測（`金控業`／`金融業`／`保險業`） |
| `--coding` | 直接指定編碼字典 `.xlsx`，蓋過產業偵測 |
| `--export csv\|excel` | 匯出而非印到 stdout |
| `-v` | 逐列顯示比對過程，**排查 N/A 的第一步** |

## Excel 匯出位置

`runfinder.py --export excel` 寫到 `~/Downloads`（`_EXCEL_EXPORT_DIR`）。CSV 匯出寫在來源資料夾裡。

## 平台

開發與驗證都在 Windows／PowerShell。程式碼本身沒有平台相依（一律用 `pathlib`），但：

- `sys.stdout.reconfigure(encoding="utf-8")` 是為了 Windows console 預設 cp1252 印不出中文。
- 文件裡的指令多半同時給 PowerShell 與 bash 版本。
