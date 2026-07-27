from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from app.catalog_import.models import DiscoveredProduct


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CHECKPOINT_DIR = PROJECT_ROOT / "var" / "catalog_import" / "checkpoints"


@dataclass
class ImportCheckpoint:
    adapter_id: str
    run_id: str
    references: list[dict[str, Any]] = field(default_factory=list)
    completed_keys: list[str] = field(default_factory=list)
    failed_keys: list[str] = field(default_factory=list)

    def discovered_products(self) -> list[DiscoveredProduct]:
        return [DiscoveredProduct(**value) for value in self.references]


class CheckpointStore:
    def __init__(self, directory: Path = DEFAULT_CHECKPOINT_DIR) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def path_for(self, adapter_id: str) -> Path:
        return self.directory / f"{adapter_id}.json"

    def load(self, adapter_id: str) -> ImportCheckpoint | None:
        path = self.path_for(adapter_id)
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("adapter_id") != adapter_id:
            raise ValueError(f"Checkpoint adapter mismatch: {path}")
        return ImportCheckpoint(**payload)

    def save(self, checkpoint: ImportCheckpoint) -> Path:
        path = self.path_for(checkpoint.adapter_id)
        payload = json.dumps(asdict(checkpoint), ensure_ascii=False, indent=2, sort_keys=True)
        temporary_name: str | None = None
        try:
            with NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                prefix=f".{checkpoint.adapter_id}_",
                suffix=".partial",
                dir=self.directory,
                delete=False,
            ) as temporary:
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_name = temporary.name
            os.replace(temporary_name, path)
            temporary_name = None
        finally:
            if temporary_name:
                Path(temporary_name).unlink(missing_ok=True)
        return path

    def create(
        self,
        *,
        adapter_id: str,
        run_id: str,
        references: list[DiscoveredProduct],
    ) -> ImportCheckpoint:
        checkpoint = ImportCheckpoint(
            adapter_id=adapter_id,
            run_id=run_id,
            references=[asdict(reference) for reference in references],
        )
        self.save(checkpoint)
        return checkpoint

    def clear(self, adapter_id: str) -> None:
        self.path_for(adapter_id).unlink(missing_ok=True)


def reference_key(reference: DiscoveredProduct) -> str:
    return reference.source_product_id or reference.url
