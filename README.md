# fracfyi_discover

Automated data pipelines for **frac.fyi Discover & Activity** market intelligence.

Fetches official **U.S. Energy Information Administration (EIA)** spot benchmarks and **Alberta Energy Crown Land Sales** data, publishing public JSON feeds to Cloudflare R2 for the mobile and web clients.

---

## 📊 Datasets & Pipelines

### 1. Commodity Benchmarks (`sync_commodity_benchmarks.py`)
* **WTI Crude Oil**: Cushing, OK Spot Price (`$US/bbl`, 260 trading days = 52-week time series).
* **Henry Hub Natural Gas**: Natural Gas Spot Price (`$US/MMBtu`, 260 trading days = 52-week time series).
* **Output**: `data/commodity_benchmarks.json`

### 2. Alberta Crown Land Sales (`sync_land_sales.py`)
* **2026 Activity Feed**: Complete bi-weekly 2026 sales results, winning bidders, $/ha pricing, and upcoming auction notices.
* **10-Year Macro Trends**: 2015–2026 annual totals, top land broker rankings, and regional spending distributions.
* **Outputs**:
  * `data/land_sales_activity_2026.json` (Activity Tab)
  * `data/land_sales_discover_macro.json` (Discover Tab)

---

## ⚙️ GitHub Actions Automation

* **`sync_discover.yml`**: Runs **Monday–Friday at 18:30 UTC** (11:30 AM MT) shortly after EIA updates daily spot prices.
* **`sync_land_sales.yml`**: Runs **every Wednesday at 20:00 UTC** (2:00 PM MT) right after Alberta Energy publishes bi-weekly Crown land sale results.

### Required Repository Secrets:
Go to **Settings** → **Secrets and variables** → **Actions** → **New repository secret**:

| Secret Name | Description |
| :--- | :--- |
| `EIA_API_KEY` | Official EIA API v2 Key |
| `R2_ACCOUNT_ID` | Cloudflare Account ID |
| `R2_ACCESS_KEY_ID` | Cloudflare R2 Token Access Key ID |
| `R2_SECRET_ACCESS_KEY` | Cloudflare R2 Token Secret Access Key |
| `R2_BUCKET_NAME` | Cloudflare R2 Bucket Name |

---

## 💻 Local Usage

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Run commodity benchmarks sync:
   ```bash
   python sync_commodity_benchmarks.py
   ```

3. Run Crown land sales sync:
   ```bash
   python sync_land_sales.py --upload-r2
   ```
