from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.catalog_import.models import NormalizedProduct, ProductImportOutcome
from app.catalog_import.parsing import canonicalize_url, normalized_identity
from app.catalog_import.policy import EligibilityDecision, default_room_types


@dataclass(frozen=True)
class ExistingMatch:
    row: sqlite3.Row | None
    conflict_ids: tuple[int, ...] = ()


class CatalogRepository:
    """Store-scoped, priority-ordered catalog upsert operations."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _columns(self, connection: sqlite3.Connection) -> set[str]:
        return {
            row["name"]
            for row in connection.execute("PRAGMA table_info(furniture)").fetchall()
        }

    def find_existing(
        self,
        connection: sqlite3.Connection,
        *,
        store_slug: str,
        source_product_id: str | None,
        canonical_url: str,
        identity: str,
    ) -> ExistingMatch:
        matches: list[sqlite3.Row] = []
        seen_ids: set[int] = set()
        lookups = []
        if source_product_id:
            lookups.append(("source_product_id", source_product_id))
        if canonical_url:
            lookups.append(("canonical_product_url", canonical_url))
        if identity:
            lookups.append(("normalized_identity", identity))

        for column, value in lookups:
            row = connection.execute(
                f"SELECT * FROM furniture WHERE source_store_slug = ? "
                f"AND {column} = ? ORDER BY id LIMIT 1",
                (store_slug, value),
            ).fetchone()
            if row is not None and int(row["id"]) not in seen_ids:
                matches.append(row)
                seen_ids.add(int(row["id"]))

        if not matches:
            return ExistingMatch(None)
        return ExistingMatch(
            matches[0],
            tuple(int(row["id"]) for row in matches[1:]),
        )

    def _identity(self, product: NormalizedProduct) -> str:
        variants = [
            value
            for value in (
                product.model_number,
                product.color,
                product.subcategory,
            )
            if value
        ]
        return normalized_identity(
            name=product.name,
            width_cm=product.width_cm,
            depth_cm=product.depth_cm,
            height_cm=product.height_cm,
            variant_values=variants,
        )

    def _values(
        self,
        product: NormalizedProduct,
        *,
        decision: EligibilityDecision,
        image_filename: str | None,
        image_sha256: str | None,
        run_id: str,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        canonical_url = canonicalize_url(product.canonical_url)
        room_types = product.room_types or default_room_types(product.category)
        availability = product.availability is True
        catalog_status = "active" if decision.eligible else "shop_only"
        reason = ",".join(decision.reasons) or None
        source_metadata = {
            **product.source_metadata,
            "_catalog_adapter_id": product.adapter_id,
        }
        return {
            "name": product.name,
            "category": product.category,
            "style": product.style,
            "color": product.color,
            "material": product.material,
            "width_cm": float(product.width_cm) if product.width_cm is not None else None,
            "depth_cm": float(product.depth_cm) if product.depth_cm is not None else None,
            "height_cm": float(product.height_cm) if product.height_cm is not None else None,
            "price": float(product.current_price),
            "image_filename": image_filename,
            "product_url": canonical_url,
            "store_name": product.source_store,
            "currency": product.currency,
            "room_types": json.dumps(room_types, ensure_ascii=False),
            "tags": json.dumps(product.tags, ensure_ascii=False),
            "availability": int(availability),
            "catalog_status": catalog_status,
            "quarantine_reason": reason,
            "catalog_updated_at": now,
            "source_store_slug": product.source_store_slug,
            "source_product_id": product.source_product_id,
            "canonical_product_url": canonical_url,
            "name_ar": product.name_ar,
            "name_en": product.name_en,
            "subcategory": product.subcategory,
            "original_price": (
                float(product.original_price)
                if product.original_price is not None
                else None
            ),
            "source_image_url": product.source_image_url,
            "additional_source_image_urls": json.dumps(
                product.image_urls[1:], ensure_ascii=False
            ),
            "diameter_cm": (
                float(product.diameter_cm) if product.diameter_cm is not None else None
            ),
            "description": product.description,
            "brand": product.brand,
            "imported_at": now,
            "source_updated_at": (
                product.source_updated_at.isoformat()
                if product.source_updated_at is not None
                else None
            ),
            "design_eligible": int(decision.eligible),
            "mounting_type": product.mounting_type,
            "is_product_set": int(product.is_product_set),
            "included_pieces": json.dumps(product.included_pieces, ensure_ascii=False),
            "normalized_identity": self._identity(product),
            "image_sha256": image_sha256,
            "last_seen_import_run": run_id,
            "model_number": product.model_number,
            "source_metadata": json.dumps(
                source_metadata,
                ensure_ascii=False,
                sort_keys=True,
            ),
        }

    @staticmethod
    def _comparable(value: Any) -> Any:
        if isinstance(value, float):
            return round(value, 6)
        return value

    def upsert(
        self,
        product: NormalizedProduct,
        *,
        decision: EligibilityDecision,
        image_filename: str | None,
        image_sha256: str | None,
        run_id: str,
        dry_run: bool = False,
    ) -> ProductImportOutcome:
        with closing(self.connect()) as connection, connection:
            available_columns = self._columns(connection)
            values = {
                key: value
                for key, value in self._values(
                    product,
                    decision=decision,
                    image_filename=image_filename,
                    image_sha256=image_sha256,
                    run_id=run_id,
                ).items()
                if key in available_columns
            }
            match = self.find_existing(
                connection,
                store_slug=product.source_store_slug,
                source_product_id=product.source_product_id,
                canonical_url=str(values["canonical_product_url"]),
                identity=str(values["normalized_identity"]),
            )
            if match.conflict_ids:
                return ProductImportOutcome(
                    action="skipped",
                    canonical_url=product.canonical_url,
                    source_product_id=product.source_product_id,
                    furniture_id=int(match.row["id"]) if match.row else None,
                    reason=(
                        "duplicate_identity_conflict:"
                        + ",".join(str(value) for value in match.conflict_ids)
                    ),
                    design_eligible=False,
                    category=product.category,
                    availability=product.availability,
                )

            if match.row is None:
                if dry_run:
                    return ProductImportOutcome(
                        action="inserted",
                        canonical_url=product.canonical_url,
                        source_product_id=product.source_product_id,
                        design_eligible=decision.eligible,
                        category=product.category,
                        availability=product.availability,
                    )
                columns = list(values)
                placeholders = ", ".join("?" for _ in columns)
                cursor = connection.execute(
                    f"INSERT INTO furniture ({', '.join(columns)}) VALUES ({placeholders})",
                    [values[column] for column in columns],
                )
                return ProductImportOutcome(
                    action="inserted",
                    canonical_url=product.canonical_url,
                    source_product_id=product.source_product_id,
                    furniture_id=int(cursor.lastrowid),
                    design_eligible=decision.eligible,
                    category=product.category,
                    availability=product.availability,
                )

            existing = match.row
            if (
                dry_run
                and image_filename is None
                and image_sha256 is not None
                and "image_sha256" in existing.keys()
                and existing["image_sha256"] == image_sha256
            ):
                # Dry runs validate but intentionally do not write media.  Reuse
                # the matching persisted filename for an honest idempotency diff.
                values["image_filename"] = existing["image_filename"]
            compare_keys = [
                key
                for key in values
                if key not in {"catalog_updated_at", "imported_at", "last_seen_import_run"}
            ]
            changed = any(
                self._comparable(existing[key]) != self._comparable(values[key])
                for key in compare_keys
            )
            if dry_run:
                action = "updated" if changed else "unchanged"
            elif changed:
                update_values = dict(values)
                update_values.pop("imported_at", None)
                assignments = ", ".join(f"{key} = ?" for key in update_values)
                connection.execute(
                    f"UPDATE furniture SET {assignments} WHERE id = ?",
                    [*update_values.values(), int(existing["id"])],
                )
                action = "updated"
            else:
                connection.execute(
                    "UPDATE furniture SET last_seen_import_run = ? WHERE id = ?",
                    (run_id, int(existing["id"])),
                )
                action = "unchanged"

            return ProductImportOutcome(
                action=action,
                canonical_url=product.canonical_url,
                source_product_id=product.source_product_id,
                furniture_id=int(existing["id"]),
                design_eligible=decision.eligible,
                category=product.category,
                availability=product.availability,
            )

    def deactivate_missing(
        self,
        *,
        store_slug: str,
        adapter_id: str,
        run_id: str,
    ) -> int:
        with closing(self.connect()) as connection, connection:
            cursor = connection.execute(
                """
                UPDATE furniture
                SET availability = 0,
                    design_eligible = 0,
                    catalog_status = 'shop_only',
                    quarantine_reason = 'source_missing_from_complete_sync',
                    catalog_updated_at = CURRENT_TIMESTAMP
                WHERE source_store_slug = ?
                  AND json_extract(
                        COALESCE(source_metadata, '{}'),
                        '$._catalog_adapter_id'
                      ) = ?
                  AND COALESCE(last_seen_import_run, '') != ?
                  AND catalog_status != 'quarantined'
                """,
                (store_slug, adapter_id, run_id),
            )
            return max(0, int(cursor.rowcount))

    def count_store(self, store_slug: str) -> int:
        with closing(self.connect()) as connection, connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM furniture WHERE source_store_slug = ?",
                    (store_slug,),
                ).fetchone()[0]
            )
