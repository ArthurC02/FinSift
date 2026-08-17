# 驗證協定

> 這個 codebase 的失效模式**不是崩潰，是靜默的錯數字**。
> 所以「測試綠燈」本身不是證據 —— 綠燈只證明沒有回歸，不證明改動正確。

---

## 1. 四道驗證

每次改動都跑，順序如下。

### V1 — 行為

```bash
python -m pytest
```

302 條。這些是 **characterization（特徵化）測試**：期望值來自「現行實際行為」，不是來自規格。所以**紅燈不等於錯**，紅燈的意思是「行為變了，去確認這個變化是不是你要的」。

### V2 — 非空白行的 multiset 比對

純搬移類的重構（把符號從 A 檔搬到 B 檔）用這道。把改動前後所有 `src/**/*.py` 的非空白行倒出來排序比對 —— 逐字搬移的話兩邊應該完全相同（只有 import 行不同）。

任何順手改名、順手加型別註解、順手調整格式都會讓這道失效，所以**搬移就是搬移**。

### V3 — 缺漏 import

```bash
python tools/undefined.py
```

預期輸出 `8/8 modules checked, 0 missing-import reference(s)`。

它做的事比 lint 多一件：**遞迴進 dict / list / tuple 找巢狀 lambda**。`decks.LOAN_RECOMPOSITION` 是九個 lambda 住在 dict literal 裡，AST 的 name-keyed 掃描看不到它們，而它們是最容易在搬移中失去 import 的東西。

實作上用 `dis` 過濾 `LOAD_GLOBAL` 而非讀 `co_names`，因為後者混入屬性名，會把 `af.collect_summary_rows` 誤報成缺漏。

### V4 — A/B 位元組比對

用 `git worktree` 拉出改動前的版本，兩邊跑同一組 fixture，比對 stdout ＋ 回傳值的序列化結果。

**這是最強的一道**，因為它不依賴我們有沒有想到要寫哪條測試。

```powershell
git worktree add ..\wt_head HEAD
python tools\ab.py ..\wt_head\src > ab_before.txt
python tools\ab.py               > ab_after.txt
Compare-Object (Get-Content ab_before.txt) (Get-Content ab_after.txt)
git worktree remove ..\wt_head --force
```

```bash
git worktree add ../wt_head HEAD
python tools/ab.py ../wt_head/src > ab_before.txt
python tools/ab.py                > ab_after.txt
diff <(tr -d '\r' < ab_before.txt) <(tr -d '\r' < ab_after.txt)
git worktree remove ../wt_head --force
```

### harness 的三條設計規則

**① 兩次都用現在這份 `tools/ab.py`。** 只有 `src` 換邊。若對照那側跑的是它自己那個版本的 harness，你比的是「兩個 harness 的差異」而不是「兩份程式碼的差異」。

**② fixture 一律從目前 checkout 解析。** `tools/ab.py` 用 `Path(__file__)` 找 `tests/fixtures/`，不接受由參數指定。讓兩邊各自提供 fixture 會變成比較兩組不同的輸入，diff 就沒有意義了。實務上這也讓 harness 能對照**還沒有 fixture 的舊 commit**（已實測）。

**③ `data/` 刻意跟著受測的 src 走。** 編碼字典與 `con_call_terms.json` 是「被比較的系統」的一部分；fixture 才是共用的輸入。

### 兩個踩過的坑

**行尾。** 曾經因為 `grep -v` 改寫了行尾而產生整份檔案的假差異，用 `od -c` 才診斷出來。bash 那側請用上面的 `tr -d '\r'`；`Compare-Object` 讀進來是行陣列，不受影響。

**worktree 殘留。** 目錄被手動刪掉但仍註冊時，`git worktree add` 會報「missing but already registered」。順序是**先刪目錄、再 `git worktree prune`**，然後才 add。

### 加碼：四個進入點

```powershell
foreach ($m in 'statements','decks','cli','disclosures') { python "src\$m.py" --help }
```

```bash
for m in statements decks cli disclosures; do python src/$m.py --help; done
```

抓得到 import 期就爆炸的錯誤，包括 `_validate_profiles` 在 import 時拒絕的不完整 profile。

---

## 1.5 交付措辭：什麼證據支持什麼宣稱

跑完驗證之後，**能宣稱到什麼程度**取決於證據種類，不取決於工作量。

| 改動類型 | 可自行完成的證據 | 仍需外部／人工證據 |
|---|---|---|
| 文件、錯字、導覽 | 連結與命令一致、`git diff --check` 通過 | 無 |
| 已知行為的 bug fix | red-before、targeted + full pytest、同 commit 翻轉 pinned assertion | 若改變金融語意或輸出契約，需使用者確認 |
| 純重構／搬移 | 四道驗證全過，**A/B 應為 byte-identical** | 無 |
| 加機構／產業／詞彙 | 依 [EXTENDING.md](EXTENDING.md) 完成設定與合成 fixture | **真實文件必須人工核對** |
| 新版型、公式、門檻 | 可先實作 parser／測試並列出假設 | **必須有真實 `.md`、原始報表或業務方確認** |
| `disclosures` 網路行為 | stub 後的解析與錯誤轉譯測試 | 真站可用性、憑證、網站改版 |

測試主體是**合成資料的回歸網**：足以保護已知行為與重構，**不足以證明沒見過的財報格式正確**（見 §7）。

缺少真實資料時，交付措辭是「已實作，合成測試通過」，**不能寫成「已對真實申報書驗證」**。

**V4 不適用的情況**：文件-only 改動。此時檢查連結、命令可執行、`git diff --check`，並在交付中說明 V4 不適用而非未執行。

---

## 2. 事先宣告預期差異

**A/B 有差異不一定是錯的，但「事後才解釋」一定有問題。**

規矩是：**動手前先寫下這次改動預期會不會有 A/B 差異、以及是哪幾行。** 然後跑，然後對照。

例：機構軸那次改動事先宣告「A/B 應完全相同，因為所有 fixture 都是單一銀行的財報、且推導視圖與原值等價」—— 跑出來確實相同。若當時跑出差異，那就是設計理解有誤，不是「順便解釋一下」。

---

## 3. 網路必須 stub

**A/B 與任何自動化都不准真的連 `banking.gov.tw`。**

```python
import disclosures
disclosures._fetch_url = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("network stubbed"))
```

先前的間歇性 SSL 失敗被誤判為「行為差異」，浪費了一輪診斷。若任何一邊真的連上外網，**視為 harness 設定失敗，不以重跑判定結果**。

同時：**不要為了讓測試過而停用 `disclosures` 的 SSL 驗證**。原始碼寫明不停用是刻意的（MITM 風險）。

---

## 4. Mutation testing —— 安全網的驗收標準

**綠燈證明不了什麼。** 要知道測試有沒有價值，就故意植入錯誤看它們抓不抓得到。

做法：手動改一處程式碼製造已知的錯誤行為（mutant），跑測試，應該要紅。**沒紅代表安全網有洞，補洞優先於繼續往下做。**

這個專案跑過 24 個以上的 mutant，兩個存活，兩個都揭露了真的漏洞：

| 存活的 mutant | 揭露的洞 |
|---|---|
| `dual-forward-splice` | 測試只數列數，而正向拼接會吞掉第二張表的分隔列卻不改變列數。改成斷言整段行序列＋各表用不同代碼 |
| `chained-not-raw` | 直接測 lambda 時每次都拿到全新的 RAW dict，而串接測試用的國泰是空 dict、迴圈根本沒跑。補了一個走完整 `collect_con_call_summary` 的玉山案例 |

## 5. Red-before

修 bug 時，**光是新測試變綠不算證據** —— 它可能只是因為新函式存在了。

```bash
git stash push -- src/
python -m pytest tests/test_xxx.py -k "新測試"    # 必須紅，而且是因為「行為」
git stash pop
```

要看到的是 `DID NOT RAISE` 或「回傳了錯的值」，**不是 `AttributeError: module has no attribute`**。後者只證明函式沒定義，沒證明舊行為是錯的。

例：機構碰撞那項的 red-before 是舊 `detect_bank` 對「玉山財報＋關係人附註提到國泰世華」實際回傳 `'國泰'`。那才是證據。

---

## 6. 六種重構失效模式（F1–F6）

搬移程式碼時特別容易發生、而且 import 檢查與 `--help` 都抓不到的：

| | 失效 | 對策 |
|---|---|---|
| F1 | 搬移後失去 import | `tools/undefined.py` |
| F2 | 同名重複定義被合併，但後者遮蔽前者且行為不同 | 專門的測試案例 |
| F3 | **巢狀在 dict literal 裡的 lambda**（AST 看不見） | `undefined.py` 遞迴 ＋ 實際執行全部九條 |
| F4 | `af.` / `cf.` 限定名跟著搬進沒有該別名的模組 | 搬移時逐一檢查 |
| F5 | `Path(__file__).parent` 深度改變 | 路徑解析的專門測試 |
| F6 | 同名但實作不同的函式被合併 | 釘住各自的輸出契約 |

F3 是上一次大型重構中**唯一**逃過所有自動檢查的類型。

---

## 7. 已知缺口

### A/B harness 不在 repo 裡 ⚠️

目前 `ab.py` 與它用的合成 fixture（`fixture/`、`deck/`、`deck2/`）住在開發機的暫存資料夾。**換一台機器就沒有，本文件 §1 V4 那段指令現在無法照跑。**

要讓它可攜，需要把 harness 移進 `tools/ab.py`、fixture 移進 `tests/fixtures/`。這件事還沒做。

### 沒有真實財報的 golden fixture

現有 fixture 全是合成的小檔案。A/B 能證明「改動前後行為相同」，**不能證明「對真實財報正確」**。

真實檔案的 golden 比對還沒建立 —— 這是目前最大的驗證缺口，尤其在目標從四家擴到整個金融業之後。

### 特徵化測試不涵蓋沒見過的格式

302 條是**回歸網**，不是**新格式網**。它保證改動不破壞已知行為，對一份沒見過的財報什麼都不保證。這是特徵化測試的本質限制，不是覆蓋率不足。

---

## 8. 回退策略

任何一道驗證沒過：

1. `git revert` 該 commit —— **不要 `reset --hard`**，保留失敗紀錄供診斷
2. 在 `TEST_DESIGN.md` 補上「本來該抓到卻沒抓到」的案例
3. 用兩個 worktree 驗證方向：新案例在 revert 後的舊 commit 上應為**綠**，在剛才失敗的 commit 上應為**紅**

第 2 步是關鍵：**每次逃逸都代表安全網有洞，補洞優先於重做。**
