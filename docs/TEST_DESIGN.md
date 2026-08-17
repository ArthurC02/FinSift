# 測試案例設計 — 重構安全網

> 狀態：**已實作**。設計當時規劃 269 條，實際落地為 **302 條 pytest 案例**，位於 `tests/`。
> 執行：`python -m pytest`
>
> 本文仍是**現行有效**的文件，兩個用途：
> 1. **§7 是已知執行期 bug 的登記簿** —— 動手前先查，避免重複發現或重複修。已修的項目標記為「已修」並保留原始症狀敘述。
> 2. 各層案例的**設計理由**（為什麼用 ECT／BVT／決策表、為什麼某條案例存在）。
>
> 未落地的部分：§6.4 的 9 條 `disclosures` 網路 stub 案例（L5）、以及 F1 traceability matrix。
> 驗證協定（四道驗證、mutation testing、red-before）已獨立成 [VERIFICATION.md](VERIFICATION.md)。

## 0. 這批測試要做的事

目標是「確保重構有測試保護」，所以這是 **characterization test（特性化測試）**，不是驗收測試。差別決定了每一條預期值怎麼寫：

| | 驗收測試 | 特性化測試（本文） |
|---|---|---|
| 預期值來源 | 規格「應該是什麼」 | 現況「實際是什麼」 |
| 遇到 bug | 讓它 fail | **讓它 pass，並標記 `# PINNED BUG:`** |
| 重構後 fail 代表 | 可能是修好了 | **一定是重構改變了行為** |

**核心規則：現有的錯誤行為要照錯的樣子釘住。** 如果測試把 bug 寫成「正確答案」，重構時任何人不小心「修好」了它，測試會綠燈通過，你就失去了「行為沒變」的保證——那正是安全網唯一的用途。修 bug 是網子架好之後、另一支 commit 的事，屆時刻意翻轉被標記的斷言。第 7 節列出全部需要這樣處理的項目。

---

## 1. 重構到底會怎麼壞掉

測試不該平均分佈，該集中在「檔案/模組搬動時會無聲壞掉」的地方。這份程式碼有六種這樣的失效模式：

| # | 失效模式 | 本專案的實例 | 抓得到的測試 |
|---|---|---|---|
| F1 | 符號搬走、import 沒跟上 | 任何跨模組呼叫 | 每個 public 符號至少被呼叫一次 |
| F2 | **重複定義被合併成一個** | `statements.py` 有兩份 `_looks_like_code` / `_CODE_SHAPE_RE`（L149/L476、L146/L479）；後者遮蔽前者，兩者行為**不同**（前者 `str(cell).strip()`，後者裸 `cell`） | §3.3 專門釘住「生效的是後者」 |
| F3 | 巢狀在資料結構裡的函式失去 import | `LOAN_RECOMPOSITION` 的 9 個 lambda，AST 掃不到 | §5.5 逐 bank 觸發每一條公式 |
| F4 | 跨模組限定名重繫結 | `cli` 的 `af.` / `cf.` 前綴 | §6 CLI 端到端 |
| F5 | `Path(__file__).parent` 深度改變 | `data/*.xlsx`、`con_call_terms.json`、`npl_cache/` | §3.9 路徑解析 |
| F6 | 同名不同實作被誤併 | `print_summary_rows`/`write_summary_csv` 在 statements 與 decks 各一份，**列的形狀不同**；`page_num` 在 statements 與 cli 各一份（相同）；`_contains_any` 兩份（相同） | §4.6 兩份分別餵各自形狀 |

F2、F3、F6 是這個 repo 特有的地雷：它們在單元測試通過、`--help` 正常、甚至 import 成功的情況下依然會出錯。

### F1 traceability 規則

實作測試時維護一份「符號 → 測試 ID → direct／indirect」矩陣，否則「每個 public 符號至少被使用一次」無法查核。本文所稱 public 符號，是各 `src/*.py` 的 top-level 非底線函式／常數與 CLI `main`；函式須被呼叫，常數須被讀取或匯入。本文另行點名的 private 高風險符號也必須列入。合法的 CLI／整合案例若確實走到該符號可標為 indirect，不必為了湊數再寫一條空洞單元測試。

零引用符號（第 7 節 #20）不適合假裝有執行期行為：矩陣中將它們標為 `static-presence`，以一條 import／AST 存在性 meta-check 保護；若重構目標明確包含刪除死碼，則在該 commit 同步移除這項檢查並記錄為刻意的行為範圍變更。traceability matrix 與這條 meta-check 不列入下方 255 條行為案例。

---

## 2. 分層與優先序

| 層 | 內容 | I/O | 案例數 | 優先 |
|---|---|---|---|---|
| **L0** | 純函式：字串、數值、期間標籤、比對強度 | 無 | 117 | ★★★ 先做 |
| **L1** | 表格結構：list 進 list 出 | 無／記憶體物件／`tmp_path` | 41 | ★★★ |
| **L2** | 決策層：優先序、排名、分類 | 無（以 monkeypatch 注入依賴） | 68 | ★★ |
| **L3/L4** | 檔案層、匯出與 CLI | fixture 目錄 + stdout | 34 | ★★ |
| **L5** | 網路（`disclosures` 抓取） | 需 stub | 9 | ☆ 最後 |

合計 **269** 條行為案例（另有 F1 traceability／static-presence meta-check，不計入此數）。本文以「可獨立命名的一個情境／參數列」算一條；同一情境內的多個 assertion 不重複計數。L0+L1 共 158 條不需真實申報書 fixture；少數案例使用記憶體 workbook 或 `tmp_path` 生成的最小 `.md`。

---

## 3. L0 — 純函式（ECT / BVT）

### 3.1 `parse_numeric` — ECT + BVT（16 條）

值解析是整條管線的地基，錯了下游全錯。

| # | 等價類 | 輸入 | 預期（釘住） |
|---|---|---|---|
| N1 | 千分位整數 | `"1,234"` | `1234`（int） |
| N2 | 小數 | `"1.27"` | `1.27`（float） |
| N3 | 半形括號負數 | `"(1,234)"` | `-1234` |
| N4 | **全形括號負數** | `"（1,234）"` | **`None`** ← PINNED BUG |
| N5 | Unicode 減號 | `"−1234"` | `-1234` |
| N6 | 百分比 | `"1.27%"` | `1.27`（% 記號被吃掉） |
| N7 | 幣別/單位尾綴 | `"14,450元"` | `14450` |
| N8 | **CJK 數量級** | `"2萬"` | **`2`** ← PINNED BUG（量級靜默遺失） |
| N9 | 半形破折 | `"-"` | `None` |
| N10 | 全形破折 | `"—"` / `"–"` | `None` |
| N11 | N/A 字樣 | `"N/A"` `"NA"` `"n/a"` | `None` |
| N12 | 空 / 空白 | `""` `"   "` | `None` |
| N13 | 純文字 | `"資產總計"` | `None` |
| N14 | BVT 零 | `"0"` | `0` |
| N15 | BVT 型別轉換 | `"0.0"` | `0`（**float→int**，`value == int(value)` 觸發） |
| N16 | BVT 負零 | `"(0)"` | `0`（非 `-0`） |

N15 特別重要：回傳型別會隨值改變，下游 `isinstance(value, (int, float))` 與 Excel `value == int(value)` 的格式判斷都依賴它。

### 3.2 `nth_value` — BVT（9 條）★ 最高風險

函式假設「值、%、值、%」交替，只取 `numeric_positions[0::2]`。這個假設破掉的方式不只一種。

`nth_value` 接收的是**完整列**，包含第一格 code／label；函式本身會略過 `cells[0]`。以下範例的 `C` 代表該第一格，不能從 fixture 省略。

| # | occurrence | 完整 cells（含 code／label） | 預期 | 說明 |
|---|---|---|---|---|
| V1 | 1 | `C,100,10%,90,9%` | `100` | 正常 |
| V2 | 2 | `C,100,10%,90,9%` | `90` | 正常 |
| V3 | 1 | `C,100,90`（**無 % 欄**） | `100` | 正常 |
| V4 | **2** | `C,100,90`（**無 % 欄**） | **`None`** ← PINNED BUG |
| V5 | **0** | `C,100,10%,90,9%` | **`90`**（`value_positions[-1]`）← PINNED BUG |
| V6 | **-1** | `C,100,10%,90,9%` | **`100`**（`value_positions[-2]`）← PINNED BUG |
| V7 | -1 | `C,100`（只有 1 個值） | **`IndexError`** ← PINNED BUG |
| V8 | 3 | `C,100,10%,90,9%`（只有 2 個值） | `None`（正常上界） |
| V9 | 1 | `C,N/A,—`（完全無數字） | `None` |

**V4 的影響鏈**：無百分比欄的兩期資產負債表 → `--period 2` 永遠 N/A → `compute_ratios` 取不到 `assets_prev` → 丟 `RuntimeError` → `collect_roa_roe` 靜默吞掉 → ROA/ROE 的 cross-check 無聲消失。使用者只會看到少了一欄，看不到原因。

**V5/V6 的影響**：`--period 0` 不會報錯，會安靜回傳**最舊**的期間。

### 3.3 `_looks_like_code` — BVT ＋ F2 遮蔽驗證（7 條）

`^[A-Za-z0-9]{3,8}$`，邊界在 3 與 8。

| # | 輸入 | 預期 |
|---|---|---|
| C1 | `"AB"`（len 2） | `False` |
| C2 | `"A00"`（len 3） | `True` |
| C3 | `"A0001234"`（len 8） | `True` |
| C4 | `"A00012345"`（len 9） | `False` |
| C5 | `"10000\n"` | `True`（`$` 容許尾換行） |
| C6 | `"（附註四）"` | `False` |
| C7 | **`_looks_like_code(10000)`（傳 int）** | **`TypeError`** ← 釘住 F2：生效的是 L479 的裸 `cell` 版本，不是 L149 的 `str(cell).strip()` 版本 |

C7 是唯一能證明「哪一份定義生效」的測試。重構若把兩份合併成 L149 那份，C7 會從 TypeError 變成 `True`，測試 fail——這正是要的。

### 3.4 `apply_cost_sign` — Decision Table（6 條）

| 條件 | R1 | R2 | R3 | R4 | R5 |
|---|---|---|---|---|---|
| `value is None` | T | F | F | F | F |
| `is_cost` | – | F | T | T | T |
| label 含「減」 | – | – | F | T | F |
| label 是 None | – | – | F | F | T |
| **動作** | 回 None | 原值 | **翻號** | 原值 | **翻號** |

R4 以 S3（真正的「減：」）與 S4（只是文字含「減」的「減損」）兩個具體案例覆蓋；兩者走同一現行分支，但語意風險不同。

具體案例：

| # | value | label | is_cost | 預期 |
|---|---|---|---|---|
| S1 | `None` | 任意 | T | `None` |
| S2 | `-800` | `員工福利費用` | T | `800` |
| S3 | `5678` | `減：所得稅費用` | T | `5678`（不翻） |
| S4 | **`-800`** | **`折舊及攤銷－減損`** | T | **`-800`（不翻）** ← PINNED BUG：「減損」含「減」但不是「減：」前綴，正常減損被誤判成回沖 |
| S5 | `-800` | `None` | T | `800` |
| S6 | `1000` | 任意 | F | `1000` |

### 3.5 期間標籤 `parse_period_label` — ECT（14 條）

| # | 輸入 | 預期 | 類 |
|---|---|---|---|
| P1 | `"FY25"` | `(2025, 5)` | 年度 |
| P2 | `"2025"` | `(2025, 5)` | 裸年 |
| P3 | `"2100"` | `None` | 超出 `(19|20)\d{2}` |
| P4 | `"4Q25"` | `(2025, 4)` | 季 |
| P5 | `"0Q25"` | `None` | 季下界外 |
| P6 | `"5Q25"` | `None` | 季上界外 |
| P7 | `"1H25"` | `(2025, 2)` | 半年 |
| P8 | `"9M25"` | `(2025, 3.0)` | 月累計 |
| P9 | **`"12M25"`** | **`(2025, 4.0)`** | 落在「季」的 rank 上 |
| P10 | **`"15M25"`** | **`(2025, 5.0)`** | 與 FY 同 rank ← PINNED BUG |
| P11 | `"Dec-25"` | `(2025, 4)` | 月-年 |
| P12 | `"2025.12"` | `(2025, 4)` | 年.月 |
| P13 | `"2Q25¹"` | `(2025, 2)` | 上標註腳剝除 |
| P14 | `"Jun 25<sup>1</sup>"` | `(2025, 2)` | HTML 註腳剝除 |

### 3.6 `_normalize_year` — BVT（5 條）★

邊界在 100。

| # | 輸入 | 預期 | 說明 |
|---|---|---|---|
| Y1 | `"25"` | `2025` | 兩位年 |
| Y2 | `"99"` | `2099` | 邊界內上限 |
| Y3 | **`"100"`** | **`100`** | 邊界翻轉 |
| Y4 | **`"114"`** | **`114`** ← PINNED BUG：民國年不轉換，排序時永遠落在所有 20xx 之後 |
| Y5 | `"2025"` | `2025` | 四位年 |

### 3.7 `_rank_periods` — Decision Table（6 條）

| # | 輸入 | prefer_quarterly | 預期順序 | 說明 |
|---|---|---|---|---|
| R1 | FY25, 4Q25 | `False` | FY25, 4Q25 | 純比大小 |
| R2 | FY25, 4Q25 | `True` | **4Q25**, FY25 | 單季優先，符合設計 |
| R3 | **FY25, 12M25** | `True` | **12M25**, FY25 | ← PINNED BUG：12M 是**累計**不是單季，rank 恰為 4 被誤當單季拉前 |
| R4 | FY25, 9M25 | `True` | FY25, 9M25 | 正確：rank 3 不加成 |
| R5 | FY25, FY24 | `True` | FY25, FY24 | 跨年 |
| R6 | `[]` | `True` | **`ValueError`** | ← 前置條件：`max()` 對空序列。呼叫端都有守衛，但直接呼叫會炸 |

### 3.8 其餘 L0（合計 44 條）

| 函式 | 案例 | 重點 |
|---|---|---|
| `despace_cjk` | 4 | CJK 間空白移除、ASCII 間空白保留、混合、空字串 |
| `_is_table_divider` | 5 | `\|---\|` T／裸 `---` **F**（區隔線非表格線，關鍵）／`\| \|` ／無管線 F／`\|:-:\|` T |
| `_split_row` | 4 | 前後管線剝除、空儲存格、單欄、無管線 |
| `format_value` / `format_pct` / `format_maybe_pct` | 8 | `None`→`"N/A"`、負數千分位、`is_percent` 二分、`0` |
| `annualize` | 4 | 正常、`None` 值、`quarter_num=0`→`None`、`quarter_num=4` 為 no-op |
| `_add` / `_sub` | 5 | 正常、含 `None` 傳染、**`_add()` 零參數 → `0`（非 `None`）** ← 邊界 |
| `page_num` / `sheet_name` | 8 | `"013_x.md"`→`"013"`、無數字前綴 fallback、`None`→`""`；sheet 名 31 字截斷、非法字元、重名去重 |
| `roc_year` / `quarter_end_month` / `thousands_to_billions` | 6 | 換算、`quarter=5`→`None`、`None`-safe |

### 3.9 路徑解析（F5，4 條）

| # | 檢查 | 預期 |
|---|---|---|
| PA1 | `INDUSTRY_CODING_FILES` 三個值 | 檔案存在 |
| PA2 | `decks` 的 `--config` 預設值 | 檔案存在 |
| PA3 | 從**其他 cwd** 用絕對路徑呼叫 | 仍找得到 |
| PA4 | `disclosures._CACHE_DIR` | 解析到 **repo 根目錄**的 `npl_cache/`，不是 `src/npl_cache/` |

PA3 是唯一能抓到 `parent.parent` 深度改錯的測試。PA4 單獨列出是因為它的正確答案是「一個尚未存在的目錄」——不能用「檔案存在」當 oracle，只能斷言解析出的路徑字串。

### 3.10 `match_strength` — ECT（6 條）

四層強度是 §5.1 整張決策表的輸入，卻沒有自己的案例，補上。

| # | term_spec | text | 預期 | 說明 |
|---|---|---|---|---|
| MS1 | aliases `["淨收益"]` | `"淨收益"` | `3` | 整格完全相等 |
| MS2 | aliases `["淨收益"]` | `"手續費淨收益合計"` | `2` | 子字串 |
| MS3 | composite，權重和 ≥ threshold | 命中的文字 | `1` | 加權過門檻 |
| MS4 | aliases `["稅後淨利"]` + negative `["稅前"]` | `"稅前淨利"` | `0` | **negative 先判，連 exact 也否決** |
| MS5 | aliases `["淨收益"]` | `"營業費用"` | `0` | 無命中 |
| MS6 | aliases **`[""]`**（空字串） | 任意文字 | **`2`**；空格則 `3` | ← 設定檔無驗證：一個空 alias 讓該 term 命中**所有**列 |

MS6 屬設定資料問題而非程式邏輯錯誤，但 `load_terms` 完全不做 schema 驗證，重構若順手加上驗證就會改變行為——所以要釘住現況。

---

## 4. L1 — 表格結構（41 條）

### 4.1 `parse_pipe_tables`（7 條）

| # | 情境 | 預期 |
|---|---|---|
| T1 | 標準 header+divider+rows | 1 表 |
| T2 | 兩表中間隔空行（`build_raw_lines` 已丟空行） | **2 表**（「下一行是 divider 者為次表 header」守衛） |
| T3 | 最後一列下方緊接裸 `---` | 該列**不被吞掉**（真實 4Q25 回歸） |
| T4 | 無 divider | 0 表 |
| T5 | 只有 header+divider，無資料列 | 1 表、`rows == []` |
| T6 | `line_idx` 正確性 | 指向 header 行索引 |
| T7 | 連續三表 | 3 表 |

### 4.2 `_split_dual_column_tables`（5 條）

| # | 情境 | 預期 |
|---|---|---|
| D1 | header 有 2 個 `...代碼` | 拆成左半段全部、再右半段全部（**非交錯**） |
| D2 | header 只有 1 個 `代碼` | **byte-identical 原樣通過** |
| D3 | 右半全空的資料列 | 只產左半 |
| D4 | 多個雙欄表在同一檔 | 由後往前 splice，索引不錯位 |
| D5 | 雙欄表但缺 divider | 不拆（`parse_pipe_tables` 看不到） |

### 4.3 `group_rows_by_code`（6 條）

| # | 情境 | 預期 |
|---|---|---|
| G1 | 已知 code 起新項 | 新 entry |
| G2 | 首格空白 → 續行 | cells 併入前一項 |
| G3 | 首格是**非追蹤**的 code | **結束當前項且不開新項**（單 code 查詢的關鍵） |
| G4 | 首格是註腳文字 `（附註四）` | 續行 |
| G5 | divider 列 | 跳過 |
| G6 | 同一 code 出現兩次 | 兩個 entry（去重在 `extract_statement`） |

### 4.4 `restrict_section`（5 條）

start 找不到 → **`None`**（呼叫端據此決定跳過或全檔掃描，語意不可改）；找到 start 無 end → 到檔尾；TOC 行不算 start；end 在 start 之前 → 到檔尾；空 `end_markers` → 到檔尾。

### 4.5 編碼字典 `_find_coding_blocks` / `_extract_coding_block`（8 條）

| # | 情境 | 預期 |
|---|---|---|
| K1 | 原/修正後雙區塊 | 兩組 span，`orig_end = rev_start - 1` |
| K2 | 只有修正後 | `orig_block is None` |
| K3 | 有「修正說明」欄 | span 在該欄**前**截斷 |
| K4 | 資料列 `10000 資產` | **不被當成 header**（歷史回歸：曾把真資料列吃掉） |
| K5 | 名稱欄逐列位移 | 取「最長非 code 文字」 |
| K6 | code 有值、name 空 | 該列丟棄 |
| K7 | 合併儲存格 | `_unmerge_fill` 填滿每個成員格 |
| K8 | 原/修正後衝突 | **修正後勝** |

### 4.6 兩份同名函式分別驗證（F6，4 條）

`statements.print_summary_rows` 吃 `{term, value, is_percent, ...}`；`decks.print_summary_rows` 吃 `{term, kind, individual/value, ...}`。各餵各自的**合法形狀**一次，斷言完整的關鍵輸出欄位與 NOTE 行；`write_summary_csv` 同理，並斷言檔名分別為 `summary_export.csv`、`con_call_summary_export.csv`，以及各自的 CSV header。

不要把「把對方的列形狀餵進去必須壞」當成 oracle：若重構後的共用 dispatcher 能正確支援兩種 schema，這仍是行為相容的實作，不應被安全網阻止。F6 要保護的是兩個公開入口的合法輸出契約，不是內部必須各自維持一份函式本體。

### 4.7 其餘 L1（6 條）

| # | 函式／情境 | 輸入與預期（釘住） |
|---|---|---|
| O1 | `_row_sections` | 分節列後的資料列取得最近分節；分節列本身為 `None` |
| O2 | `classify_metric_row` | 指標標籤跨 2 格仍可辨識 |
| O3 | `extract_metrics` | 位置式取 5 個指標；`"—"` 全形破折不被 `_METRIC_TOKEN_RE` 的 `-` 匹配、不佔位，後續指標全部左移 ← PINNED BUG #15 |
| O4 | `_has_entity_heading_before` / layout 3 | 檔首先有 `1. 前言`，後面才是單一 entity 獲利能力表；函式回 `True`，`extract_single_entity_profitability_tables` 跳過整張表、結果為 `[]` ← PINNED BUG #9 |
| O5 | `extract_transposed_entity_tables` | `1. 測試銀行` heading 後的表格 header 為 `115年1月1日至3月31日`；未錨定的 `parse_single_date` 取起始日，輸出 `period_label == "115年1月1日"`、`quarter_num == 1`，而不是季末 ← PINNED BUG #14 |
| O6 | `_ENTITY_ROW_RE` / `group_rows_by_entity` | 首格為 `存放銀行同業` 時被當成新 entity entry，而不是一般科目／續行 ← PINNED BUG #18 |

---

## 5. L2 — 決策層（Decision Table，68 條）

### 5.1 `find_value_in_table` — 主決策表（14 規則）

條件樁：

| 條件 | R1 | R2 | R3 | R4 | R5 | R6 | R7 | R8 | R9 | R10 | R11 | R12 | R13 | R14 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| C1 偵測到 orientation | F | T | T | T | T | T | T | T | T | T | T | T | T | T |
| C2 heading 被 negative_terms 否決 | – | T | F | F | F | F | F | F | F | F | F | F | F | F |
| C3 orientation | – | – | row | row | row | row | row | col | col | col | col | col | col | col |
| C4 有欄/列標籤命中 | – | – | T | T | F | F | F | T | T | F | F | F | T | T |
| C5 `require_absolute` | – | – | F | T | – | – | – | F | T | – | – | – | F | F |
| C6 命中者是 share/growth 欄 | – | – | – | T | – | – | – | – | T | – | – | – | – | – |
| C7 heading 命中 term | – | – | – | – | T | T | F | – | – | T | T | F | – | – |
| C8 恰好 1 個非 share 值欄/列 | – | – | – | – | T | F | – | – | – | T | F | – | – | – |
| C9 目標期間有可解析數字 | – | – | T | – | T | – | – | T | – | T | – | – | F | T |
| C10 儲存格是 `%` 且 require_absolute | – | – | – | – | – | – | – | – | – | – | – | – | – | T |
| **動作** | | | | | | | | | | | | | | |
| 回傳值 | – | – | ✓ | – | ✓ | – | – | ✓ | – | ✓ | – | – | – | – |
| 回 `None` | ✓ | ✓ | – | ✓* | – | ✓ | ✓ | – | ✓* | – | ✓ | ✓ | ✓ | ✓ |
| matched_label 用 heading | – | – | – | – | ✓ | – | – | – | – | ✓ | – | – | – | – |

\* R4／R9 的 `None` 路徑不同：**row_period 先過濾 candidates 再判斷**（share 欄被濾光就掉進 heading fallback）；**col_period 若濾光則保留原 candidates**（見原始碼註解，刻意不對稱）。這個不對稱是重構最容易「順手統一」掉的地方，必須各有一條測試。

不可行規則：C1=F 時 C3–C10 全部不可達（已以 `–` 標示）；C4=T 且 C7 同時為 T 不影響結果（C4 優先），不另立規則。

### 5.2 `collect_roa_roe` 內層 `build` — 優先序表（11 條）

| 條件 | B1 | B2 | B3 | B4 | B5 | B6 | B7 | B8 | B9 | B10 | B11 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 揭露表有該指標 | T | T | F | F | F | F | T | T | T | F | F |
| concall 值存在 | – | – | T | T | F | F | – | – | – | T | T |
| manual 可推導 | F | T | F | T | T | F | T | T | T | T | F |
| 值/crosscheck 同號且差 > 2x | – | F | – | F | – | – | T | – | T | **T** | – |
| 值超出合理區間 | F | F | F | F | F | – | F | T | T | F | **T** |
| **source** | 揭露 | 揭露 | concall | concall | manual | – | 揭露 | 揭露 | 揭露 | concall | concall |
| **crosscheck 欄** | None | 有值 | None | **有值** | **清空** | – | 有值 | 有值 | 有值 | 有值 | None |
| **note** | "" | "" | "" | "" | "" | – | 分歧 | 不合理 | **兩則併存** | **分歧** | **不合理** |
| **回傳** | dict | dict | dict | dict | dict | **None** | dict | dict | dict | dict | dict |

B4 是容易漏掉的分支：concall 是主值時，只要 manual 可推導，manual 仍保留為 crosscheck。B5 的「crosscheck 清空」則是刻意的（manual 值本身就是主值，不能拿自己 cross-check 自己）。B9 驗證兩個 note 用 `; ` 併存而非互相覆蓋。

B10／B11 補上另一個容易漏的事實：**兩個 note 的判斷都在 `if/elif` 優先序之外，對任何 source 都會跑**。分歧 note 不是「揭露值專用」，concall 主值一樣會被拿去和 manual 比對。

**不可行規則（必須明確標記，不要為它寫案例）**：`source = manual` 時分歧 note **永不可能**觸發——manual 分支在賦值後立刻把 `crosscheck` 設為 `None`，而分歧檢查的前置條件是 `crosscheck is not None`。這條不可行性是刻意設計（不能拿自己 cross-check 自己），重構若不慎讓它變成可觸發，屬於行為改變。

### 5.3 分歧檢查的符號等價類（6 條）★

`max(value, crosscheck) / min(abs(value), abs(crosscheck) or 1e-9)` — 分子沒取絕對值。

| # | value | crosscheck | 實際觸發 | 應該觸發 |
|---|---|---|---|---|
| X1 | 1.0 | 3.0 | ✓ | ✓ |
| X2 | 3.0 | 1.0 | ✓ | ✓ |
| X3 | **-3.0** | **1.0** | **✗** | ✓ ← PINNED BUG |
| X4 | -1.0 | 3.0 | ✓ | ✓ |
| X5 | **-3.0** | **-1.0** | **✗** | ✓ ← PINNED BUG |
| X6 | 0.8 | 0.9 | ✗ | ✗ |

虧損季（負 ROA/ROE）是合法輸入——合理區間本身就允許負值到 -5%／-50%——所以 X3/X5 不是理論邊界。

### 5.4 `classify_folder` — Decision Table + BVT（10 條）

| 條件 | F1 | F2 | F3 | F4 | F5 |
|---|---|---|---|---|---|
| `code_hits >= 5` | T | T | T | F | F |
| finsum 標記存在 | F | T | T | – | – |
| 檔數 `<= 30` | – | T | F | – | – |
| `con_hits > 0` | – | – | – | T | F |
| **結果** | `fin_report` | `fin_report_summary` | `fin_report` | `con_call` | `None` |

BVT（4 條）：`code_hits` = 4／5／6；檔數 = 30／31。

**額外 1 條**：把 `.md` 放進**子資料夾**。`classify_folder` 用 `glob("*.md")`（非遞迴），而 `detect_bank` / `find_code_value` / `collect_statement_rows` / `find_term_value` 全用 `rglob`。同一個資料夾，cli 分類為 `None`（跳過），statements 直接跑卻完全正常。釘住此不一致。

### 5.5 `LOAN_RECOMPOSITION` — 逐 bank 逐公式（F3，12 條）

四家銀行共 9 條 lambda + 1 個空 dict。**每一條都要被實際呼叫**，因為它們藏在巢狀 dict 裡，AST 掃描與 import 檢查都看不到——這正是先前重構漏掉 `_add`/`_sub` import 的原因。

| bank | 公式 | 測試 |
|---|---|---|
| 北富銀 | 企業放款、個人放款 | 2 條正常 + 1 條含 `None` 傳染 |
| 中信 | 企業放款、個人放款、放款餘額合計、外幣放款 | 4 條正常 |
| 玉山 | 房貸、個人放款、放款餘額合計 | 3 條正常（**其中須有一條走 `collect_con_call_summary` 的真實迴圈**，見下） |
| 國泰 | （空 dict） | 1 條：原值原樣通過 |

**對帳檢查（1 條）**：四項成分和與總額差 > `2.5` → `note` 非空；差 = 2.5 → 不觸發（BVT，`>` 非 `>=`）。

**玉山那 3 條不能只用直接呼叫 lambda 的方式測。** 每次直接呼叫都給一份全新的 `raw_values`，看不出迴圈有沒有把結果餵回去；而原始碼註解宣稱的「formulas read the RAW values, so none of them can see another's output」正好只在玉山可觀測——它的總額公式重新計算 `房貸 + 個人擔保貸款`，而 `房貸` 本身在同一個 dict 裡更早被重組。若改成鏈式，總額會從 2317.0 變成 2417.0，但所有直接呼叫的案例仍全綠。因此玉山至少一條必須以 `collect_con_call_summary`（stub 掉 `find_term_value` 與 `_GOV_BANK_NAMES`）驅動，並同時斷言 `matched_label` 已改寫為 `重組：…`。

### 5.6 `resolve_period` — Decision Table（5 條）

| 條件 | Q1 | Q2 | Q3 | Q4 | Q5 |
|---|---|---|---|---|---|
| 有指定 year+month | F | T | T | T | T |
| 該月已發布 | – | T | F | F | F |
| 有更早的月份 | – | – | T | T | F |
| **回傳** | 最新 | 該月 | 最近的更早月 | 最近的更早月 | **`min(links)`** |
| **exact** | `True` | `True` | `False` | `False` | `False` |

Q5 值得標注：docstring 說「絕不回傳更晚的月份」，但當請求早於所有已發布資料時，`min(links)` 回傳的是**更晚**的月份。`exact=False` 有揭露，所以不算靜默錯誤，但重構時容易被「修正」——釘住。

### 5.7 `merge_fin_and_con_rows`（4 條）★

| # | 情境 | 預期 |
|---|---|---|
| M1 | 兩側 term 不重疊 | 依 `_MERGED_TERM_ORDER` 排序 |
| M2 | term 不在 order 清單中 | 附加在尾端，保留原順序 |
| M3 | **同一 term 兩側都有** | **出現兩次**（fin 版在序中、con 版被附加到尾端）← PINNED BUG，docstring 明寫「each term appearing exactly once」 |
| M4 | 一側為空 | 另一側完整通過 |

### 5.8 其餘 L2（6 條）

`_select_profitability_entry`（entity=None 永遠 in-scope／別名命中優先／有值者優先）、`entity_tier`（`primary_aliases=None` 的舊行為分支）、`_rank_key`（strength → tier → quarterly_bonus 三層，**不含期間新舊**，故同強度同層時由檔名排序決定勝出）。

**CIR-NONE（PINNED BUG #4）**：monkeypatch `build_code_index`，令 `4xxxx` 回正常淨收益、`58400` 回 `(label, None, source_file)`（模擬 label-fallback 命中列但該期無值）；呼叫 `collect_summary_rows` 時預期在 `abs(opex_value)` 丟 `TypeError`。這條必須走 `build_code_index` 的 label-fallback 結果形狀，不能只測 `find_code_value` 的 code 通道，因為後者會跳過 `None`。

**ZERO-ASSETS（PINNED BUG #21）**：monkeypatch `find_code_value`，令 `10000` 兩期都回 `0`。`compute_ratios` 在 `net_income / ((0+0)/2)` 丟 **`ZeroDivisionError`**——它**不是** `RuntimeError` 的子類，而 `collect_roa_roe` 只寫 `except RuntimeError`，所以整個 run 直接崩潰，不會像其他取不到值的情況那樣降級成「cross-check 不可用」。釘住這個例外型別會逸出。

---

## 6. L3/L4 — 檔案層與 CLI

### 6.1 檔案／CLI — All-Pairs 21 條 + targeted 7 條

因子與水準：

| 因子 | 水準數 | 水準 |
|---|---|---|
| statement | 7 | balance_sheet / income_statement / cash_flow / equity_statement / ratios / all / summary |
| coding 來源 | 3 | `--coding` 明給 / `--industry` 明給 / 自動偵測 |
| period | 3 | 1 / 2 / 0 |
| export | 2 | 無 / csv |
| bank | 3 | `--bank` 明給 / 自動偵測成功 / 自動偵測失敗 |

全組合 378；**受限 all-pairs 為 21 條**。下方 E1–E3 是另加的 targeted 例外案例，不包含在這 21 條內。

限制與不可行組合（必須排除，否則 pairwise 產生器會生出無意義案例）：

- `statement=summary` **不讀** coding 字典 → coding 因子在此列不可行，固定為「自動」。
- `statement ∈ {balance_sheet, income_statement, cash_flow}` **不用** bank → bank 因子固定。
- `statement=equity_statement` → 必定失敗，其他因子不影響。
- bank=自動偵測失敗 只在 `{summary, ratios, all}` 有意義。

必含的三條 CLI 例外案例：

| # | 指令 | 預期（釘住） |
|---|---|---|
| E1 | `equity_statement` | **未捕捉的 `ValueError` traceback**（docstring 宣稱「raises a clear error」，實際是裸 traceback）← PINNED BUG |
| E2 | `--coding <不存在的路徑>` | openpyxl 原始例外，非友善訊息 |
| E3 | `--period 0` | **exit 0，輸出最舊期間的數字**（見 V5）← PINNED BUG |

另加 4 條檔案／設定／匯出案例：

| # | 呼叫 | 預期（釘住） |
|---|---|---|
| E4 | `write_combined_csv(..., ratio_rows=[同時含 ROA/ROE fallback 值的列], used_fallback=True)` | ratios 列只在第一個 ratio 欄寫 `roa_posttax_annualized`；`roe_posttax_annualized` 未寫入 CSV ← PINNED BUG #10 |
| E5 | 同一個 code 出現在**兩個不同 `.md` 檔** | `collect_statement_rows` 輸出**兩列**。`extract_statement` 的 `seen_codes` 只在單檔內去重，跨檔不去重 ← 釘住現況，因為「合併掃描」型重構最容易把它變成全域去重 |
| E6 | 一個資料夾，銀行名只出現在**第 2 個** `.md`（第 1 檔沒有） | `detect_bank` 回 **`None`**（只讀 `paths[0]`），但 `detect_industry_category` 在同一資料夾回**成功**（讀前 5 檔）← PINNED BUG #22 |
| E7 | `load_terms` 讀取 `{"壞設定":{"type":"composite","components":[{"terms":["x"]}]}}`（component 缺 `weight`） | `Component(**c)` 的裸 **`TypeError`** 直接逸出，沒有檔名／term／schema 說明 ← PINNED BUG #23 |

E6 的後果：`cli` 會印「Couldn't auto-detect the bank」並整個跳過該資料夾，而 `statements <folder> balance_sheet` 對同一資料夾卻完全正常——因為後者只需要產業別，不需要銀行別。

### 6.2 `cli` 配對 — Decision Table（6 條）★

| 條件 | U1 | U2 | U3 | U4 | U5 | U6 |
|---|---|---|---|---|---|---|
| 恰 1 fin + 1 con | T | T | T | F | T | T |
| fin 的 `detect_bank` 成功 | T | **F** | T | T | T | T |
| `--export excel` | T | T | F | T | F | T |
| 兩資料夾相同 | F | F | F | F | F | T |
| **結果** | 合併成 1 sheet | **con 的列被整批丟棄** | 各自印出 | 各自 1 sheet | 各自印出 | 去重成 1 個 |

U2 是實質缺陷：`run_fin_report` 在偵測不到銀行時回 `None` → `pending_fin_rows` 從未設定 → 合併區塊不觸發 → 但 `pending_con_rows` 早已被扣住不放進 `excel_sheets` → **法說會那份資料完全消失在 Excel 裡，且沒有任何訊息**。釘住。

### 6.3 Fixture 需求

L3/L4 需要一組合成 `.md`：

| fixture | 內容 | 涵蓋 |
|---|---|---|
| `fin_normal/` | 4 檔，含資產負債表（**有 % 欄**）、損益表、獲利能力（layout 3） | 主要路徑 |
| `fin_nopct/` | 資產負債表**無 % 欄**、兩期 | V4 / compute_ratios 失效鏈 |
| `fin_dual/` | 中信式雙欄資產負債表 | D1 |
| `fin_nested/` | `.md` 放在子目錄 | glob/rglob 不一致 |
| `fin_nobank/` | 無任何銀行別名 | U2 |
| `deck_normal/` | 法說會封面 + row_period 表 + col_period 表 | 主要路徑 |
| `deck_units/` | 同一檔內混用百萬元/拾億元 | `detect_unit_scale` |

合成資料即可，**不要放真實申報書**（體積、著作權，且真實檔案更新會讓測試漂移）。

### 6.4 `disclosures`（L5，9 條）

`_fetch_url` 必須 stub。純函式部分（`resolve_period` §5.6、`_parse_number`、`_find_header_column`、`roc_year` 等）不需網路，已列在 L0/L2。需要 stub 的只有：`_list_period_links` 的 href 解析（3 條：正常、PDF 雙胞胎不搶位、無匹配時 `RuntimeError`）、`_xlsx_from_zip`（2 條）、`_extract_columns_by_bank`（3 條：正常、堆疊 3 列表頭、找不到欄位丟 `RuntimeError`）、SSL 錯誤轉譯（1 條）。

**不要讓 CI 真的連 `banking.gov.tw`。** 先前測試已遇到間歇性 SSL 失敗被誤判為行為差異。

---

## 7. 必須「照錯的樣子釘住」的清單

重構期間這些全部維持現狀；網子完成後再逐條翻轉。每個執行期項目都必須能對應到前文的具名案例；不能只列在本節而沒有測試。#20 屬靜態符號盤點，另由 F1 traceability matrix 管理。

| # | 位置 | 現行（錯誤）行為 | 嚴重度 |
|---|---|---|---|
| 1 | `nth_value` | 無 % 欄的表，`period=2` 恆為 `None`，連帶使 ROA/ROE cross-check 靜默消失 | **高** |
| 2 | `nth_value` | `period=0` 回最舊期間、`period=-1` 回第一個值、單值時 `IndexError` | **高** |
| 3 | `cli.main` | fin 偵測不到銀行時，配對的 con-call 資料整批無聲丟失 | **高** |
| 4 | `collect_summary_rows` CIR | label-fallback 取到 `value=None` 時 `abs(None)` → `TypeError`（label 通道不像 code 通道會跳過 None） | **高** |
| 5 | `merge_fin_and_con_rows` | 兩側同名 term 會輸出兩次 | 中 |
| 6 | `collect_roa_roe` | 分歧檢查分子未取絕對值，負值（虧損季）漏報 | 中 |
| 7 | `apply_cost_sign` | 「減損」被誤判為「減：」前綴，減損不翻號 | 中 |
| 8 | `parse_numeric` | 全形括號負數 → `None`；`"2萬"` → `2`（量級遺失） | 中 |
| 9 | `_has_entity_heading_before` | 掃到檔首任何編號標題（如「1. 前言」）就停用整檔 layout 3 | 中 |
| 10 | `write_combined_csv` | fallback 分支只寫 ROA，**ROE 整個丟失** | 中 |
| 11 | `classify_folder` | 用 `glob` 而非 `rglob`，與其他四處掃描不一致 | 中 |
| 12 | `_normalize_year` | 民國年（114）不轉換，排序永遠墊底 | 中 |
| 13 | `_rank_periods` | `prefer_quarterly` 把 12M 累計誤當單季拉前 | 中 |
| 14 | `extract_transposed_entity_tables` | 直接用未錨定的 `parse_single_date`，期間區間被誤標為起始日（layout 2 未套用 `parse_period_header_date` 的修正） | 低 |
| 15 | `extract_metrics` | 全形破折不佔位，導致 5 個指標整排位移 | 低 |
| 16 | `resolve_period` | 請求早於所有資料時回傳**更晚**的月份，與 docstring 相反 | 低 |
| 17 | `main`（statements） | `equity_statement` 丟裸 traceback 而非友善訊息 | 低 |
| 18 | `_ENTITY_ROW_RE` | 「存放銀行同業」等一般科目被當成 entity 列 | 低 |
| 19 | `_looks_like_code` / `_CODE_SHAPE_RE` | 各有兩份定義，後者遮蔽前者且行為不同 | **重構風險** |
| 24 | `statements._ENTITY_HEADING_NAME_RE` vs `decks._ENTITY_NAME_RE` | 兩份幾乎相同的「公司名關鍵字」regex，各自一份（修 #9 時新增，因為 `statements` 不能反向匯入 `decks`）。正確的家是 `core/text.py`，但搬移屬重構、不能混進修 bug 的 commit | **重構風險**（F6 型） |
| 20 | 死碼 | **Phase 6 已處理。** 已刪除：`term_matches`、`RATIO_CODES`（連同其已過時的區段 banner）、`find_statement_rows`，以及 `decks` 三個從未被呼叫的匯入（`_is_table_divider`、`_split_row`、`format_value`）。**保留**：`fetch_latest_overdue_loans`、`DISCLOSURE_PAGE_URL` —— 兩者明寫是 backwards-compatible alias，repo 內零引用但無法從這裡確認外部腳本是否依賴，已依 REFACTOR_PLAN §8 的既定規則加上 `# ponytail:` 註解保留，待你確認後可刪 | 已處理 |
| 21 | `compute_ratios` | 兩期資產皆為 0 時丟 `ZeroDivisionError`；它非 `RuntimeError` 子類，`collect_roa_roe` 的 `except RuntimeError` 攔不住，整個 run 崩潰而非降級 | **高** |
| 22 | `detect_bank` vs `detect_industry_category` | 前者只讀第 1 檔、後者讀前 5 檔。銀行名不在封面時，產業偵測成功但銀行偵測失敗，cli 整個資料夾被跳過 | 中 |
| 23 | `load_terms` | 完全無 schema 驗證：一個空字串 alias 讓該 term 命中所有列（MS6，strength 2）；`Component(**c)` 欄位錯誤只會丟裸 `TypeError`（E7） | 低（設定面） |
| 25 | `detect_bank` | 依 `BANK_NAME_ALIASES` 順序**首次命中就贏**。偵測別名必須是短名（法說會封面寫「玉山金控」，不寫登記名稱），而台灣銀行短名大量互為子字串，且財報在關係人／同業拆款附註提到同業是常態。命中兩家時不會降級成 N/A，而是**靜默套用錯的一家**的 `COMPOSITE_TERMS` 與 `SUMMARY_CODE_OVERRIDES`，產出整組看似正常的錯數字。實測：玉山財報第二頁含「與國泰世華商業銀行之拆款」→ 判成 `國泰`。**已修**（`bank_candidates` 收集全部命中，唯一時才解析；曖昧走既有的「偵測不到」路徑） | **高**（規模相依，4 家時罕見、近 40 家時必然） |
| 26 | `SUMMARY_LAYOUT` / `collect_summary_rows` | summary 模式**不載入任何產業編碼字典**，SUMMARY_LAYOUT 的 code 是 raw 比對，因此 code 不帶產業資訊。同一數字在不同 scheme 意義不同（`INDUSTRY_CODING_FILES` 註解自己就記了 58200：金融業＝呆帳提存，保險業＝保險成本線）。套到非銀行 scheme 不會失敗，而是**把正確解析的數字掛上錯誤的標準化 term**。實測：國泰人壽財報 → 產業正確判為保險業卻完全未被使用，`保險成本` 以 term `呆帳提存(收回)` 輸出並被 `apply_cost_sign` 翻號；`matched_label` 仍保留原標籤，但 CSV/Excel 與 `_MERGED_TERM_ORDER` 都以 `term` 為鍵。**已修**（`INDUSTRY_SUMMARY_LAYOUTS` 產業化，無 layout 的產業一律拒絕，不預設） | **高** |

19、20 不是執行期錯誤，但正因如此最危險：重構時「順手清掉」不會有任何測試變紅。第 3.3 節的 C7 就是為 19 準備的。

25、26 是把目標從「四家銀行」擴到「金融業所有實體」後才浮現的：兩者在四家樣本下都不會觸發，卻都不是降級而是**靜默給出錯數字**。共同的修法原則是**拒絕而非猜測** —— 判不出唯一銀行就要求 `--bank`，判不出對應 layout 就要求 `--industry`，兩者都沿用既有的「偵測不到」控制流，不新增分支。

**26 刻意不做的事**：沒有為保險業補一份 layout。那需要保險業 scheme 自己的保費收入／保險給付等 code，本 repo 沒有任何依據；憑空填會產生與原 bug 完全相同的錯誤標註，而且更難察覺。

### 機構軸（`BANK_PROFILES`）

以機構為鍵的設定原本散在**六個地方**：`BANKS`、`BANK_NAME_ALIASES`、`SUMMARY_CODE_OVERRIDES`、`SUMMARY_CODE_OVERRIDES_FINSUM`、`COMPOSITE_TERMS`（`statements`）、`PRIMARY_BANK_ENTITIES`（`decks`）。新增一家要改六處，而**沒有任何東西檢查是否都改到**；漏掉 `composites` 只會讓那家的摘要少一列 N/A，與「該機構真的沒揭露」完全無法區分。

現已收攏成單一 `BANK_PROFILES`，六個舊名稱全部改為由它推導（外部行為不變），並加上 `_validate_profiles()` 在 **import 時**拒絕不完整的 profile。

`industries` 欄位是關鍵：它讓 #25 的碰撞在**身分層**就不成立，而不只是靠 #26 的 layout 守衛事後攔截。`國泰人壽` 的財報產業判為保險業，而沒有任何 profile 把保險業列入 `industries`，因此 `國泰` 這個別名根本不參與比對。產業判不出來時（法說會 deck 從不載法定全名）則不收斂，維持全體候選 —— 否則法說會端偵測會整個失效。

**刻意留在原地**：`decks.LOAN_RECOMPOSITION`。它是**邏輯不是資料**（巢狀在 dict literal 裡的 lambda，正是 F3 失效類型），搬移風險與其他五張表不同級；而且它自身安全降級 —— 沒有條目的機構就是不做重組，這是正確預設（`國泰` 本來就沒有），不像缺 `composites`／`aliases` 會產生錯的或消失的數字。因此完整性檢查對它沒有東西可強制。

---

## 8. 刻意不測的部分

| 項目 | 理由 |
|---|---|
| `pick_folder` / `pick_folders` | tkinter 對話框，需 GUI；純 I/O 無邏輯 |
| `open_file` | `os.startfile` 單行包裝 |
| `write_excel_merged` 的視覺格式 | 測數值與 `number_format` 字串即可，不驗算渲染結果 |
| 真實政府網站往返 | 見 §6.4 |
| `print_*` 的精確排版 | 只驗「有無 NOTE 行」與欄位存在，不逐字比對，否則調字距就變紅 |

---

## 9. 建議執行順序

1. **L0 §3.1–3.3、§3.9–3.10**（42 條）— 地基 + F2 遮蔽 + F5 路徑 + 比對強度。這 42 條先綠，才動任何檔案。
2. **L1 §4.1–4.3、§4.6**（22 條）— 表格解析 + F6 同名函式。
3. **L2 §5.5**（12 條）— `LOAN_RECOMPOSITION` 全 lambda 觸發（F3）。這層最容易被漏，卻是先前重構唯一逃過所有檢查的失效。
4. 其餘 L0/L1/L2。
5. L3/L4 fixture 與 CLI。
6. L5 stub。

前三步共 76 條，覆蓋六種失效模式中的四種（F2、F3、F5、F6）——而 F2、F3、F6 正是靠 import 檢查與 `--help` 冒煙測試抓不到的那幾種。
