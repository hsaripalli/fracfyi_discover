#!/usr/bin/env python3
"""
sync_land_sales.py

Fetches official Alberta Crown Petroleum & Natural Gas (PNG) and Oil Sands (OS)
Public Sale Results (PSR/OSR) and Public Offering Notices (PON/OON) directly from
the Alberta Energy XML endpoints.

Generates:
  1. data/land_sales_activity_2026.json -> Complete 2026 YTD bi-weekly sales, upcoming notices, and parcel results for the Activity Tab.
  2. data/land_sales_discover_macro.json -> 10+ Year historical trends (2015-2026), top land broker rankings, and regional breakdowns for the Discover Tab.

Uploads both datasets to Cloudflare R2.
"""

import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import requests
from dotenv import load_dotenv

# Load local .env
load_dotenv()

R2_BUCKET = os.getenv("R2_BUCKET_NAME", "well-licence-list-ab").strip()
R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID", "").strip()
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID", "").strip()
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY", "").strip()
R2_ENDPOINT_URL = os.getenv("R2_ENDPOINT_URL", "").strip()

if not R2_ENDPOINT_URL and R2_ACCOUNT_ID:
    R2_ENDPOINT_URL = f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com"

PNG_INDEX_URL = "https://content2.energy.alberta.ca/petroleum-and-natural-gas-tenure-public-offerings-and-results"
OS_INDEX_URL = "https://content2.energy.alberta.ca/oil-sands-public-offerings-and-results"
BASE_DOWNLOAD_URL = "https://content2.energy.alberta.ca/download/"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko)"
}


def clean_client_name(name: str) -> str:
    if not name:
        return "Unknown"
    name = re.sub(r"\s+", " ", name).strip()
    return name.title().replace(" Ltd.", " Ltd").replace(" Inc.", " Inc").replace(" Corp.", " Corp")


def fetch_xml(stream_type: str, doc_id: str) -> ET.Element | None:
    """Fetches and parses XML document from Alberta Energy."""
    url = f"{BASE_DOWNLOAD_URL}?s={stream_type}&id={doc_id}&ft=.xml"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code == 200 and r.text.strip():
            content = r.content.decode("utf-8", errors="replace")
            # Strip all xmlns attributes to prevent XML parse errors with unbound prefixes
            clean_xml = re.sub(r'\sxmlns(:\w+)?="[^"]+"', '', content)
            return ET.fromstring(clean_xml)
    except Exception:
        pass
    return None


def parse_sale_xml(root: ET.Element, stream_type: str, sale_id: str) -> dict:
    """Parses a PSR or OSR XML file into structured parcel records."""
    sale_date_raw = root.findtext(".//SaleDate") or sale_id[:8]
    sale_date = sale_date_raw.replace("/", "-")
    if len(sale_date) == 8 and "-" not in sale_date:
        sale_date = f"{sale_date[:4]}-{sale_date[4:6]}-{sale_date[6:]}"

    mineral_type = root.findtext(".//MineralType") or ("OIL SANDS" if stream_type == "os" else "PETROLEUM AND NATURAL GAS")

    parcels = []
    for p in root.findall(".//Parcel"):
        parcel_num = p.findtext("ParcelNumber") or ""
        region = p.findtext("Region") or ("OIL SANDS" if stream_type == "os" else "PLAINS REGION")
        mrt = p.findtext("MRT") or ""
        status = (p.findtext("Status") or "").upper().strip()
        
        try:
            bonus = float((p.findtext("Bonus") or "0").replace(",", ""))
        except ValueError:
            bonus = 0.0
            
        try:
            dollar_per_ha = float((p.findtext("DollarPerHectare") or "0").replace(",", ""))
        except ValueError:
            dollar_per_ha = 0.0

        hectares = (bonus / dollar_per_ha) if (dollar_per_ha > 0 and bonus > 0) else 0.0

        clients = []
        for c in p.findall(".//ClientName"):
            if c.text and c.text.strip():
                clients.append(clean_client_name(c.text))
        if not clients:
            client_single = p.findtext("ClientName")
            if client_single:
                clients.append(clean_client_name(client_single))

        primary_client = " / ".join(clients) if clients else "No Bids"

        parcels.append({
            "parcel_number": parcel_num,
            "region": region,
            "mrt": mrt,
            "status": status,
            "bonus": round(bonus, 2),
            "dollar_per_ha": round(dollar_per_ha, 2),
            "hectares": round(hectares, 2),
            "client": primary_client,
            "clients": clients,
            "stream": stream_type,
        })

    return {
        "sale_id": sale_id,
        "sale_date": sale_date,
        "mineral_type": mineral_type,
        "stream": stream_type,
        "parcels": parcels,
    }


def parse_pon_xml(root: ET.Element, notice_id: str) -> dict:
    """Parses a PON (Public Offering Notice) XML for upcoming auction details."""
    sale_date_raw = root.findtext(".//SaleDate") or notice_id[:8]
    sale_date = sale_date_raw.replace("/", "-")
    if len(sale_date) == 8 and "-" not in sale_date:
        sale_date = f"{sale_date[:4]}-{sale_date[4:6]}-{sale_date[6:]}"
    
    total_hectares = 0.0
    parcel_count = 0
    regions = set()

    for schedule in root.findall(".//Contract"):
        parcel_count += 1
        h_text = schedule.findtext("Hectares") or "0"
        try:
            total_hectares += float(h_text.replace(",", ""))
        except ValueError:
            pass

    for reg in root.findall(".//Region/Name"):
        if reg.text and reg.text.strip():
            regions.add(reg.text.strip())

    return {
        "notice_id": notice_id,
        "sale_date": sale_date,
        "parcel_count": parcel_count,
        "total_hectares": round(total_hectares, 1),
        "regions": sorted(list(regions)),
    }


def discover_sale_ids():
    """Finds all PSR, OSR, and PON IDs from index pages & historical archive."""
    print("Discovering Alberta Energy PNG & Oil Sands sales...", flush=True)
    psr_ids, osr_ids, pon_ids = set(), set(), set()

    try:
        r_png = requests.get(PNG_INDEX_URL, headers=HEADERS, timeout=15)
        psr_ids.update(re.findall(r'id=(20\d{6}PSR)', r_png.text))
        pon_ids.update(re.findall(r'id=(20\d{6}PON)', r_png.text))
    except Exception as e:
        print(f"  ⚠️ Error fetching PNG index: {e}")

    try:
        r_os = requests.get(OS_INDEX_URL, headers=HEADERS, timeout=15)
        osr_ids.update(re.findall(r'id=(20\d{6}OSR)', r_os.text))
    except Exception as e:
        print(f"  ⚠️ Error fetching OS index: {e}")

    def fetch_historical(url: str, year: int):
        p, o, n = set(), set(), set()
        try:
            yr_res = requests.post(url, data={"ddlYear": str(year), "btnApply": "1"}, headers=HEADERS, timeout=10)
            if yr_res.status_code == 200:
                p.update(re.findall(r'id=(20\d{6}PSR)', yr_res.text))
                o.update(re.findall(r'id=(20\d{6}OSR)', yr_res.text))
                n.update(re.findall(r'id=(20\d{6}PON)', yr_res.text))
        except Exception:
            pass
        return p, o, n

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = []
        for y in range(2015, 2027):
            futures.append(pool.submit(fetch_historical, PNG_INDEX_URL, y))
            futures.append(pool.submit(fetch_historical, OS_INDEX_URL, y))
        for f in as_completed(futures):
            p, o, n = f.result()
            psr_ids.update(p)
            osr_ids.update(o)
            pon_ids.update(n)

    psr_list = sorted(list(psr_ids))
    osr_list = sorted(list(osr_ids))
    pon_list = sorted(list(pon_ids))

    print(f"Found: {len(psr_list)} PNG sales, {len(osr_list)} Oil Sands sales, {len(pon_list)} upcoming notices.", flush=True)
    return psr_list, osr_list, pon_list


def build_datasets():
    psr_ids, osr_ids, pon_ids = discover_sale_ids()
    sales_by_date = {}

    def ingest_sale(stream_type: str, sid: str):
        root = fetch_xml(stream_type, sid)
        if root is not None:
            return parse_sale_xml(root, stream_type, sid)
        return None

    # 1. Fetch PNG Sales
    print(f"Ingesting {len(psr_ids)} PNG sales concurrently...", flush=True)
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = {pool.submit(ingest_sale, "png", sid): sid for sid in psr_ids}
        done = 0
        for f in as_completed(futures):
            done += 1
            res = f.result()
            if res:
                sdate = res["sale_date"]
                if sdate not in sales_by_date:
                    sales_by_date[sdate] = {
                        "sale_date": sdate,
                        "png_sale_id": res["sale_id"],
                        "os_sale_id": None,
                        "parcels": [],
                    }
                sales_by_date[sdate]["png_sale_id"] = res["sale_id"]
                sales_by_date[sdate]["parcels"].extend(res["parcels"])

    # 2. Fetch OS Sales
    print(f"Ingesting {len(osr_ids)} Oil Sands sales concurrently...", flush=True)
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = {pool.submit(ingest_sale, "os", sid): sid for sid in osr_ids}
        done = 0
        for f in as_completed(futures):
            done += 1
            res = f.result()
            if res:
                sdate = res["sale_date"]
                if sdate not in sales_by_date:
                    sales_by_date[sdate] = {
                        "sale_date": sdate,
                        "png_sale_id": None,
                        "os_sale_id": res["sale_id"],
                        "parcels": [],
                    }
                sales_by_date[sdate]["os_sale_id"] = res["sale_id"]
                sales_by_date[sdate]["parcels"].extend(res["parcels"])

    # 3. Upcoming Notices
    upcoming_notices = []
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def ingest_pon(nid: str):
        root = fetch_xml("png", nid)
        if root is not None:
            return parse_pon_xml(root, nid)
        return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(ingest_pon, nid) for nid in pon_ids]
        for f in as_completed(futures):
            pon_data = f.result()
            if pon_data and pon_data["sale_date"] >= today_str:
                upcoming_notices.append(pon_data)

    upcoming_notices = sorted(upcoming_notices, key=lambda x: x["sale_date"])
    print(f"Found {len(upcoming_notices)} future auctions scheduled.", flush=True)

    # 4. Compile 2026 Activity Feed
    print("Compiling 2026 Activity Feed...", flush=True)
    sales_2026 = []
    ytd_bonus = 0.0
    ytd_hectares = 0.0
    ytd_parcels_sold = 0

    all_sorted_dates = sorted(sales_by_date.keys(), reverse=True)
    for sdate in all_sorted_dates:
        if not sdate.startswith("2026"):
            continue
        sdata = sales_by_date[sdate]
        parcels = sdata["parcels"]

        accepted_parcels = [p for p in parcels if p["status"] == "ACCEPTED"]
        no_offer_parcels = [p for p in parcels if p["status"] != "ACCEPTED"]

        sale_bonus = sum(p["bonus"] for p in accepted_parcels)
        sale_hectares = sum(p["hectares"] for p in accepted_parcels)
        avg_price_ha = (sale_bonus / sale_hectares) if sale_hectares > 0 else 0.0

        # Top bidder for this sale
        bidder_totals = {}
        for p in accepted_parcels:
            for c in p.get("clients") or [p["client"]]:
                bidder_totals[c] = bidder_totals.get(c, 0.0) + p["bonus"]
        top_bidder_tuple = sorted(bidder_totals.items(), key=lambda x: x[1], reverse=True)
        top_bidder_name = top_bidder_tuple[0][0] if top_bidder_tuple else "None"
        top_bidder_amount = top_bidder_tuple[0][1] if top_bidder_tuple else 0.0

        # Top $/ha parcel
        sorted_by_price_ha = sorted(accepted_parcels, key=lambda x: x["dollar_per_ha"], reverse=True)
        top_parcel = sorted_by_price_ha[0] if sorted_by_price_ha else None

        sales_2026.append({
            "sale_date": sdate,
            "png_sale_id": sdata["png_sale_id"],
            "os_sale_id": sdata["os_sale_id"],
            "total_bonus": round(sale_bonus, 2),
            "total_hectares": round(sale_hectares, 1),
            "avg_dollar_per_ha": round(avg_price_ha, 2),
            "parcels_total": len(parcels),
            "parcels_sold": len(accepted_parcels),
            "parcels_no_offers": len(no_offer_parcels),
            "top_bidder": top_bidder_name,
            "top_bidder_bonus": round(top_bidder_amount, 2),
            "top_parcel": {
                "mrt": top_parcel["mrt"],
                "region": top_parcel["region"],
                "bonus": top_parcel["bonus"],
                "dollar_per_ha": top_parcel["dollar_per_ha"],
                "client": top_parcel["client"],
            } if top_parcel else None,
            "parcels": [
                {
                    "parcel_number": p["parcel_number"],
                    "region": p["region"],
                    "mrt": p["mrt"],
                    "bonus": p["bonus"],
                    "dollar_per_ha": p["dollar_per_ha"],
                    "hectares": p["hectares"],
                    "client": p["client"],
                    "stream": p["stream"],
                }
                for p in sorted(accepted_parcels, key=lambda x: x["bonus"], reverse=True)
            ],
        })

        ytd_bonus += sale_bonus
        ytd_hectares += sale_hectares
        ytd_parcels_sold += len(accepted_parcels)

    activity_payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "ytd_summary": {
            "year": 2026,
            "total_bonus": round(ytd_bonus, 2),
            "total_hectares": round(ytd_hectares, 1),
            "avg_dollar_per_ha": round(ytd_bonus / ytd_hectares, 2) if ytd_hectares > 0 else 0.0,
            "total_parcels_sold": ytd_parcels_sold,
            "sales_count": len(sales_2026),
        },
        "next_upcoming_auction": upcoming_notices[0] if upcoming_notices else None,
        "upcoming_auctions": upcoming_notices[:5],
        "sales": sales_2026,
    }

    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    DATA_DIR = os.path.join(BASE_DIR, "data")
    os.makedirs(DATA_DIR, exist_ok=True)
    activity_path = os.path.join(DATA_DIR, "land_sales_activity_2026.json")
    with open(activity_path, "w") as f:
        json.dump(activity_payload, f, indent=2)
    print(f"✓ Saved 2026 Activity Feed to {activity_path} ({os.path.getsize(activity_path)/1024:.1f} KB)", flush=True)

    # 5. Compile Macro Discover Trends (2015-2026)
    print("Compiling Discover Macro Trends (2015–2026)...", flush=True)
    annual_map = {}
    broker_map = {}
    region_map = {}

    for sdate, sdata in sales_by_date.items():
        year = sdate[:4]
        if year not in annual_map:
            annual_map[year] = {
                "year": int(year),
                "total_bonus": 0.0,
                "png_bonus": 0.0,
                "os_bonus": 0.0,
                "total_hectares": 0.0,
                "parcels_sold": 0,
                "sales_count": 0,
            }
        annual_map[year]["sales_count"] += 1

        for p in sdata["parcels"]:
            if p["status"] == "ACCEPTED":
                b = p["bonus"]
                h = p["hectares"]
                annual_map[year]["total_bonus"] += b
                annual_map[year]["total_hectares"] += h
                annual_map[year]["parcels_sold"] += 1
                if p["stream"] == "os":
                    annual_map[year]["os_bonus"] += b
                else:
                    annual_map[year]["png_bonus"] += b

                # Broker rollup
                clients = p.get("clients") or [p["client"]]
                for client in clients:
                    if client and client != "Unknown" and client != "No Bids":
                        if client not in broker_map:
                            broker_map[client] = {
                                "client_name": client,
                                "total_bonus": 0.0,
                                "parcels_count": 0,
                                "hectares": 0.0,
                                "first_seen": year,
                                "last_seen": year,
                            }
                        broker_map[client]["total_bonus"] += b / len(clients)
                        broker_map[client]["parcels_count"] += 1
                        broker_map[client]["hectares"] += h / len(clients)
                        broker_map[client]["last_seen"] = max(broker_map[client]["last_seen"], year)

                # Regional rollup
                reg = p["region"] or "OTHER"
                region_map[reg] = region_map.get(reg, 0.0) + b

    annual_trends = []
    for yr in sorted(annual_map.keys()):
        item = annual_map[yr]
        item["avg_dollar_per_ha"] = round(item["total_bonus"] / item["total_hectares"], 2) if item["total_hectares"] > 0 else 0.0
        item["total_bonus"] = round(item["total_bonus"], 2)
        item["png_bonus"] = round(item["png_bonus"], 2)
        item["os_bonus"] = round(item["os_bonus"], 2)
        item["total_hectares"] = round(item["total_hectares"], 1)
        annual_trends.append(item)

    top_brokers = sorted(broker_map.values(), key=lambda x: x["total_bonus"], reverse=True)[:30]
    for tb in top_brokers:
        tb["total_bonus"] = round(tb["total_bonus"], 2)
        tb["hectares"] = round(tb["hectares"], 1)
        tb["avg_dollar_per_ha"] = round(tb["total_bonus"] / tb["hectares"], 2) if tb["hectares"] > 0 else 0.0

    total_reg_bonus = sum(region_map.values())
    regional_share = [
        {
            "region": reg,
            "total_bonus": round(val, 2),
            "share_pct": round((val / total_reg_bonus) * 100, 1) if total_reg_bonus > 0 else 0.0,
        }
        for reg, val in sorted(region_map.items(), key=lambda x: x[1], reverse=True)
    ]

    discover_payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "annual_trends": annual_trends,
        "top_land_brokers": top_brokers,
        "regional_breakdown": regional_share,
    }

    discover_path = os.path.join(DATA_DIR, "land_sales_discover_macro.json")
    with open(discover_path, "w") as f:
        json.dump(discover_payload, f, indent=2)
    print(f"✓ Saved Discover Macro Trends to {discover_path} ({os.path.getsize(discover_path)/1024:.1f} KB)", flush=True)

    return activity_path, discover_path


def upload_to_r2(files: list[str]):
    """Uploads formatted JSON payloads to Cloudflare R2."""
    if not (R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY and R2_ENDPOINT_URL):
        print("ℹ R2 credentials missing. Skipping R2 cloud upload.", flush=True)
        return

    import boto3
    from botocore.config import Config

    print(f"Uploading files to Cloudflare R2 bucket '{R2_BUCKET}'...", flush=True)
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

    for file_path in files:
        fname = os.path.basename(file_path)
        key = f"data/{fname}"
        with open(file_path, "rb") as f:
            s3.put_object(
                Bucket=R2_BUCKET,
                Key=key,
                Body=f.read(),
                ContentType="application/json",
                CacheControl="public, max-age=300",
            )
        print(f"✓ Uploaded {file_path} -> s3://{R2_BUCKET}/{key}", flush=True)


if __name__ == "__main__":
    activity_file, discover_file = build_datasets()
    if "--upload-r2" in sys.argv or R2_ACCESS_KEY_ID:
        upload_to_r2([activity_file, discover_file])
