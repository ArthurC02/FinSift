# 帳戶代碼查找工具（account_code_finder）交接手冊

> 這份文件的目的：讓完全沒碰過這個專案的人，看完後能獨立維護、除錯、擴充這套工具。
> **如果手冊內容跟程式碼實際行為衝突，一律以程式碼為準**——
> 程式碼裡緊鄰重要常數（門檻值、容忍度、合理範圍上下限）的註解，通常說明了「為什麼設這個值」，
> 修改前請先讀那些註解。

---

## ⚠️ 撰寫後的變更（讀本文前先看這張表）

這份手冊寫於重構與修 bug 之前。下列章節**已經過時**，各自指向現在正確的來源。
其餘章節（核心觀念、各函式的判斷理由、門檻值的由來）仍然有效，而且是這些知識唯一的完整出處。

| 手冊章節 | 現況 | 看哪裡 |
|---|---|---|
| §2 系統整體架構 | 共用解析層已抽出成 `src/core/`（`text` / `numbers` / `tables`） | [ARCHITECTURE.md §2](ARCHITECTURE.md) |
| §4.5 `SUMMARY_CODE_OVERRIDES` | 六張以機構為鍵的表已收攏成 `BANK_PROFILES`，舊名稱全部改為推導視圖，**不可直接編輯** | [ARCHITECTURE.md §1.2](ARCHITECTURE.md) |
| 未涵蓋：產業軸 | `SUMMARY_LAYOUT` 已產業化為 `INDUSTRY_SUMMARY_LAYOUTS`，沒有 layout 的產業一律拒絕 | [ARCHITECTURE.md §1.1](ARCHITECTURE.md) |
| 未涵蓋：機構偵測 | `detect_bank` 改為多重命中即拒絕，並依產業收斂候選 | [ARCHITECTURE.md §1.2](ARCHITECTURE.md) |
| 未涵蓋：`nth_value` | 值欄 stride 改由表頭推斷（`percent_stride_map`），不再固定為 2 | [ARCHITECTURE.md §3.1](ARCHITECTURE.md) |
| 未涵蓋：覆蓋率警示 | 摘要過半 N/A 會出 `WARNING` | [ARCHITECTURE.md §4](ARCHITECTURE.md) |
| 提及但已刪除的符號 | `RATIO_CODES`、`find_statement_rows`、`term_matches` 已作為死碼刪除 | [TEST_DESIGN.md §7 #20](TEST_DESIGN.md) |

擴充操作請看 [EXTENDING.md](EXTENDING.md)，不要照本手冊的舊結構操作。

## 目錄

1. 專案是什麼、為什麼存在
2. 系統整體架構
3. 核心觀念
4. `statements.py` 逐項細節（財報抓取）
5. `decks.py` 逐項細節（法說會抓取）
6. `disclosures.py` 逐項細節（金管會網站抓取）
7. `cli.py` 逐項細節（整合執行 + Excel 匯出）
8. `con_call_terms.json` 結構與新增詞彙教學
9. 環境需求與執行總整理
10. 已知限制與「遇到這些狀況該怎麼辦」
11. 未來擴充完整指南
12. 定期維護檢查清單
13. 名詞對照表 / 檔案總覽附錄

---

## 1. 專案是什麼、為什麼存在

這套工具的目的：**自動從台灣四家銀行（國泰世華、中國信託/CTBC、台北富邦、玉山）的兩種公開文件裡，抓出固定的一組財務數字**，不用人工一頁一頁翻 PDF 找數字、算比率。

兩種來源文件：

1. **財務報告書（個別財報）**：銀行依法規揭露的資產負債表、損益表，每個項目都有官方「會計科目代碼」（一組固定數字，類似身分證字號），例如「10000」永遠代表「資產總計」。這種文件用**代碼精準比對**，不用猜文字。
2. **法人說明會簡報（法說會 / con-call deck）**：銀行對法人投資者做的簡報，內容包含放款結構、利差、成本效益比等經營指標，但**完全沒有統一格式**——每家銀行、甚至同一家銀行不同季度的簡報，用詞、排版都可能不同。這種文件靠**關鍵字比對**（而且是刻意設計成能容忍用詞差異的比對方式）。

除了這兩種文件，還有第三個資料來源：

3. **金管會銀行局公開的月報網站**——某些數字法說會根本不會公布（例如逾放比率、信用卡循環信用餘額），只能從金管會每月公布的全體銀行統計表格抓。

整套工具存在的理由：讓「這一季四家銀行的財報 + 法說會 + 政府網站」這三種來源的數字，**用一致、可重複、可驗證的方式**變成一張張整理好的表格（CSV 或 Excel），而不是每次都靠人工肉眼核對。

**這套工具不是、也不應該被誤會成：**
- 不是 AI／語言模型判讀 PDF 內容的工具——完全是規則式的文字/表格解析（正規表示式、表格欄位比對），可預測、可重現，沒有「猜測」的成分。
- 不會計算或推測任何官方文件沒有寫出來的數字，除非是明確定義好的「重組公式」（見 5.11）。
- 目前只支援這 4 家銀行。第 5 家銀行（例如台新銀）因為財報格式完全不同（沒有代碼欄位），目前**不支援**，強行套用等於會拿到全部錯的結果。

---

## 2. 系統整體架構

專案位置：`C:\Users\Brian Chang\.claude\scratch\account_code_finder\`

| 檔案 | 角色 |
|---|---|
| `statements.py` | 財報抓取主程式（代碼比對） |
| `decks.py` | 法說會抓取主程式（關鍵字比對） |
| `disclosures.py` | 金管會網站抓取（獨立小模組） |
| `cli.py` | 整合執行器（自動判斷資料夾種類 + 合併匯出 Excel） |
| `con_call_terms.json` | 法說會關鍵字字典（decks.py 讀取） |
| `con_call_terms_example.json` | 極簡範例字典（只給人看格式，不影響實際執行） |
| `金控業.xlsx` / `金融業.xlsx` / `保險業.xlsx` | 官方會計科目代碼對照表 |
| `Account Coding.xlsx` | 早期/備用的科目代碼檔（可用 `--coding` 指定） |
| `npl_cache/` | 金管會下載檔案的本地快取（自動產生） |
| `README.md` | 專案自帶說明（可能落後於程式碼，有疑問以本手冊與程式碼為準） |

**資料流向：**

```
財報 .md 資料夾 → statements.py（代碼比對 + 科目字典）→ 財報摘要 rows
法說會 .md 資料夾 → decks.py（關鍵字比對 + con_call_terms.json）→ 法說會摘要 rows
                        │
                        └─ 缺信用卡循環/逾放比率等 → disclosures.py → 金管會網站即時抓取
cli.py：自動判斷兩個資料夾各是財報還是法說會，各自呼叫上面兩支程式，
              可合併輸出成一份 Excel（含千分位、百分比格式）
```

**重要前提：輸入是 PDF 轉出的 markdown（.md）檔資料夾，不是 PDF 本身。** 每個 `.md` 檔用 Markdown 表格語法呈現原始 PDF 表格，檔名格式通常是 `頁碼_隨機碼.md`。PDF→md 轉檔流程不在本專案範圍內。

---

## 3. 核心觀念

### 3.1 兩種比對哲學
- **`statements.py`（財報）：精確代碼比對。** 找第一欄等於官方代碼的列，幾乎不會抓錯，除非代碼本身找不到（見 3.2）。
- **`decks.py`（法說會）：加權關鍵字比對，三層強度：**
  1. 強度3：整段文字完全等於某別名
  2. 強度2：文字裡包含某別名（子字串）
  3. 強度1：composite 型，加權分數 ≥ threshold
  同一詞多處命中時選強度最高者；打平則選「本行自己的表格」而非集團/子公司表格（entity_tier）；再打平選標籤文字最短者。

### 3.2 代碼不可靠時的退路：label_fallback
極少數加總列代碼欄位是空的，這時退而求其次用「文字標籤完全比對」（`SUMMARY_LABEL_FALLBACKS` / `find_value_by_label`），是完全比對，不是模糊比對。

### 3.3 期間判斷
財報：`period` 參數（1=當期，2=上期），日期格式主要是民國年（「114年12月31日」或「114.12.31」）。
法說會：格式更多樣（`4Q25`/`FY25`/`1H25`/`9M25`/`Dec 25`/`Dec-25`/`2025.12`），常常混用，邏輯集中在 `decks.py` 的 `parse_period_label()` 與相關正規表示式常數。**抓錯期間時第一個該查的地方。**

### 3.4 單位不統一（con-call 專屬）
同一份簡報不同頁單位可能不同（百萬元 vs 十億元）。`detect_unit_scale()` 往上找「單位：新台幣xx元」宣告，換算成十億元倍率。**只套用在餘額類數字，不套用在比率類數字**（比率無單位，分子分母抵銷）。找不到宣告時預設倍率 1.0。

### 3.5 「重組」（LOAN_RECOMPOSITION）
各銀行放款分類不同，無法直接抓到一個詞就代表要的數字。做法：先抓一堆「原始零件」，再用**人工核對過的公式**組合成標準分類。公式在 `decks.py` 的 `LOAN_RECOMPOSITION`，**每家銀行不同，是根據實際簡報核對出來的，不是猜的**。銀行改版簡報格式時最需要重新核對的地方。

### 3.6 交叉驗證與合理範圍檢查的哲學
文件揭露的數字永遠是主要答案，自算公式結果只當旁證附註提醒，**絕不用自算結果覆蓋揭露值**。合理範圍檢查（如 ROA 應在 ±5% 內）超出時只加註提醒，**不會自動攔截或改寫數值**。

---

## 4. `statements.py` 逐項細節

### 4.1 一句話
給一個 `.md` 資料夾 + 銀行名稱，回傳固定順序（`SUMMARY_LAYOUT`）的財務項目清單。

### 4.2 三種科目定義方式（`SUMMARY_LAYOUT` 的 `kind`）
1. **`"code"`**：直接用代碼找，例：`{"kind": "code", "code": "10000", "term": "總資產", "is_cost": False}`
2. **`"composite"`**：好幾個代碼加總，且**不同銀行公式不同**，實際公式在 `COMPOSITE_TERMS[name]` 按銀行分開列（例：其他非利息收益，國泰/北富銀是 `["49700","49750","49800"]`，中信多了 `49815`/`49899`，玉山用 `49899` 取代 `49800`）。這些組合是財務人員核對官方科目表定義出來的，**改動前務必跟會計對過**。
3. **`"label"`**：沒有代碼，純文字標籤找（目前只有 `活存比`，四家銀行都還沒揭露，先留輸出格位，之後真的出現時能自動接上）。

新增財報輸出項目時，先判斷屬於哪一種，照格式加進 `SUMMARY_LAYOUT`（composite 還要補 `COMPOSITE_TERMS`；某銀行公式還沒查到就不要放進該銀行的 key，會自動 N/A，不會出錯）。

### 4.3 批次抓取：`build_code_index()`
效能核心。舊版「每個代碼各自掃全資料夾一次」（15 科目 × 50 檔 = 750 次讀取），現在**只掃一次**，邊掃邊解析所有還沒找到的代碼，全部找到就提早結束。`collect_summary_rows()` 一次收集所有需要的代碼再呼叫一次。輸出跟舊版逐一呼叫 `find_code_value()` 完全一致（已驗證過）。

### 4.4 支出科目正負號：`apply_cost_sign` / `is_cost`
`is_cost=True` 的科目會把負數轉正顯示，**除非該列文字標籤已經有「減」字**（代表文件已經用「要扣多少」的正數形式寫，不用再轉一次）。費用正負號顯示錯誤時，先查 `is_cost` 設定跟該列標籤是否含「減」字。

### 4.5 銀行別代碼覆寫：`SUMMARY_CODE_OVERRIDES`
目前只有：`{"國泰": {"64000": "63000"}}`（國泰的稅後淨利用 63000 不是 64000）。新增銀行時若某代碼對不上，先檢查是否需要一條覆寫規則。

### 4.6 標籤退路：`SUMMARY_LABEL_FALLBACKS`
某些小計列代碼欄位本身是空白的，用文字標籤完全比對取代（如 `"64000": ["本期稅後淨利", "本期淨利", "本年度淨利"]`）。`"20000"` 這條目前沒有任何地方在用（原本是給已移除的資產=負債+權益檢核用的，保留著以防之後想恢復）。

### 4.7 雙欄位表格：`_split_dual_column_tables`
中信、玉山的資產負債表把「資產」「負債+權益」左右並排在同一實體列。偵測方式：表頭有沒有「以『代碼』結尾的欄位標題出現超過一次」，有的話把每列從中間切開成兩個獨立連續區塊。在 `build_raw_lines()` 裡自動呼叫，所有讀檔案的地方都會經過。新銀行若負債/權益整組是 N/A 但資產正常，先懷疑是不是雙欄排版。

### 4.8 ROA/ROE 三層優先順序與交叉驗證：`collect_roa_roe`
1. 財報自己揭露的獲利能力表格，**原文照抄，不做年化調整**（早期版本自動 ×4/季數，後來驗證發現不是每家銀行都適用這假設，中信/玉山第一季數字已接近年化，國泰卻是約1/4，無法從文件本身可靠判斷，故不再調整）。
2. 沒有的話用法說會自己公布的 ROA/ROE（由 `cli.py` 傳入，因為 statements 不能 import decks，避免循環引用）。
3. 都沒有才用手動公式（`compute_ratios()`：淨利÷平均資產/權益），明確標註是估算值。

不論主要來源為何，只要手動公式算得出來就額外算一次當交叉驗證，差距超過 `_ROA_ROE_CROSSCHECK_DIVERGENCE_FACTOR = 2.0` 倍才加註提醒，**絕不覆蓋主要答案**。另有獨立合理範圍檢查：ROA `[-5%, +5%]`、ROE `[-50%, +50%]`，超出才提醒。

**已知未修缺口**：中信某季財報因 `derive_quarter_num()` 解析不出季度，導致該季交叉驗證公式跑不出來（不影響主要揭露值，是明確決定過的已知限制）。

### 4.9 CIR 的計算方式
已從法說會移除，改在財報這邊，公式固定：`CIR = abs(營業費用) ÷ 淨收益 × 100`，重複使用已抓到的 `58400`/`4xxxx`，**不做跨檔案交叉驗證**（依明確指示）。移過來的原因：法說會「成本效率比」表用的口徑跟財報個別報表對不上（曾在中信案例中發現差近十個百分點，是集團/合併口徑 vs 個別口徑的差異），移除比較，改成財報端自己算自己的。

### 4.10 已移除功能：資產=負債+權益檢核
已依指示整個移除（輸出列本身，以及為它額外抓 `20000`/`30000` 的批次邏輯）。`SUMMARY_LABEL_FALLBACKS["20000"]` 保留但未使用。

### 4.11 常用函式速查

| 函式 | 用途 |
|---|---|
| `collect_summary_rows(folder, bank, period=1, ...)` | 整份摘要主入口 |
| `build_code_index(folder, codes, label_fallbacks=None, period=1)` | 批次代碼抓取 |
| `find_code_value(folder, code, period=1, label_fallback=None)` | 單一代碼抓取（除錯用） |
| `find_value_by_label(folder, label_aliases, period=1)` | 純文字標籤比對 |
| `collect_roa_roe(folder, bank, ...)` | ROA/ROE 三層邏輯 |
| `compute_ratios(folder, bank, coding, ...)` | 手動公式計算 |
| `print_summary_rows(rows)` / `write_summary_csv(folder, rows)` | 輸出 |
| `detect_bank(folder)` | 自動判斷銀行 |
| `resolve_coding_path(folder, explicit_path)` | 自動判斷用哪本科目字典 |

### 4.12 怎麼跑
```bash
python statements.py <資料夾路徑> summary --period 1
python statements.py <資料夾路徑> summary --export csv
python statements.py <資料夾路徑> summary -v
```
不加 `--bank` 會自動偵測。單次執行幾秒內完成，不需網路。

---

## 5. `decks.py` 逐項細節

### 5.1 一句話
給法說會 `.md` 資料夾 + `con_call_terms.json`，用模糊加權關鍵字比對找出經營指標，必要時連網補金管會網站的數字。

### 5.2 `TermSpec` 資料結構
```python
@dataclass
class TermSpec:
    name: str
    type: str = "exact"          # "exact" 或 "composite"
    aliases: list
    components: list             # 只有 composite 用
    threshold: float = 0.8       # 只有 composite 用
    negative_terms: list
    search_start: list = None
    search_end: list = None
```
- `"exact"`：只看 `aliases`。
- `"composite"`：`components` 是好幾組「同義詞+權重」，全部命中組別加總 ≥ threshold 才算命中（強度1）。

### 5.3 三層比對強度（`match_strength`）
強度3=完全相同 > 強度2=包含關係 > 強度1=composite加權 > 強度0=沒命中/被否決。抓錯詞時第一步永遠是查 `con_call_terms.json` 裡的定義（曾發生「Avg. rate of interest-earning assets」誤植成「放款均率」別名，範圍不同，已修正移除）。

### 5.4 `negative_terms`：兩層否決範圍
同時作用在「這一列自己的文字」跟「所屬章節/表格標題」。**兩次真實 bug**：
1. 中信「存放利差」表標題含「業界平均利差」字樣，`negative_terms` 誤含「業界平均」導致整張表被否決——已移除該否決詞。
2. 中信「個人放款」公式讀到「合併基礎」附錄表（已扣除信用卡循環）造成重複扣除——已把「合併基礎」加進相關詞的 `negative_terms`，強制鎖定 headline 表。

**改動 negative_terms 後務必實際核對抓到的數字，不要只看有沒有報錯。**

### 5.5 `entity_tier`：分辨表格屬於哪個實體
排除「非本行」的表格（金控母公司/其他子公司），**直接排除、不是排到候選最後**。`PRIMARY_BANK_ENTITIES` 定義各銀行自己在文件裡的所有名稱寫法。

### 5.6 表格方向：`row_period` / `col_period`
`detect_orientation()` 用多數決判斷哪一軸是期間軸，不要求全部命中（容忍佔比/成長率等非期間欄混雜）。

### 5.7 期間排序：`_rank_periods` / `prefer_quarterly`
`prefer_quarterly=True` 優先選單季而非累計欄。

### 5.8 標籤重複問題：`_row_sections`
中信「存放利差與淨利息收益率」表裡「放款利率」「存款利率」各出現三次（台幣/外幣/整體小節）。`_row_sections(rows)` 找出每列前面最近的「純小節標題列」（只有第一格有字），讓 `negative_terms` 能用小節標題否決台幣/外幣段，只留整體段。這是通用機制，未來遇到類似排版不需重寫。

### 5.9 單位換算：`detect_unit_scale`
往表格上方最多找8行找單位宣告，只套用在 `BALANCE_TERMS`，不套用在比率類與 CIR 輸入。找不到預設 1.0。

### 5.10 三組詞彙清單
```python
RATIO_TERMS = ["NIM", "放款均率", "存款均率", "存放利差"]   # 存放比已依指示移除
BALANCE_TERMS = ["企業放款", "房貸", "個人放款", "信用卡循環",
                 "法說會放款餘額合計", "法說會外幣放款"]
HELPER_TERMS = ["其他放款", "政府放款", "信貸", "其他個人授信其他",
                "海外子行", "海外分行", "OBU_DBU", "個人擔保貸款", "小額信貸"]
```
`BALANCE_TERMS` 裡多數項目是靠 `LOAN_RECOMPOSITION` 重組出來，不是直接抓詞對應。`HELPER_TERMS` 只當計算原料，不輸出。**CIR、逾期放款總額已從此檔案輸出移除**（CIR 移到 statements.py；逾期放款總額依指示整個拿掉）。目前額外從金管會補的兩個比率是 `逾放比率`、`備抵呆帳/逾期放款`（見 5.13）。

### 5.11 放款重組公式：`LOAN_RECOMPOSITION`
每家銀行各自的加減公式，**只能讀原始零件（raw_values），絕對不能讀其他重組後結果**（避免循環依賴）。這些公式是跟財務人員逐項核對法說會簡報內容才寫定的，**不是憑欄位名稱猜的**。銀行改版簡報格式或新增銀行時最需要重新核對的地方，屬於業務邏輯維護，不是「程式壞了」。

### 5.12 重組後的內部勾稽檢查
四分類加總 vs 法說會自己公布的放款餘額合計，差距超過 `_LOAN_RECONCILE_TOLERANCE = 2.5`（原本0.5，因中信自己簡報的分項加總對不上自己的總計數字才放寬）就加註警告。**用來抓異常大的落差，不是精確勾稽。**

### 5.13 金管會網站整合
`collect_con_call_summary()` 流程：
1. `detect_con_call_year(folder)` 解析西元年（從標題）。
2. `detect_con_call_quarter(folder)` 解析季別（找不到則退回 `derive_quarter_num`）。
3. 換算成民國年月：`disclosures.roc_year()` / `disclosures.quarter_end_month()`。
4. **分開呼叫兩個資料集**：
   - `fetch_credit_card_revolving()` → 只補「信用卡循環」（簡報自己有數字時優先用簡報）。
   - `fetch_overdue_loans()` → 輸出「逾放比率」「備抵呆帳/逾期放款」，**永遠**用這個來源（法說會完全沒有這兩個數字）。
5. 整段包在 `try/except`，網路/解析失敗只讓這幾欄變 N/A，不影響其他輸出。

`_GOV_BANK_NAMES` 是簡稱→政府資料集官方全名對照表，**新增銀行時必須補上，否則永遠抓不到政府資料（`legal` 會是 `None`，整段跳過）**。

期間解析失敗時不放棄，改抓「目前已公布的最新一期」，並在 `period_label`/`matched_label` 誠實標示。輸出的政府網站欄位期間跟簡報季度對不上時，先查 `detect_con_call_year`/`detect_con_call_quarter` 有沒有正確解析標題。

### 5.14 輸出函式
`print_summary_rows(rows)` / `write_summary_csv(folder, rows)`。CSV 裡數字是**已格式化的文字字串**，不適合拿去做 Excel 公式計算，需要能算的 Excel 用 `cli.py --export excel`。

### 5.15 怎麼跑
```bash
python decks.py --folder <資料夾路徑>
python decks.py --folder <資料夾路徑> --export csv
python decks.py --folder <資料夾路徑> "放款均率,存放利差"
python decks.py --folder <資料夾路徑> -v
```
本機比對幾秒內完成；連網部分（信用卡循環/逾放比率/備抵呆帳）第一次會有網路延遲，之後靠 `npl_cache/` 快取加速。需要能連 `banking.gov.tw`，連不上時這幾欄自動變 N/A，不影響其他欄位。

---

## 6. `disclosures.py` 逐項細節

### 6.1 定位
完全獨立，不 import 專案內其他檔案；`decks.py` 用「函式內部 import」呼叫它，即使它壞掉/網路不通也不會讓 decks.py 整個載入失敗。**未來加功能切記不要讓它反過來 import statements/decks，會循環引用。**

### 6.2 兩個資料集

| | 資料集1 `NPL_PAGE_URL` (id=590) | 資料集2 `CREDIT_CARD_PAGE_URL` (id=591) |
|---|---|---|
| 官方表名 | 本國銀行資產品質評估分析統計表 | 信用卡重要業務及財務資訊揭露 |
| 格式 | `.xlsx` | `.zip`（內含.xlsx+.ods） |
| 涵蓋 | 全體銀行整體（不分業務別） | 全體銀行信用卡業務專屬 |
| 抓取欄位 | 逾期放款總額、逾放比率、備抵呆帳/逾期放款 | 循環信用餘額 |
| 單位 | 新臺幣百萬元（比率是%） | 新臺幣千元 |

**⚠️重要教訓**：網址上的 `id=590`/`id=591` 不一定可靠對應資料集內容（曾發生截圖網址寫 id=591、但內容其實是資料集1）。**判斷資料集要看表格標題和欄位名稱，不要只看網址id。** 也曾誤把逾放比率/備抵呆帳接到信用卡資料集裡「名稱很像但定義不同」的兩個欄位（信用卡專屬的逾期三個月比率/備抵呆帳提足率），已改正接到資料集1的正確欄位。**新增這類欄位務必拿官方實際數字核對，不要只憑欄位名稱兜起來。**

### 6.3 期間辨識
```python
_NPL_PERIOD_RE = re.compile(r"(\d{3})_(\d{1,2})(?:\(\d+\))?\.xlsx", re.IGNORECASE)
_CC_PERIOD_RE = re.compile(r"/(\d{3})(\d{2})_[^/]*\.zip", re.IGNORECASE)
```
`_list_period_links()` 抓純伺服器渲染的HTML（不需JS引擎），正規表示式抓不到連結時代表**網站改版**，需要對照新格式調整正規表示式（外部風險，無法預防，只能事後修）。

`resolve_period()`：沒指定期間→用最新；有指定但未公布→往前找最近但不晚於要求的一期，標記 `exact=False`，絕不用更晚的期間頂替。

### 6.4 表頭定位：`_find_header_column`（本次修過的關鍵函式）
兩輪：
1. 單一儲存格完全比對（多數欄位適用）。
2. 找不到則找「表頭真正開始的列」（同列≥2欄有文字，用來跟只有第一欄有字的裝飾性標題列區分），從那列往下數3列把每欄文字接起來比對（處理「逾放比率(%)」這種被拆成3列疊放的表頭）。

目前寫死「往下數3列」，若金管會改版疊放列數不同需要調整。除錯時打開快取檔案攤開前10列看實際結構（範例程式碼見手冊內文）。

### 6.5 資料抓取
`_extract_by_bank()`（單欄）包在 `_extract_columns_by_bank()`（多欄，一次開檔案）外面。`_parse_number()` 同時處理數字型別跟純文字數字（信用卡表存文字，NPL表存數字）。

### 6.6 快取：`download_file` / `npl_cache/`
同名檔案已存在就不重下載。政府網站若同檔名重新上傳新內容（少見），本地快取不會自動偵測，需手動刪除對應快取檔案強迫重抓。目前無自動清理機制。

### 6.7 單位換算
`thousands_to_billions()`：千元→十億元，只用在信用卡循環餘額，**不適用**逾放比率/備抵呆帳（已是%數）。

### 6.8 對外函式
```python
fetch_overdue_loans(roc_year=None, month=None, banks=TARGET_BANKS, verbose=False)
# → {"values":..., "npl_ratios":..., "coverage_ratios":...}

fetch_credit_card_revolving(roc_year=None, month=None, banks=TARGET_BANKS, verbose=False)
# → {"values":...}

fetch_for_quarter(western_year, quarter, banks=TARGET_BANKS, verbose=False)
# 一次呼叫上面兩個；decks.py目前【沒有】用這個，是分開個別呼叫

quarter_end_month(quarter)   # 1→3,2→6,3→9,4→12
roc_year(western_year)       # 西元年-1911
thousands_to_billions(value)
```

### 6.9 SSL憑證問題
`_fetch_url()` 遇到憑證問題會給清楚錯誤訊息，指示 `pip install pip-system-certs`，**刻意不提供關閉驗證選項**（安全性底線，不要改成 verify=False）。

### 6.10 怎麼跑
```bash
python disclosures.py
python disclosures.py --year 2025 --quarter 4
python disclosures.py -v
```
第一次無快取5~20秒，有快取幾乎瞬間。與 `.md` 資料夾完全無關，只跟年/季有關。

---

## 7. `cli.py` 逐項細節

### 7.1 用途
自動判斷資料夾是財報還是法說會，各自呼叫對應程式，可合併輸出成一份 Excel。

### 7.2 資料夾自動分類：`classify_folder`
先看代碼命中數（≥5判定財報，結構性證據優先），代碼不夠再看法說會關鍵字（法說會/法人說明會/說明會/投資人簡報/法人電話會議），兩者都沒有回傳 `None`（跳過，提示手動指定）。

### 7.3 財報+法說會配對（ROA/ROE 最後退路）
**只有剛好一個財報+一個法說會資料夾**時才配對觸發（見4.8第2層），選0個或2個同類型都不觸發。

### 7.4 Excel合併匯出：`write_excel_merged`
`excel_rows` 是 `(term, value, term_found, page, note, is_percent)` 6元素tuple。**格式化規則（本次修改重點）：**
- 每個資料夾一個工作表（名稱來自資料夾basename，超過31字元截斷，重複自動編號）。
- **比率類（is_percent=True）**：寫入時**先除以100**，套用Excel原生`"0.00%"`格式。**這個除以100只發生在寫入Excel的瞬間，是純顯示轉換，不影響程式內部其他任何地方「已經乘以100」的既有慣例**——修改這段時務必注意不要把這個轉換誤植到別處。
- **非比率類**：逐一儲存格判斷是否為整數，整數用`"#,##0"`（無小數），非整數用`"#,##0.00"`（兩位小數，千分位）。
- N/A（None）留空白，不套格式、不寫文字。

相關常數：`_EXCEL_THOUSANDS_INT_FORMAT`、`_EXCEL_THOUSANDS_DECIMAL_FORMAT`、`_EXCEL_PERCENT_FORMAT`。

### 7.5 為什麼excel_rows是統一6元素tuple
`fin_report_rows()`/`con_call_rows()`各自把不同結構的row dict轉成統一格式，`write_excel_merged`本身不需要知道兩邊原始欄位名稱不同（財報用`value`，法說會比率列用`individual`）。

### 7.6 輸出位置與衝突處理
固定輸出到 `~/Downloads/runfinder_export.xlsx`。檔案被Excel開著時（PermissionError）自動改用時間戳記檔名重存，不會讓執行失敗。執行完自動用 `os.startfile`（**Windows專屬**）開啟，跨平台需改用subprocess判斷作業系統。

### 7.7 怎麼跑
```bash
python cli.py
python cli.py <財報資料夾> <法說會資料夾>
python cli.py --export excel <資料夾1> <資料夾2>
python cli.py --export csv <資料夾>
```
資料夾選擇視窗用tkinter，只在有圖形介面環境能用，伺服器排程需改用直接帶路徑執行。

---

## 8. `con_call_terms.json` 結構與新增詞彙教學

`decks.py` 讀取的唯一字典檔案（`load_terms(config_path)`）。`con_call_terms_example.json` 只是格式範例，不影響實際執行。

### 8.1 新增exact型詞彙範例
```json
"CIR": {
  "type": "exact",
  "aliases": ["成本收入比", "成本收益比", "費用收入比", "營業費用率", "CIR",
              "Cost Income Ratio", "Cost to Income Ratio"]
}
```
每加一個別名，一定要在至少一份真實簡報裡確認語意相同，**不能只憑字面聯想**（Avg. rate of interest-earning assets 誤植教訓）。

### 8.2 新增composite型詞彙範例
```json
"放款均率": {
  "type": "composite",
  "aliases": ["放款均率", "放款平均利率", "放款利率", "單季放款利率", "放款收益率"],
  "negative_terms": ["台幣", "臺幣", "外幣", "新台幣", "新臺幣"],
  "threshold": 0.8,
  "components": [
    {"terms": ["放款", "貸款", "授信", "Loan", "Loans", "Lending"], "weight": 0.5},
    {"terms": ["均率", "平均利率", "收益率", "殖利率", "Yield", "Average Rate", "Rate"], "weight": 0.5}
  ]
}
```
只有措辭變化太多、窮舉別名列不完的詞才用composite型。

### 8.3 negative_terms兩層否決範圍
同時作用在列文字跟章節/表格標題，修改後務必對照真實簡報確認沒有連坐誤判。

### 8.4 search_start/search_end
限定搜尋範圍，只給「詞彙太籠統、容易在無關章節誤判、但知道一定只出現在特定章節」的情況用。

### 8.5 新增詞彙後的驗證流程
1. 單獨用 `-v` 測試這一個詞。
2. 至少2~3份不同銀行/季度的真實資料夾都測過。
3. 若要加進正式輸出（RATIO_TERMS等），跑完整`collect_con_call_summary()`確認沒有波及其他項目。

---

## 9. 環境需求與執行總整理

### 9.1 依賴套件
```bash
pip install openpyxl
```
唯一第三方依賴。`tkinter`部分精簡版Python環境可能沒有（Windows官方安裝包通常內建）。

### 9.2 依賴關係圖
```
statements.py    ← 完全獨立
decks.py    ← 執行時動態 import disclosures（函式內部import，非檔案頂部）
disclosures.py    ← 完全獨立
cli.py     ← import statements as af; import decks as cf
```
**statements.py絕對不能反過來import decks.py**（會循環引用，因為callfinder已import acctfinder的共用工具函式）。

### 9.3 輸入格式要求
必須是PDF轉出的`.md`資料夾，檔名`頁碼_隨機碼.md`格式（`page_num()`解析頁碼，格式不符時退而用整個檔名，不會出錯但顯示不直觀）。

### 9.4 網路需求總表

| 情境 | 需要網路 |
|---|---|
| statements.py單獨執行 | 否 |
| decks.py，簡報自己有信用卡循環數字，不需逾放比率/備抵呆帳 | 否 |
| decks.py，需要補信用卡循環或逾放比率/備抵呆帳 | 是（banking.gov.tw） |
| disclosures.py任何執行 | 是（banking.gov.tw） |
| cli.py | 視分類結果而定 |

---

## 10. 已知限制與「遇到這些狀況該怎麼辦」

### 情境A：某個科目/欄位突然全部變成N/A（原本正常）
1. 先確認資料夾路徑是否還存在：`Path(folder).exists()`、`list(Path(folder).glob("*.md"))`——本次談話實際發生過資料夾被搬走導致全部N/A，跟程式邏輯無關。
2. 開`-v`模式重跑，看有沒有嘗試比對。
3. 財報端：確認代碼在文件裡實際存在，若存在但抓不到，檢查雙欄位排版（4.7）或代碼欄空白需要label fallback（4.6）。
4. 法說會端：先確認這份簡報「這一頁本來就沒有」的可能性（每季內容不一定相同，曾有案例是拿到不完整版本的簡報，找到更完整版本後才抓到）。
5. 若確認文件裡有內容但抓不到：檢查negative_terms連坐否決（情境B相關）、search_start/search_end範圍、composite的threshold是否過高。

### 情境B：抓到的數字看起來合理，但跟官方/銀行數字對不上（最危險，程式不會報錯）
1. **先確認你的「正確答案」跟程式抓的是不是同一件事**——CIR案例（合併vs個別口徑，兩個都對，只是口徑不同）、逾放比率案例（信用卡專屬 vs 銀行整體，是兩組不同數字）都是這類教訓。**拿到「對不上」的回報，先問正確答案來源是哪份文件的哪個欄位，不要急著改邏輯。**
2. 確認來源文件口徑/範圍說明（合併vs個別、稅前vs稅後、累計vs單季）跟程式假設是否一致。
3. 財報端可手動驗算資產=負債+權益（雖然自動檢查列已移除，邏輯上仍是黃金標準）：
   ```python
   idx = af.build_code_index(folder, {"10000", "20000", "30000"})
   ```
4. 法說會重組公式：拆開逐步印出每個原始零件的值，跟簡報原文核對加減方向，不要只看加總後數字是否「看起來合理」。

### 情境C：政府網站相關欄位抓不到或報錯
1. 先確認是否網路問題：單獨執行 `python disclosures.py -v`。
   - SSL憑證錯誤→ `pip install pip-system-certs`，不要關閉驗證。
   - 完全連不上→檢查防火牆/VPN是否封鎖banking.gov.tw。
2. 確認是否網站改版：`_list_period_links`報錯「No dataset links matching」→用瀏覽器打開頁面比對新的檔名格式，修改對應正規表示式。
3. 確認是否表格內部排版變了：連結/下載都正常但`_extract_columns_by_bank`報「Couldn't locate header cell」→打開快取檔案攤開前10列比對，檢查程式裡的欄位名稱常數是否仍完全一致（空白、全形符號差異）。
4. 確認是否只是正常的「頂替期間」而非抓錯：檢查回傳結果的`exact`/`note`欄位，`exact=False`是正常設計行為（政府資料還沒公布）。

### 情境D：新增第五家銀行後一堆項目N/A或錯誤
見第11.1節完整清單。核心提醒：不是插名字進清單就好，需要人工逐項核對格式假設是否成立。

---

## 11. 未來擴充完整指南

### 11.1 新增第五家銀行

**前置作業：**
- [ ] 拿到至少一季財報+一季法說會`.md`資料夾
- [ ] 確認財報有代碼欄位（沒有的話財報端無法支援，需另外設計文字比對邏輯）
- [ ] 確認資產負債表單欄/雙欄排版

**statements.py：**
- [ ] `BANKS`加入簡稱
- [ ] `BANK_NAME_ALIASES`加入所有全名/簡稱寫法
- [ ] 逐一核對`SUMMARY_LAYOUT`每個代碼，對不上的補`SUMMARY_CODE_OVERRIDES`或`SUMMARY_LABEL_FALLBACKS`
- [ ] `COMPOSITE_TERMS`跟會計核對補上代碼組合
- [ ] 雙欄排版的話測試`_split_dual_column_tables`偵測是否適用
- [ ] 真實資料跑`collect_summary_rows`人工核對

**decks.py：**
- [ ] `PRIMARY_BANK_ENTITIES`加入名稱寫法
- [ ] `_GOV_BANK_NAMES`補上政府資料集官方全名
- [ ] 重新設計`LOAN_RECOMPOSITION`公式（工作量最大，需跟業務逐項核對；若簡報已互斥分類則給空字典`{}`）
- [ ] 測試現有RATIO_TERMS能否正常抓取，抓不到就擴充別名/否決詞

**disclosures.py：**
- [ ] `TARGET_BANKS`加入兩個政府資料集裡的官方全名（先核對兩資料集寫法是否一致）

**驗收：**
- [ ] `cli.py --export excel`跑完整流程，逐項核對官方數字/簡報原文，不只看有沒有報錯

### 11.2 新增財報輸出項目
1. 判斷屬於code/composite/label哪一種
2. 找官方科目代碼表確認代碼數字，不要猜
3. `SUMMARY_LAYOUT`加入新筆（決定顯示順序位置）
4. composite型補`COMPOSITE_TERMS`各銀行組合
5. 判斷是否為`is_percent`
6. 四家銀行真實資料跑一次人工核對

### 11.3 新增法說會輸出項目
1. `con_call_terms.json`先定義（見第8節），務必真實簡報驗證
2. 決定歸入RATIO_TERMS/BALANCE_TERMS/HELPER_TERMS
3. 需要跨表格加減的話定義LOAN_RECOMPOSITION公式（只能讀原始零件）
4. 至少2~3份真實簡報測試

### 11.4 新增政府資料來源
1. 若是全新獨立網頁/檔案，仿造現有兩函式模式新增獨立`fetch_xxx()`：期間解析正規表示式、`_find_header_column`/`_extract_columns_by_bank`抓資料、`_result()`包裝
2. 先確認新資料集欄位定義跟既有詞彙有沒有「名稱像但定義不同」陷阱
3. `collect_con_call_summary`整合時包在try/except，不讓新來源失敗拖垮整體

---

## 12. 定期維護檢查清單

**每季新資料出來時：**
- [ ] `-v`模式跑過，檢查新出現的N/A
- [ ] 抽查關鍵數字（總資產、稅後淨利、ROA/ROE）跟官方公布核對

**每半年到一年：**
- [ ] 檢查`npl_cache/`大小，視情況清理
- [ ] 檢查金管會網站結構是否仍符合假設（`python disclosures.py -v`）
- [ ] 銀行若改版簡報格式，重新核對`LOAN_RECOMPOSITION`

**遇到抓錯回報時：**
- [ ] 先分類：輸入資料問題／口徑定義搞混（情境B）／真正邏輯bug
- [ ] 修正後檢查同樣bug是否存在於其他銀行/季度（本專案內多次bug都是「一處發現、回頭一查發現不只一處」）

---

## 13. 名詞對照表 / 檔案總覽附錄

### 13.1 名詞對照表

| 名詞 | 意思 |
|---|---|
| 財報 / fin_report | 個別財務報告書，有官方會計科目代碼 |
| 法說會 / con_call | 法人說明會簡報，無統一格式 |
| 科目代碼 | 官方統一會計項目編號 |
| SUMMARY_LAYOUT | statements.py固定輸出順序清單 |
| COMPOSITE_TERMS | 跨代碼加總、各銀行公式不同的項目定義 |
| build_code_index | 一次掃描批次解析多代碼的效能優化函式 |
| TermSpec | decks.py每個關鍵字詞條的資料結構 |
| exact / composite | 兩種比對型態 |
| negative_terms | 否決詞 |
| entity_tier | 判斷表格屬於本行還是集團/子公司 |
| row_period / col_period | 表格方向 |
| LOAN_RECOMPOSITION | 各銀行放款分類重組公式 |
| unit_scale / detect_unit_scale | 金額單位換算成十億元 |
| crosscheck | 交叉驗證，旁證不覆蓋主答案 |
| plausibility bounds | 合理範圍檢查，超出只提醒 |
| disclosures | 金管會網站抓取模組 |
| 逾期放款總額 | NPL，已從法說會輸出移除 |
| 逾放比率 / 備抵呆帳/逾期放款 | 銀行整體比率，來自金管會id=590資料集 |
| 循環信用餘額 / 信用卡循環 | 信用卡循環信用餘額，來自信用卡專屬揭露資料集 |
| cli.py | 整合執行器 |
| exact=False | npl_finder回傳結果裡，代表用了較早一期頂替 |

### 13.2 檔案總覽

| 檔案 | 可否手動編輯 | 備註 |
|---|---|---|
| statements.py | 是 | 核心程式，改動需真實資料驗證 |
| decks.py | 是 | 核心程式，比對邏輯較複雜，改動風險較高 |
| disclosures.py | 是 | 外部風險在於政府網站可能改版 |
| cli.py | 是 | 整合層 |
| con_call_terms.json | **是，最常需要調整** | 新增/修改詞彙主要改這裡 |
| con_call_terms_example.json | 僅供格式參考 | 不影響實際執行 |
| 金控業/金融業/保險業.xlsx | **不應手動編輯** | 官方科目代碼表，唯讀權威資料源 |
| Account Coding.xlsx | 備用科目代碼檔 | `--coding`參數指定使用 |
| npl_cache/ | 自動產生，可刪單一檔案強迫重抓 | 不需版控 |
| README.md | 專案自帶說明 | 可能落後於程式碼，有疑問以本手冊+程式碼為準 |

### 13.3 本手冊涵蓋不到的地方
- PDF→`.md`轉檔流程不在本專案範圍內
- 反映撰寫當下（2026年8月）程式碼狀態，與程式碼衝突時以程式碼為準
- 手冊提到的具體數字都直接寫在程式碼常數裡並附註解，修改前先讀程式碼裡的註解
