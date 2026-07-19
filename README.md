# taiwan-rent-monitor｜台灣租屋監控

以一份 `rent_config.toml` 設定想找的縣市、行政區與房屋條件，定期搜尋租屋網站，並把新房源、降價及其他重要更新傳到 Discord。

通知優先順序固定為：

1. 新房源
2. 價格下降
3. 其他重要更新

同一類事件內，才依房源分數與行政區整理。程式使用 SQLite 保存已看過及已通知的狀態，因此不會只用「刊登時間」猜測房源是否為新。

## 支援範圍

- 591：內建台灣 22 個縣市、368 個行政區與目前站方 section ID 的對照，使用者只需填中文縣市和行政區，不必查數字 ID；站內非行政區的舊標籤只供解析相容，不會出現在可選清單。
- PTT `Rent_tao`：看板本身以桃園租屋為主，不是全台來源。
- 樂屋網、永慶、5168、信義：屬選用來源，實際地區覆蓋、頁面格式與可用性依各站方而定。

591 的主要入口可參考 [591 租屋首頁](https://rent.591.com.tw/)；縣市搜尋方式可參考 [591 官方說明](https://www.591.com.tw/help-helpser3_6_1.html)。站方日後若調整地區 ID 或頁面格式，程式內的對照與 parser 也需要更新。

## 搜尋流程

每一輪會依序：

1. 讀取 `[locations]`，正規化「台／臺」，並嚴格驗證縣市、行政區及兩者的隸屬關係。
2. 依「行政區 × 房型」替 591 產生獨立搜尋 URL。逐區搜尋可避免熱門區房源占滿前幾頁，讓其他已選區域永遠抓不到。
3. 依各來源限制低頻抓取資料，解析成統一的房源格式。
4. 套用租金、坪數、套房、限女性及地區等條件。
5. 視設定套用分類與台大距離評分，之後依價格、坪數、租補、報稅、獨洗、垃圾代收等資訊排序。
6. 以 `source + listing_id` 更新 SQLite；另以地址、租金等指紋抑制疑似跨來源重複通知。
7. 從資料庫找出尚未通知的新房源或重要異動，依「新房源 → 價格下降 → 其他更新」傳送 Discord。

## 快速安裝

需求：Python 3.11+、Discord webhook；若啟用 591，還需要 Playwright Chromium。

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m playwright install chromium
Copy-Item .env.example .env
Copy-Item rent_config.example.toml rent_config.toml
python scripts/init_db.py
```

把 Discord webhook 寫入 `.env`：

```env
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
OPENAI_API_KEY=
GOOGLE_MAPS_API_KEY=
```

測試 Discord：

```powershell
python main.py test-discord
```

`.env`、`rent_config.toml`、SQLite 與 log 都已排除於 Git；不要把 webhook 或 API key 寫進 TOML 或提交到 GitHub。

## 統一設定檔：`rent_config.toml`

先複製 `rent_config.example.toml`，日後主要只需修改這個本機檔案。

### 縣市與行政區

`[locations]` 同時是房源篩選白名單，也是 591 自動產生搜尋網址的依據：

```toml
[locations]
"台北市" = ["大安區", "文山區"]
"台中市" = ["西屯區", "北區"]
"高雄市" = ["左營區", "苓雅區"]
```

搜尋整個縣市時，用 `"*"`，而且必須單獨使用：

```toml
[locations]
"台南市" = ["*"]
```

`["*"]` 會大幅減少請求數，但 `max_pages` 會套在整個縣市，熱門行政區可能占滿頁面；若特別在意各區都能被掃到，應明確列出行政區，讓程式逐區搜尋。

程式會自動把「臺」正規化為「台」。未知縣市、未知行政區，或把行政區填到錯誤縣市下，都會在啟動時直接顯示錯誤，不會靜默搜尋錯誤地區。

可先列出所有目前可填的縣市與 591 站內行政區名稱：

```powershell
python main.py locations
```

舊版 `[filters.allowed_districts]` 仍可讀取，但它與 `[locations]` 不能同時存在；新設定一律建議使用 `[locations]`。

### 房屋條件與 591 房型

```toml
[filters]
max_rent = 18600
min_area_ping = 6.0
suite_only = true
exclude_female_only = true

[sources.591]
enabled = true
room_types = ["獨立套房", "分租套房"]
search_urls = []
max_pages = 1
```

`room_types` 只接受 `"獨立套房"`、`"分租套房"`，可只保留其中一種。

`search_urls = []` 代表依 `[locations]` 自動產生網址。只在進階用途才填入網址；一旦 `search_urls` 非空，就會完整取代自動產生的 591 搜尋網址，但 `[locations]` 仍會作為後續地區篩選白名單。逐區自動產生最多 120 個 URL，以避免誤設造成過高流量；超過時可減少行政區、房型，或在理解上述覆蓋取捨後改用 `["*"]`。

### 選用的台大距離評分

一般全台搜尋請關閉：

```toml
[ranking]
enable_ntu_ranking = false
enable_google_maps = false
```

這個評分只適合「想住在台大附近」的使用者，不應影響台中、高雄或其他縣市的通用排序。啟用 `enable_ntu_ranking` 後：

- 地址有明確路名與段別時，依台大校園邊界、入口及周邊步行道路規則評分。
- 沒有精確地址時，才退回明確捷運站或較保守的行政區基準；廣告文案只給低信心，不會因「近台大、近捷運」等關鍵字直接拿高分。
- 若同時開啟 `enable_google_maps` 且在 `.env` 提供 `GOOGLE_MAPS_API_KEY`，有地址的房源會優先使用 Google Routes 到台大主校區的時間。

關閉時 Discord 不會顯示台大距離欄位，其他價格、坪數與設備排序仍照常運作。

### 來源提醒

```toml
[sources.ptt]
enabled = false
max_pages = 2
```

只有在搜尋桃園且確實需要 PTT `Rent_tao` 時才建議開啟。其他選用來源也要個別設定 `enabled = true` 與其搜尋網址；請先用對應的 `test-*` 指令確認頁面仍可解析。

## 指令

建立或更新目前房源基線：

```powershell
python main.py backfill
```

執行一次增量監控：

```powershell
python main.py watch
```

查看最近房源：

```powershell
python main.py list --limit 20
```

只測試來源、不寫入資料庫也不送 Discord：

```powershell
python main.py test-591
python main.py test-rakuya --url "https://rent.rakuya.com.tw/..."
python main.py test-yungching --url "https://rent.yungching.com.tw/..."
python main.py test-houseprice --url "https://rent.houseprice.tw/"
python main.py test-sinyi --url "https://www.sinyi.com.tw/rent"
```

## 每小時執行與漏跑補抓

頻率的單一設定位置是：

```toml
[schedule]
interval_minutes = 60
catch_up_grace_minutes = 15
source_591_catch_up_max_pages = 2
```

- `interval_minutes`：Windows 排程間隔；改完後需重跑排程安裝腳本。
- `catch_up_grace_minutes`：超過正常間隔多少分鐘後，判定曾漏跑。
- `source_591_catch_up_max_pages`：補跑時每個 591 搜尋網址最多抓幾頁，必須不小於平常的 `sources.591.max_pages`。

程式在 SQLite 保存「上次成功完成 watch 的時間」。以每小時 `:16` 執行為例，若 `3:16` 因關機沒跑、`4:16` 才執行，間隔已超過 `60 + 15` 分鐘，這輪會把 591 搜尋深度暫時提高到補抓上限。它不是硬查 `2:16–3:16` 的時間區間，而是掃描目前仍看得到的頁面，再由 SQLite 判斷哪些房源從未見過；因此仍在補抓頁面內的房源會被找回，但在下次執行前已下架、或已掉到補抓範圍之外的房源無法保證找回。

### Windows 工作排程

先確認 `rent_config.toml` 的頻率，再安裝：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows_task.ps1
```

腳本預設建立 `TaiwanRentWatch`，隱藏執行視窗，並啟用 `StartWhenAvailable`。電腦恢復可用且使用者已登入時，Windows 會盡快補跑錯過的工作；同一時間只允許一個執行個體。

若要固定從下一個 `:16` 開始，可明確指定時間：

```powershell
$next = Get-Date -Minute 16 -Second 0
if ($next -le (Get-Date)) { $next = $next.AddHours(1) }
powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows_task.ps1 -StartAt $next
```

查看狀態：

```powershell
Get-ScheduledTask -TaskName TaiwanRentWatch
Get-ScheduledTaskInfo -TaskName TaiwanRentWatch
```

停用或移除：

```powershell
Disable-ScheduledTask -TaskName TaiwanRentWatch
Unregister-ScheduledTask -TaskName TaiwanRentWatch -Confirm:$false
```

本機 log 位於 `logs/rent-watch.log`。如果需要更高的漏抓保障，應改用長時間開機的主機或排程服務，而不是只增加抓取頻率。

## GitHub Actions

`.github/workflows/rent-watch.yml` 支援手動執行及每小時 `:16` 執行。GitHub cron 使用 UTC 且屬 best effort；每小時的分鐘數不受時區影響，但仍可能延遲。雲端頻率由 workflow 的 `cron` 決定，不會動態讀取 `schedule.interval_minutes`。

Repository Secret 必填：

- `DISCORD_WEBHOOK_URL`

選填 Secrets：

- `OPENAI_API_KEY`
- `GOOGLE_MAPS_API_KEY`

workflow 預設讀取 `rent_config.example.toml`，並允許部分 Repository Variables 覆寫。房屋地點與主要條件建議直接維護在版本控制中的設定檔；私人的 webhook 與 API key 永遠放 Secrets。

Actions 以 cache 保存 SQLite 狀態，讓新房源判定可跨 runner 延續，但 cache 不是永久資料庫備份。若 cache 被清除，當前可見房源可能再次被視為新房源；需要長期可靠服務時，請使用持久主機與磁碟。

## 測試與檢查

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p "test_*.py" -v
.\.venv\Scripts\python.exe -B -m compileall -q rent_bot tests
.\.venv\Scripts\python.exe -c "import sqlite3; c=sqlite3.connect('rent_bot.sqlite3'); print(c.execute('PRAGMA quick_check').fetchone()[0]); c.close()"
```

## 檔案結構

```text
rent_config.example.toml  可提交的完整設定範例
rent_config.toml          個人設定，不進 Git
.env                      secrets，不進 Git
rent_bot/                 crawler、filter、ranking、DB、Discord
scripts/                  初始化與 Windows 排程腳本
tests/                    設定、parser、DB、通知與評分測試
.github/workflows/        選用的 GitHub Actions 排程
```

## 合規與安全

- 遵守各來源的使用條款、`robots.txt` 與合理頻率；遇到 401、403、429 或 CAPTCHA 時停止該來源，不嘗試繞過。
- 不提交 `.env`、`rent_config.toml`、SQLite、log、Discord webhook 或 API key。
- 不要在錯誤訊息或公開 issue 貼出完整 webhook；若曾外洩，立即在 Discord 重建 webhook。
