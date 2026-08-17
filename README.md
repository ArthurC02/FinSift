# account_code_finder（中文版）

從一個裝滿轉檔後 markdown（`.md`）財務報表的資料夾裡，用**專屬的會計科目代碼字典**（而不是關鍵字/文字比對）抽取科目數值——因為每一筆會計項目本來就有一個固定代碼（例如 `19999`、`A00010`、`3110`）跟科目名稱一對一綁定。原始文件的表格列，是靠「開頭的代碼儲存格」跟字典裡的代碼**完全相等**來比對的。

這是 `financial_keyword_finder` 的姊妹專案，用於「已經有一份權威的代碼對科目名稱對照表，不需要模糊關鍵字比對」的情況。

## 專案結構

```
account_code_finder/
├── src/       statements.py  decks.py  cli.py  disclosures.py
├── data/      金控業.xlsx  金融業.xlsx  保險業.xlsx  con_call_terms.json
├── docs/      HANDOFF.md（交接手冊）  con_call_terms_example.json（詞彙設定檔範例）
└── archive/   Account Coding.xlsx（已被 3 本產業活頁簿取代）
              build_manual_excel.py / build_fictional_excel.py（一次性的 Excel 產生腳本）
```

現在只有一個進入點：`src/userInteractions/cli.py`。不帶子命令就是自動分類＋合併輸出；帶 `acct` / `call` / `npl` 則轉給對應套件原本的 CLI，旗標完全不變（例如 `python src/userInteractions/cli.py acct <folder> summary`）。`data/` 裡的檔案是用 `Path(__file__).resolve().parent.parent.parent` 從 repo 根目錄定位的（模組移進套件後多了一層），所以從別的工作目錄、用絕對路徑呼叫也一定找得到。`archive/` 裡的東西不在任何執行路徑上，純粹留著備查。金管會資料集的下載快取會落在 repo 根目錄的 `npl_cache/`（已列入 `.gitignore`）。

## 依產業分類的科目代碼字典（金控業 / 金融業 / 保險業）

同一個代碼數字在不同產業裡可能代表**不同的科目**——例如代碼 `58200` 在某個產業的架構裡是呆帳提存科目，但在另一個產業裡卻是保險業專屬的成本科目（已對照真實申報文件確認過）。因此科目代碼字典拆成隨專案附帶的 3 本產業專屬活頁簿，全部放在 `data/`：`金控業.xlsx`（金融控股公司）、`金融業.xlsx`（銀行）、`保險業.xlsx`（人壽與產物保險業者）——取代這個專案最初附帶的單一 `Account Coding.xlsx`（已移到 `archive/`，不再被任何程式讀取）。

`balance_sheet`/`income_statement`/`cash_flow`/`ratios`/`all` 模式會**自動偵測**一份申報文件屬於哪個產業類別（`detect_industry_category()`），做法是掃描前幾個 `.md` 檔案，尋找申報機構自己完整法定名稱的字尾（`...商業銀行股份有限公司` → 金融業，`...金融控股股份有限公司` → 金控業，`...人壽保險股份有限公司`/`...產物保險股份有限公司` → 保險業）——而不是用像「銀行」這種單一關鍵字，因為這種字眼會以子字串形式出現在每個產業申報文件都有的普通科目項目裡（例如「銀行存款」「存放央行及拆借銀行同業」）。可用 `--industry` 覆寫，或用 `--coding <path>` 指向完全不同的檔案。

每本活頁簿本身，每張報表都包含**兩組**平行的代碼架構——一組是原始/修訂前的區塊，另一組是修訂後（IFRS17 時代的重編）區塊——用一個能處理合併儲存格、依標記驅動的解析器讀取（`_find_coding_blocks`/`_extract_coding_block`），而不是寫死欄位位置，因為兩本活頁簿的欄位排版並不完全一致。當同一個代碼在兩個區塊裡出現不同名稱時，以修訂後架構的項目為準。

`summary` 模式**完全不受上述任何規則影響**：它從來不會去查閱科目代碼字典裡的名稱做比對（下方會說明它實際顯示的內容），它固定的代碼清單（`10000`、`4xxxx`、`49010` 等）是**另外獨立驗證過的**，對照四家銀行真實的個體申報文件——先用一份合成的玉山商業銀行範例測試，再對照真實轉檔後的申報文件（每份 150～280 個 `.md` 檔）逐一人工核對每個摘要代碼跟複合項目都正確無誤。**如果一個 `.md` 資料夾是銀行個體申報文件，基於這個原因，優先使用 `summary` 而非整份報表傾印模式**——因為它完全不依賴「產業類別字典有沒有選對」這件事。

已知缺口：這 3 本產業活頁簿是用通用型解析器抽取出來的，還沒有像 `summary` 的固定代碼清單那樣逐項對照真實申報文件驗證過——`balance_sheet`/`income_statement`/`cash_flow` 的輸出名稱可視為相當可靠，但還沒有達到跟 `summary` 相同的驗證標準。

## 章節標記退路機制（已對照一份真實轉檔驗證過）

針對上面提到的同一份真實北富銀申報文件測試，發現並修正了兩個 bug：

1. **`balance_sheet`/`income_statement`/`cash_flow` 曾經對這份申報文件回傳零列結果。** 它實際使用的轉檔工具，把每張報表拆成獨立檔案時，**沒有在資料頁本身重複該報表的標題**（標題只出現在目錄裡，以及其他地方不相關的註腳提及）——導致 `restrict_section` 原本假設一定找得到的標記需求，在整個資料夾裡全部悄悄失敗。`extract_statement` 現在改成：找不到章節標記時，改成**不設限地掃描整份檔案**，而不是直接跳過該檔案——因為比對本身是精確代碼相等，不是鬆散的文字搜尋，所以這樣做是安全的。
2. **`Account Coding.xlsx` 損益表分頁裡一個只有空白字元的代碼儲存格**，逃過了「是否為空白」的檢查，被當成一個空字串的字典鍵值載入，導致文件裡任何「第一格是空白」的間隔列（例如一個「負債」區段的分隔列）都被誤判命中。`load_code_dictionary` 現在會先去除代碼的空白字元再檢查是否為空，`group_rows_by_code` 也加了一層防護，絕不會把空白標籤當成命中——屬於縱深防禦。

## 法說會詞彙搜尋（`decks.py`）

`statements.py` 是給季度財務報告（有代碼的會計科目項目）用的。
`decks.py` 是**另一個獨立的進入點**，給法人說明會（法說會）逐字稿用，它的用詞完全不同（例如 NIM、存放利差、逾放比），也沒有綁定固定代碼——你要根據自己在掃描的是哪一種 `.md` 檔案，明確選擇執行其中一支程式。

```
python src/userInteractions/cli.py call <term> --folder <folder> --config data/con_call_terms.json [--export csv] [-v]
```

**詞彙比對分兩層**，依照 `Bank_Term_Weighted_Decomposition.xlsx`（`con_call_terms.json` 就是從這份原始字典產生的——共32個詞彙，涵蓋 NIM、存放利差/放款均率/存款均率、存放比、逾放比/逾期放款覆蓋率、CIR、企業放款/房貸/個人放款/信用卡循環/其他放款、法說會放款餘額合計/法說會外幣放款、總資產/淨收益/利息淨收益/手續費淨收益/評價及已實現/其他非利息收益、營業費用/員工福利費用/折舊及攤銷費用/其他費用/呆帳提存(沖回)、稅前淨利/所得稅費用/稅後淨利、ROA(稅後年化)/ROE(稅後年化)、活存比(餘額))：

1. **`exact`（精確型）**——一份扁平的別名清單（該詞彙的標準名稱、縮寫、以及已確立的整段詞組變體）。
2. **`composite`（複合型）**——只有在沒有任何別名命中時才會評估。加權的子詞彙（例如一個修飾詞＋核心概念）只要在該列裡找到對應的同義詞組，就各自貢獻自己的權重；加總權重達到該詞彙的門檻（0.8）就算通過。跟 `financial_keyword_finder.py` 原本的 `KeywordSpec` 是同樣的 `exact`/`composite` 設計——移植過來是因為本質上是同一個問題（在文章/表格裡做模糊的詞彙辨識）。

在這兩者之內，**整格完全相等的比對，優先於單純的子字串命中**（`match_strength` 的排序是 3/完全相等 > 2/子字串別名命中 > 1/複合型 > 0/沒命中）——像「淨收益」這種通用詞彙，本身是很多不相關複合科目項目（如「手續費淨收益合計」「利息淨收益」）的literal子字串，如果接受在資料夾裡任何地方找到的第一個子字串命中，可能會悄悄抓到錯誤的那一列；因此改用在**整個資料夾**範圍內找到的最強命中，而不是遇到的第一個。另外有一個可選的 `negative_terms`（否決詞）清單，會直接排除某一列，不管它原本的分數再高——這是為了明確標記出來的互斥詞對而加的（稅前淨利 vs 稅後淨利、ROA vs ROE、逾放比 vs 逾期放款覆蓋率）。

**數值抽取是「感知表頭、自動判斷方向」的，不是靠位置**——已對照一份真實的52頁法說會簡報（國泰世華銀行 4Q25 法人說明會）驗證過，顯示法說會投影片的表格至少有兩種不同形狀，有時候同一頁還會混用兩種：

- **`row_period`（列為期間）**：每一資料列是一個期間（`FY24`、`FY25`……），表頭把每個指標各自命名成一欄（例如一張放款結構表，`企業放款` 後面緊接著一個獨立的 `企業放款占比` 百分比欄）。
- **`col_period`（欄為期間）**：每一資料列是一個指標/實體，表頭把每個期間各自命名成一欄——跟 `statements.py` 裡為玉山金控「獲利能力」表格建立的形狀相同。

方向是逐表自動偵測的（某一軸多數的儲存格能被解析成期間標籤——`parse_period_label` 能處理 `FY25`、`4Q25`、`1H25`、`9M25`，以及同一張表裡混雜不同顆粒度的情況），混雜在真正期間欄位之間的非期間欄位（例如一個 `FY25/FY24 % Chg` 成長率欄、一個 `企業放款占比` 占比欄）會被排除，不會被誤判成真正的期間欄。另外還對照那份真實簡報驗證了兩件事：

- **實體範圍界定**：同一個數字（例如「營業費用」）可能同時、以完全相同的方式出現在一家銀行子公司自己的表格裡，以及它的金控母公司合併報表的表格裡。當一個詞彙在兩邊命中程度相同時，表頭有指名該銀行（含有「銀行」字樣）的表格，會優先於金控母公司的表格——這份詞彙字典從頭到尾都是銀行層級的。已驗證：`CIR`（計算方式為 `abs(營業費用)/淨收益`，兩個詞彙各自獨立查找）在兩個輸入上都落在**同一張**銀行層級的表格上，並且精確重現了該申報文件自己揭露的「成本收入比率」（48.64%）。
- **標題退路**：有些投影片的表格只有通用的列標籤（`放款餘額`/`占全行放款`），實際的指標名稱只出現在上方的章節標題裡（`## 外幣放款`）。當沒有任何列/欄標籤能辨識出詞彙時，改為檢查最近的前一個 markdown 標題，並且選用該表格自己的絕對值列（優先選提到「餘額/總額/合計」的列，排除占比列），而不是用猜的。

### 精選摘要（預設模式）

```
python src/userInteractions/cli.py call [summary] --folder <folder> --config data/con_call_terms.json [--export csv] [-v]
```

不帶 `<term>`（或明確帶 `summary`）執行時，只回報一組固定、跟業務相關的子集——**不是**完整的32詞字典，因為其中好幾個詞彙（總資產、淨收益、稅前/稅後淨利、ROA/ROE 等）在概念上跟 `statements.py` 的財報抽取重疊，不希望出現在法說會的輸出裡：

- **比率類詞彙**（`NIM`、`存放比`、`放款均率`、`存款均率`、`CIR`）——每個都回報**兩個**數字，分開命名：`(單季)` 是文件裡直接找到的最新一季數值，`(年化)` 是把那個數值年化後的結果（× 4/季數，季別的自動偵測方式跟 `statements.py` 的 `ratios` 相同）。`CIR` 不是直接比對來的——是用 `abs(營業費用) / 淨收益 × 100` 算出來的，使用這兩個詞彙各自比對到的值。
- **餘額類詞彙**（`企業放款`、`房貸`、`個人放款`、`信用卡循環`、`其他放款`、`法說會放款餘額合計`、`法說會外幣放款`）——期末放款餘額（存量,不是流量），所以只回報最新一季的數值，沒有年化數字。
- 每個詞彙只查找**一次**——取整個資料夾裡找到的最強單一命中（見上文），不會把每次出現都累加起來，所以一個詞彙出現在好幾頁時，輸出裡不會重複。

**關於「年化」數字的注意事項**：對於像 NIM 這種「流量除以餘額」的比率，× 4/季數在數學上是合理的（跟 `statements.py` 的 ROA/ROE 邏輯相同）。但對於 `存放比`（兩個期末餘額的比率，不是流量）跟 `CIR`（兩個累計流量的比率，年化係數理應在分子分母之間互相抵銷），年化後的數字並不具備相同的真實經濟意義——這兩項請以 `individual`/`單季` 數值為準。

**現況**：已對照一份真實的52頁法說會簡報（國泰世華銀行 4Q25 法人說明會）驗證。**12個精選詞彙裡有10個正確抽取**，已對照原始 `.md` 檔案人工逐一核對：`NIM`、`存放比`、`放款均率`、`存款均率`、`CIR`、`企業放款`、`房貸`、`個人放款`、`法說會放款餘額合計`、`法說會外幣放款`。另外兩項顯示 `N/A` 是有真實原因的，不是 bug：`其他放款` 在這份簡報裡確實完全不存在（它的放款結構表只拆分出4個分類），而 `信用卡循環`（循環信用餘額，特指*循環*餘額）刻意**不**去比對 `信用卡放款`（信用卡放款總額，一個不同且範圍更廣的數字，已確認）——這份簡報裡別的地方也不存在真正的循環餘額數字。原始規格書裡提到的上下文關鍵字加權、嵌入向量相似度、字元袋比對，都刻意先不做，當作未經驗證的精進項目——規格書本身也註明它的門檻值（0.8）「是一個起始假設，不是量測出來的數值」。隨著更多簡報（其他銀行、其他季度）被檢查，應該持續重新驗證，就像 `statements.py` 的科目代碼抽取當初也是對照國泰/玉山金控/北富銀的資料逐步精進出來的一樣。

## 安裝設定

```
pip install openpyxl
```

## 使用方式

```
python src/userInteractions/cli.py acct <folder> <statement> [--coding <path>] [--period N] [--export csv] [-v]
```

- `<folder>`：要掃描的 `.md` 檔案資料夾（會遞迴搜尋子資料夾）。只有真正包含目標報表章節標題的檔案才會被掃描——其他檔案會被跳過。
- `<statement>`：`balance_sheet`、`income_statement`、`cash_flow`、`ratios`（見下方）其中之一，或 `all`（一次執行資產負債表＋損益表＋現金流量表＋比率）。
- `--coding`：（選用）科目代碼字典 `.xlsx` 的路徑，用來覆寫自動偵測。省略時會依申報機構自己的法定名稱，自動選用 `data/` 裡對應的產業活頁簿（見上方「依產業分類的科目代碼字典」）；也可以直接指向別處的檔案，例如 Downloads 裡的。
- `--period`：要抽取哪一期，依**文件本身的順序**由左至右計數（這些申報文件永遠把最近一期列在最前面）：`1` = 最近一期（預設），`2` = 次近一期，以此類推。資產負債表頁面有4期；損益表跟現金流量表有2期。
- `--export csv`：把結果寫成輸入資料夾裡的 `<statement>_export.csv`，而不是印到終端機。
- `-v` / `--verbose`：印出逐檔案的細節，包括載入了多少代碼，以及哪些代碼的列有找到但在要求的期間裡解析不出可用數值。

輸出欄位：`code | name (來自字典) | value | source file`。結尾出現 `*` 標記代表低信心結果——這個代碼的列有找到，但在要求的期間裡解析不出數值（例如一個小計標題列本身沒有數字）。

### 為什麼期間選擇是用位置，不是用日期標籤

這些轉檔後的報表沒有可用的、帶日期的表頭列可以比對：分隔列經常被 PDF→md 轉檔過程放錯位置（跑到*第一筆資料列*之後，而不是表頭之後），而真正的期間標籤是放在一個獨立的小表格裡,或是資料表格上方的一般文字敘述裡，不在資料表格本身上。**可靠的規律**是：每一列都是把各期數字依「最近到最舊」的順序、以千分位逗號分隔列出（例如 `14,450,034,484`），旁邊的百分比欄位則從來不帶逗號。所以做法是：一列的會計科目代碼精確比對字典，跨行折行的長科目名稱會被折併回同一列，目標期間的數值就是該列裡第 N 個帶千分位逗號的數字。

## ROA / ROE

```
python src/userInteractions/cli.py acct <folder> ratios [--coding <path>] [--export csv] [-v]
```

**主要來源：申報機構自己揭露的獲利能力表格。** 台灣的金控申報文件會直接揭露 ROA/ROE——資產報酬率（ROA）和淨值報酬率（ROE），各自拆成稅前/稅後,加上純益率，涵蓋合併集團跟每個子公司，同時有本期跟去年同期。只會顯示**稅後**數字（稅前會從輸出中拿掉，但內部仍會解析以維持欄位位置正確）。因為揭露的數字是年初至今的累計數——不是年化的——所以會同時顯示原始揭露數字跟一個年化版本（× 4/季數）。輸出格式是每個 (期間, 實體) 一列：`period | entity | quarter | roa_posttax | roa_posttax_annualized | roe_posttax | roe_posttax_annualized | profit_margin | source_file`。`N/A` 代表該指標在申報文件裡顯示為 `-`（該實體/期間沒有揭露）。使用官方揭露表格時，`--export csv` 會寫出 `profitability_export.csv`。

支援兩種排版，依表頭內容逐表自動偵測：

1. **實體為列，指標為欄**（已對照真實國泰 `.md` 資料驗證過，欄位表頭是表格上方的一般文字，不在表格內部）——每個期間區塊各一張表。
2. **指標為列，期間為欄**（在一份玉山/玉山金控的申報文件裡看到）——每個實體各有一張小表，各自用一個編號標題引出（例如 `1. 玉山金控及子公司`）。**這個路徑假設轉檔工具會把表頭直接放進表格本身裡**（已確認這是該轉檔工具未來的既定行為），目前只對照從該申報文件原始 PDF 文字建構出的合成 `.md` 驗證過，尚未對照真實轉檔輸出驗證——等有這種排版的真實 `.md` 轉檔可用時，要再重新驗證。

**退路：手動估算公式**，只有在資料夾裡沒有任何一份申報文件揭露這兩種排版的獲利能力表格時才會使用（一定會有一行可見的 `NOTE:` 說明有觸發這個退路）：

- **ROA(稅後年化，估算)** = `69000`（本期稅後淨利（淨損），含非控制權益）÷ 平均（本期 `19999`、上期 `19999`）÷ 季數 × 4
- **ROE(稅後年化，估算)** = 相同分子 ÷ 平均（本期 `39999`、上期 `39999`）÷ 季數 × 4

「上一期」就是資產負債表本身的第2欄期間（這些申報文件裡本期期末在前、上期期末在後）——不需要另外一份上一季的申報文件。季數是從資產負債表頁面標題裡的民國曆日期解析出來的（例如 `115年3月31日` → 第1季），這正是讓這個公式能正確把一個年初至今的累計淨利數字年化的關鍵。

## 全部報表＋比率一起執行

```
python src/userInteractions/cli.py acct <folder> all [--coding <path>] [--export csv] [-v]
```

一次執行 `balance_sheet`、`income_statement`、`cash_flow`、`ratios`。

- 不帶 `--export`：把四個區塊都印到終端機，各自在一個 `=== section ===` 標題底下，格式跟個別執行時一致。
- 帶 `--export csv`：在輸入資料夾裡寫出單一一份 `combined_export.csv`——一個檔案涵蓋所有內容，開頭有一個 `section` 欄位（`balance_sheet` / `income_statement` / `cash_flow` / `ratios`）分辨不同區段的列。不適用於某一列所屬區段的欄位（例如比率列的 `code`/`name`，或報表列的 `period`/`entity`/`roa_posttax`）會留空而不是省略，讓每一列都有相同的欄位排版。

不論哪種方式，`-v` 都會顯示逐檔案細節（兩種排版下找到哪些獲利能力表格/實體，或手動退路公式背後的原始數字）。

## 精選銀行別摘要（`summary`）

```
python src/userInteractions/cli.py acct <folder> summary [--bank 國泰] [--export csv] [-v]
```

一組固定、精選的特定代碼，加上兩個複合/衍生詞彙，用於跨銀行比較（國泰、中信、北富銀、玉山）——`--bank` 決定套用哪家銀行專屬的代碼覆寫跟公式。`--bank` 可以用簡稱或完整的替代名稱（例如 `北富銀` 跟 `台北富邦銀行`/`臺北富邦銀行` 是等價的）；完全不指定的話，會自動掃描資料夾裡第一個 `.md` 檔（通常是封面頁）尋找任何一家銀行的名稱來偵測。如果明確指定的 `--bank` 跟自動偵測都無法判斷銀行，工具會直接報錯，而不是用猜的。跟上面整份報表傾印模式不同的地方：

- 代碼是直接對照文件裡的列比對，**不管這個代碼有沒有出現在任何一本產業代碼字典裡**——這份精選清單裡大部分代碼本來就不在字典裡。這是預期中的行為——不是每家銀行都會申報每一個代碼，這也是為什麼有些項目需要銀行專屬的覆寫（例如國泰用 `63000` 而不是 `64000`），或是複合詞彙需要完全不同的公式。
- 不套用任何報表章節的限制——會搜尋整份文件，因為某個代碼在四家銀行裡不一定都待在同一張報表（資產負債表 vs 損益表 vs 現金流量表）。
- 每一列輸出的是**固定、標準化的詞彙**（`SUMMARY_LAYOUT`），而不是每家銀行文件剛好用的措辭——例如「資產合計」/「資產總計」/「資產」都會顯示成「總資產」。這是刻意脫離「永遠用文件自己的標籤」原則的設計：目標是跨銀行可比較性。文件自己的措辭**不會遺失**——會保留在一個獨立的 `term_found` 欄位/欄位值（`matched_label`）裡，緊鄰著標準化詞彙旁邊。
- **費用列顯示為正值。** 真實申報文件在淨利往下走的表格裡，把費用存成*負數*；`SUMMARY_LAYOUT` 裡的費用列（營業費用、員工福利費用、折舊及攤銷費用、其他費用、呆帳提存(收回)、所得稅費用）會為了顯示把符號反轉,讓真正的費用顯示為正值，而真正的利益/迴轉（文件本身數字剛好是正的少見情況）顯示為負值——`apply_cost_sign()`。有一個例外：中信自己的申報文件印出 `減：所得稅費用`（帶「減：」前綴）時已經是正數——這種慣例*本身*已經是費用為正的形式，所以會原樣讀入，不會再反轉一次變成錯誤的利益。
- 一個代碼在申報文件裡找不到——或複合詞彙裡任一個組成代碼缺失——會顯示為 **`N/A`**，讓每一個預期的項目永遠都會出現，而不是被悄悄省略。

固定、有順序的詞彙清單（`SUMMARY_LAYOUT`）及各自對應的代碼：

| 詞彙 | 代碼 | 是否為費用？ |
|---|---|---|
| 總資產 | `10000` | |
| 淨收益 | `4xxxx` | |
| 利息淨收益 | `49010` | |
| 評價及已實現 | *(複合項目，見下方)* | |
| 營業費用 | `58400` | ✓ |
| 員工福利費用 | `58500` | ✓ |
| 折舊及攤銷費用 | `59000` | ✓ |
| 其他費用 | `59500` | ✓ |
| 呆帳提存(收回) | `58200` | ✓ |
| 稅前淨利 | `61001` | |
| 所得稅費用 | `61003` | ✓ |
| 稅後淨利 | `64000`（國泰用 `63000`） | |

**評價及已實現**複合項目，依銀行分別加總不同的代碼組合：國泰/中信/北富銀 = `49200+49310+49450+49600`；玉山 = `49200+49310+49600`。

`手續費淨收益` 跟 `其他非利息收益`（另一個複合項目）在這份清單標準化之前曾經是輸出的一部分，現在預設不再包含——`COMPOSITE_TERMS` 裡仍然保留公式定義，以備之後又想要用的時候。

輸出/匯出的欄位排版是 `term | value | term_found | page`——刻意不顯示代碼數字本身，只顯示標準化詞彙、文件自己的措辭，以及頁碼。

> **中文版譯註（本次交接時補充，反映目前程式碼實際狀態，英文原版未更新）**：以上這份 `summary` 相關敘述、以及後面法說會 CIR 段落的內容，**部分已經跟目前 `statements.py`/`decks.py` 的實際程式碼行為不一致**——例如 `手續費淨收益`（代碼 `49100`）跟 `其他非利息收益` 這兩項現在**已經重新加回輸出**；`CIR` 已經整個從法說會（`decks.py`）搬到財報端（`statements.py`），改成不做交叉驗證、直接用 `abs(營業費用)/淨收益` 計算；法說會端的 `存放比`、`逾期放款總額` 已依指示從輸出中移除；新增了 `活存比`（目前四家銀行都還沒揭露，固定顯示 N/A）；法說會端新增了兩個從金管會網站抓的欄位「逾放比率」「備抵呆帳/逾期放款」。**這份 README 的英文原版內容偏舊，如果內容跟實際程式行為衝突，請以程式碼本身、或 `docs/HANDOFF.md` 交接手冊為準。**

## 限制：不支援權益變動表

科目代碼活頁簿的第4個分頁（權益變動表）用的是轉置排版——代碼是欄位表頭,科目標籤沿著列往下排，跟其他三張報表「一列一個代碼」的排版不同。這個工具「一列對一個科目」的比對方式無法處理這種排版，所以要求 `equity_statement` 會直接丟出清楚的錯誤訊息，而不是產生錯誤的輸出。

## 跟 financial_keyword_finder 的差異

| | financial_keyword_finder | account_code_finder |
|---|---|---|
| 比對方式 | 模糊/關鍵字文字比對（別名、加權複合評分） | 對照字典的精確代碼查找 |
| 設定檔 | 每個關鍵字一份 JSON（`keywords_example.json`） | 沒有——科目代碼 `.xlsx` 本身*就是*字典 |
| 選擇方式 | 每次查找一個關鍵字 | 一次執行傾印整份報表 |
| 輸入 | `.md` 跟 `.pdf` | 僅 `.md` |

表格解析、期間欄位偵測、數字清理邏輯，兩個專案在精神上是共用的（是各自調整過的版本，不是直接 import，讓兩個專案保持互相獨立）。

## 最終驗證回合（四家銀行，2025年第四季簡報）

`cli.py` 已對照四家銀行真實的4Q25法人說明會簡報（國泰／中信／玉山／富邦）驗證過。每一個抽取出來的數值都用**獨立**的表格解析器（不是 `decks` 自己那套）重新核對過,確認每個數字確實落在「標籤×期間」交叉點上；計算出來的CIR也手動重新推算過，並對照每份簡報自己揭露的成本收入比核對（有揭露的情況下）（中信 54.0%、富邦 −53.20%、玉山全年 47.7%）。

這一輪驗證發現並修正的 bug：

- **相鄰的表格被合併。** `parse_pipe_tables` 只要兩張表格中間只隔著一個空白行（空白行會被 `build_raw_lines` 拿掉），就會把第二張表格的表頭列誤判成第一張表格的資料列，導致第二張表格整個遺失。這隱藏了國泰簡報裡藏在年度表格底下的單季NIM表格，讓NIM回報了FY25的數字（1.55%）而不是4Q25（1.56%）。
- **錯誤的銀行子公司。** 實體偏好判斷只看「銀行」這個子字串，但每個子公司名稱裡都有這兩個字。富邦的簡報同時揭露了富邦華一銀行（中國大陸，人民幣計價）跟富邦銀行(香港)，跟台北富邦銀行用一模一樣的列標籤——導致存放比跟放款餘額抓到的是人民幣那張表（72.17%、81,769）。現在每份簡報都會先判斷自己的*主要*銀行（`PRIMARY_BANK_ENTITIES`），並直接排除其他具名公司（`entity_tier`）。
- **從通用標籤誤判出實體。** 實體偵測原本用一份「軸標籤」的黑名單，所以任何不在清單裡的標籤（例如一個表頭儲存格寫著 `Quarterly`）都會被誤判成公司名稱。改成用「公司類型標記」的白名單（`_ENTITY_NAME_RE`）。
- **百分比被誤讀成餘額。** 餘額類詞彙原本沒有防範比率儲存格的機制，導致房貸比對到富邦「業務別逾放比」（NPL比率）表格的房貸欄，回報了0.08，而法說會放款餘額合計比對到「逾期放款／總放款」這個比率列，回報了0.12。餘額類詞彙現在會排除百分比格式的儲存格，以及占比/成長率欄位（`require_absolute`）。
- **偵測不到季別。** 封面頁的正規表示式只接受中文數字（第四季），所以玉山的「第 4 季」跟富邦的「全年」都偵測不出季別，導致所有年化數字都是空白。
- **無法解析的期間格式。** `2025.12`（玉山）跟 `Dec-25`（富邦）沒有被辨識成期間，導致那幾份簡報的主要放款結構表整張被跳過。
- **帶幣別限定的比率。** 存放比比對到了台幣存放比，以及一欄純粹標示「存放比」但實際上是外幣專屬、藏在「外幣放款…佔外幣存款比例」底下的欄位。否決詞現在涵蓋了幣別限定詞，並且同時套用在投影片標題跟列標籤上。
- **CIR輸入配對錯誤。** CIR現在要求兩個輸入必須共用同一個期間標籤，而不只是同一個檔案。
- **`row_period` 的標題退路。** 一個只靠投影片標題命名的詞彙（例如 `## 房屋貸款餘額` 底下一張唯一數值欄叫「餘額」的表格），現在能被正確解析——但*只有*在剛好只有一個非期間、非成長率的數值欄位存在時才會這樣做，所以一張幣別拆分的表格（`台幣授信 | 外幣授信`）會維持不比對,而不是悄悄只取一半的數字。

剩下的 `N/A` 都逐一掃過簡報裡每一個表格標籤確認過確實不存在：國泰 信用卡循環/其他放款、中信 放款均率/存款均率/個人放款/信用卡循環、玉山 其他放款、富邦 其他放款——完全沒有任何候選標籤。富邦 存放比 有三個候選，全部都被正確排除（純台幣、純外幣、以及富邦華一銀行的），也就是說那份簡報沒有揭露全行層級的存放比。

以下是解讀上的但書，不是 bug：中信按幣別區分放款，所以企業放款對應到台幣法人放款，不包含外幣計價的企業放款；富邦企業放款用的是企業授信，依它自己的註腳，這包含了信用狀買斷與應收帳款承購。

---

---

# account_code_finder (English)

Extracts account values from a folder of converted markdown (`.md`) financial statements using
a dedicated account-coding dictionary instead of keyword/text matching, since every account line
already has a fixed code (e.g. `19999`, `A00010`, `3110`) tied 1:1 to its account name. A source
document's table row is matched by its leading code cell equalling a dictionary code exactly.

This is a sibling project to `financial_keyword_finder`, for the case where you have an
authoritative code-to-account mapping instead of needing fuzzy keyword matching.

## Layout

```
account_code_finder/
├── src/       statements.py  decks.py  cli.py  disclosures.py
├── data/      金控業.xlsx  金融業.xlsx  保險業.xlsx  con_call_terms.json
├── docs/      HANDOFF.md (handover manual)  con_call_terms_example.json (term-config sample)
└── archive/   Account Coding.xlsx (superseded by the 3 industry workbooks)
              build_manual_excel.py / build_fictional_excel.py (one-off Excel generators)
```

All four scripts are run from the repo root, e.g. `python src/userInteractions/cli.py acct <folder> summary`.
Files under `data/` are located via `Path(__file__).resolve().parent.parent.parent` - three levels up from `src/<package>/`, not two, since the modules moved into packages. So invoking a script by
absolute path from any working directory (`python C:\...\src/userInteractions/cli.py`) still resolves them.
Nothing in `archive/` is on any execution path — it is kept for reference only. The FSC dataset
download cache lands in `npl_cache/` at the repo root (already in `.gitignore`).

## Industry-category coding dictionaries (金控業 / 金融業 / 保險業)

The same code number can mean a **different account** depending on industry — e.g. code `58200`
is a bad-debt-provision line in one industry's scheme but an insurance-specific cost line in
another's (confirmed against real filings). The coding dictionary is therefore split into 3
industry-specific workbooks bundled with the project under `data/`: `金控業.xlsx` (financial
holding companies), `金融業.xlsx` (banks), `保險業.xlsx` (life/`人壽` and property-casualty/`產險`
insurers) — replacing the single `Account Coding.xlsx` this project originally shipped with
(now parked in `archive/`, read by nothing).

`balance_sheet`/`income_statement`/`cash_flow`/`ratios`/`all` modes **auto-detect** which category
a filing belongs to (`detect_industry_category()`), by scanning the first few `.md` files for the
reporting entity's own full legal-name suffix (`...商業銀行股份有限公司` → 金融業,
`...金融控股股份有限公司` → 金控業, `...人壽保險股份有限公司`/`...產物保險股份有限公司` → 保險業) —
not a bare keyword like `銀行`, since that appears as a substring in ordinary line items
(`銀行存款`, `存放央行及拆借銀行同業`) that every industry's filing has. Override with `--industry`,
or point at a completely different file with `--coding <path>`.

Each workbook itself contains **two** parallel code schemes per statement — an original/
pre-revision block and a revised/post-revision block (an IFRS17-era reconciliation) — read via a
merged-cell-aware, marker-driven parser (`_find_coding_blocks`/`_extract_coding_block`) rather
than hardcoded column positions, since the two workbooks don't share identical column layouts.
Revised-scheme entries win when a code exists in both blocks with different names.

`summary` mode is **unaffected** by any of this: it never consults a coding-dictionary name for
matching (see below for what it *does* show), and its fixed code list (`10000`, `4xxxx`, `49010`,
etc.) was separately verified against real bank-standalone (個體) filings from all 4 banks — first
against a synthetic E.Sun 玉山商業銀行 sample, then against real converted filings (each 150–280
`.md` files) where every summary code and composite term matched and checked out by hand. **If a
`.md` folder is a bank-standalone filing, prefer `summary` over the whole-statement dumps** for
this reason — it never depends on getting the industry-category dictionary exactly right.

Known gap: the 3 industry workbooks were extracted with a general-purpose parser and are not yet
verified code-by-code against real filings the way `summary`'s fixed code list was — treat
`balance_sheet`/`income_statement`/`cash_flow` output names as reasonably reliable but not to the
same verified standard as `summary`'s.

## Section-marker fallback (confirmed against a real conversion)

Tested against the same real 北富銀 filing above, which surfaced two bugs since fixed:

1. **`balance_sheet`/`income_statement`/`cash_flow` used to return zero rows for this filing.**
   Its actual conversion tool splits one statement per file *without repeating the statement's
   title on the data page itself* (the title only appears in the table of contents and in
   unrelated footnote mentions elsewhere) — so `restrict_section`'s marker requirement, previously
   assumed always findable, silently failed for the entire folder. `extract_statement` now falls
   back to scanning the whole file unrestricted when no section marker is found, rather than
   skipping the file — safe because matching is exact-code equality, not a loose text search.
2. **A whitespace-only code cell in `Account Coding.xlsx`'s income-statement sheet** was slipping
   past the blank-code check and being loaded as an empty-string dictionary key, causing every
   blank-first-cell spacer row in a document (e.g. a `負債` section divider row) to falsely match.
   `load_code_dictionary` now strips a code before checking whether it's blank, and
   `group_rows_by_code` guards against ever matching an empty label as defense in depth.

## Earnings-call term search (`decks.py`)

`statements.py` is for quarterly financial reports (coded account line items).
`decks.py` is a **separate entry point** for earnings-call (法說會) transcripts, whose
vocabulary is completely different (e.g. NIM, 存放利差, 逾放比) and isn't tied to a fixed code —
you explicitly run one script or the other depending on which kind of `.md` files you're scanning.

```
python src/userInteractions/cli.py call <term> --folder <folder> --config data/con_call_terms.json [--export csv] [-v]
```

**Term matching is two-layer**, per `Bank_Term_Weighted_Decomposition.xlsx` (the source dictionary
`con_call_terms.json` was generated from — 32 terms, covering NIM, 存放利差/放款均率/存款均率,
存放比, 逾放比/逾期放款覆蓋率, CIR, 企業放款/房貸/個人放款/信用卡循環/其他放款,
法說會放款餘額合計/法說會外幣放款, 總資產/淨收益/利息淨收益/手續費淨收益/評價及已實現/
其他非利息收益, 營業費用/員工福利費用/折舊及攤銷費用/其他費用/呆帳提存(沖回), 稅前淨利/
所得稅費用/稅後淨利, ROA(稅後年化)/ROE(稅後年化), 活存比(餘額)):

1. **`exact`** — a flat alias list (the term's standard name, acronym, and well-established
   whole-phrase variants).
2. **`composite`** — only evaluated when no alias hits. Weighted sub-terms (e.g. a qualifier +
   core concept) each contribute their weight when their synonym set is found in the row; accepted
   once the summed weight reaches the term's threshold (0.8). Same `exact`/`composite` design as
   `financial_keyword_finder.py`'s original `KeywordSpec` — ported here since it's the same
   underlying problem (fuzzy term identification in prose/tables).

Within those, an **exact whole-cell match is preferred over a mere substring hit** (`match_strength`
ranks 3/exact > 2/substring-alias > 1/composite > 0/none) — a generic term like `淨收益` is a
literal substring of many unrelated compound line items (`手續費淨收益合計`, `利息淨收益`), so
accepting the first substring hit found anywhere in a folder can silently grab the wrong row; the
strongest match found across the *entire* folder is used instead of just the first one encountered.
An optional `negative_terms` list rejects a row outright regardless of an otherwise-passing score —
added for the explicitly flagged mutually-exclusive pairs (稅前淨利 vs 稅後淨利, ROA vs ROE, 逾放比
vs 逾期放款覆蓋率).

**Value extraction is header-aware and orientation-detecting**, not positional — verified against a
real 52-page earnings-call deck (國泰世華銀行 4Q25 analyst meeting), which showed con-call slide
tables come in at least two different shapes, sometimes both on the same page:

- **`row_period`**: each data row is one period (`FY24`, `FY25`, ...), and the header names each
  metric as its own column (e.g. a loan-structure table with `企業放款` and a separate
  `企業放款占比` percentage column right after it).
- **`col_period`**: each data row is one metric/entity, and the header names each period as its own
  column — the same shape built for 玉山金控's 獲利能力 table in `statements.py`.

Orientation is auto-detected per table (majority of one axis's cells parsing as period labels -
`parse_period_label` handles `FY25`, `4Q25`, `1H25`, `9M25`, mixed granularity in the same table),
and non-period columns mixed in among real ones (a `FY25/FY24 % Chg` growth column, a `企業放款占比`
share column) are excluded rather than mistaken for the real one. Two more things verified against
that real deck:

- **Entity scoping**: the same figure (e.g. `營業費用`) can appear identically in both a bank
  subsidiary's own table and its FHC parent's consolidated table. When a term matches equally well
  in both, the table whose header names the bank (containing `銀行`) is preferred over the FHC
  parent's — this term dictionary is bank-level throughout. Verified: `CIR` (computed as
  `abs(營業費用)/淨收益`, both terms looked up independently) landed on the *same* bank-scoped table
  for both inputs and reproduced that filing's own disclosed `成本收入比率` (48.64%) exactly.
- **Heading fallback**: some slides' tables have only generic row labels (`放款餘額`/`占全行放款`)
  with the actual metric name only in the section title above (`## 外幣放款`). When no row/column
  label identifies a term, the nearest preceding markdown heading is checked instead, and the
  table's own absolute-value row is picked (preferring one mentioning 餘額/總額/合計, rejecting
  percentage-share rows) rather than guessing.

### Curated summary (default mode)

```
python src/userInteractions/cli.py call [summary] --folder <folder> --config data/con_call_terms.json [--export csv] [-v]
```

Running with no `<term>` (or explicitly `summary`) reports only a fixed, business-relevant subset
— **not** the full 32-term dictionary, since several of those terms (總資產, 淨收益, 稅前/稅後淨利,
ROA/ROE, etc.) conceptually overlap with `statements.py`'s financial-report extraction and
aren't wanted in con-call output:

- **Ratio terms** (`NIM`, `存放比`, `放款均率`, `存款均率`, `CIR`) — each reports **two** figures,
  named separately: `(單季)` the latest-quarter value as directly found in the document, and
  `(年化)` that value annualized (× 4/quarter number, quarter auto-detected the same way as
  `statements.py`'s `ratios`). `CIR` isn't matched directly — it's computed as
  `abs(營業費用) / 淨收益 × 100`, using those two terms' own matched values.
- **Balance terms** (`企業放款`, `房貸`, `個人放款`, `信用卡循環`, `其他放款`,
  `法說會放款餘額合計`, `法說會外幣放款`) — period-end loan balances (stock, not flow), so only
  the latest-quarter value is reported; no annualized figure.
- Each term is looked up **once** — the single strongest match found across the whole folder (see
  above), not accumulated per occurrence, so a term appearing on multiple pages doesn't get
  repeated in the output.

**Caveat on the `年化` figure**: `× 4/quarter#` is mathematically sound for a flow-over-balance
ratio like NIM (same logic as `statements.py`'s ROA/ROE). For `存放比` (a ratio of two
period-end balances, not a flow) and `CIR` (a ratio of two cumulative flows, where the
annualization factor should actually cancel between numerator and denominator), the annualized
figure doesn't carry the same real economic meaning — treat `individual`/`單季` as authoritative
for those two specifically.

**Status**: verified against a real 52-page earnings-call deck (國泰世華銀行 4Q25 analyst meeting).
**10 of the 12 curated terms extract correctly**, cross-checked by hand against the source `.md`
files: `NIM`, `存放比`, `放款均率`, `存款均率`, `CIR`, `企業放款`, `房貸`, `個人放款`,
`法說會放款餘額合計`, `法說會外幣放款`. Two report `N/A` for real reasons, not bugs: `其他放款` is
genuinely absent from this deck (its loan-structure table only breaks out 4 categories), and
`信用卡循環` (循環信用餘額, the *revolving* balance specifically) is deliberately **not** matched
against `信用卡放款` (total card loans, a different and broader figure, confirmed) — no true
revolving-balance figure exists elsewhere in this deck. Context-keyword boosting, embedding
similarity, and bag-of-characters matching (mentioned in the source spec) were intentionally left
out as unvalidated refinements — the spec itself notes its threshold (0.8) is "a starting
assumption, not a measured value." Re-verify further as more decks (other banks, other quarters)
are checked, the same way `statements.py`'s account-code extraction was refined against Cathay/
玉山金控/北富銀 data.

## Setup

```
pip install openpyxl
```

## Usage

```
python src/userInteractions/cli.py acct <folder> <statement> [--coding <path>] [--period N] [--export csv] [-v]
```

- `<folder>`: folder of `.md` files to scan (searched recursively). Only files that actually
  contain the target statement's section heading are scanned — others are skipped.
- `<statement>`: one of `balance_sheet`, `income_statement`, `cash_flow`, `ratios` (see below), or
  `all` (balance sheet + income statement + cash flow + ratios together in one run).
- `--coding`: (optional) path to a coding dictionary `.xlsx`, overriding auto-detection. When
  omitted, the matching industry workbook in `data/` is picked from the filing's own legal name
  (see "Industry-category coding dictionaries" above); you can also point at a file elsewhere,
  e.g. one in Downloads.
- `--period`: which period to extract, counting left-to-right **in document order** (these
  filings always list the most recent period first): `1` = most recent (default), `2` = next
  most recent, etc. Balance sheet pages have 4 periods; income statement and cash flow have 2.
- `--export csv`: write results to `<statement>_export.csv` in the input folder instead of
  printing to stdout.
- `-v` / `--verbose`: print per-file detail, including how many codes loaded and any code whose
  row was matched but had no parseable value for the requested period.

Output columns: `code | name (from dictionary) | value | source file`. A trailing `*` marks a
low-confidence result — the code's row was found but no value could be parsed for the requested
period (e.g. a subtotal heading row with no figure of its own).

### Why period selection is positional, not by date label

These converted statements don't have a usable dated header row to match against: divider rows
are frequently misplaced by the PDF→md conversion (landing after the *first data row* instead of
after a header), and the real period labels sit in a separate mini-table or in plain prose above
the data, not on the data table itself. What **is** reliable is that every row lists its periods
most-recent-first, comma-grouped (e.g. `14,450,034,484`), with percentage columns alongside them
that never have commas. So a row's account code is matched exactly against the coding dictionary,
long account names that wrap onto a second physical line are folded back into the same row, and
the target period's value is just the Nth comma-grouped number found in that row.

## ROA / ROE

```
python src/userInteractions/cli.py acct <folder> ratios [--coding <path>] [--export csv] [-v]
```

**Primary source: the filer's own reported 獲利能力 (profitability) table.** Taiwanese financial
holding filings disclose ROA/ROE directly — 資產報酬率 (ROA) and 淨值報酬率 (ROE), each split into
稅前/稅後 (pretax/posttax), plus 純益率 (profit margin), for the consolidated group (合併) and
every subsidiary, for both the current quarter and the same quarter last year. Only **稅後
(posttax)** is surfaced (pretax is dropped from the output, though still parsed internally where
needed to keep column position correct). Since the disclosed figure is cumulative year-to-date —
not annualized — both the as-reported number and an annualized version (× 4/quarter number) are
shown. Output is one row per (period, entity): `period | entity | quarter | roa_posttax |
roa_posttax_annualized | roe_posttax | roe_posttax_annualized | profit_margin | source_file`.
`N/A` marks a metric shown as `-` in the filing (not disclosed for that entity/period).
`--export csv` writes `profitability_export.csv` when a reported table was used.

Two layouts are supported, auto-detected per table from its header content:

1. **Entities as rows, metrics as columns** (verified against real Cathay `.md` data, where column
   headers are plain prose above the table, not inside it) — one table per period block.
2. **Metrics as rows, periods as columns** (seen in an E.Sun/玉山金控 filing) — one small table per
   entity, each introduced by a numbered heading like `1. 玉山金控及子公司`. **This path assumes
   the conversion tool captures column headers inside the table itself** (confirmed as the
   intended behavior of the actual tool going forward) and has only been verified against a
   synthetic `.md` built from that filing's raw PDF text, not yet against real converted output —
   re-verify once real `.md` conversions of this layout are available.

**Fallback: a manual approximation**, used only when no filing in the folder has a 獲利能力
table of either layout (a visible `NOTE:` line always announces when this happens):

- **ROA(稅後年化, approximated)** = `69000` (本期稅後淨利（淨損）, incl. non-controlling
  interest) ÷ average(`19999` this quarter, `19999` last quarter) ÷ quarter number × 4
- **ROE(稅後年化, approximated)** = same numerator ÷ average(`39999` this quarter, `39999` last
  quarter) ÷ quarter number × 4

"Last quarter" is the balance sheet's own 2nd period column (current quarter-end first, prior
quarter-end second in these filings) — no separate prior-quarter filing is needed. The quarter
number is parsed from the ROC-calendar date in the balance sheet page's title (e.g. `115年3月31日`
→ Q1), which is what makes this correctly annualize a cumulative year-to-date net income figure.

## All statements + ratios together

```
python src/userInteractions/cli.py acct <folder> all [--coding <path>] [--export csv] [-v]
```

Runs `balance_sheet`, `income_statement`, `cash_flow`, and `ratios` in one pass.

- Without `--export`: prints all four sections to the console, each under a `=== section ===`
  header, in the same format as running them individually.
- With `--export csv`: writes a single `combined_export.csv` in the input folder — one file
  covering everything, with a leading `section` column (`balance_sheet` / `income_statement` /
  `cash_flow` / `ratios`) so rows from different sections can still be told apart. Columns that
  don't apply to a given row's section (e.g. `code`/`name` for a ratios row, or
  `period`/`entity`/`roa_posttax` for a statement row) are left blank rather than omitted, so
  every row has the same column layout.

`-v` shows per-file detail either way (which profitability tables/entities were found in either
layout, or the underlying figures behind the manual fallback).

## Curated per-bank summary (`summary`)

```
python src/userInteractions/cli.py acct <folder> summary [--bank 國泰] [--export csv] [-v]
```

A fixed, curated set of specific codes plus two composite/derived terms, for cross-bank
comparison across 國泰, 中信, 北富銀, and 玉山 — `--bank` resolves bank-specific code overrides
and formulas. `--bank` accepts either the short form or a full alternate name (e.g. `北富銀` or
`台北富邦銀行`/`臺北富邦銀行` are equivalent); if omitted entirely, the bank is auto-detected by
scanning the first `.md` file in the folder (typically the cover page) for any bank's name. If
neither an explicit `--bank` nor auto-detection resolves a bank, the tool errors out rather than
guessing. Unlike the whole-statement dumps above:

- Codes are matched directly against a document's rows **regardless of whether they're in any
  industry coding dictionary** — most of this curated list isn't. This is expected — not every
  bank reports every code, which is also why some entries need a bank-specific override (e.g. 國泰
  uses `63000` instead of `64000`) or a different formula entirely for the composite term.
- No statement-section restriction is applied — the whole document is searched, since a given
  code isn't guaranteed to live in the same statement (balance sheet vs. income statement vs.
  cash flow) across all 4 banks.
- Output shows a **fixed, standardized term** per line (`SUMMARY_LAYOUT`), not whatever wording
  each bank's document happens to use — e.g. `資產合計`/`資產總計`/`資產` all display as `總資產`.
  This is a deliberate departure from "always use the document's own label": the goal is
  cross-bank comparability. The document's own wording is **not lost** — it's kept in a separate
  `term_found` column/field (`matched_label`), right next to the standardized term.
- **Cost lines display as positive.** A real filing stores an expense as a *negative* number in
  its net-income-walk table; `SUMMARY_LAYOUT`'s cost lines (營業費用, 員工福利費用, 折舊及攤銷費用,
  其他費用, 呆帳提存(收回), 所得稅費用) flip that sign for display, so a genuine cost shows
  positive and a genuine benefit/reversal (an unusual case where the filing's own number is
  positive) shows negative — `apply_cost_sign()`. One exception: 中信's own filing prints
  `減：所得稅費用` (a "less:" prefix) as an already-positive number — that convention is *already*
  cost-positive, so it's read through unchanged rather than flipped again into a false benefit.
- A code not found in a filing — or the composite term, if any one of its component codes is
  missing — shows as **`N/A`**, so every expected line always appears rather than being silently
  omitted.

The fixed, ordered term list (`SUMMARY_LAYOUT`) and the codes behind each:

| Term | Code | Cost? |
|---|---|---|
| 總資產 | `10000` | |
| 淨收益 | `4xxxx` | |
| 利息淨收益 | `49010` | |
| 評價及已實現 | *(composite, see below)* | |
| 營業費用 | `58400` | ✓ |
| 員工福利費用 | `58500` | ✓ |
| 折舊及攤銷費用 | `59000` | ✓ |
| 其他費用 | `59500` | ✓ |
| 呆帳提存(收回) | `58200` | ✓ |
| 稅前淨利 | `61001` | |
| 所得稅費用 | `61003` | ✓ |
| 稅後淨利 | `64000` (`63000` for 國泰) | |

**評價及已實現** composite, summed from bank-specific code sets: 國泰/中信/北富銀 =
`49200+49310+49450+49600`; 玉山 = `49200+49310+49600`.

`手續費淨收益` and `其他非利息收益` (a second composite term) were part of the output before this
list was standardized and are no longer included by default — `COMPOSITE_TERMS` still has the
formula defined if they're wanted again.

Output/export shape is `term | value | term_found | page` — the numeric code itself is
intentionally not shown, only the standardized term, the document's own wording, and the page.

## Limitation: equity statement not supported

The coding workbook's 4th sheet (權益變動表 / statement of changes in equity) uses a transposed
layout — codes are column headers with account labels running down rows, rather than one code
per row like the other three statements. This tool's row-per-account matching can't handle that
layout, so requesting `equity_statement` raises a clear error rather than producing wrong output.

## How it differs from financial_keyword_finder

| | financial_keyword_finder | account_code_finder |
|---|---|---|
| Matching | Fuzzy/keyword text (aliases, weighted composite scoring) | Exact code lookup against a dictionary |
| Config | Per-keyword JSON (`keywords_example.json`) | None — the coding `.xlsx` *is* the dictionary |
| Selection | One keyword per lookup | Whole statement dumped in one pass |
| Input | `.md` and `.pdf` | `.md` only |

Table parsing, period-column detection, and numeric cleanup logic are shared in spirit (adapted,
not imported, to keep the two projects independent).

## Final verification pass (all four banks, 2025 Q4 decks)

`cli.py` was validated against all four banks' real 4Q25 analyst-meeting decks
(國泰 / 中信 / 玉山 / 富邦). Every extracted value was re-checked with an **independent**
table parser (not `decks`'s own), confirming each figure sits at the reported
label × period intersection; computed CIRs were re-derived by hand and cross-checked
against each deck's own disclosed cost-income ratio where one exists
(中信 54.0%, 富邦 −53.20%, 玉山 annual 47.7%).

Bugs this pass found and fixed:

- **Adjacent tables merged.** `parse_pipe_tables` treated a second table's header row as
  a data row of the first whenever only a blank line separated them (blank lines are
  dropped by `build_raw_lines`), losing the second table entirely. That hid the quarterly
  NIM table sitting under the annual one in 國泰's deck, making NIM report the FY25 figure
  (1.55%) instead of 4Q25 (1.56%).
- **Wrong bank subsidiary.** Entity preference keyed only on the substring "銀行", which every
  subsidiary's name contains. 富邦's deck reports 富邦華一銀行 (mainland China, RMB) and
  富邦銀行(香港) alongside 台北富邦銀行, using identical row labels — so 存放比 and 放款餘額
  came from the RMB table (72.17%, 81,769). Now each deck resolves its *primary* bank
  (`PRIMARY_BANK_ENTITIES`) and rejects other named companies outright (`entity_tier`).
- **Entity detected from a generic label.** Entity detection used a blocklist of axis labels,
  so any unlisted one (e.g. a header cell reading `Quarterly`) was read as a company name.
  Replaced with an allowlist of company-type markers (`_ENTITY_NAME_RE`).
- **Percentages read as balances.** Balance terms had no guard against ratio cells, so 房貸
  matched the 房貸 column of 富邦's 業務別逾放比 (NPL-ratio) table and reported 0.08, and
  法說會放款餘額合計 matched the ratio row 逾期放款／總放款 and reported 0.12. Balance terms
  now reject percent-formatted cells and share/growth columns (`require_absolute`).
- **Quarter not detected.** The cover-page regex only accepted Chinese numerals (第四季), so
  玉山's "第 4 季" and 富邦's "全年" yielded no quarter and blanked every 年化 figure.
- **Period formats unparsed.** `2025.12` (玉山) and `Dec-25` (富邦) weren't recognised as
  periods, so those decks' main loan-structure tables were skipped entirely.
- **Currency-qualified ratios.** `存放比` matched 台幣存放比 and an FX-only column headed plainly
  `存放比` under 外幣放款…佔外幣存款比例. Negative terms now cover the currency qualifiers and
  are applied to the slide heading as well as the label.
- **Mismatched CIR inputs.** CIR now requires both inputs to share the same period label, not
  merely the same file.
- **`row_period` heading fallback.** A term named only by the slide heading (e.g. `## 房屋貸款餘額`
  over a table whose only value column is `餘額`) is now resolved — but *only* when exactly one
  non-period, non-growth value column exists, so a currency-split table
  (`台幣授信 | 外幣授信`) stays unmatched rather than silently yielding half the figure.

Remaining `N/A`s were each confirmed genuinely absent by scanning every table label in the
deck: 國泰 信用卡循環/其他放款, 中信 放款均率/存款均率/個人放款/信用卡循環, 玉山 其他放款,
富邦 其他放款 — zero candidate labels anywhere. 富邦 存放比 has three candidates, all correctly
rejected (台幣-only, FX-only, and 富邦華一銀行's), i.e. that deck discloses no overall
bank-level loan-to-deposit ratio.

Interpretation caveats that are *not* bugs: 中信 segments loans by currency, so 企業放款 maps to
台幣法人放款 and excludes FX-denominated corporate lending; 富邦 企業放款 uses 企業授信, which per
its own footnote includes 信用狀買斷與應收帳款承購.
