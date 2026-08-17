# 重構計畫

> ## 📁 狀態：**已執行完畢，本文為歷史紀錄**
>
> **不要照這份文件動手。** 現行的操作指引在 [EXTENDING.md](EXTENDING.md)，
> 架構現況在 [ARCHITECTURE.md](ARCHITECTURE.md)，驗證協定在 [VERIFICATION.md](VERIFICATION.md)。
>
> 執行結果：
>
> | Phase | 結果 |
> |---|---|
> | 0–1 | 安全網完成，302 條（設計時規劃 269） |
> | 2 | 機械式去重完成 |
> | 3 | `src/core/` 抽出完成（`text` / `numbers` / `tables`） |
> | 4 | **未執行** —— 深層拆分 `fin/` `call/`，經評估後刻意停在此處（見 §7 的理由） |
> | 5 | **未執行** —— CLI 薄化，優先權讓給修 bug |
> | 6 | 死碼刪除完成 |
> | 7 | 執行期 bug 修正完成 |
>
> 其後另外完成三項本計畫成書時尚未發現的問題（機構偵測碰撞、產業軸、機構軸），
> 見 [TEST_DESIGN.md §7](TEST_DESIGN.md) 的 #25、#26 與「機構軸」小節。
>
> **本文仍有價值的部分**：§9 的驗證方法論（已擴寫成 VERIFICATION.md）、§10 回退策略、
> §11「明確不做的事」—— 其中的禁令（不動 SSL、不合併兩份 `print_summary_rows`、
> 不改 CLI 介面）**現在依然有效**。

---

> 搭配 [TEST_DESIGN.md](TEST_DESIGN.md) 閱讀。
> 每個 Phase 都是獨立 commit、獨立可回退，且都有明確的 exit criteria。未達標不進下一階段。

## 0. 這份計畫的兩條硬規則

**規則一：沒有對應測試就不動程式碼。** 採分階段硬門檻：Phase 2 前完成 1a–1c；Phase 3 前完成 1d；Phase 5 前完成 1e；Phase 6 前完成 1f 並讓 269 條全綠。上一次重構的失效（`bankfinder/summary.py` 缺 `_add`/`_sub` import）躲過了 92 條行為基準、multiset 行數比對、`--help` 冒煙測試、以及 AST 缺漏 import 檢查——四道關卡全過，只有真正跑一次 summary 才炸出來。安全網不是形式，是唯一攔得住這類失效的東西。

**規則二：重構期間不修執行期 bug。** TEST_DESIGN §7 的 23 項全部先照現況釘住；其中 21 個執行期 bug（#1–#18、#21–#23）一律排到 Phase 7，每項一支 commit，並明確翻轉對應的 `# PINNED BUG:` 斷言。#19 是重複定義風險，由 Phase 2 在不改變生效行為的前提下處理；#20 是死碼盤點，由 Phase 6 經外部相容性確認後處理。理由：如果重構 commit 裡混著行為修正，「測試變紅」就失去診斷力——你分不清是搬壞了還是修好了。

---

## 1. 現況盤點

```
src/financialReports/statements.py   2,166 行   CLI + Excel 字典 + markdown 解析 + 數值 + 報表擷取
                               + 獲利能力 3 layout + 比率計算 + 策展摘要 + CSV + tkinter
src/earningsCalls/decks.py   1,331 行   從 statements 匯入 13 個名字
src/userInteractions/cli.py      479 行   同時匯入 statements 與 decks，並自帶一份 page_num
src/regulatorDatasets/disclosures.py     445 行   刻意獨立，無跨模組匯入
                    ─────
                    4,421 行   測試覆蓋率 0%
```

真正的耦合問題只有一個：**`decks` 從 `statements` 匯入 13 個名字**，其中 11 個其實是與財報無關的通用解析工具（`parse_numeric`、`_split_row`、`parse_pipe_tables`、`format_*`…）。`statements` 因此同時是「財報擷取器」和「共用解析函式庫」，這是所有結構問題的根。

其餘是局部污垢，不是結構問題：3 組重複定義、5 個零引用符號、1 個檔案過長。

---

## 2. 階段總覽

| Phase | 內容 | 行為改變 | 前置條件 |
|---|---|---|---|
| **0** | commit 現狀、建立 pytest 骨架 | 無 | — |
| **1** | 寫安全網（TEST_DESIGN 分三梯次） | 無 | P0 |
| **2** | 機械式去重 | `_looks_like_code` 的死定義消失，執行期行為不變 | P1a–1c 全綠 |
| **3** | 抽出 `core/` 共用層 | 無 | P2 + P1d 全綠 |
| **4** | ⚠️ **決策點**：是否再拆 `fin/` `call/` | 無 | P3 + 269 條全綠 + 你的決定 |
| **5** | CLI 薄化 | 無 | P3 或 P4；且 P1e 全綠 |
| **6** | 刪死碼 | 符號消失 | P5 + P1f 全綠（269 條全綠） |
| **7** | 逐項修執行期 bug，一項一 commit | **21 處**（#19/#20 已於 P2/P6 處理） | P6 全綠 |

Phase 0–3 + 5–6 是「行為不變」的重構主體。Phase 4 是可選的深化。Phase 7 才是修正。

---

## 3. Phase 0 — 地基

1. **先 commit 現有的檔案結構調整**（13 個 rename + 7 行路徑 + README + 兩份 docs）。目前這批還在 working tree，不 commit 的話 Phase 2 以後的 diff 會跟它混在一起，A/B 比對失去基準。
2. 建立測試骨架：

```
tests/
  conftest.py          # 只放 sys.path 插入 src/，不放 fixture 工廠
  fixtures/            # 合成 .md（見 TEST_DESIGN §6.3）
  test_l0_*.py
  test_l1_*.py
  ...
pytest.ini             # 只設 testpaths 與 -q
```

**刻意不做**：不裝 tox、不設 CI、不用 factory-boy／hypothesis、不建 fixture 繼承階層。純 pytest + 純函式參數化就夠。等真的痛了再加。

3. 把 scratchpad 裡驗證過的兩支工具收進 `tools/`：`segment.py`（AST 取符號行區間）與 `undefined.py`（含 `walk_functions`，能遞迴進 dict/list 找出巢狀 lambda 的缺漏 import）。Phase 3 以後每次搬移都要跑。

**Exit criteria**：`pytest` 能跑（0 條測試也算）、`git status` 乾淨。

---

## 4. Phase 1 — 安全網

依 TEST_DESIGN §9 的順序，分三梯次，每梯次一支 commit：

| 梯次 | 範圍 | 條數 | 為什麼是這個順序 |
|---|---|---|---|
| 1a | §3.1–3.3、§3.9–3.10 | 42 | 數值/編碼/路徑/比對強度是所有上層的輸入；F2 遮蔽與 F5 路徑深度也在這裡 |
| 1b | §4.1–4.3、§4.6 | 22 | 表格解析 + F6 同名函式契約 |
| 1c | §5.5 | 12 | `LOAN_RECOMPOSITION` 九條 lambda 全觸發（F3）——上次唯一漏網的失效類型 |
| 1d | 其餘 L0/L1/L2 | 150 | Phase 3 前完成 |
| 1e | L3/L4 fixture、設定、匯出與 CLI | 34 | Phase 5 前完成 |
| 1f | L5 stub | 9 | |

**1a–1c 這 76 條是 Phase 2 的硬門檻**：它們涵蓋 F2、F3、F5、F6 四種「import 檢查與 `--help` 都抓不到」的失效模式。76 條沒綠，Phase 2 不准開始。1d 可以與 Phase 2 並行，但沒完成就不准開始 Phase 3；1e 是 Phase 5 的門檻；1f 完成、269 條全綠後才可進 Phase 6。可選的 Phase 4 因拆分面更廣，也要求 269 條全數到位。

**各梯次 Exit criteria**：該梯次全綠；F1 traceability matrix 已更新（符號 → 測試 ID → direct/indirect），且涉及的 §7 執行期項目都有具名案例對應。Phase 1 整體完成的定義是 269 條全綠，另加 static-presence meta-check 全綠。

---

## 5. Phase 2 — 機械式去重

四項，每項獨立 commit。

| # | 動作 | 行為 |
|---|---|---|
| 2.1 | 刪除 `statements.py` L146–155 的 `_CODE_SHAPE_RE` + `_looks_like_code`（**前**一份） | **無改變** |
| 2.2 | `cli.page_num` 改為從 `statements` 匯入，刪本地副本 | 無改變 |
| 2.3 | `decks._contains_any` 改為從 `statements` 匯入，刪本地副本 | 無改變 |
| 2.4 | 不動 `print_summary_rows` / `write_summary_csv` | 無改變 |

**2.1 的方向很重要，容易做反。** 生效的是 **後**一份（L479，裸 `cell`，非字串會丟 `TypeError`）；前一份（L149，`str(cell).strip()`）從第 476 行被定義的那一刻起就是死碼。**刪前者、留後者 = 零行為改變**，測試 C7 保持綠。若反過來刪後者、留前者，`_looks_like_code(10000)` 會從 `TypeError` 變成 `True`，C7 變紅——那是行為改變，需要另外決定要不要，不能混在這階段。

2.4 不動的理由見 TEST_DESIGN §4.6：兩者名字相同但列的形狀不同，合併是設計決定不是清理，留到 Phase 4 再談。

**Exit criteria**：1a–1c 與當下已實作的其他測試全綠；`git diff` 顯示為純刪除（2.1）與 import 替換（2.2/2.3），無任何邏輯行變動。

---

## 6. Phase 3 — 抽出 `core/`

這是整個計畫唯一真正解決結構問題的一步。

### 目標形狀

```
src/
  core/
    __init__.py
    text.py      despace_cjk, _contains_any, _is_toc_like, strip_footnote,
                 _strip_footnote_suffix, page_num  (+ 各自的 regex)
    numbers.py   parse_numeric, nth_value, format_value, format_pct,
                 format_maybe_pct, annualize
    tables.py    build_raw_lines, _split_row, _is_table_divider,
                 parse_pipe_tables, _split_dual_column_tables,
                 restrict_section, group_rows_by_code, _looks_like_code
  statements.py  (≈1,450 行)
  decks.py  (≈1,280 行)
  cli.py
  disclosures.py
```

`core/` 不依賴任何上層，內部依賴只允許單向的 `tables.py → text.py`（`despace_cjk`）；`text.py`、`numbers.py` 不回頭依賴 `tables.py`。`disclosures.py` 維持獨立，不碰。

搬完之後，`decks` 對 `statements` 的匯入從 13 個降到 3 個：`derive_quarter_num`、`detect_bank`（這兩個是真的財報語意，不是通用工具，留在 `statements` 正確），以及 `pick_folder`（tkinter 對話框，語意上不屬於任一邊）。

`pick_folder` 可以另開 `core/ui.py` 收掉，把數字壓到 2——但那是個只有一個函式的模組，而 `cli` 還有自己的 `pick_folders`（不同函式，不是重複）。**建議先留著不動**，等 Phase 5 CLI 薄化時它自然會跟著 `main()` 一起搬，屆時再看要不要獨立。

### 搬移方式

**逐字搬移，不重寫。** 用 `tools/segment.py` 取出每個符號的精確行區間（含其上方的註解區塊），整段剪下貼上。禁止順手改名、順手加型別註解、順手調整格式——任何一項都會讓「行數 multiset 比對」這道驗證失效。

分三個**原子 commit**：`text.py` → `numbers.py` → `tables.py`。每一支 commit 都必須同時完成「新增目標模組 → 更新所有使用端 import／必要的暫時 re-export → 刪除原定義」，並立即跑該階段驗證；禁止先剪走三批符號、最後才一次補 import，因為那會讓中間 commit 無法 import、無法獨立回退。

### 已知陷阱（上次踩過）

| 陷阱 | 對策 |
|---|---|
| `try: import openpyxl / except ImportError` 是無名 AST 節點，name-keyed manifest 會漏掉 | 手動確認搬移後 `openpyxl` 仍在 `statements` 命名空間 |
| 巢狀在 dict literal 裡的 lambda 失去 import | 跑 `tools/undefined.py`，且**實際執行** Phase 1c 的 12 條 |
| `af.` / `cf.` 限定名跟著搬進沒有該別名的模組 | Phase 3 不搬 `cli`，這個風險延到 Phase 5 |
| 註解區塊與它說明的程式碼分家 | `segment.py` 已處理（向上吃連續註解，跨空行但不跨前一節點） |

**Exit criteria**：L0/L1/L2 全綠 + 下方 §9 對 Phase 3 規定的四道驗證全過 + `decks` 對 `statements` 的匯入 ≤ 3 個，且其中不含任何通用解析工具。

---

## 7. Phase 4 — ⚠️ 決策點：要不要再拆

Phase 3 做完，結構問題已經解決：共用層獨立、`decks` 對 `statements` 的匯入從 13 降到最多 3 個。剩下的只是「`statements.py` 還有 1,450 行」。

**我的建議是先停在這裡，把 Phase 5–7 做完再回頭看。** 理由：

- 1,450 行、職責是「財報擷取」單一領域，不是 Phase 3 之前那種「擷取器兼函式庫」的混雜。
- 上一次的 19 模組拆分我實際做過，4,800 行搬完之後**唯一**逃過所有自動檢查的失效就出在最深的那一層。模組數與這類風險成正比，收益卻遞減。
- 再拆的動機目前是「檔案很長」，不是任何具體的痛。YAGNI。

如果你之後有明確理由（例如要獨立測試獲利能力的三個 layout、或要讓別的專案重用財報擷取），再拆的路線是：

```
fin/    coding.py（Excel 字典）statements.py（擷取/索引）
        profitability.py（3 layouts）ratios.py summary.py
call/   terms.py periods.py tables.py summary.py
```

屆時 Phase 1 的安全網已經完整，風險比現在低得多。**這一步需要你決定，我不會自己往下做。**

---

## 8. Phase 5–7

### Phase 5 — CLI 薄化

**前置門檻：** Phase 1e 的 34 條 L3/L4 案例全綠；若 Phase 4 跳過，仍不得略過這個門檻。

`statements.py` / `decks.py` / `cli.py` 的 `main()` 與 argparse 設定移到 `src/cli/`，原檔留兩行 wrapper 以維持 `python src/userInteractions/cli.py acct ...` 不變。

**這一步會改變 `Path(__file__)` 的深度**（`src/cli/acct.py` 比 `src/financialReports/statements.py` 深一層），`decks` 的 `--config` 預設值要跟著調。TEST_DESIGN §3.9 的 PA1–PA4 正是為此存在。

同時處理 Phase 3 延後的 `af.`/`cf.` 限定名重繫結。

### Phase 6 — 刪死碼

`term_matches`、`RATIO_CODES`、`fetch_latest_overdue_loans`、`DISCLOSURE_PAGE_URL`、`find_statement_rows`。

刪除的同時，**移除 TEST_DESIGN §1 為它們設的 static-presence meta-check**，並在 commit message 記錄這是刻意的行為範圍縮減。這是全計畫唯一一次「刪掉一個檢查」是正確的。

`fetch_latest_overdue_loans` 與 `DISCLOSURE_PAGE_URL` 的註解寫著「backwards-compatible alias」——需先確認沒有外部腳本依賴。若不確定，保留並加 `# ponytail: 保留供外部呼叫，內部零引用`。

### Phase 7 — 修 bug

TEST_DESIGN §7 的 21 個執行期 bug（#19 已在 Phase 2 處理，#20 已在 Phase 6 處理）。嚴重度決定優先關注，但 #1 因變更面最大，刻意排到最後：

| 批次 | 項目 | 說明 |
|---|---|---|
| 7a | #2 #3 #4 #21 | 高：`nth_value` 非法 period、cli 資料丟失、CIR `abs(None)`、`compute_ratios` 除零逸出 |
| 7b | #5–#13 #22 | 中 |
| 7c | #14–#18 #23 | 低 |
| 7d | #1 | 最後處理：無 % 欄的 `nth_value` 核心假設 |

每一項一支 commit，內容 = 程式修正 + 翻轉對應的 `# PINNED BUG:` 斷言，兩者同一個 commit。**不准一支 commit 修多項**，否則測試變紅時分不清是哪一項的回歸。

#1（`nth_value` 在無 % 欄時取不到第 2 期）要特別小心：它的修法會改變 `numeric_positions[0::2]` 這個核心假設，影響每一份財報的每一列。這項放在 7d 最後做，且做完要對全部 fixture 做完整 golden 比對，只允許事先列明的 period-2 差異。

---

## 9. 分 Phase 驗證協定

測試綠只是第一道，但「行為完全相等」只適用於行為不變的重構，不能套到刻意刪符號或修 bug 的 Phase。

| # | 檢查 | 適用範圍 | 抓什麼 |
|---|---|---|---|
| V1 | `pytest`：執行當下已完成的 suite；Phase 6 起固定跑 269 條 + meta-check | 全部 Phase | 非預期行為改變 |
| V2 | **行數 multiset 比對**：搬移前後所有 `.py` 的非空行做多重集合比對，差集只能是預先列明的 import／wrapper 行 | Phase 3、5 | 搬移過程中的無意改寫 |
| V3 | `tools/undefined.py`（含 `walk_functions`） | 任何改 import 的 commit；至少 Phase 2、3、5 | 缺漏的 import，含巢狀在 dict/list 裡的 lambda |
| V4 | **`git worktree` A/B**：對上一個 commit 開 worktree，兩邊各跑全部 fixture 的四個進入點；CSV 做 byte diff，XLSX 比較 sheet 名、cell 值／公式、`number_format` 等語意內容 | Phase 3、5 | 前三道都漏掉的行為差異 |

V4 是行為不變重構的最後防線。XLSX 是 ZIP 容器，封裝 metadata／時間戳不屬於使用者契約，不能用整檔 byte diff；TEST_DESIGN §8 也只要求數值與 `number_format`，不驗算視覺渲染。

Phase 6 與 Phase 7 使用專屬協定：

- **Phase 6**：V1 全綠；static-presence meta-check 只移除本 commit 明列的死符號；對 backwards-compatible alias 取得外部使用決策。V2/V4 等價比對不適用。
- **Phase 7**：每支 commit 只翻轉一個 bug 項目的 pinned assertion；所有不涉及該項的案例仍須全綠。golden／A/B 比對只允許該項預先列明的差異，不能要求整體 byte-identical。V2 不適用。

**兩個上次踩過的坑**：

- `git diff --stat` 加了路徑過濾會**關掉 rename 偵測**，整個檔案顯示成新增。要看真實 diff 規模請用 `git diff -M` 不帶路徑參數。
- CI 與 A/B 比對一律 stub `disclosures` 網路。若任何一邊真的連到 `banking.gov.tw`，視為 harness 設定失敗，不以重跑判定結果。

---

## 10. 回退策略

每個 Phase 一個 commit（Phase 2、3、7 內部再細分）。任何一個 Phase 的驗證沒過：

1. `git revert` 該 commit——不要 `reset --hard`，保留失敗紀錄供診斷。
2. 在 TEST_DESIGN 補上「本來該抓到卻沒抓到」的案例。
3. 用兩個 worktree 驗證方向：新案例在 revert 後的正常舊 commit 上應為**綠**，在剛才失敗的重構 commit 上應為**紅**。兩者都成立，才重做該 Phase。

第 2 步是關鍵：每次逃逸都代表安全網有洞，補洞優先於重做。

---

## 11. 明確不做的事

| 項目 | 理由 |
|---|---|
| 改變任何 CLI 介面／參數名／輸出格式 | 使用者面向契約，重構不碰 |
| 加型別註解 | 與重構無關，會污染 V2 行數比對；要加就另開一批 |
| 換套件管理／加 `pyproject.toml`／打包成可安裝套件 | 目前 `python src/x.py` 就能跑，沒有痛點 |
| 統一 `print_summary_rows` 兩份實作 | 見 TEST_DESIGN §4.6，是設計決定不是清理 |
| 修 `disclosures` 的 SSL 行為 | 原始碼已寫明不停用驗證是刻意的（MITM 風險），照做 |
| 動 `data/*.xlsx` 與 `con_call_terms.json` | 資料不是程式碼 |
| Phase 4 的深層拆分 | 需你明確指示，見 §7 |

---

## 12. 建議節奏

| | 內容 | 相對工作量 |
|---|---|---|
| 第一段 | Phase 0 + Phase 1a–1c（76 條硬門檻） | 最大 |
| 第二段 | Phase 2；可與 Phase 1d（150 條）並行，但 Phase 3 等 1d 全綠 | 中 |
| 第三段 | Phase 3；同時完成 Phase 1e（34 條） | 大 |
| 第四段 | Phase 5；完成 Phase 1f（9 條）後進 Phase 6 | 中 |
| 第五段 | Phase 7a（4 項高嚴重度） | 中 |
| 第六段 | Phase 7b + 7c | 中 |
| 第七段 | Phase 7d（#1，最後） | 中 |

第一段結束就有實質價值：即使之後決定完全不重構，76 條測試本身就攔得住未來任何一次意外改動。**這是整個計畫裡最不可能後悔的投資**，也是我建議無論如何都先做完的部分。
