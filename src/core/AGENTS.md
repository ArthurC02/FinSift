# core — 套件指引

兩個擷取器共用的解析層。**這裡不知道什麼是財報，也不知道什麼是法說會。**

> 全域紅線與驗證協定在 repo 根目錄的 [AGENTS.md](../../AGENTS.md)。這份只放這個套件內部的事。

## 分層（單向）

```
industry   （產業判定、三本科目字典的路徑）
lookup     （find_value_by_label / find_code_value / build_code_index）
    └── tables   （markdown 表格、雙欄拆分、續行折疊、stride）
            └── text     （despace、腳註剝除、TOC 判斷）
    └── numbers  （parse_numeric / nth_value / format_*）
```

**`core/` 不得匯入任何擷取器。** 這是唯一一條硬規則。一旦 `tables` import 了 `financialReports`，兩個擷取器就再也不能各自演化，而且 A/B harness 會失去它唯一的共同地板。

某個東西該不該住進 `core/`：**兩邊都要用，而且兩邊都不擁有它。** 只有一邊用的留在那一邊——即使它看起來很泛用。

## 路徑陷阱

`data/` 在 repo 根目錄，也就是 `src/core/` 往上**三層**，不是兩層：

```python
_DATA = Path(__file__).resolve().parent.parent.parent / "data"
```

兩層會靜默指向 `src/data/`，而且**沒有測試會抓到**——summary 模式從不載入科目工作簿，只有逐報表模式會。這件事在這個 repo 咬過四次（`core/industry.py`、`earningsCalls/summary.py` 的 `--config` 預設、`regulatorDatasets/disclosures.py` 的快取目錄、`userInteractions/cli.py`）。

`tests/test_l1_coding.py` 有一條測試把每個模組算出來的 bundled data 路徑實際 resolve 一次，就是為了釘住這件事。

## 不要做的事

- **不要把 `nth_value` 的位置成對走改成靠數字大小或逗號分組判斷。** 小到沒有逗號的當期數字會被跳過，抓到同一列的去年同期值 → [reading-tables.md](../../docs/knowledge/reading-tables.md#值與百分比交錯stride)
- **不要拿掉 `_is_table_divider` 的 pipe 要求。** 裸 `---` 會讓表格最後一列（也就是最新一季）被靜默丟掉 → [reading-tables.md](../../docs/knowledge/reading-tables.md#分隔列與水平線的差別)
- **不要把 `find_value_by_label` 的整格精確比對放寬成子字串。**

## 這個套件的知識文件

[reading-tables](../../docs/knowledge/reading-tables.md) ·
[industry-and-layout](../../docs/knowledge/industry-and-layout.md) ·
[account-codes](../../docs/knowledge/account-codes.md)
