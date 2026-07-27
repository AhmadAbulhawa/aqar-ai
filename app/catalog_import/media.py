from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile

from PIL import Image, UnidentifiedImageError

try:  # Registers AVIF support when the optional project dependency is present.
    import pillow_avif  # noqa: F401
except ImportError:  # pragma: no cover - AVIF then fails normal validation.
    pillow_avif = None

from app.catalog_import.http import CachedHttpClient


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FURNITURE_MEDIA_DIR = PROJECT_ROOT / "uploads" / "furniture"
MAX_PRODUCT_IMAGE_BYTES = 15 * 1024 * 1024

CONTENT_TYPE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/avif": ".avif",
}

FORMAT_EXTENSIONS = {
    "JPEG": ".jpg",
    "PNG": ".png",
    "WEBP": ".webp",
    "AVIF": ".avif",
}


@dataclass(frozen=True)
class ValidatedImage:
    content: bytes
    sha256: str
    extension: str
    width_px: int
    height_px: int


@dataclass(frozen=True)
class StoredImage:
    filename: str
    sha256: str
    downloaded: bool
    reused: bool
    width_px: int
    height_px: int


def validate_image_bytes(
    content: bytes,
    content_type: str | None,
    *,
    max_bytes: int = MAX_PRODUCT_IMAGE_BYTES,
) -> ValidatedImage:
    if not content:
        raise ValueError("Image response is empty")
    if len(content) > max_bytes:
        raise ValueError(f"Image exceeds {max_bytes} bytes")

    normalized_type = str(content_type or "").split(";", 1)[0].strip().lower()
    if normalized_type not in CONTENT_TYPE_EXTENSIONS:
        raise ValueError(f"Unsupported or missing image content type: {normalized_type!r}")

    try:
        with Image.open(BytesIO(content)) as image:
            image_format = str(image.format or "").upper()
            width, height = image.size
            image.verify()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError("Response body is not a valid supported image") from exc

    extension = FORMAT_EXTENSIONS.get(image_format)
    if extension is None:
        raise ValueError(f"Unsupported decoded image format: {image_format!r}")
    expected = CONTENT_TYPE_EXTENSIONS[normalized_type]
    if extension != expected:
        compatible_jpeg = {extension, expected} == {".jpg", ".jpeg"}
        if not compatible_jpeg:
            raise ValueError(
                f"Image content type {normalized_type!r} does not match {image_format!r}"
            )
    if width <= 0 or height <= 0:
        raise ValueError("Image has invalid pixel dimensions")

    return ValidatedImage(
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
        extension=extension,
        width_px=width,
        height_px=height,
    )


class ProductImageStore:
    def __init__(
        self,
        media_dir: Path = FURNITURE_MEDIA_DIR,
        *,
        max_bytes: int = MAX_PRODUCT_IMAGE_BYTES,
    ) -> None:
        self.media_dir = Path(media_dir)
        self.max_bytes = max_bytes
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self._known_hashes: dict[str, str] | None = None

    def _index_existing_images(self) -> dict[str, str]:
        if self._known_hashes is not None:
            return self._known_hashes
        hashes: dict[str, str] = {}
        for path in self.media_dir.iterdir():
            if not path.is_file() or path.name.startswith("."):
                continue
            try:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                continue
            hashes.setdefault(digest, path.name)
        self._known_hashes = hashes
        return hashes

    def fetch_and_validate(
        self,
        source_url: str,
        client: CachedHttpClient,
    ) -> ValidatedImage:
        response = client.get(source_url, max_bytes=self.max_bytes)
        if not 200 <= response.status_code < 300:
            raise ValueError(
                f"Image request returned HTTP {response.status_code}: {source_url}"
            )
        return validate_image_bytes(
            response.content,
            response.headers.get("content-type"),
            max_bytes=self.max_bytes,
        )

    def store_validated(
        self,
        image: ValidatedImage,
        *,
        store_slug: str,
    ) -> StoredImage:
        existing = self._index_existing_images().get(image.sha256)
        if existing:
            return StoredImage(
                filename=existing,
                sha256=image.sha256,
                downloaded=False,
                reused=True,
                width_px=image.width_px,
                height_px=image.height_px,
            )

        safe_slug = "".join(
            character if character.isalnum() or character in {"-", "_"} else "_"
            for character in store_slug.casefold()
        ).strip("_") or "catalog"
        filename = f"{safe_slug}_{image.sha256}{image.extension}"
        target = self.media_dir / filename

        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != image.sha256:
                raise FileExistsError(f"Refusing to overwrite unrelated image: {target}")
            self._index_existing_images()[image.sha256] = filename
            return StoredImage(
                filename=filename,
                sha256=image.sha256,
                downloaded=False,
                reused=True,
                width_px=image.width_px,
                height_px=image.height_px,
            )

        temporary_name: str | None = None
        try:
            with NamedTemporaryFile(
                mode="wb",
                prefix=f".{safe_slug}_",
                suffix=".partial",
                dir=self.media_dir,
                delete=False,
            ) as temporary:
                temporary.write(image.content)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_name = temporary.name
            os.replace(temporary_name, target)
            temporary_name = None
        finally:
            if temporary_name:
                Path(temporary_name).unlink(missing_ok=True)

        self._index_existing_images()[image.sha256] = filename
        return StoredImage(
            filename=filename,
            sha256=image.sha256,
            downloaded=True,
            reused=False,
            width_px=image.width_px,
            height_px=image.height_px,
        )

    def download(
        self,
        source_url: str,
        client: CachedHttpClient,
        *,
        store_slug: str,
    ) -> StoredImage:
        return self.store_validated(
            self.fetch_and_validate(source_url, client),
            store_slug=store_slug,
        )
