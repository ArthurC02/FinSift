# 擴充指南

> 三種擴充：**加一家機構**、**開一個產業**、**加一個法說會詞彙**。
> 每一種都附「怎麼驗證」。沒跑完驗證的擴充不算完成。

先讀 [ARCHITECTURE.md §1](ARCHITECTURE.md) 的三個軸，否則會不知道東西該放哪。

---

## 1. 加一家機構（產業已支援）

目前已支援的產業：**金融業、金控業**。加一家銀行或金控屬於這一類。

### 只改一個地方

`src/financialReports/statements.py` 的 `BANK_PROFILES`：

```python
"永豐": {
    "industries": ["金融業", "金控業"],
    "aliases": ["永豐"],
    "primary_entities": ["永豐銀", "Bank SinoPac"],
    "code_overrides": {},
    "code_overrides_finsum": {},
    "composites": {
        "評價及已實現": ["49200", "49310", "49450", "49600"],
        "其他非利息收益": ["49700", "49750", "49800"],
    },
},
```

`BANKS`、`BANK_NAME_ALIASES`、`SUMMARY_CODE_OVERRIDES`、`SUMMARY_CODE_OVERRIDES_FINSUM`、`COMPOSITE_TERMS`、`decks.PRIMARY_BANK_ENTITIES` 全部自動跟上 —— **它們是推導視圖，不要手動編輯**。

### 每個欄位怎麼填

| 欄位 | 怎麼決定 |
|---|---|
| `industries` | 這家的財報寫在哪些準則下。銀行＋金控通常兩者都列 |
| `aliases` | **短名**，要能在法說會封面命中（那裡不寫登記名稱）。「臺」「台」都列 |
| `primary_entities` | 法說會裡代表**這家主體銀行**的名稱，含英文（簡報附錄常用英文表頭） |
| `code_overrides` | **先留空**。只有在實際比對真實財報、確認代碼與預設不同時才填 |
| `code_overrides_finsum` | 同上，針對季報摘要揭露那份短文件 |
| `composites` | 從真實財報查出這家的組成子科目。**不要從別家複製** |

### 三個容易踩的坑

**別名碰撞。** 新別名若是既有別名的子字串（或反之），兩家會同時命中，`detect_bank` 會拒絕解析而不是猜 —— 這是設計，但你會看到「Several banks are named」。解法是把別名寫得更具體，不是放寬偵測。

**`composites` 不要抄。** 四家現有機構裡，玉山的「評價及已實現」就比其他三家少一個代碼。抄別家會得到一個看起來正常的錯數字。

**別名太短會誤命中。** 「富邦」單獨列是刻意的（法說會封面只寫富邦金控），前提是**沒有其他 profile 含「富邦」**。新增含相同字的機構時要一起重新檢視。

### 驗證

```bash
python -m pytest                      # _validate_profiles 會在 import 時擋下不完整的 profile
python src/userInteractions/cli.py acct <新機構資料夾> summary -v
```

**必看**：輸出有沒有 `WARNING: N of M summary rows are N/A`。過半 N/A 代表解析沒讀懂這家的版面，不是這家沒揭露。

理想情況是這一筆 profile 就夠了。**如果你需要寫超過兩三條 `code_overrides`，那是解析器有洞的信號** —— 該修共用解析器（對所有機構同時生效），不是繼續堆機構例外。

---

## 2. 開一個新產業

例如保險業。這比加機構重得多，因為要**新增一份 summary layout**。

### 前置：字典

`data/` 目前有金控業、金融業、保險業三本。**證券商與票券金融公司沒有**，要先外部取得該準則的科目代碼表，存成同樣四張表（資產負債表／綜合損益表／權益變動表／現金流量表）的 `.xlsx`。

### 步驟

**① 註冊字典**（若是全新產業）

```python
INDUSTRY_CODING_FILES = {..., "證券業": str(Path(__file__).parent.parent / "data" / "證券業.xlsx")}
```

**② 註冊法定名稱關鍵字**

```python
INDUSTRY_CATEGORY_KEYWORDS = [
    ("保險業", ["人壽保險股份有限公司", "產物保險股份有限公司", ...]),
    ...
]
```

用**完整法定名稱**，不要用「保險」這種裸詞 —— 一般科目名稱（如「保險費用」）會誤命中。順序也重要：金控財報常提到子公司，所以最具體的排前面。

**③ 寫 layout —— 這步是實質工作**

```python
INDUSTRY_SUMMARY_LAYOUTS["保險業"] = [
    {"kind": "code", "code": "10000", "term": "總資產", "is_cost": False},
    ...
]
```

**必須根據真實財報 ＋ 該產業字典逐項查證，不准從銀行 layout 改名。** 銀行 layout 的利息淨收益、呆帳提存、活存比對壽險都不成立；`58200` 在保險業根本是另一個科目。

每一列要決定三件事：
- `kind`：`code`（單一代碼）／`composite`（多代碼加總）／`label`（無代碼，純標籤比對）
- `term`：標準化顯示名。**這是跨機構比較的鍵**，也是 CSV/Excel 匯出用的欄位
- `is_cost`：這列是不是費用。為 `True` 時 `apply_cost_sign` 會翻正負號讓費用顯示為正

`is_cost` 可從真實財報的正負號慣例判斷；`term` 清單是**業務決定**，需要業務方確認要哪幾列。

**④ 把機構的 `industries` 加上這個產業**，或新增該產業的機構 profile。`_validate_profiles` 會檢查每個機構都定義了它的 layout 需要的 composite。

**⑤ 更新 `cli._MERGED_TERM_ORDER`**，若新 layout 的 term 要進合併輸出。

### 驗證

除了標準協定，另外要：

```bash
python src/userInteractions/cli.py acct <新產業資料夾> summary -v      # 逐列看 matched_label 是否對得上
python src/userInteractions/cli.py acct <新產業資料夾> balance_sheet   # 確認字典本身載得起來
```

**逐列核對 `matched_label`（文件自己的措辭）與 `term`（我們的標準化名稱）語意是否一致。** 這正是保險業那個 bug 的形狀：數字解析完全正確、標籤完全錯誤。

---

## 3. 加一個法說會詞彙

**不用改程式碼。** 編輯 `data/con_call_terms.json`，格式見 `docs/con_call_terms_example.json`。

```json
"淨利差": {
  "type": "composite",
  "aliases": ["淨利差", "NIM"],
  "negative_terms": ["調整前"],
  "components": [
    {"terms": ["淨", "net"], "weight": 0.5},
    {"terms": ["利差", "margin"], "weight": 0.5}
  ],
  "threshold": 0.8
}
```

| 欄位 | 作用 |
|---|---|
| `aliases` | 完全相等得 strength 3，包含得 2 |
| `components` + `threshold` | 加權和達門檻得 strength 1 |
| `negative_terms` | 命中就整列否決 |
| `search_start` / `search_end` | 限縮到特定段落 |

### 兩個陷阱

**空字串 alias 會匹配每一列。** 空字串是所有字串的子字串，一個空白條目讓該詞彙以 strength 2 命中整個資料夾的每一列，壓過所有複合項。`TermSpec.from_dict` 會擋，但要知道為什麼有這道檢查。

**alias 寫太寬擋不住。** schema 驗證抓得到格式錯誤，抓不到語意過寬。加完務必實跑比對。

### 驗證

```bash
python -m pytest tests/test_l0_terms.py
python src/userInteractions/cli.py call --folder <法說會資料夾> -v      # 看 matched_label 是不是預期那列
```

---

## 4. 通用規則

### 什麼該進設定、什麼該留在程式碼

三個條件**同時成立**才外部化：

1. 知識的擁有者不是我們（外部權威或業務方定義）
2. 改它不需要重新理解程式（加一筆不改變控制流）
3. 「什麼叫合法」能寫成 schema

任何影響**控制流**、或錯了會**靜默給出錯數字**的東西，留在程式碼裡受測試保護。

### 修 bug 的紀律

1. 先寫測試釘住**現行的錯誤行為**，或直接證明它會錯
2. 改碼
3. **翻轉斷言與改碼放同一支 commit**
4. Red-before：把改動 `git stash` 起來，確認新測試是因為**行為**而紅，不是因為函式不存在
5. 一支 commit 只修一項

### 遇到「該猜還是該拒絕」

**拒絕。** 這個 codebase 反覆出現的失效模式不是崩潰，是靜默的錯數字。判不出唯一機構就要求 `--bank`，判不出 layout 就要求 `--industry`，沒有依據就不要憑空補 layout。

現有的兩條控制流（「偵測不到」與「拒絕」）已經涵蓋這些情境，**新增拒絕情境時沿用它們，不要新增分支**。
