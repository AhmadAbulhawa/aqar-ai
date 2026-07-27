import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "aqar_ai.db"


SCHEMA_MIGRATIONS = (
    (
        "20260714_00_nullable_room_budget",
        "Rebuild legacy room_requests so budget is optional",
    ),
    (
        "20260714_01_catalog_quarantine",
        "Add catalog status and quarantine semantically invalid products",
    ),
    (
        "20260714_02_immutable_selections",
        "Add immutable selections, selection items, and generated designs",
    ),
    (
        "20260714_03_template_candidates",
        "Treat source-linked template products as candidates",
    ),
    (
        "20260720_04_catalog_import_compatibility",
        "Add nullable imported metadata, Shop-only status, and store-scoped identities",
    ),
)


CATALOG_METADATA_COLUMNS = {
    "source_store_slug": "TEXT",
    "source_product_id": "TEXT",
    "canonical_product_url": "TEXT",
    "name_ar": "TEXT",
    "name_en": "TEXT",
    "subcategory": "TEXT",
    "original_price": "REAL CHECK(original_price IS NULL OR original_price >= 0)",
    "source_image_url": "TEXT",
    "additional_source_image_urls": "TEXT NOT NULL DEFAULT '[]'",
    "diameter_cm": "REAL CHECK(diameter_cm IS NULL OR diameter_cm > 0)",
    "description": "TEXT",
    "brand": "TEXT",
    "model_number": "TEXT",
    "source_metadata": "TEXT NOT NULL DEFAULT '{}'",
    "imported_at": "TIMESTAMP",
    "source_updated_at": "TIMESTAMP",
    "design_eligible": (
        "INTEGER NOT NULL DEFAULT 1 CHECK(design_eligible IN (0, 1))"
    ),
    "mounting_type": "TEXT",
    "is_product_set": (
        "INTEGER NOT NULL DEFAULT 0 CHECK(is_product_set IN (0, 1))"
    ),
    "included_pieces": "TEXT NOT NULL DEFAULT '[]'",
    "normalized_identity": "TEXT",
    "image_sha256": "TEXT",
    "last_seen_import_run": "TEXT",
}


KNOWN_SOURCE_STORE_SLUGS = {
    "IKEA Jordan": "ikea_jo",
    "Leaders Center": "leaders_jo",
}


TRACKING_QUERY_PARAMETERS = {
    "dclid",
    "fbclid",
    "gclid",
    "msclkid",
    "srsltid",
}

REQUIRED_SCHEMA_COLUMNS = {
    "furniture": {
        "id", "name", "category", "style", "color", "material",
        "width_cm", "depth_cm", "height_cm", "price", "image_filename",
        "product_url", "store_name", "currency", "room_types", "tags",
        "availability", "catalog_status", "quarantine_reason",
        "catalog_updated_at", *CATALOG_METADATA_COLUMNS,
    },
    "room_requests": {
        "id", "image_filename", "original_filename", "content_type",
        "room_type", "budget", "style", "preferred_colors",
        "room_length_cm", "room_width_cm", "room_height_cm",
    },
    "design_templates": {
        "id", "name", "room_type", "style", "size_category",
        "layout_type", "wall_color_family", "floor_color_family",
        "floor_material", "natural_light_level", "room_openness",
        "density_target", "focal_point", "description",
        "preview_image_filename", "source_name", "source_url",
        "source_image_url", "template_type", "tags", "is_active",
        "imported_at", "created_at", "updated_at",
    },
    "design_template_products": {
        "id", "template_id", "category", "quantity", "role", "priority",
        "preferred_style", "preferred_colors", "exact_product_id",
    },
    "design_template_source_products": {
        "id", "template_id", "source_product_name", "source_product_url",
        "source_product_image_url", "matched_furniture_id", "match_status",
        "matched_by", "created_at", "updated_at",
    },
    "schema_migrations": {"version", "description", "applied_at"},
    "design_selections": {
        "id", "room_request_id", "template_id", "mode", "status",
        "selection_signature", "blueprint_signature", "catalog_signature",
        "room_analysis_signature", "blueprint_json",
        "composition_validation_json", "created_at",
    },
    "design_selection_items": {
        "id", "selection_id", "product_id", "quantity", "selection_role",
        "slot_key", "slot_keys_json", "repeat_group_id", "assignment_json",
        "product_snapshot_json", "created_at",
    },
    "generated_designs": {
        "id", "room_request_id", "selection_id", "generated_image_filename",
        "manifest_json", "compliance_status", "compliance_json", "model_name",
        "created_at",
    },
}

REQUIRED_SCHEMA_INDEXES = {
    "idx_furniture_catalog_status",
    "idx_furniture_source_product_unique",
    "idx_furniture_canonical_url_unique",
    "idx_furniture_normalized_identity_unique",
    "idx_design_selections_room",
    "idx_generated_designs_selection",
    "idx_design_templates_source_url",
    "idx_design_templates_room_type_active",
    "idx_template_source_products_template",
    "idx_template_source_products_match_status",
}

REQUIRED_SCHEMA_TRIGGERS = {
    "trg_selection_items_no_update",
    "trg_selection_items_no_delete",
    "trg_selections_immutable_contract",
}


def get_connection():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


# =========================================================
# Furniture table schema
# =========================================================

FURNITURE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS furniture (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        category TEXT NOT NULL,
        style TEXT,
        color TEXT,
        material TEXT,
        width_cm REAL CHECK(width_cm IS NULL OR width_cm > 0),
        depth_cm REAL CHECK(depth_cm IS NULL OR depth_cm > 0),
        height_cm REAL CHECK(height_cm IS NULL OR height_cm > 0),
        price REAL NOT NULL CHECK(price >= 0),
        image_filename TEXT,
        product_url TEXT NOT NULL,
        store_name TEXT,
        currency TEXT NOT NULL DEFAULT 'JOD',
        room_types TEXT NOT NULL,
        tags TEXT NOT NULL DEFAULT '[]',
        availability INTEGER NOT NULL DEFAULT 1 CHECK(availability IN (0, 1)),
        catalog_status TEXT NOT NULL DEFAULT 'active'
            CHECK(catalog_status IN ('active', 'quarantined', 'shop_only')),
        quarantine_reason TEXT,
        catalog_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        source_store_slug TEXT,
        source_product_id TEXT,
        canonical_product_url TEXT,
        name_ar TEXT,
        name_en TEXT,
        subcategory TEXT,
        original_price REAL CHECK(original_price IS NULL OR original_price >= 0),
        source_image_url TEXT,
        additional_source_image_urls TEXT NOT NULL DEFAULT '[]',
        diameter_cm REAL CHECK(diameter_cm IS NULL OR diameter_cm > 0),
        description TEXT,
        brand TEXT,
        model_number TEXT,
        source_metadata TEXT NOT NULL DEFAULT '{}',
        imported_at TIMESTAMP,
        source_updated_at TIMESTAMP,
        design_eligible INTEGER NOT NULL DEFAULT 1
            CHECK(design_eligible IN (0, 1)),
        mounting_type TEXT,
        is_product_set INTEGER NOT NULL DEFAULT 0
            CHECK(is_product_set IN (0, 1)),
        included_pieces TEXT NOT NULL DEFAULT '[]',
        normalized_identity TEXT,
        image_sha256 TEXT,
        last_seen_import_run TEXT,
        CHECK(catalog_status != 'shop_only' OR design_eligible = 0)
    )
"""


ROOM_REQUESTS_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS room_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        image_filename TEXT NOT NULL,
        original_filename TEXT NOT NULL,
        content_type TEXT NOT NULL,
        room_type TEXT NOT NULL,
        budget REAL CHECK(budget IS NULL OR budget > 0),
        style TEXT,
        preferred_colors TEXT,
        room_length_cm REAL CHECK(room_length_cm IS NULL OR room_length_cm > 0),
        room_width_cm REAL CHECK(room_width_cm IS NULL OR room_width_cm > 0),
        room_height_cm REAL CHECK(room_height_cm IS NULL OR room_height_cm > 0)
    )
"""


DESIGN_TEMPLATES_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS design_templates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        room_type TEXT NOT NULL,
        style TEXT,
        size_category TEXT,
        layout_type TEXT,
        wall_color_family TEXT,
        floor_color_family TEXT,
        floor_material TEXT,
        natural_light_level TEXT,
        room_openness TEXT,
        density_target TEXT,
        focal_point TEXT,
        description TEXT,
        preview_image_filename TEXT,
        source_name TEXT,
        source_url TEXT,
        source_image_url TEXT,
        template_type TEXT NOT NULL DEFAULT 'inspiration',
        tags TEXT NOT NULL DEFAULT '[]',
        is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
        imported_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
"""


DESIGN_TEMPLATE_PRODUCTS_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS design_template_products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        template_id INTEGER NOT NULL,
        category TEXT NOT NULL,
        quantity INTEGER NOT NULL DEFAULT 1 CHECK(quantity > 0),
        role TEXT,
        priority TEXT,
        preferred_style TEXT,
        preferred_colors TEXT,
        exact_product_id INTEGER,
        FOREIGN KEY (template_id)
            REFERENCES design_templates(id)
            ON DELETE CASCADE,
        FOREIGN KEY (exact_product_id)
            REFERENCES furniture(id)
            ON DELETE SET NULL
    )
"""


DESIGN_TEMPLATE_SOURCE_PRODUCTS_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS design_template_source_products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        template_id INTEGER NOT NULL,
        source_product_name TEXT,
        source_product_url TEXT NOT NULL,
        source_product_image_url TEXT,
        matched_furniture_id INTEGER,
        match_status TEXT NOT NULL DEFAULT 'missing',
        matched_by TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(template_id, source_product_url),
        FOREIGN KEY (template_id)
            REFERENCES design_templates(id)
            ON DELETE CASCADE,
        FOREIGN KEY (matched_furniture_id)
            REFERENCES furniture(id)
            ON DELETE SET NULL
    )
"""


DESIGN_SELECTIONS_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS design_selections (
        id TEXT PRIMARY KEY,
        room_request_id INTEGER NOT NULL,
        template_id INTEGER,
        mode TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'locked'
            CHECK(status IN ('locked', 'rendered', 'invalidated')),
        selection_signature TEXT NOT NULL,
        blueprint_signature TEXT,
        catalog_signature TEXT NOT NULL,
        room_analysis_signature TEXT NOT NULL,
        blueprint_json TEXT,
        composition_validation_json TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(room_request_id, selection_signature),
        FOREIGN KEY (room_request_id) REFERENCES room_requests(id),
        FOREIGN KEY (template_id) REFERENCES design_templates(id)
    )
"""


DESIGN_SELECTION_ITEMS_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS design_selection_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        selection_id TEXT NOT NULL,
        product_id INTEGER NOT NULL,
        quantity INTEGER NOT NULL CHECK(quantity > 0),
        selection_role TEXT NOT NULL,
        slot_key TEXT,
        slot_keys_json TEXT NOT NULL DEFAULT '[]',
        repeat_group_id TEXT,
        assignment_json TEXT NOT NULL DEFAULT '[]',
        product_snapshot_json TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(selection_id, product_id),
        FOREIGN KEY (selection_id)
            REFERENCES design_selections(id) ON DELETE CASCADE,
        FOREIGN KEY (product_id) REFERENCES furniture(id)
    )
"""


GENERATED_DESIGNS_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS generated_designs (
        id TEXT PRIMARY KEY,
        room_request_id INTEGER NOT NULL,
        selection_id TEXT,
        generated_image_filename TEXT NOT NULL,
        manifest_json TEXT NOT NULL,
        compliance_status TEXT NOT NULL,
        compliance_json TEXT NOT NULL,
        model_name TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (room_request_id) REFERENCES room_requests(id),
        FOREIGN KEY (selection_id) REFERENCES design_selections(id)
    )
"""


# =========================================================
# Helpers
# =========================================================

def get_table_columns(cursor: sqlite3.Cursor, table_name: str) -> set[str]:
    cursor.execute(f"PRAGMA table_info({table_name})")
    rows = cursor.fetchall()
    return {row["name"] for row in rows}


def migration_value(
    columns: set[str],
    column_name: str,
    default_expression: str
) -> str:
    if column_name in columns:
        return column_name
    return default_expression


def canonicalize_catalog_url(raw_url: str | None) -> str | None:
    """Remove known tracking data without discarding meaningful variants."""

    if raw_url is None:
        return None

    value = str(raw_url).strip()
    if not value:
        return None

    try:
        parsed = urlsplit(value)
    except ValueError:
        return value

    filtered_query = [
        (key, query_value)
        for key, query_value in parse_qsl(parsed.query, keep_blank_values=True)
        if not (
            key.casefold() in TRACKING_QUERY_PARAMETERS
            or key.casefold().startswith("utm_")
        )
    ]

    return urlunsplit((
        parsed.scheme.casefold(),
        parsed.netloc.casefold(),
        parsed.path,
        urlencode(filtered_query, doseq=True),
        "",
    ))


def add_missing_columns(
    cursor: sqlite3.Cursor,
    table_name: str,
    columns: dict[str, str]
) -> None:
    existing_columns = get_table_columns(cursor, table_name)

    for column_name, column_definition in columns.items():
        if column_name in existing_columns:
            continue

        cursor.execute(
            f"""
            ALTER TABLE {table_name}
            ADD COLUMN {column_name} {column_definition}
            """
        )


def database_schema_is_current() -> bool:
    """Return True when startup can safely avoid every migration write.

    Uvicorn reload and multi-worker modes import the application in spawned
    processes.  A fully migrated database must therefore be checked through a
    read-only connection before any CREATE/UPDATE statement is attempted.
    """

    if not DB_PATH.exists():
        return False

    try:
        connection = sqlite3.connect(
            f"{DB_PATH.resolve().as_uri()}?mode=ro",
            uri=True,
        )
        connection.row_factory = sqlite3.Row
        try:
            objects = connection.execute(
                "SELECT type, name FROM sqlite_master "
                "WHERE type IN ('table', 'index', 'trigger')"
            ).fetchall()
            object_names = {
                object_type: {
                    row["name"]
                    for row in objects
                    if row["type"] == object_type
                }
                for object_type in ("table", "index", "trigger")
            }

            if not set(REQUIRED_SCHEMA_COLUMNS) <= object_names["table"]:
                return False
            if not REQUIRED_SCHEMA_INDEXES <= object_names["index"]:
                return False
            if not REQUIRED_SCHEMA_TRIGGERS <= object_names["trigger"]:
                return False

            for table_name, required_columns in REQUIRED_SCHEMA_COLUMNS.items():
                columns = {
                    row["name"]
                    for row in connection.execute(
                        f"PRAGMA table_info({table_name})"
                    ).fetchall()
                }
                if not required_columns <= columns:
                    return False

            room_columns = connection.execute(
                "PRAGMA table_info(room_requests)"
            ).fetchall()
            budget_column = next(
                (row for row in room_columns if row["name"] == "budget"),
                None,
            )
            if budget_column is None or int(budget_column["notnull"]) != 0:
                return False

            furniture_columns = {
                row["name"]: row
                for row in connection.execute(
                    "PRAGMA table_info(furniture)"
                ).fetchall()
            }
            for nullable_column in (
                "style",
                "color",
                "width_cm",
                "depth_cm",
                "height_cm",
            ):
                column = furniture_columns.get(nullable_column)
                if column is None or int(column["notnull"]) != 0:
                    return False

            furniture_table = connection.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'table' AND name = 'furniture'"
            ).fetchone()
            if (
                furniture_table is None
                or "'shop_only'" not in str(furniture_table["sql"])
            ):
                return False

            applied_migrations = {
                row["version"]
                for row in connection.execute(
                    "SELECT version FROM schema_migrations"
                ).fetchall()
            }
            required_migrations = {version for version, _ in SCHEMA_MIGRATIONS}
            if not required_migrations <= applied_migrations:
                return False

            suspect_catalog_row = connection.execute(
                """
                SELECT 1
                FROM furniture
                WHERE (
                    (
                        category = 'rug' AND lower(name) LIKE '%tea towel%'
                    ) OR (
                        category = 'sofa' AND width_cm < 70
                    )
                ) AND (
                    catalog_status != 'quarantined'
                    OR availability != 0
                    OR quarantine_reason IS NOT CASE
                        WHEN lower(name) LIKE '%tea towel%'
                            THEN 'semantic_category_mismatch: textile is not a rug'
                        WHEN category = 'sofa' AND width_cm < 70
                            THEN 'implausible_dimensions: sofa width below 70 cm'
                    END
                    OR catalog_updated_at IS NULL
                )
                LIMIT 1
                """
            ).fetchone()
            if suspect_catalog_row is not None:
                return False

            legacy_template_role = connection.execute(
                "SELECT 1 FROM design_template_products "
                "WHERE role = 'source_product' LIMIT 1"
            ).fetchone()
            return legacy_template_role is None
        finally:
            connection.close()
    except sqlite3.DatabaseError:
        # Missing/corrupt/legacy objects should follow the existing migration
        # path, which will report the concrete SQLite error if repair fails.
        return False


def backup_database_for_migration(label: str) -> Path | None:
    """Create a consistent SQLite backup before a destructive migration."""

    if not DB_PATH.exists():
        return None

    # Custom/isolated databases keep their own migration backups beside the
    # database.  For production DB_PATH this remains ``<project>/backups``.
    backup_dir = DB_PATH.resolve().parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    target = backup_dir / f"aqar_ai_{label}_{timestamp}.db"

    source_connection = sqlite3.connect(DB_PATH)
    backup_connection = sqlite3.connect(target)
    try:
        source_connection.backup(backup_connection)
    finally:
        backup_connection.close()
        source_connection.close()

    return target


def furniture_catalog_compatibility_is_current() -> bool:
    """Check the furniture table shape without opening a write connection."""

    if not DB_PATH.exists():
        return True

    connection = sqlite3.connect(
        f"{DB_PATH.resolve().as_uri()}?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    try:
        columns = {
            row["name"]: row
            for row in connection.execute(
                "PRAGMA table_info(furniture)"
            ).fetchall()
        }
        if not columns:
            return True
        if not set(CATALOG_METADATA_COLUMNS) <= set(columns):
            return False
        if any(
            int(columns[column_name]["notnull"]) != 0
            for column_name in (
                "style",
                "color",
                "width_cm",
                "depth_cm",
                "height_cm",
            )
        ):
            return False

        table_row = connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'table' AND name = 'furniture'"
        ).fetchone()
        return bool(
            table_row is not None
            and "'shop_only'" in str(table_row["sql"])
        )
    finally:
        connection.close()


def migrate_furniture_catalog_compatibility() -> None:
    """Rebuild legacy furniture storage without inventing catalog facts.

    SQLite cannot relax NOT NULL or replace a CHECK constraint in place. This
    migration therefore uses a backed-up, transactional table rebuild while
    retaining furniture IDs so all template and immutable-selection foreign
    keys continue to point at the same products.
    """

    if not DB_PATH.exists() or furniture_catalog_compatibility_is_current():
        return

    backup_database_for_migration("before_catalog_compatibility")
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.create_function(
        "canonical_catalog_url",
        1,
        canonicalize_catalog_url,
    )

    try:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("BEGIN IMMEDIATE")
        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(furniture)"
            ).fetchall()
        }
        if not columns:
            connection.rollback()
            return

        def value(column_name: str, default: str = "NULL") -> str:
            return column_name if column_name in columns else default

        image_filename = value("image_filename")
        if "product_url" in columns:
            product_url = "product_url"
        elif "purchase_url" in columns:
            product_url = "purchase_url"
        else:
            product_url = "'https://example.com'"

        if "store_name" in columns:
            store_name = "store_name"
        elif "supplier" in columns:
            store_name = "supplier"
        else:
            store_name = "NULL"

        if "room_types" in columns:
            room_types = "COALESCE(room_types, '[]')"
        elif "room_type" in columns:
            room_types = (
                "CASE WHEN room_type IS NULL OR trim(room_type) = '' "
                "THEN '[]' ELSE '[\"' || room_type || '\"]' END"
            )
        else:
            room_types = "'[]'"

        availability = (
            "CASE WHEN availability = 0 THEN 0 ELSE 1 END"
            if "availability" in columns
            else "1"
        )
        catalog_status = (
            "CASE WHEN catalog_status IN "
            "('active', 'quarantined', 'shop_only') "
            "THEN catalog_status ELSE 'active' END"
            if "catalog_status" in columns
            else "'active'"
        )
        known_store_slug = (
            f"CASE {store_name} "
            + " ".join(
                f"WHEN '{store}' THEN '{slug}'"
                for store, slug in KNOWN_SOURCE_STORE_SLUGS.items()
            )
            + " ELSE NULL END"
        )
        source_store_slug = (
            "COALESCE(NULLIF(trim(source_store_slug), ''), "
            f"{known_store_slug})"
            if "source_store_slug" in columns
            else known_store_slug
        )
        canonical_url = (
            "COALESCE(NULLIF(trim(canonical_product_url), ''), "
            f"canonical_catalog_url({product_url}))"
            if "canonical_product_url" in columns
            else f"canonical_catalog_url({product_url})"
        )
        raw_design_eligible = (
            "CASE WHEN design_eligible = 0 THEN 0 ELSE 1 END"
            if "design_eligible" in columns
            else "1"
        )
        design_eligible = (
            f"CASE WHEN ({availability}) = 1 "
            f"AND ({catalog_status}) = 'active' "
            f"THEN ({raw_design_eligible}) ELSE 0 END"
        )

        dimension = lambda column_name: (
            f"CASE WHEN {column_name} IS NULL OR {column_name} > 0 "
            f"THEN {column_name} ELSE NULL END"
            if column_name in columns
            else "NULL"
        )
        non_negative = lambda column_name: (
            f"CASE WHEN {column_name} IS NULL OR {column_name} >= 0 "
            f"THEN {column_name} ELSE NULL END"
            if column_name in columns
            else "NULL"
        )

        target_columns = [
            "id", "name", "category", "style", "color", "material",
            "width_cm", "depth_cm", "height_cm", "price",
            "image_filename", "product_url", "store_name", "currency",
            "room_types", "tags", "availability", "catalog_status",
            "quarantine_reason", "catalog_updated_at",
            "source_store_slug", "source_product_id",
            "canonical_product_url", "name_ar", "name_en", "subcategory",
            "original_price", "source_image_url",
            "additional_source_image_urls", "diameter_cm", "description",
            "brand", "model_number", "source_metadata",
            "imported_at", "source_updated_at", "design_eligible",
            "mounting_type", "is_product_set", "included_pieces",
            "normalized_identity", "image_sha256", "last_seen_import_run",
        ]
        select_expressions = [
            value("id"),
            value("name", "'Unknown furniture'"),
            value("category", "'sofa'"),
            value("style"),
            value("color"),
            value("material"),
            dimension("width_cm"),
            dimension("depth_cm"),
            dimension("height_cm"),
            value("price", "0"),
            image_filename,
            product_url,
            store_name,
            value("currency", "'JOD'"),
            room_types,
            "COALESCE(tags, '[]')" if "tags" in columns else "'[]'",
            availability,
            catalog_status,
            value("quarantine_reason"),
            value("catalog_updated_at", "CURRENT_TIMESTAMP"),
            source_store_slug,
            value("source_product_id"),
            canonical_url,
            value("name_ar"),
            value("name_en"),
            value("subcategory"),
            non_negative("original_price"),
            value("source_image_url"),
            (
                "COALESCE(additional_source_image_urls, '[]')"
                if "additional_source_image_urls" in columns
                else "'[]'"
            ),
            dimension("diameter_cm"),
            value("description"),
            value("brand"),
            value("model_number"),
            (
                "COALESCE(source_metadata, '{}')"
                if "source_metadata" in columns
                else "'{}'"
            ),
            value("imported_at"),
            value("source_updated_at"),
            design_eligible,
            value("mounting_type"),
            (
                "CASE WHEN is_product_set = 1 THEN 1 ELSE 0 END"
                if "is_product_set" in columns
                else "0"
            ),
            (
                "COALESCE(included_pieces, '[]')"
                if "included_pieces" in columns
                else "'[]'"
            ),
            value("normalized_identity"),
            value("image_sha256"),
            value("last_seen_import_run"),
        ]

        connection.execute("DROP TABLE IF EXISTS furniture_catalog_migration")
        connection.execute(FURNITURE_TABLE_SQL.replace(
            "furniture (",
            "furniture_catalog_migration (",
            1,
        ))
        connection.execute(
            "INSERT INTO furniture_catalog_migration ("
            + ", ".join(target_columns)
            + ") SELECT "
            + ", ".join(select_expressions)
            + " FROM furniture"
        )
        connection.execute("DROP TABLE furniture")
        connection.execute(
            "ALTER TABLE furniture_catalog_migration RENAME TO furniture"
        )

        foreign_key_errors = connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()
        if foreign_key_errors:
            raise sqlite3.IntegrityError(
                "Furniture catalog migration would break foreign keys."
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.close()


def migrate_room_requests_budget_nullable() -> None:
    """Rebuild only legacy room_requests tables whose budget is NOT NULL."""

    if not DB_PATH.exists():
        return

    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    try:
        columns = connection.execute(
            "PRAGMA table_info(room_requests)"
        ).fetchall()
        if not columns:
            return

        budget = next(
            (row for row in columns if row["name"] == "budget"),
            None,
        )
        if budget is None or int(budget["notnull"]) == 0:
            return
    finally:
        connection.close()

    backup_database_for_migration("before_nullable_budget")
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("BEGIN IMMEDIATE")
        existing = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(room_requests)"
            ).fetchall()
        }
        connection.execute(ROOM_REQUESTS_TABLE_SQL.replace(
            "room_requests (", "room_requests_new (", 1
        ))

        target_columns = [
            "id", "image_filename", "original_filename", "content_type",
            "room_type", "budget", "style", "preferred_colors",
            "room_length_cm", "room_width_cm", "room_height_cm",
        ]
        select_values = [
            column if column in existing else "NULL"
            for column in target_columns
        ]
        connection.execute(
            f"INSERT INTO room_requests_new ({', '.join(target_columns)}) "
            f"SELECT {', '.join(select_values)} FROM room_requests"
        )
        connection.execute("DROP TABLE room_requests")
        connection.execute(
            "ALTER TABLE room_requests_new RENAME TO room_requests"
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.close()


# =========================================================
# Furniture migration
# =========================================================

def migrate_furniture_table_if_needed(cursor: sqlite3.Cursor):
    columns = get_table_columns(cursor, "furniture")

    if not columns:
        return

    needs_migration = (
        "image_url" in columns
        or "image_filename" not in columns
        or "room_type" in columns
        or "purchase_url" in columns
        or "supplier" in columns
    )

    if not needs_migration:
        return

    name_expr = migration_value(columns, "name", "'Unknown furniture'")
    category_expr = migration_value(columns, "category", "'sofa'")
    style_expr = migration_value(columns, "style", "NULL")
    color_expr = migration_value(columns, "color", "NULL")
    material_expr = migration_value(columns, "material", "NULL")
    width_expr = migration_value(columns, "width_cm", "NULL")
    depth_expr = migration_value(columns, "depth_cm", "NULL")
    height_expr = migration_value(columns, "height_cm", "NULL")
    price_expr = migration_value(columns, "price", "0")

    image_filename_expr = "image_filename" if "image_filename" in columns else "NULL"

    if "product_url" in columns:
        product_url_expr = "product_url"
    elif "purchase_url" in columns:
        product_url_expr = "purchase_url"
    else:
        product_url_expr = "'https://example.com'"

    if "store_name" in columns:
        store_name_expr = "store_name"
    elif "supplier" in columns:
        store_name_expr = "supplier"
    else:
        store_name_expr = "NULL"

    currency_expr = migration_value(columns, "currency", "'JOD'")

    if "room_types" in columns:
        room_types_expr = "room_types"
    elif "room_type" in columns:
        room_types_expr = """
            CASE
                WHEN room_type IS NULL OR room_type = '' THEN '[]'
                ELSE '["' || room_type || '"]'
            END
        """
    else:
        room_types_expr = "'[]'"

    tags_expr = migration_value(columns, "tags", "'[]'")
    availability_expr = migration_value(columns, "availability", "1")

    cursor.execute("""
        ALTER TABLE furniture
        RENAME TO furniture_old_migration
    """)

    cursor.execute(FURNITURE_TABLE_SQL)

    cursor.execute(f"""
        INSERT INTO furniture (
            id,
            name,
            category,
            style,
            color,
            material,
            width_cm,
            depth_cm,
            height_cm,
            price,
            image_filename,
            product_url,
            store_name,
            currency,
            room_types,
            tags,
            availability
        )
        SELECT
            id,
            {name_expr},
            {category_expr},
            {style_expr},
            {color_expr},
            {material_expr},
            {width_expr},
            {depth_expr},
            {height_expr},
            {price_expr},
            {image_filename_expr},
            {product_url_expr},
            {store_name_expr},
            {currency_expr},
            {room_types_expr},
            {tags_expr},
            {availability_expr}
        FROM furniture_old_migration
    """)

    cursor.execute("""
        DROP TABLE furniture_old_migration
    """)


def ensure_room_request_dimension_columns(cursor: sqlite3.Cursor) -> None:
    add_missing_columns(
        cursor=cursor,
        table_name="room_requests",
        columns={
            "room_length_cm": "REAL CHECK(room_length_cm IS NULL OR room_length_cm > 0)",
            "room_width_cm": "REAL CHECK(room_width_cm IS NULL OR room_width_cm > 0)",
            "room_height_cm": "REAL CHECK(room_height_cm IS NULL OR room_height_cm > 0)"
        }
    )


def ensure_catalog_integrity_columns(cursor: sqlite3.Cursor) -> None:
    add_missing_columns(
        cursor=cursor,
        table_name="furniture",
        columns={
            "catalog_status": (
                "TEXT NOT NULL DEFAULT 'active' "
                "CHECK(catalog_status IN ('active', 'quarantined', 'shop_only'))"
            ),
            "quarantine_reason": "TEXT",
            # SQLite ALTER TABLE accepts only constant defaults. Fresh tables
            # still receive CURRENT_TIMESTAMP from FURNITURE_TABLE_SQL.
            "catalog_updated_at": "TIMESTAMP",
            **CATALOG_METADATA_COLUMNS,
        },
    )

    # Preserve suspect rows for audit, but remove them from all selection paths.
    # The repair predicate prevents a timestamp-only write on every startup.
    cursor.execute(
        """
        UPDATE furniture
        SET catalog_status = 'quarantined',
            availability = 0,
            quarantine_reason = CASE
                WHEN lower(name) LIKE '%tea towel%'
                    THEN 'semantic_category_mismatch: textile is not a rug'
                WHEN category = 'sofa' AND width_cm < 70
                    THEN 'implausible_dimensions: sofa width below 70 cm'
                ELSE quarantine_reason
            END,
            catalog_updated_at = CURRENT_TIMESTAMP
        WHERE (
            (
                category = 'rug' AND lower(name) LIKE '%tea towel%'
            ) OR (
                category = 'sofa' AND width_cm < 70
            )
        ) AND (
            catalog_status != 'quarantined'
            OR availability != 0
            OR quarantine_reason IS NOT CASE
                WHEN lower(name) LIKE '%tea towel%'
                    THEN 'semantic_category_mismatch: textile is not a rug'
                WHEN category = 'sofa' AND width_cm < 70
                    THEN 'implausible_dimensions: sofa width below 70 cm'
            END
            OR catalog_updated_at IS NULL
        )
        """
    )


def ensure_catalog_import_indexes(cursor: sqlite3.Cursor) -> None:
    """Enforce retailer-scoped identity without cross-store deduplication."""

    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_furniture_source_product_unique
        ON furniture(source_store_slug, source_product_id)
        WHERE source_store_slug IS NOT NULL
          AND trim(source_store_slug) != ''
          AND source_product_id IS NOT NULL
          AND trim(source_product_id) != ''
        """
    )
    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_furniture_canonical_url_unique
        ON furniture(source_store_slug, canonical_product_url)
        WHERE source_store_slug IS NOT NULL
          AND trim(source_store_slug) != ''
          AND canonical_product_url IS NOT NULL
          AND trim(canonical_product_url) != ''
        """
    )
    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_furniture_normalized_identity_unique
        ON furniture(source_store_slug, normalized_identity)
        WHERE source_store_slug IS NOT NULL
          AND trim(source_store_slug) != ''
          AND source_product_id IS NULL
          AND canonical_product_url IS NULL
          AND normalized_identity IS NOT NULL
          AND trim(normalized_identity) != ''
        """
    )


def ensure_immutable_selection_triggers(cursor: sqlite3.Cursor) -> None:
    cursor.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_selection_items_no_update
        BEFORE UPDATE ON design_selection_items
        BEGIN
            SELECT RAISE(ABORT, 'design selection items are immutable');
        END
        """
    )
    cursor.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_selection_items_no_delete
        BEFORE DELETE ON design_selection_items
        BEGIN
            SELECT RAISE(ABORT, 'design selection items are immutable');
        END
        """
    )
    cursor.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_selections_immutable_contract
        BEFORE UPDATE ON design_selections
        WHEN NEW.room_request_id IS NOT OLD.room_request_id
          OR NEW.template_id IS NOT OLD.template_id
          OR NEW.mode IS NOT OLD.mode
          OR NEW.selection_signature IS NOT OLD.selection_signature
          OR NEW.blueprint_signature IS NOT OLD.blueprint_signature
          OR NEW.catalog_signature IS NOT OLD.catalog_signature
          OR NEW.room_analysis_signature IS NOT OLD.room_analysis_signature
          OR NEW.blueprint_json IS NOT OLD.blueprint_json
          OR NEW.composition_validation_json IS NOT OLD.composition_validation_json
        BEGIN
            SELECT RAISE(ABORT, 'design selection contract is immutable');
        END
        """
    )


def ensure_corrective_architecture_tables(cursor: sqlite3.Cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            description TEXT NOT NULL,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(DESIGN_SELECTIONS_TABLE_SQL)
    cursor.execute(DESIGN_SELECTION_ITEMS_TABLE_SQL)
    cursor.execute(GENERATED_DESIGNS_TABLE_SQL)
    ensure_immutable_selection_triggers(cursor)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_furniture_catalog_status "
        "ON furniture(catalog_status, availability)"
    )
    cursor.execute(
        """
        UPDATE design_template_products
        SET role = 'source_candidate', priority = 'candidate'
        WHERE role = 'source_product'
        """
    )
    cursor.executemany(
        "INSERT OR IGNORE INTO schema_migrations(version, description) VALUES (?, ?)",
        SCHEMA_MIGRATIONS,
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_design_selections_room "
        "ON design_selections(room_request_id, created_at)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_generated_designs_selection "
        "ON generated_designs(selection_id, created_at)"
    )


def ensure_design_template_columns(cursor: sqlite3.Cursor) -> None:
    """Safely upgrades older MVP design_templates tables in place."""

    add_missing_columns(
        cursor=cursor,
        table_name="design_templates",
        columns={
            "floor_material": "TEXT",
            "room_openness": "TEXT",
            "source_name": "TEXT",
            "source_url": "TEXT",
            "source_image_url": "TEXT",
            "template_type": "TEXT NOT NULL DEFAULT 'inspiration'",
            "imported_at": "TIMESTAMP"
        }
    )


def ensure_design_template_indexes(cursor: sqlite3.Cursor) -> None:
    # SQLite cannot safely add UNIQUE through ALTER TABLE, so use a
    # partial unique index for imported source URLs instead.
    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_design_templates_source_url
        ON design_templates(source_url)
        WHERE source_url IS NOT NULL
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_design_templates_room_type_active
        ON design_templates(room_type, is_active)
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_template_source_products_template
        ON design_template_source_products(template_id)
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_template_source_products_match_status
        ON design_template_source_products(match_status)
        """
    )


# =========================================================
# Init database
# =========================================================

def init_db():
    # Avoid all startup mutation for an already migrated database. This is
    # important on Windows, where Uvicorn --reload imports the app in a spawned
    # child and an unconditional no-op UPDATE can still require write access.
    if database_schema_is_current():
        return

    migrate_room_requests_budget_nullable()
    migrate_furniture_catalog_compatibility()
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(FURNITURE_TABLE_SQL)
        migrate_furniture_table_if_needed(cursor)
        ensure_catalog_integrity_columns(cursor)
        ensure_catalog_import_indexes(cursor)

        cursor.execute(ROOM_REQUESTS_TABLE_SQL)
        ensure_room_request_dimension_columns(cursor)

        cursor.execute(DESIGN_TEMPLATES_TABLE_SQL)
        ensure_design_template_columns(cursor)

        cursor.execute(DESIGN_TEMPLATE_PRODUCTS_TABLE_SQL)
        cursor.execute(DESIGN_TEMPLATE_SOURCE_PRODUCTS_TABLE_SQL)

        ensure_corrective_architecture_tables(cursor)

        ensure_design_template_indexes(cursor)

        connection.commit()

    except sqlite3.OperationalError as exc:
        connection.rollback()
        if "readonly" in str(exc).lower():
            raise RuntimeError(
                "Database initialization requires a schema or catalog update, "
                f"but SQLite cannot write to '{DB_PATH}'. Grant Modify access "
                "to both the database file and its parent directory."
            ) from exc
        raise

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()
