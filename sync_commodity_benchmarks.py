#!/usr/bin/env python3
"""
sync_commodity_benchmarks.py

Fetches official U.S. EIA API v2 benchmark spot prices for:
- WTI Crude Oil (Cushing Spot, $US/bbl)
- Henry Hub Natural Gas Spot ($US/MMBtu)

Generates 30-day and 52-week time series with High/Low/Delta metrics
and uploads `data/commodity_benchmarks.json` to Cloudflare R2.
"""

import json
import os
import sys
from datetime import datetime, timezone
import requests
from dotenv import load_dotenv

# Load local .env if present
load_dotenv()

EIA_API_KEY = os.getenv("EIA_API_KEY", "YDRQnIvGMmoBTl4wYRVjwitwEV2yGa3w2Pc9nlN2").strip()
R2_BUCKET = os.getenv("R2_BUCKET_NAME", "fracfyi-discover").strip()
R2_KEY = os.getenv("R2_OBJECT_KEY", "data/commodity_benchmarks.json").strip()
R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID", "").strip()
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID", "").strip()
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY", "").strip()
R2_ENDPOINT_URL = os.getenv("R2_ENDPOINT_URL", "").strip()

if not R2_ENDPOINT_URL and R2_ACCOUNT_ID:
    R2_ENDPOINT_URL = f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com"


def fetch_eia_series(api_url: str, name: str, symbol: str, unit: str, description: str) -> dict:
    """Fetches and processes EIA v2 series data into 30D and 52W metrics."""
    print(f"Fetching {name} from EIA API...")
    resp = requests.get(api_url, headers={"Accept": "application/json"}, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    rows = data.get("response", {}).get("data", [])
    valid_rows = []
    for r in rows:
        val_str = r.get("value")
        period = r.get("period")
        if val_str is not None and period:
            try:
                val = float(val_str)
                valid_rows.append({"date": str(period), "value": val})
            except ValueError:
                continue

    if not valid_rows:
        raise ValueError(f"No valid price records returned for {name}")

    latest = valid_rows[0]
    prev = valid_rows[1] if len(valid_rows) > 1 else latest
    change_pct = ((latest["value"] - prev["value"]) / prev["value"]) * 100 if prev["value"] != 0 else 0.0

    all_vals = [r["value"] for r in valid_rows]
    high_52w = max(all_vals)
    low_52w = min(all_vals)

    oldest_52w = valid_rows[-1]
    change_52w_pct = (
        ((latest["value"] - oldest_52w["value"]) / oldest_52w["value"]) * 100
        if oldest_52w["value"] != 0
        else 0.0
    )

    # Reverse to chronological order (oldest -> newest) for plotting
    history_52w = list(reversed(valid_rows))
    history_30d = history_52w[-30:]

    return {
        "name": name,
        "symbol": symbol,
        "description": description,
        "price": round(latest["value"], 2),
        "unit": unit,
        "date": latest["date"],
        "change_pct": round(change_pct, 2),
        "high_52w": round(high_52w, 2),
        "low_52w": round(low_52w, 2),
        "change_52w_pct": round(change_52w_pct, 2),
        "history_30d": history_30d,
        "history_52w": history_52w,
        "history_12m": history_52w,
    }


def upload_to_r2(payload_json: str):
    """Uploads formatted JSON payload to Cloudflare R2."""
    if not (R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY and R2_ENDPOINT_URL):
        print("ℹ R2 credentials not fully specified in environment. Skipping R2 cloud upload.")
        return

    import boto3
    from botocore.config import Config

    print(f"Uploading {R2_KEY} to Cloudflare R2 bucket '{R2_BUCKET}'...")
    s3 = boto3.client(
        "s3",
        endpoint_url=R2_ENDPOINT_URL,
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        region_name="auto",
        config=Config(
            retries={"max_attempts": 3, "mode": "standard"},
            connect_timeout=10,
            read_timeout=30,
            s3={"addressing_style": "path"},
        ),
    )

    s3.put_object(
        Bucket=R2_BUCKET,
        Key=R2_KEY,
        Body=payload_json.encode("utf-8"),
        ContentType="application/json",
        CacheControl="public, max-age=3600",
    )
    print("✓ Successfully uploaded to Cloudflare R2!")


def main():
    wti_url = (
        f"https://api.eia.gov/v2/petroleum/pri/spt/data/"
        f"?api_key={EIA_API_KEY}&frequency=daily&data[0]=value"
        f"&facets[series][]=RWTC&sort[0][column]=period&sort[0][direction]=desc&length=260"
    )
    hh_url = (
        f"https://api.eia.gov/v2/natural-gas/pri/fut/data/"
        f"?api_key={EIA_API_KEY}&frequency=daily&data[0]=value"
        f"&facets[series][]=RNGWHHD&sort[0][column]=period&sort[0][direction]=desc&length=260"
    )

    wti = fetch_eia_series(wti_url, "WTI Crude", "WTI", "$US/bbl", "Cushing, OK Spot Price")
    henry_hub = fetch_eia_series(hh_url, "Henry Hub", "NG", "$US/MMBtu", "Natural Gas Spot Price")

    payload = {
        "wti": wti,
        "henry_hub": henry_hub,
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }

    json_str = json.dumps(payload, indent=2)

    # Save local copy
    os.makedirs("data", exist_ok=True)
    local_path = os.path.join("data", "commodity_benchmarks.json")
    with open(local_path, "w") as f:
        f.write(json_str)
    print(f"✓ Saved local copy to {local_path}")

    # Upload to R2
    upload_to_r2(json_str)

    print(f"\nSummary:")
    print(f"• WTI Crude: ${wti['price']} {wti['unit']} (52W: ${wti['low_52w']} - ${wti['high_52w']}) [{wti['date']}]")
    print(f"• Henry Hub: ${henry_hub['price']} {henry_hub['unit']} (52W: ${henry_hub['low_52w']} - ${henry_hub['high_52w']}) [{henry_hub['date']}]")


if __name__ == "__main__":
    main()
