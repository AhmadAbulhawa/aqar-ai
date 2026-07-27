from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from urllib.parse import quote

import requests


STORE_NAMES = (
    "IKEA Jordan",
    "Leaders Center",
    "Shahwan Furniture",
    "Midas Jordan",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify catalog API and media HTTP.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8011")
    parser.add_argument("--db-path", type=Path, default=Path("aqar_ai.db"))
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")
    failures: list[str] = []

    catalog_response = requests.get(f"{base_url}/furniture", timeout=30)
    if catalog_response.status_code != 200:
        failures.append(f"catalog_http_{catalog_response.status_code}")
        catalog: list[dict] = []
    else:
        catalog = catalog_response.json()

    store_results: dict[str, dict[str, object]] = {}
    for store_name in STORE_NAMES:
        response = requests.get(
            f"{base_url}/furniture?store_name={quote(store_name)}",
            timeout=30,
        )
        items = response.json() if response.status_code == 200 else []
        exact = all(item.get("store_name") == store_name for item in items)
        if response.status_code != 200 or not exact:
            failures.append(f"store_filter:{store_name}")
        store_results[store_name] = {
            "http_status": response.status_code,
            "product_count": len(items),
            "all_store_names_exact": exact,
        }

    eligible_response = requests.get(
        f"{base_url}/furniture?design_eligible=true",
        timeout=30,
    )
    eligible_items = (
        eligible_response.json() if eligible_response.status_code == 200 else []
    )
    eligibility_exact = all(
        item.get("design_eligible") is True
        and item.get("catalog_status") == "active"
        and item.get("availability") is True
        for item in eligible_items
    )
    if eligible_response.status_code != 200 or not eligibility_exact:
        failures.append("design_eligible_filter")

    shop_response = requests.get(
        f"{base_url}/furniture?catalog_status=shop_only",
        timeout=30,
    )
    shop_items = shop_response.json() if shop_response.status_code == 200 else []
    shop_only_exact = all(
        item.get("catalog_status") == "shop_only"
        and item.get("design_eligible") is False
        for item in shop_items
    )
    if shop_response.status_code != 200 or not shop_only_exact:
        failures.append("shop_only_filter")

    connection = sqlite3.connect(args.db_path)
    try:
        filenames = [
            str(row[0])
            for row in connection.execute(
                """
                SELECT image_filename FROM furniture
                WHERE source_metadata LIKE '%_catalog_adapter_id%'
                  AND image_filename IS NOT NULL
                ORDER BY id
                """
            ).fetchall()
        ]
    finally:
        connection.close()

    media_statuses: dict[str, int] = {}
    for filename in filenames:
        response = requests.get(
            f"{base_url}/uploads/furniture/{quote(filename)}",
            timeout=30,
        )
        media_statuses[filename] = response.status_code
        if (
            response.status_code != 200
            or not response.headers.get("content-type", "").startswith("image/")
        ):
            failures.append(f"media:{filename}")

    result = {
        "catalog_http_status": catalog_response.status_code,
        "catalog_product_count": len(catalog),
        "store_filters": store_results,
        "design_eligible_filter": {
            "http_status": eligible_response.status_code,
            "product_count": len(eligible_items),
            "all_rows_exact": eligibility_exact,
        },
        "shop_only_filter": {
            "http_status": shop_response.status_code,
            "product_count": len(shop_items),
            "all_rows_exact": shop_only_exact,
        },
        "media_http_200_count": sum(
            status == 200 for status in media_statuses.values()
        ),
        "media_checked_count": len(media_statuses),
        "failures": failures,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
