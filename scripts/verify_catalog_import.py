from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from PIL import Image


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify persisted catalog-import rows.")
    parser.add_argument("--db-path", type=Path, default=Path("aqar_ai.db"))
    parser.add_argument(
        "--media-dir",
        type=Path,
        default=Path("uploads") / "furniture",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    connection = sqlite3.connect(args.db_path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT * FROM furniture
            WHERE source_metadata LIKE '%_catalog_adapter_id%'
            ORDER BY id
            """
        ).fetchall()
        store_counts = [
            dict(row)
            for row in connection.execute(
                """
                SELECT source_store_slug,
                       COUNT(*) AS product_count,
                       SUM(design_eligible) AS design_eligible_count,
                       SUM(catalog_status = 'shop_only') AS shop_only_count
                FROM furniture
                WHERE source_metadata LIKE '%_catalog_adapter_id%'
                GROUP BY source_store_slug
                ORDER BY source_store_slug
                """
            ).fetchall()
        ]
        duplicate_groups = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM (
                    SELECT source_store_slug, source_product_id
                    FROM furniture
                    WHERE source_store_slug IS NOT NULL
                      AND source_product_id IS NOT NULL
                    GROUP BY source_store_slug, source_product_id
                    HAVING COUNT(*) > 1
                )
                """
            ).fetchone()[0]
        )
        total_count = int(
            connection.execute("SELECT COUNT(*) FROM furniture").fetchone()[0]
        )
        shop_only_design_eligible_violations = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM furniture
                WHERE source_metadata LIKE '%_catalog_adapter_id%'
                  AND catalog_status = 'shop_only'
                  AND design_eligible != 0
                """
            ).fetchone()[0]
        )
        eligible_state_violations = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM furniture
                WHERE source_metadata LIKE '%_catalog_adapter_id%'
                  AND design_eligible = 1
                  AND (catalog_status != 'active' OR availability IS NOT 1)
                """
            ).fetchone()[0]
        )
    finally:
        connection.close()

    invalid_images: list[dict[str, object]] = []
    for row in rows:
        filename = row["image_filename"]
        path = args.media_dir / str(filename or "")
        if not filename or not path.is_file():
            invalid_images.append({"id": row["id"], "reason": "missing"})
            continue
        try:
            with Image.open(path) as image:
                image.verify()
        except Exception as exc:
            invalid_images.append(
                {"id": row["id"], "reason": f"{type(exc).__name__}: {exc}"}
            )

    result = {
        "database_product_count": total_count,
        "imported_row_count": len(rows),
        "store_counts": store_counts,
        "duplicate_source_identity_groups": duplicate_groups,
        "shop_only_design_eligible_violations": (
            shop_only_design_eligible_violations
        ),
        "eligible_state_violations": eligible_state_violations,
        "validated_local_image_count": len(rows) - len(invalid_images),
        "invalid_images": invalid_images,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return (
        1
        if (
            invalid_images
            or duplicate_groups
            or shop_only_design_eligible_violations
            or eligible_state_violations
        )
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
