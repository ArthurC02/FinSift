# account_code_finder — agent 導覽

台灣金融機構財報／法說會的數字擷取工具。輸入是 PDF 轉出的 `.md`，輸出是標準化的比較用摘要。

**這份是入口，只放地圖與紅線。** 細節在 `docs/`，需要時才讀，不要一次讀完。

---

## 輪廓

| 進入點 | 做什麼 |
|---|---|
| `src/financialReports/` | 財報擷取。**用法定科目代碼精確比對**，不是文字比對。套件本身就是門面：`import financialReports as fin` 之後 `fin.X` 一律有效，不必知道名字住在哪個檔 |
| `src/earningsCalls/` | 法說會簡報擷取。文字比對，詞彙定義在 `data/con_call_terms.json`。套件即門面：`import earningsCalls as ec` |
| `src/userInteractions/cli.py` | 自動判斷資料夾是財報還是法說會，跑對應的擷取器並合併輸出 |
| `src/regulatorDatasets/disclosures.py` | 抓金管會銀行局公開月報。**刻意完全獨立**，不匯入其他三個 |

財報側再往下分三層，**單向**、不得回頭：

```
financialReports/__init__.py   ← 門面，對外只有這一個地址
    statements（三大報表 dump、科目字典、acct 子命令）
        └── summary（layout、collect_summary_rows、匯出）
                └── ratios（獲利能力表解析、compute_ratios、collect_roa_roe）
                        └── entities（BANK_PROFILES、代碼 fallback、機構偵測）
                                └── core（industry / lookup / tables / numbers / text）
```

`entities` 是 `summary` 與 `ratios` 的共同地板：`compute_ratios` 需要 `SUMMARY_CODE_OVERRIDES`／`SUMMARY_LABEL_FALLBACKS`，而 `collect_summary_rows` 需要 `collect_roa_roe` —— 兩邊互相依賴，所以不能只切兩塊。

法說會側同樣分層，**單向**：

```
earningsCalls/__init__.py   ← 門面
    summary（策展摘要、放款重組、call 子命令）
        └── matching（entity/單位/期別過濾、find_term_value）
                ├── periods（哪一軸是期別、4Q25 / FY25 / 114年12月 怎麼讀）
                └── terms（詞彙與 match_strength；TermSpec 是信任邊界）
```

`src/core/` 是共用解析層（`industry` / `lookup` / `tables` / `numbers` / `text`），單向依賴 `lookup → tables → text`，**不得匯入任何擷取器**。

> **測試打 monkeypatch 要打在真正被讀取的模組上**，不是 `financialReports` 這個門面。`compute_ratios` 住在 `ratios`，`collect_summary_rows` 住在 `summary`（它在 import 時就把 `collect_roa_roe` 綁進自己的命名空間）。打錯地方 = 測試全綠但跑的是真程式。

相依只有 `openpyxl` 與 `pytest`（無 `pyproject.toml`）。建環境、CLI 用法、旗標 → [docs/SETUP.md](docs/SETUP.md)

## 動手前必讀的五條

違反這些會造成**靜默的錯誤數字**，而不是報錯：

1. **不要停用 `disclosures` 的 SSL 驗證。** 原始碼寫明這是刻意的（MITM 風險）。
2. **不要讓 CI 或 A/B 真的連 `banking.gov.tw`。** 必須 stub `_fetch_url`。真的連上外網視為 harness 設定失敗，不以重跑判定結果。
3. **不要合併 `statements` 與 `decks` 兩份同名的 `print_summary_rows` / `write_summary_csv`。** 名字相同但列的形狀不同，合併是設計決定不是清理。
4. **測試是 characterization（特徵化）測試**，期望值來自「現行實際行為」而非規格。修 bug 時，**改碼與翻轉斷言必須在同一支 commit**。
5. **判不出來就拒絕，不要猜。** 這個 codebase 反覆出現的失效不是崩潰，是靜默的錯數字。判不出唯一機構就要求 `--bank`，判不出 layout 就要求 `--industry`，沒有依據就不要憑空補設定。

## 驗證（每次改動）

```powershell
python -m pytest            # V1 行為
python tools\undefined.py   # V3 缺漏 import（含巢狀在 dict 裡的 lambda）
python tools\knowledge_links.py   # V5 註解引用的知識章節是否還在
python src\userInteractions\cli.py --help                      # V2 四個 CLI
foreach ($c in 'acct','call','npl') { python src\userInteractions\cli.py $c --help }
python tools\ab.py <改動前的 src> > before.txt   # V4 A/B 位元組比對
```

**綠燈本身不是證據。** 四道驗證的完整做法、A/B 的 worktree 步驟與三條設計規則、事先宣告預期差異、mutation testing、red-before、**以及「什麼證據能支持什麼交付宣稱」** → [docs/VERIFICATION.md](docs/VERIFICATION.md)

> 不要相信任何文件裡硬編碼的測試條數。以 `pytest --collect-only` 為準。

## 索引

| 想知道 | 讀 |
|---|---|
| 建環境、CLI 用法與旗標 | [docs/SETUP.md](docs/SETUP.md) |
| 業務領域：財報 vs 法說會、科目代碼、產業字典、機構清單 | [docs/DOMAIN.md](docs/DOMAIN.md) |
| 架構：三個軸、模組地圖、資料流、每個關注點住哪裡 | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| **新增一家機構／一個產業／一個法說會詞彙** | [docs/EXTENDING.md](docs/EXTENDING.md) |
| 驗證協定全文、A/B harness、交付措辭紅線 | [docs/VERIFICATION.md](docs/VERIFICATION.md) |
| 各函式逐項細節、為什麼某個門檻是那個值 | [docs/HANDOFF.md](docs/HANDOFF.md)（**部分過時，看它開頭的差異表**） |
| 測試設計、**已知執行期 bug 登記簿**（§7） | [docs/TEST_DESIGN.md](docs/TEST_DESIGN.md) |
| 使用者面向的完整用法（中英雙語） | [README.md](README.md) |
| 重構計畫的歷史紀錄 | [docs/REFACTOR_PLAN.md](docs/REFACTOR_PLAN.md)（**已執行完畢，歷史文件**） |

## 知識文件

長篇的來龍去脈（比對過哪幾份財報、踩過的坑、試過而放棄的作法）放在 `docs/knowledge/`。程式碼裡只留**規則本身**——門檻值旁的一行、「不要 X」的祈使句——再加一行 `→ docs/knowledge/...#章節`。

判準：**這行註解消失後，會不會有人做出一個看起來完全合理的修改，而產生靜默的錯數字？**
會 → 留在程式碼旁。不會 → 進知識文件。

**依主題切，不依套件切。** 這個 repo 的檔案切法已經變過四次，綁在檔案上的文件會被切爛；主題不會。同一個主題常橫跨兩三個套件（機構解析同時住在 `entities`、`matching`、`profitability`）。

| 想知道 | 讀 |
|---|---|
| 機構怎麼認、為什麼歧義要拒絕、金管會名稱對照 | [entity-resolution.md](docs/knowledge/entity-resolution.md) |
| 科目代碼、每機構覆寫與 composite、查不到時的三層退路 | [account-codes.md](docs/knowledge/account-codes.md) |
| 產業判定、layout 為什麼綁死產業、科目字典工作簿 | [industry-and-layout.md](docs/knowledge/industry-and-layout.md) |
| 表格結構、期別標籤、單位、數字解析 | [reading-tables.md](docs/knowledge/reading-tables.md) |
| N/A 的六種成因、「判不出來就拒絕」的實作 | [na-and-refusal.md](docs/knowledge/na-and-refusal.md) |
| ROA/ROE 三個來源、年化、交叉核對、CIR | [ratios.md](docs/knowledge/ratios.md) |
| 法說會詞彙比對、放款重組 | [earnings-call-matching.md](docs/knowledge/earnings-call-matching.md) |
| 金管會資料集、SSL、欄位定位 | [regulator-datasets.md](docs/knowledge/regulator-datasets.md) |
| 資料夾分類、合併輸出、Excel 慣例 | [cli-and-export.md](docs/knowledge/cli-and-export.md) |

`tools/knowledge_links.py` 只保證被引用的章節**還存在**，不保證內容**還正確**。綠燈的意思是「地址還在」。

## 判斷來源可信度的順序

程式碼 > 緊鄰常數的註解 > `docs/knowledge/` > 其餘 `docs/` > commit message 的歷史敘述。

門檻值、容忍度、合理範圍上下限旁邊的註解通常說明「為什麼是這個值」，**改之前先讀那段註解**。
