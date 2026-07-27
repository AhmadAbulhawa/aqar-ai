from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests


DEFAULT_USER_AGENT = (
    "AqarAI-CatalogImporter/1.0 "
    "(+official public catalog synchronization; contact: local administrator)"
)


@dataclass(frozen=True)
class CachedResponse:
    url: str
    status_code: int
    headers: dict[str, str]
    content: bytes

    @property
    def text(self) -> str:
        content_type = self.headers.get("content-type", "")
        charset = "utf-8"
        if "charset=" in content_type.lower():
            charset = content_type.lower().split("charset=", 1)[1].split(";", 1)[0].strip()
        try:
            return self.content.decode(charset, errors="replace")
        except LookupError:
            return self.content.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.text)


class CachedHttpClient:
    """Conservative public HTTP client with bounded retries and run-local cache."""

    retry_statuses = {408, 425, 429, 500, 502, 503, 504}

    def __init__(
        self,
        *,
        user_agent: str = DEFAULT_USER_AGENT,
        min_interval_seconds: float = 0.5,
        connect_timeout_seconds: float = 10.0,
        read_timeout_seconds: float = 45.0,
        max_retries: int = 3,
        backoff_seconds: float = 0.75,
        respect_robots: bool = True,
    ) -> None:
        self.user_agent = user_agent
        self.min_interval_seconds = max(0.0, min_interval_seconds)
        self.timeout = (connect_timeout_seconds, read_timeout_seconds)
        self.max_retries = max(0, max_retries)
        self.backoff_seconds = max(0.0, backoff_seconds)
        self.respect_robots = respect_robots
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept-Language": "en,ar;q=0.9",
            }
        )
        self._cache: dict[str, CachedResponse] = {}
        self._last_request_at: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser | bool] = {}
        self.request_count = 0
        self.cache_hit_count = 0

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> CachedHttpClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _cache_key(
        self,
        method: str,
        url: str,
        *,
        json_body: Any = None,
        headers: dict[str, str] | None = None,
    ) -> str:
        payload = {
            "method": method.upper(),
            "url": url,
            "json": json_body,
            "headers": {
                key.lower(): value
                for key, value in sorted((headers or {}).items())
                if key.lower() in {"accept", "content-type", "origin", "referer"}
            },
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()

    def _throttle(self, url: str) -> None:
        host = urlparse(url).netloc.lower()
        elapsed = time.monotonic() - self._last_request_at.get(host, 0.0)
        remaining = self.min_interval_seconds - elapsed
        if remaining > 0:
            time.sleep(remaining)
        self._last_request_at[host] = time.monotonic()

    def _retry_delay(self, attempt: int, headers: requests.structures.CaseInsensitiveDict) -> float:
        retry_after = headers.get("Retry-After")
        if retry_after:
            try:
                return min(60.0, max(0.0, float(retry_after)))
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(retry_after)
                    return min(60.0, max(0.0, retry_at.timestamp() - time.time()))
                except (TypeError, ValueError, OverflowError):
                    pass
        return min(30.0, self.backoff_seconds * (2**attempt))

    def _robots_origin(self, url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"

    def ensure_allowed(self, url: str) -> None:
        if not self.respect_robots:
            return
        origin = self._robots_origin(url)
        policy = self._robots.get(origin)
        if policy is None:
            robots_url = urljoin(origin + "/", "robots.txt")
            try:
                response = self._request(
                    "GET",
                    robots_url,
                    cache=True,
                    check_robots=False,
                    max_bytes=512_000,
                )
            except requests.RequestException as exc:
                raise RuntimeError(
                    f"Could not verify robots policy for {origin}: {exc}"
                ) from exc
            if response.status_code in {401, 403}:
                self._robots[origin] = False
                raise PermissionError(f"Robots policy is not publicly readable for {origin}")
            if response.status_code == 200:
                parser = RobotFileParser()
                parser.set_url(robots_url)
                parser.parse(response.text.splitlines())
                self._robots[origin] = parser
                policy = parser
            elif 400 <= response.status_code < 500:
                self._robots[origin] = True
                policy = True
            else:
                raise RuntimeError(
                    f"Could not verify robots policy for {origin}: HTTP "
                    f"{response.status_code}"
                )
        if policy is False:
            raise PermissionError(f"Public crawling is not allowed for {origin}")
        if isinstance(policy, RobotFileParser) and not policy.can_fetch(self.user_agent, url):
            raise PermissionError(f"robots.txt disallows catalog request: {url}")

    def _request(
        self,
        method: str,
        url: str,
        *,
        json_body: Any = None,
        headers: dict[str, str] | None = None,
        cache: bool = True,
        check_robots: bool = True,
        max_bytes: int | None = None,
    ) -> CachedResponse:
        if check_robots:
            self.ensure_allowed(url)
        key = self._cache_key(method, url, json_body=json_body, headers=headers)
        if cache and key in self._cache:
            self.cache_hit_count += 1
            return self._cache[key]

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self._throttle(url)
            try:
                raw = self.session.request(
                    method,
                    url,
                    json=json_body,
                    headers=headers,
                    timeout=self.timeout,
                    allow_redirects=True,
                    stream=max_bytes is not None,
                )
                self.request_count += 1
                if raw.status_code in self.retry_statuses and attempt < self.max_retries:
                    time.sleep(self._retry_delay(attempt, raw.headers))
                    raw.close()
                    continue
                if max_bytes is None:
                    content = raw.content
                else:
                    content_length = raw.headers.get("Content-Length")
                    if content_length and int(content_length) > max_bytes:
                        raw.close()
                        raise ValueError(
                            f"Response exceeds {max_bytes} bytes: {url}"
                        )
                    chunks: list[bytes] = []
                    total = 0
                    for chunk in raw.iter_content(chunk_size=64 * 1024):
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > max_bytes:
                            raw.close()
                            raise ValueError(
                                f"Response exceeds {max_bytes} bytes: {url}"
                            )
                        chunks.append(chunk)
                    content = b"".join(chunks)
                result = CachedResponse(
                    url=raw.url,
                    status_code=raw.status_code,
                    headers={key.lower(): value for key, value in raw.headers.items()},
                    content=content,
                )
                raw.close()
                if cache:
                    self._cache[key] = result
                return result
            except (requests.RequestException, OSError) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    raise
                time.sleep(min(30.0, self.backoff_seconds * (2**attempt)))
        if last_error:
            raise last_error
        raise RuntimeError(f"Request failed without a response: {url}")

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        cache: bool = True,
        max_bytes: int | None = None,
    ) -> CachedResponse:
        return self._request(
            "GET",
            url,
            headers=headers,
            cache=cache,
            max_bytes=max_bytes,
        )

    def get_text(self, url: str, *, max_bytes: int = 5_000_000) -> str:
        response = self.get(url, max_bytes=max_bytes)
        if not 200 <= response.status_code < 300:
            raise RuntimeError(f"HTTP {response.status_code} while reading {url}")
        content_type = response.headers.get("content-type", "").lower()
        if "html" not in content_type and "json" not in content_type and "text" not in content_type:
            raise ValueError(f"Unexpected document content type {content_type!r}: {url}")
        return response.text

    def get_json(self, url: str, *, max_bytes: int = 5_000_000) -> Any:
        response = self.get(url, max_bytes=max_bytes)
        if not 200 <= response.status_code < 300:
            raise RuntimeError(f"HTTP {response.status_code} while reading {url}")
        return response.json()

    def post_json(
        self,
        url: str,
        payload: Any,
        *,
        headers: dict[str, str] | None = None,
        max_bytes: int = 5_000_000,
    ) -> Any:
        merged_headers = {"Content-Type": "application/json", **(headers or {})}
        response = self._request(
            "POST",
            url,
            json_body=payload,
            headers=merged_headers,
            max_bytes=max_bytes,
        )
        if not 200 <= response.status_code < 300:
            raise RuntimeError(f"HTTP {response.status_code} while posting to {url}")
        return response.json()

