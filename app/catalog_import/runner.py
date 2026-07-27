from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence
from uuid import uuid4

from app.catalog_import.base import RetailerAdapter, SkipProduct
from app.catalog_import.checkpoint import (
    CheckpointStore,
    ImportCheckpoint,
    reference_key,
)
from app.catalog_import.http import CachedHttpClient
from app.catalog_import.media import ProductImageStore
from app.catalog_import.models import ImportReport, ProductImportOutcome
from app.catalog_import.policy import evaluate_design_eligibility
from app.catalog_import.repository import CatalogRepository


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT_DIR = PROJECT_ROOT / "var" / "catalog_import" / "reports"


@dataclass(frozen=True)
class ImportOptions:
    dry_run: bool = False
    limit: int | None = None
    category_filter: tuple[str, ...] = ()
    full_sync: bool = False
    resume: bool = False
    retry_failed: bool = False
    command: str = ""


class CatalogImportRunner:
    def __init__(
        self,
        *,
        repository: CatalogRepository,
        image_store: ProductImageStore,
        checkpoint_store: CheckpointStore | None = None,
        report_dir: Path = DEFAULT_REPORT_DIR,
        http_client: CachedHttpClient | None = None,
    ) -> None:
        self.repository = repository
        self.image_store = image_store
        self.checkpoint_store = checkpoint_store or CheckpointStore()
        self.report_dir = Path(report_dir)
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self.http_client = http_client

    def _discover(
        self,
        adapter: RetailerAdapter,
        client: CachedHttpClient,
        options: ImportOptions,
    ) -> tuple[ImportCheckpoint, list]:
        existing = self.checkpoint_store.load(adapter.adapter_id)
        if options.resume or options.retry_failed:
            if existing is None:
                raise ValueError(f"No checkpoint exists for {adapter.adapter_id}")
            references = existing.discovered_products()
            if options.retry_failed:
                failed = set(existing.failed_keys)
                references = [
                    reference
                    for reference in references
                    if reference_key(reference) in failed
                ]
                existing.completed_keys = [
                    key for key in existing.completed_keys if key not in failed
                ]
                existing.failed_keys = []
            return existing, references

        references = list(adapter.discover(client, options.category_filter))
        adapter.last_discovered_products = len(references)
        if options.limit is not None:
            references = references[: options.limit]
        run_id = (
            f"{adapter.adapter_id}_"
            f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_"
            f"{uuid4().hex[:8]}"
        )
        checkpoint = self.checkpoint_store.create(
            adapter_id=adapter.adapter_id,
            run_id=run_id,
            references=references,
        )
        return checkpoint, references

    def run(self, adapter: RetailerAdapter, options: ImportOptions) -> ImportReport:
        started = datetime.now(timezone.utc)
        report = ImportReport(
            adapter_id=adapter.adapter_id,
            source_store=adapter.source_store,
            source_store_slug=adapter.source_store_slug,
            started_at=started.isoformat(),
            command=options.command,
            dry_run=options.dry_run,
            full_sync=options.full_sync,
            category_filter=list(options.category_filter),
            official_category_pages=list(
                adapter.selected_category_pages(options.category_filter).values()
            ),
            category_mapping=dict(adapter.category_mapping),
        )

        owns_client = self.http_client is None
        client = self.http_client or CachedHttpClient()
        try:
            checkpoint, references = self._discover(adapter, client, options)
            report.discovered_products = int(
                getattr(adapter, "last_discovered_products", len(references))
            )
            report.pages_scanned = int(
                getattr(adapter, "last_discovery_pages_scanned", 0)
            )
            completed = set(checkpoint.completed_keys)
            failed = set(checkpoint.failed_keys)

            for reference in references:
                key = reference_key(reference)
                if key in completed:
                    continue
                try:
                    product = adapter.parse_product(reference, client)
                    if product.adapter_id != adapter.adapter_id:
                        raise ValueError("Adapter returned a mismatched adapter_id")
                    if product.source_store_slug != adapter.source_store_slug:
                        raise ValueError("Adapter returned a mismatched store slug")
                    if product.currency != "JOD":
                        raise ValueError(f"Unsupported currency {product.currency!r}")
                    if product.current_price < 0:
                        raise ValueError("Current price cannot be negative")

                    image_filename = None
                    image_sha256 = None
                    image_downloaded = False
                    image_reused = False
                    image_failed = False
                    has_valid_image = False
                    if product.source_image_url:
                        try:
                            validated = self.image_store.fetch_and_validate(
                                product.source_image_url,
                                client,
                            )
                            has_valid_image = True
                            image_sha256 = validated.sha256
                            if not options.dry_run:
                                stored = self.image_store.store_validated(
                                    validated,
                                    store_slug=adapter.source_store_slug,
                                )
                                image_filename = stored.filename
                                image_sha256 = stored.sha256
                                image_downloaded = stored.downloaded
                                image_reused = stored.reused
                        except Exception as exc:
                            image_failed = True
                            report.errors.append(
                                {
                                    "url": product.source_image_url,
                                    "stage": "image",
                                    "error": f"{type(exc).__name__}: {exc}",
                                }
                            )

                    decision = evaluate_design_eligibility(
                        product,
                        has_valid_local_image=has_valid_image,
                    )
                    outcome = self.repository.upsert(
                        product,
                        decision=decision,
                        image_filename=image_filename,
                        image_sha256=image_sha256,
                        run_id=checkpoint.run_id,
                        dry_run=options.dry_run,
                    )
                    outcome.image_downloaded = image_downloaded
                    outcome.image_reused = image_reused
                    outcome.image_failed = image_failed or not bool(product.source_image_url)
                    if outcome.reason is None and decision.reasons:
                        outcome.reason = ",".join(decision.reasons)
                    if outcome.reason and "duplicate_identity_conflict" in outcome.reason:
                        report.duplicate_count += 1
                    report.record(outcome)
                    completed.add(key)
                    failed.discard(key)
                except SkipProduct as exc:
                    outcome = ProductImportOutcome(
                        action="skipped",
                        canonical_url=reference.url,
                        source_product_id=reference.source_product_id,
                        reason=str(exc),
                        category=reference.normalized_category,
                        availability=reference.availability_hint,
                    )
                    report.record(outcome)
                    completed.add(key)
                    failed.discard(key)
                except Exception as exc:
                    outcome = ProductImportOutcome(
                        action="failed",
                        canonical_url=reference.url,
                        source_product_id=reference.source_product_id,
                        reason=f"{type(exc).__name__}: {exc}",
                        category=reference.normalized_category,
                        availability=reference.availability_hint,
                    )
                    report.record(outcome)
                    report.errors.append(
                        {
                            "url": reference.url,
                            "stage": "product",
                            "error": outcome.reason,
                        }
                    )
                    failed.add(key)
                checkpoint.completed_keys = sorted(completed)
                checkpoint.failed_keys = sorted(failed)
                self.checkpoint_store.save(checkpoint)

            complete_unlimited_sync = (
                options.full_sync
                and not options.dry_run
                and options.limit is None
                and not options.category_filter
                and not failed
                and report.parsing_failures == 0
            )
            if complete_unlimited_sync:
                report.deactivated_products = self.repository.deactivate_missing(
                    store_slug=adapter.source_store_slug,
                    adapter_id=adapter.adapter_id,
                    run_id=checkpoint.run_id,
                )

            report.finished_at = datetime.now(timezone.utc).isoformat()
            self._write_report(report)
            if not failed:
                self.checkpoint_store.clear(adapter.adapter_id)
            return report
        finally:
            if owns_client:
                client.close()

    def _write_report(self, report: ImportReport) -> Path:
        timestamp = report.started_at.replace(":", "").replace("+00:00", "Z")
        path = self.report_dir / f"{report.adapter_id}_{timestamp}.json"
        path.write_text(
            json.dumps(asdict(report), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return path


def run_many(
    runner: CatalogImportRunner,
    adapters: Sequence[RetailerAdapter],
    options: ImportOptions,
) -> tuple[list[ImportReport], list[dict[str, str]]]:
    reports: list[ImportReport] = []
    failures: list[dict[str, str]] = []
    for adapter in adapters:
        try:
            reports.append(runner.run(adapter, options))
        except Exception as exc:
            failures.append(
                {
                    "adapter_id": adapter.adapter_id,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    return reports, failures
