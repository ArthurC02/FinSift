# 架構

> 讀這份之前先看 [../AGENTS.md](../AGENTS.md) 的 30 秒輪廓。
> 這份講的是**結構與為什麼**；逐函式細節在 [HANDOFF.md](HANDOFF.md)。

---

## 1. 三個軸

整個系統的變異可以拆成三個獨立的軸。**看懂這三個軸，就看懂了每個設定該住哪裡。**

| 軸 | 決定什麼 | 住在哪裡 |
|---|---|---|
| **產業** industry | 科目編碼字典、摘要報表定義、合理性範圍 | `INDUSTRY_CODING_FILES`、`INDUSTRY_SUMMARY_LAYOUTS` |
| **機構** entity | 名稱別名、代碼覆寫、複合項組成、法說會主體別名 | `BANK_PROFILES` |
| **文件類型** doctype | 走哪條擷取路徑 | `cli.classify_folder` |

### 1.1 產業軸

台灣的財報編製準則按**產業**分，不按機構分。同一個代碼在不同準則下意義不同 —— `58200` 在金融業是呆帳提存，在保險業是保險成本線。

```python
INDUSTRY_CODING_FILES   = {"金控業": ..., "金融業": ..., "保險業": ...}   # data/*.xlsx
INDUSTRY_SUMMARY_LAYOUTS = {"金融業": SUMMARY_LAYOUT, "金控業": SUMMARY_LAYOUT}
```

**沒有 layout 的產業一律拒絕，不預設。** `summary` 模式完全不載入編碼字典（它 raw 比對代碼），所以代碼身上不帶產業資訊；套到別的 scheme 不會失敗，而是把正確解析的數字掛上錯誤的標準化名稱。保險業字典雖然已備妥，但**刻意沒有 layout** —— 憑空填會重現同一個錯誤標註，只是更難察覺。

產業軸的天花板很低：金融業的編製準則總共約六種（金控、銀行、保險、證券商、票券、期貨商），不是機構家數。

### 1.2 機構軸

```python
BANK_PROFILES = {
    "國泰": {
        "industries": ["金融業", "金控業"],   # ← 這個欄位是安全的關鍵
        "aliases": [...],                    # 偵測與 --bank 正規化
        "primary_entities": [...],           # 法說會裡的主體子公司
        "code_overrides": {...},             # 個體財報的代碼覆寫
        "code_overrides_finsum": {...},      # 季報摘要揭露的代碼覆寫
        "composites": {...},                 # 複合項的組成代碼
    },
    ...
}
```

`industries` 不是分類標籤，是**碰撞防線**。偵測別名必須是短名（法說會封面寫「玉山金控」，永遠不寫登記名稱），而短名在集團內互為子字串：「國泰人壽保險股份有限公司」含「國泰」。沒有這個欄位，一家壽險會解析成兄弟銀行並繼承那家銀行的覆寫與複合公式。

`_validate_profiles()` 在 **import 時**執行。欄位缺漏、空別名、不存在的產業、或「該機構的 layout 需要卻沒定義的 composite」都會讓 import 直接失敗。這是六張散表做不到的事。

**推導視圖**：`BANKS`、`BANK_NAME_ALIASES`、`SUMMARY_CODE_OVERRIDES`、`SUMMARY_CODE_OVERRIDES_FINSUM`、`COMPOSITE_TERMS`、`decks.PRIMARY_BANK_ENTITIES` 全部由 `BANK_PROFILES` 推導，改一處即可。**不要直接編輯推導視圖。**

### 1.3 文件類型軸

`cli.classify_folder` 用**結構證據**而非文字證據判斷：

| 類型 | 判準 |
|---|---|
| `fin_report` | ≥5 列的首格是三本字典裡的真實代碼 |
| `fin_report_summary` | 同上 ＋ 含「活期性存款比率」＋ 檔案數 ≤30 |
| `con_call` | 含法說會標記字串，且沒有足夠的代碼列 |

代碼列勝過文字標記 —— 法說會簡報常把「資產負債表」當成表格的一列標籤，純文字比對會誤判。

---

## 2. 模組地圖

```
src/
├── core/            共用解析層，不依賴任何上層
│   ├── text.py      CJK 空白、目錄行、頁碼、附註剝除
│   ├── numbers.py   數值解析、第 N 期取值、格式化、年化
│   └── tables.py    markdown 表格解析、雙欄拆分、代碼分組、% 欄推斷
├── statements.py    財報擷取 ＋ 產業字典 ＋ 機構 profile ＋ 摘要 ＋ CLI
├── decks.py    法說會擷取（匯入 statements 四個名字）
├── cli.py     資料夾分類與合併（匯入 statements 與 decks）
└── disclosures.py    金管會資料集（完全獨立）
```

依賴方向嚴格單向：

```
cli ──> decks ──> statements ──> core/tables ──> core/text
     └──────────────────────────┘                core/numbers
disclosures （不連接任何一邊）
```

`decks` 對 `statements` 只匯入四個名字：`derive_quarter_num`、`pick_folder`、`detect_bank`、`BANK_PROFILES`。**這四個都是金融領域語意或機構設定，不是通用解析工具** —— 通用的那些已經在 `core/`。若哪天發現又多了一個通用工具被從 `statements` 匯入，那是它該搬進 `core/` 的信號。

---

## 3. 資料流

### 3.1 財報路徑

```
資料夾 .md
  └─ build_raw_lines          去空行、附註處理、雙欄表格拆分
      └─ percent_stride_map   從表頭判斷值欄的 stride（有無 % 欄）
          └─ group_rows_by_code   依代碼分組，續行折疊
              └─ nth_value    取第 N 期的數字
```

**`percent_stride_map` 值得單獨理解。** 一列 `["10000","資產總計","6,120,884","100.0"]`（一期＋佔比）和 `["10000","資產總計","6,120,884","5,900,000"]`（兩期）從**列本身**無法區分 —— `%` 只出現在表頭，永遠不在資料格。任何列內啟發式都會對其中一種給出錯的數字。所以 stride 由表頭決定，由 `group_rows_by_code` 一路帶到 `nth_value`。

### 3.2 摘要路徑（`summary` 模式）

```
detect_industry_category  →  產業
bank_candidates(產業)     →  機構候選（依產業收斂）
  ├─ 0 個 → 拒絕，訊息區分「不支援此產業」與「認不出機構」
  ├─ ≥2 個 → 拒絕，要求 --bank（絕不猜）
  └─ 1 個 → collect_summary_rows
                ├─ INDUSTRY_SUMMARY_LAYOUTS[產業]   ← 沒有就 raise
                ├─ BANK_PROFILES[機構] 的覆寫與複合項
                ├─ build_code_index  一次掃描解析所有需要的代碼
                └─ summary_coverage_warning  過半 N/A 就出警示
```

**設計原則：拒絕而非猜測。** 判不出唯一機構就要求 `--bank`，判不出 layout 就要求 `--industry`。兩者都沿用同一條「偵測不到」控制流，不新增分支。理由是這些情境的失效不是降級成 N/A，而是**靜默給出錯數字**。

### 3.3 法說會路徑

```
load_terms(con_call_terms.json)  →  TermSpec（有 schema 驗證）
  └─ match_strength   三層：完全相等(3) > 包含(2) > 複合加權過門檻(1)
      └─ entity_tier  排除非主體子公司的表格
          └─ LOAN_RECOMPOSITION  各家放款口徑重組（lambda）
```

`LOAN_RECOMPOSITION` 是**邏輯偽裝成資料** —— 巢狀在 dict literal 裡的 lambda，靜態 import 檢查看不見。這是 `tools/undefined.py` 要遞迴進 dict、以及 L2 有九條測試逐一執行它們的原因。它刻意沒被收進 `BANK_PROFILES`。

---

## 4. 三層防護

同一個數字最多有三道獨立檢查，全部只**標記**不覆寫：

| 機制 | 做什麼 |
|---|---|
| ROA/ROE crosscheck | 揭露值與手算公式並存，差距過大或**正負號相反**就掛 note |
| 合理範圍 bounds | ROA 0.24–1.12%、ROE 3.33–15.02% 觀測範圍，抓解析錯誤而非正常變異 |
| `summary_coverage_warning` | 過半 N/A 時警示，把靜默降級變成有聲降級 |

**沒有任何一道會覆寫數字。** 交叉驗證的 note 明講「這是叫你去看一眼，不是證據說哪個數字錯」—— 因為手算公式自己的假設也未經完整驗證。

---

## 5. 已知的結構性限制

| 限制 | 現況 |
|---|---|
| `summary` 模式只支援金融業／金控業 | 保險業字典已備妥，缺 layout（刻意，見 §1.1） |
| 機構清單是四家 | `resolve_bank_name` 拒絕清單外的名字；擴充見 [EXTENDING.md](EXTENDING.md) |
| 不支援權益變動表 | 見 README 對應章節 |
| A/B harness 不在 repo 裡 | 見 [VERIFICATION.md](VERIFICATION.md) 已知缺口 |
| 只對四家銀行的合成 fixture 驗證過 | 沒有真實財報的 golden fixture |
