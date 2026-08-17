# financialReports — 套件指引

財報擷取。**用法定科目代碼精確比對**，不是文字比對。

套件本身就是門面：`import financialReports as fin` 之後 `fin.X` 一律有效，不必知道名字住在哪個檔。

> 全域紅線與驗證協定在 repo 根目錄的 [AGENTS.md](../../AGENTS.md)。這份只放這個套件內部的事。

## 分層（單向、不得回頭）

```
__init__.py   ← 門面，對外只有這一個地址
    statements（三大報表 dump、科目字典、acct 子命令）
        └── summary（layout、collect_summary_rows、_validate_profiles、匯出）
                └── ratios（compute_ratios、collect_roa_roe、交叉核對）
                        └── profitability（獲利能力表的三種版面解析）
                                └── entities（BANK_PROFILES、代碼 fallback、機構偵測）
                                        └── core（industry / lookup / tables / numbers / text）
```

`entities` 是共同地板，不是隨手切的一層：`compute_ratios` 需要 `SUMMARY_CODE_OVERRIDES`／`SUMMARY_LABEL_FALLBACKS`，而 `collect_summary_rows` 需要 `collect_roa_roe`——兩邊互相依賴。**只切成 summary 與 ratios 兩塊會產生循環**，把機構／代碼宣告下沉才讓分層無環。

`profitability` 只負責「讀財報自己印的獲利能力表」，`ratios` 負責「自己算並交叉核對」。`ratios` import `profitability`，反過來是循環。

`_validate_profiles` 留在 `summary` 而不是跟資料一起放 `entities`，因為它是 **profiles × layouts 的交叉檢查**；放到資料那邊會把那個環又造出來。

## 測試 monkeypatch 打在哪

**打在真正被讀取的模組上，不是 `financialReports` 這個門面。**

| 要 stub 的東西 | 打在 |
|---|---|
| `compute_ratios` | `financialReports.ratios` |
| `collect_roa_roe` | `financialReports.summary`（它在 import 時就綁進自己的命名空間） |
| `collect_summary_rows` | `financialReports.summary` |
| `find_profitability_entries` | `financialReports.ratios` |

打錯地方 = **測試全綠但跑的是真程式**。這個 codebase 已經因此出過三次全綠假象。

## 不要做的事

- **不要合併 `summary.print_summary_rows` / `write_summary_csv` 與 `earningsCalls` 的同名函式。** 名字相同但列的形狀不同（財報側有 `crosscheck_value`，法說會側有 `kind` 的兩種形狀），合併是設計決定不是清理。
- **不要給沒有 layout 的產業一個預設值。** 套錯 layout 不會失敗，它會把解析正確的數字貼上錯誤標籤 → [industry-and-layout.md](../../docs/knowledge/industry-and-layout.md#summary_layout-為什麼綁死產業)
- **不要從別家複製 `composites`。** 組成代碼真的每家不同 → [account-codes.md](../../docs/knowledge/account-codes.md#每機構的-composite-組成)

## 這個套件的知識文件

[entity-resolution](../../docs/knowledge/entity-resolution.md) ·
[account-codes](../../docs/knowledge/account-codes.md) ·
[industry-and-layout](../../docs/knowledge/industry-and-layout.md) ·
[ratios](../../docs/knowledge/ratios.md) ·
[na-and-refusal](../../docs/knowledge/na-and-refusal.md)
