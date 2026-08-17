# earningsCalls — 套件指引

法說會簡報擷取。**文字比對**——簡報沒有科目代碼，所以詞彙本身就是識別碼。詞彙定義在 `data/con_call_terms.json`。

套件即門面：`import earningsCalls as ec`。

> 全域紅線與驗證協定在 repo 根目錄的 [AGENTS.md](../../AGENTS.md)。這份只放這個套件內部的事。

## 分層（單向）

```
__init__.py   ← 門面
    summary（策展摘要、放款重組、金管會退路、call 子命令）
        └── matching（entity/單位/占比/百分比四層過濾、find_term_value）
                ├── periods（哪一軸是期別、4Q25 / FY25 / 114年12月 怎麼讀）
                └── terms（詞彙與 match_strength；TermSpec 是信任邊界）
```

`periods` 與 `terms` 互不相依，各自也不依賴套件內任何東西——它們是這一側的地板。

這一側**單向依賴 `financialReports`**（`entities.detect_bank`、`ratios.derive_quarter_num`、`statements.pick_folder`）。反方向是循環：財報側要法說會的 ROA/ROE 時，是由 `userInteractions.cli` 查好了傳進去的。

## 信任邊界在 `terms.TermSpec`

設定檔是外部輸入。`TermSpec.from_dict` 要拒絕會匹配所有東西的設定，而不是讓它通過去產生有自信的錯數字——**空白別名是每個字串的子字串**，一筆就讓那個詞以強度 2 命中資料夾裡每一列。→ [earnings-call-matching.md](../../docs/knowledge/earnings-call-matching.md#空白別名為什麼是信任邊界)

## 測試 monkeypatch 打在哪

| 要 stub 的東西 | 打在 |
|---|---|
| `find_term_value` | `earningsCalls.summary`（import 時就綁進命名空間） |
| `find_value_in_table` / `detect_unit_scale` | `earningsCalls.matching` |
| `parse_period_label` / `_rank_periods` | `earningsCalls.periods` |
| `disclosures.fetch_*` | `regulatorDatasets.disclosures`（`summary` 是 late import） |

打錯地方 = **測試全綠但跑的是真程式**。

## 不要做的事

- **不要把 `LOAN_RECOMPOSITION` 併進 `BANK_PROFILES`。** 它是邏輯不是資料（dict 字面量裡的 lambda，靜態檢查看不見），而且缺條目會安全退化，缺 `composites` 不會 → [earnings-call-matching.md](../../docs/knowledge/earnings-call-matching.md#為什麼不併進-bank_profiles)
- **不要把比率項目乘 `x4/季數`。** 簡報的比率已經是年化率或時點比率，縮放會產生 >100% 的存放比 → [ratios.md](../../docs/knowledge/ratios.md#法說會的比率為什麼不年化)
- **不要合併 `summary.print_summary_rows` / `write_summary_csv` 與 `financialReports` 的同名函式。** 名字相同、列的形狀不同。
- **不要在跨表格算術之前跳過單位正規化。** 同一份簡報混用百萬元與拾億元 → [reading-tables.md](../../docs/knowledge/reading-tables.md#單位不是全篇一致)

## 這個套件的知識文件

[earnings-call-matching](../../docs/knowledge/earnings-call-matching.md) ·
[reading-tables](../../docs/knowledge/reading-tables.md) ·
[entity-resolution](../../docs/knowledge/entity-resolution.md) ·
[regulator-datasets](../../docs/knowledge/regulator-datasets.md)
