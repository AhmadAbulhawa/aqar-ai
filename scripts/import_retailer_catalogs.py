from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app.database as database
from app.catalog_import.checkpoint import CheckpointStore
from app.catalog_import.http import CachedHttpClient
from app.catalog_import.media import ProductImageStore
from app.catalog_import.registry import adapter_registry
from app.catalog_import.repository import CatalogRepository
from app.catalog_import.runner import CatalogImportRunner, ImportOptions, run_many


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Synchronize official Jordan retailer catalogs into Aqar AI."
    )
    parser.add_argument(
        "--store",
        action="append",
        choices=[
            "all",
            "leaders_jo",
            "shahwan_jo",
            "ikea_jo_ceiling_lights",
            "midas_jo",
        ],
        help="Retailer adapter to run; repeat for multiple stores (default: all).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Parse and validate without database or media writes.")
    parser.add_argument("--limit", type=int, help="Process at most this many discovered products per store.")
    parser.add_argument("--category", action="append", default=[], help="Adapter source or normalized category filter; repeat as needed.")
    parser.add_argument("--full-sync", action="store_true", help="Deactivate rows absent from a complete, successful, unlimited store sync.")
    resume = parser.add_mutually_exclusive_group()
    resume.add_argument("--resume", action="store_true", help="Resume the interrupted adapter checkpoint.")
    resume.add_argument("--retry-failed", action="store_true", help="Retry only failed products from the adapter checkpoint.")
    parser.add_argument("--db-path", type=Path, default=database.DB_PATH)
    parser.add_argument("--media-dir", type=Path, default=PROJECT_ROOT / "uploads" / "furniture")
    parser.add_argument("--state-dir", type=Path, default=PROJECT_ROOT / "var" / "catalog_import")
    parser.add_argument("--min-request-interval", type=float, default=0.5)
    parser.add_argument("--max-retries", type=int, default=3)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    effective_argv = list(argv) if argv is not None else sys.argv[1:]
    args = parser.parse_args(effective_argv)
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be greater than zero")
    if args.full_sync and args.limit is not None:
        parser.error("--full-sync cannot be combined with --limit")
    if (args.resume or args.retry_failed) and len(args.store or []) != 1:
        parser.error("--resume/--retry-failed requires exactly one --store")

    source_db_path = args.db_path.resolve()
    dry_run_directory: tempfile.TemporaryDirectory[str] | None = None
    if args.dry_run:
        dry_run_directory = tempfile.TemporaryDirectory(prefix="aqar_catalog_dry_run_")
        db_path = Path(dry_run_directory.name) / source_db_path.name
        if source_db_path.exists():
            shutil.copy2(source_db_path, db_path)
    else:
        db_path = source_db_path
    database.DB_PATH = db_path
    database.init_db()

    registry = adapter_registry()
    requested = args.store or ["all"]
    selected_names = list(registry) if "all" in requested else list(dict.fromkeys(requested))
    adapters = [registry[name] for name in selected_names]
    state_dir = args.state_dir.resolve()
    command = " ".join(
        ["python", "scripts/import_retailer_catalogs.py", *effective_argv]
    )
    options = ImportOptions(
        dry_run=args.dry_run,
        limit=args.limit,
        category_filter=tuple(args.category),
        full_sync=args.full_sync,
        resume=args.resume,
        retry_failed=args.retry_failed,
        command=command,
    )

    with CachedHttpClient(
        min_interval_seconds=args.min_request_interval,
        max_retries=args.max_retries,
    ) as client:
        runner = CatalogImportRunner(
            repository=CatalogRepository(db_path),
            image_store=ProductImageStore(args.media_dir.resolve()),
            checkpoint_store=CheckpointStore(state_dir / "checkpoints"),
            report_dir=state_dir / "reports",
            http_client=client,
        )
        reports, failures = run_many(runner, adapters, options)

    print(
        json.dumps(
            {
                "reports": [asdict(report) for report in reports],
                "store_failures": failures,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    exit_code = 1 if failures or any(report.parsing_failures for report in reports) else 0
    if dry_run_directory is not None:
        dry_run_directory.cleanup()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
