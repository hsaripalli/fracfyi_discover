# fracfyi_discover

Automated data pipeline for **frac.fyi Discover** energy commodity benchmarks.

Fetches official **U.S. Energy Information Administration (EIA)** physical spot market benchmarks on daily schedules and publishes public JSON feeds to Cloudflare R2 for the mobile and web clients.

---

## 📊 Datasets

* **WTI Crude Oil**: Cushing, OK Spot Price (`$US/bbl`, 260 trading days = 52-week time series).
* **Henry Hub Natural Gas**: Natural Gas Spot Price (`$US/MMBtu`, 260 trading days = 52-week time series).

**Output File**: `data/commodity_benchmarks.json`

---

## ⚙️ GitHub Actions Automation

The workflow `.github/workflows/sync_discover.yml` runs **Monday–Friday at 18:30 UTC** (1:30 PM EST / 11:30 AM MT) shortly after EIA updates spot data.

### Required Repository Secrets:
Go to **Settings** → **Secrets and variables** → **Actions** → **New repository secret**:

| Secret Name | Description | Example |
| :--- | :--- | :--- |
| `EIA_API_KEY` | Official EIA API v2 Key | `YDRQnIvGMmoBTl4wYRVjwitwEV2yGa3w2Pc9nlN2` |
| `R2_ACCOUNT_ID` | Cloudflare Account ID | *(From Cloudflare Dashboard)* |
| `R2_ACCESS_KEY_ID` | Cloudflare R2 Token Access Key ID | `...` |
| `R2_SECRET_ACCESS_KEY` | Cloudflare R2 Token Secret Access Key | `...` |
| `R2_BUCKET_NAME` | Cloudflare R2 Bucket Name | `fracfyi-discover` |

---

## 💻 Local Usage

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Run the sync:
   ```bash
   python sync_commodity_benchmarks.py
   ```
