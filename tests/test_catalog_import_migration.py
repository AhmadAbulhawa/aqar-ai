from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

import app.database as database


LEGACY_FURNITURE_SQL = """
CREATE TABLE furniture (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    style TEXT NOT NULL,
    color TEXT NOT NULL,
    material TEXT,
    width_cm REAL NOT NULL CHECK(width_cm > 0),
    depth_cm REAL NOT NULL CHECK(depth_cm > 0),
    height_cm REAL NOT NULL CHECK(height_cm > 0),
    price REAL NOT NULL CHECK(price >= 0),
    image_filename TEXT,
    product_url TEXT NOT NULL,
    store_name TEXT,
    currency TEXT NOT NULL DEFAULT 'JOD',
    room_types TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]',
    availability INTEGER NOT NULL DEFAULT 1 CHECK(availability IN (0, 1)),
    catalog_status TEXT NOT NULL DEFAULT 'active'
        CHECK(catalog_status IN ('active', 'quarantined')),
    quarantine_reason TEXT,
    catalog_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""


class CatalogCompatibilityMigrationTests(unittest.TestCase):
    def test_legacy_catalog_is_rebuilt_without_losing_ids_or_inventing_values(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "legacy.db"
            with closing(sqlite3.connect(db_path)) as connection, connection:
                connection.execute(LEGACY_FURNITURE_SQL)
                connection.execute(
                    """
                    INSERT INTO furniture (
                        id, name, category, style, color, material,
                        width_cm, depth_cm, height_cm, price, image_filename,
                        product_url, store_name, currency, room_types, tags,
                        availability, catalog_status
                    ) VALUES (
                        7, 'Legacy Sofa', 'sofa', 'modern', 'Blue', NULL,
                        200, 90, 80, 499, 'legacy.jpg',
                        'https://example.test/item?utm_source=old',
                        'IKEA Jordan', 'JOD', '["living_room"]', '[]', 1, 'active'
                    )
                    """
                )

            original_path = database.DB_PATH
            database.DB_PATH = db_path
            try:
                database.migrate_furniture_catalog_compatibility()
            finally:
                database.DB_PATH = original_path

            with closing(sqlite3.connect(db_path)) as connection:
                connection.row_factory = sqlite3.Row
                row = connection.execute(
                    "SELECT * FROM furniture WHERE id = 7"
                ).fetchone()
                columns = {
                    value["name"]
                    for value in connection.execute(
                        "PRAGMA table_info(furniture)"
                    ).fetchall()
                }
                table_sql = connection.execute(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'furniture'"
                ).fetchone()[0]

            self.assertIsNotNone(row)
            self.assertEqual(row["name"], "Legacy Sofa")
            self.assertEqual(row["source_store_slug"], "ikea_jo")
            self.assertEqual(row["canonical_product_url"], "https://example.test/item")
            self.assertEqual(row["design_eligible"], 1)
            self.assertIsNone(row["diameter_cm"])
            self.assertIsNone(row["model_number"])
            self.assertEqual(row["source_metadata"], "{}")
            self.assertIn("shop_only", table_sql)
            self.assertTrue(
                {
                    "source_product_id",
                    "source_image_url",
                    "original_price",
                    "model_number",
                    "source_updated_at",
                    "mounting_type",
                }.issubset(columns)
            )


if __name__ == "__main__":
    unittest.main()
