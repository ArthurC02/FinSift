# regulatorDatasets — 套件指引

抓金管會銀行局的公開月報。**刻意完全獨立**，不匯入其他三個套件，所以它能單獨測試與排程。

> 全域紅線與驗證協定在 repo 根目錄的 [AGENTS.md](../../AGENTS.md)。這份只放這個套件內部的事。

## 兩條紅線，這裡是它們的現場

**一、不要停用 SSL 驗證。**

`_fetch_url` 用系統預設的驗證 context。在某些環境會丟 `SSLCertVerificationError`——那代表**你這台機器的 CA 信任存放區缺了中介憑證**，不代表這個連線該降級。修法是 `pip install pip-system-certs` 或 certifi。停用驗證會重新打開 MITM 攻擊面。原始碼裡寫明了這是刻意的。

**二、CI 與 A/B 都不可以真的連 `banking.gov.tw`。**

必須 stub `_fetch_url`（`tools/ab.py` 就是這樣做的）。真的連上外網視為 **harness 設定失敗**，不以重跑判定結果——外網回來的資料每個月都不同，拿它比對等於沒有比對。

## 這個套件的形狀

```
_list_period_links   解析頁面上每個連結的 (民國年, 月)
resolve_period       決定用哪一期（要求的月份沒公布時往前退，不往後）
download_file        依 URL 自己的檔名快取到 npl_cache/
_find_header_column  靠標題文字定位欄位，兩趟（單格、3 列堆疊）
fetch_overdue_loans / fetch_credit_card_revolving
```

兩個資料集用**不同的檔名慣例**，所以各有自己的期別正規表達式，不是共用一個猜測。

## 不要做的事

- **不要用命名公式去組 URL。** 「最新」一律是解析每個連結的 (年, 月) 取最大值。
- **不要把欄位定位改成固定索引。** 靠標題文字找，欄位重排才不會靜默回傳錯的指標 → [regulator-datasets.md](../../docs/knowledge/regulator-datasets.md#欄位靠標題文字定位)
- **不要對逾放比率／備抵呆帳再做任何乘除。** 試算表裡已經是百分比尺度 → [regulator-datasets.md](../../docs/knowledge/regulator-datasets.md#逾放比率曾經取錯表)
- **不要靠猜去補 `_GOV_BANK_NAMES`。** key 必須是金管會試算表自己印的字串，而確認它需要讀一份真實檔案——這個 repo 不能去抓 → [entity-resolution.md](../../docs/knowledge/entity-resolution.md#金管會名稱對照為什麼只有四家)

## 這個套件的知識文件

[regulator-datasets](../../docs/knowledge/regulator-datasets.md) ·
[entity-resolution](../../docs/knowledge/entity-resolution.md)
