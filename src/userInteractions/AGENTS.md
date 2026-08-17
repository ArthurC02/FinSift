# userInteractions — 套件指引

**唯一的使用者進入點。** 自動判斷資料夾是財報還是法說會，跑對應的擷取器，把結果合成一份輸出。

> 全域紅線與驗證協定在 repo 根目錄的 [AGENTS.md](../../AGENTS.md)。這份只放這個套件內部的事。

## 四個 CLI 都從這裡進

```
python src/userInteractions/cli.py [folders...]   # 自動分類 + 合併輸出
python src/userInteractions/cli.py acct  ...      # → financialReports.statements
python src/userInteractions/cli.py call  ...      # → earningsCalls.summary
python src/userInteractions/cli.py npl   ...      # → regulatorDatasets.disclosures
```

子命令是**偷看 `argv[1]`** 分派的，不是 argparse subparsers：`cli` 自己吃裸的位置參數（`cli <folder> <folder>`），在外面包 subparsers 會改掉大家已經在用的介面。子命令名稱永遠不會是資料夾名稱，所以這個 peek 明確。

被路由的三個套件各自保留自己的 `argparse main()` 完全不變。**改它們的旗標不需要動這裡。**

## 只有這裡能同時 import 兩個擷取器

`financialReports` 去 import `earningsCalls` 是循環（法說會側已經 import 了財報側）。所以財報要法說會的 ROA/ROE 時，是 `lookup_concall_roa_roe` 在這裡查好再傳進去。

**新的跨擷取器邏輯一律落在這裡**，不要為了方便在任一擷取器裡開一條捷徑。

## 這個檔案要能用路徑直接跑

`python src/userInteractions/cli.py` 會把**這個目錄**放進 `sys.path` 而不是 `src/`，兄弟套件因此解析不到。所以檔案開頭自己 bootstrap `src/`，而不是要求每個人設 `PYTHONPATH`。這是唯一一個使用者被告知要用路徑執行的檔案。

## 不要做的事

- **不要把 `_MERGED_TERM_ORDER` 改成「財報詞 + 剩下的法說會詞」。** 完整列出來才是合併表列序的唯一真相來源 → [cli-and-export.md](../../docs/knowledge/cli-and-export.md#合併輸出的列順序)
- **不要把 `/100` 或 `/1000` 搬離匯出邊界。** 內部值保持文件自己的單位；換算只在寫 Excel 那一刻發生 → [cli-and-export.md](../../docs/knowledge/cli-and-export.md#excel-的百分比與千分之一)
- **不要把 `classify_folder` 的 `rglob` 改回 `glob`。** `.md` 放在子目錄的資料夾會被靜默跳過。
- **匯出檔名 `runfinder_export*.xlsx` 是使用者面向的產物**，不隨模組改名而改。

## 這個套件的知識文件

[cli-and-export](../../docs/knowledge/cli-and-export.md) ·
[ratios](../../docs/knowledge/ratios.md) ·
[industry-and-layout](../../docs/knowledge/industry-and-layout.md)
