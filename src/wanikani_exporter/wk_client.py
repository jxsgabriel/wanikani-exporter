"""WaniKani API v2 client with pagination, rate-limit handling, and local caching."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
import time
from typing import Any, Callable
import urllib.error
import urllib.parse
import urllib.request

try:
    import requests  # type: ignore[import-not-found]
except ImportError:
    requests = None

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.wanikani.com/v2"
WANIKANI_REVISION = "20170710"
DEFAULT_CACHE_PATH = Path("data/raw_subjects_cache.json")


class WaniKaniAPIError(Exception):
    """Raised when the WaniKani API returns an unrecoverable error."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"WaniKani API error ({status_code}): {message}")
        self.status_code = status_code
        self.message = message


@dataclass
class _SimpleResponse:
    """Lightweight HTTP response compatible with the subset of `requests.Response` used."""

    status_code: int
    text: str
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> dict[str, Any]:
        return json.loads(self.text)


class _UrllibSession:
    """Standard-library HTTP session fallback when `requests` is not installed."""

    def __init__(self) -> None:
        self.headers: dict[str, str] = {}

    def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> _SimpleResponse:
        full_url = url
        if params:
            query_string = urllib.parse.urlencode(params)
            separator = "&" if "?" in full_url else "?"
            full_url = f"{full_url}{separator}{query_string}"

        req = urllib.request.Request(full_url, headers=dict(self.headers), method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                resp_headers = {k: v for k, v in resp.headers.items()}
                return _SimpleResponse(
                    status_code=resp.status,
                    text=body,
                    headers=resp_headers,
                )
        except urllib.error.HTTPError as exc:
            body = (
                exc.read().decode("utf-8", errors="replace")
                if exc.fp is not None
                else str(exc)
            )
            resp_headers = {k: v for k, v in exc.headers.items()} if exc.headers else {}
            return _SimpleResponse(
                status_code=exc.code,
                text=body,
                headers=resp_headers,
            )


class WaniKaniClient:
    """Client for interacting with WaniKani API v2 endpoints."""

    def __init__(
        self,
        api_token: str,
        base_url: str = DEFAULT_BASE_URL,
        session: Any | None = None,
        max_retries: int = 3,
        sleep_fn: Callable[[float], None] = time.sleep,
        timeout: float = 30.0,
    ) -> None:
        token = api_token.strip()
        if not token:
            raise ValueError("A valid WaniKani API v2 token is required.")
        self.api_token = token
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self.sleep_fn = sleep_fn
        self.timeout = timeout
        if session is not None:
            self.session = session
        elif requests is not None:
            self.session = requests.Session()
        else:
            self.session = _UrllibSession()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self.api_token}",
                "Wanikani-Revision": WANIKANI_REVISION,
                "Accept": "application/json",
            }
        )

    def _request_json(
        self,
        url: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Perform a GET request with automatic retry on HTTP 429 rate limits."""
        attempt = 0
        while True:
            response = self.session.get(url, params=params, timeout=self.timeout)
            if response.status_code == 429 and attempt < self.max_retries:
                attempt += 1
                wait_seconds = self._compute_rate_limit_wait(response)
                logger.warning(
                    "Rate limit reached (HTTP 429). Sleeping %.1fs before retry %d/%d...",
                    wait_seconds,
                    attempt,
                    self.max_retries,
                )
                self.sleep_fn(wait_seconds)
                continue

            if not response.ok:
                try:
                    err_payload = response.json()
                    err_msg = err_payload.get("error", response.text)
                except ValueError:
                    err_msg = response.text
                raise WaniKaniAPIError(response.status_code, str(err_msg))

            return response.json()

    @staticmethod
    def _compute_rate_limit_wait(response: Any) -> float:
        """Determine how many seconds to wait from RateLimit-Reset or Retry-After headers."""
        headers = getattr(response, "headers", {}) or {}
        # Case-insensitive header lookup
        lower_headers = {str(k).lower(): v for k, v in headers.items()}

        retry_after = lower_headers.get("retry-after")
        if retry_after:
            try:
                return max(1.0, float(retry_after))
            except ValueError:
                pass

        reset_ts = lower_headers.get("ratelimit-reset")
        if reset_ts:
            try:
                delta = float(reset_ts) - time.time()
                return max(1.0, delta + 0.5)
            except ValueError:
                pass

        return 5.0

    def fetch_paginated(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Fetch all pages of a WaniKani collection endpoint.

        Returns a tuple of (items_list, collection_data_updated_at).
        """
        url: str | None = (
            endpoint
            if endpoint.startswith("http://") or endpoint.startswith("https://")
            else f"{self.base_url}/{endpoint.lstrip('/')}"
        )
        current_params = dict(params) if params else None
        results: list[dict[str, Any]] = []
        latest_updated_at: str | None = None

        while url:
            payload = self._request_json(url, params=current_params)
            # Only pass query params on the initial request; next_url already includes them.
            current_params = None

            data_updated_at = payload.get("data_updated_at")
            if data_updated_at and (
                latest_updated_at is None or data_updated_at > latest_updated_at
            ):
                latest_updated_at = data_updated_at

            page_data = payload.get("data", [])
            if isinstance(page_data, list):
                results.extend(page_data)

            pages = payload.get("pages") or {}
            url = pages.get("next_url")

        return results, latest_updated_at

    def fetch_vocabulary_subjects(
        self,
        levels: list[int] | None = None,
        use_cache: bool = True,
        cache_path: Path | None = DEFAULT_CACHE_PATH,
    ) -> list[dict[str, Any]]:
        """Fetch vocabulary and kana_vocabulary subjects from WaniKani API v2.

        If `use_cache` is True and `cache_path` is provided, loads cached subjects
        and performs an incremental sync via `updated_after` if all needed levels
        are already cached.
        """
        requested_levels = sorted(set(levels)) if levels else None
        cached_subjects: dict[int, dict[str, Any]] = {}
        cached_updated_at: str | None = None
        cached_all_levels = False

        if use_cache and cache_path and cache_path.exists():
            try:
                raw_cache = json.loads(cache_path.read_text(encoding="utf-8"))
                cached_updated_at = raw_cache.get("data_updated_at")
                cached_all_levels = bool(raw_cache.get("all_levels", False))
                for item in raw_cache.get("subjects", []):
                    subj_id = int(item["id"])
                    cached_subjects[subj_id] = item
            except (OSError, ValueError, KeyError) as exc:
                logger.warning("Ignoring unreadable cache file %s: %s", cache_path, exc)
                cached_subjects = {}
                cached_updated_at = None
                cached_all_levels = False

        params: dict[str, Any] = {
            "types": "vocabulary,kana_vocabulary",
            "hidden": "false",
        }

        can_incremental_sync = bool(
            use_cache
            and cached_subjects
            and cached_updated_at
            and (
                cached_all_levels
                or (
                    requested_levels
                    and self._cache_covers_levels(cached_subjects, requested_levels)
                )
            )
        )

        if can_incremental_sync:
            params["updated_after"] = cached_updated_at
            if requested_levels and not cached_all_levels:
                params["levels"] = ",".join(str(lvl) for lvl in requested_levels)
            new_items, new_updated_at = self.fetch_paginated("/subjects", params=params)
            for item in new_items:
                cached_subjects[int(item["id"])] = item
            final_updated_at = new_updated_at or cached_updated_at
            is_all_levels = cached_all_levels
        else:
            if requested_levels:
                params["levels"] = ",".join(str(lvl) for lvl in requested_levels)
            fetched_items, final_updated_at = self.fetch_paginated(
                "/subjects", params=params
            )
            for item in fetched_items:
                cached_subjects[int(item["id"])] = item
            is_all_levels = cached_all_levels or (requested_levels is None)

        if use_cache and cache_path:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_payload = {
                "data_updated_at": final_updated_at,
                "all_levels": is_all_levels,
                "subjects": list(cached_subjects.values()),
            }
            cache_path.write_text(
                json.dumps(cache_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        subjects = [
            item
            for item in cached_subjects.values()
            if item.get("data", {}).get("hidden_at") is None
        ]
        if requested_levels:
            level_set = set(requested_levels)
            subjects = [
                item
                for item in subjects
                if int(item.get("data", {}).get("level", 0)) in level_set
            ]

        subjects.sort(
            key=lambda s: (
                int(s.get("data", {}).get("level", 0)),
                int(s.get("id", 0)),
            )
        )
        return subjects

    @staticmethod
    def _cache_covers_levels(
        cached_subjects: dict[int, dict[str, Any]],
        requested_levels: list[int],
    ) -> bool:
        present_levels = {
            int(item.get("data", {}).get("level", 0))
            for item in cached_subjects.values()
        }
        return all(lvl in present_levels for lvl in requested_levels)

    def fetch_assignments(
        self,
        levels: list[int] | None = None,
        unlocked_only: bool = False,
        min_srs_stage: int | None = None,
    ) -> dict[int, dict[str, Any]]:
        """Fetch user assignments for vocabulary and kana_vocabulary subjects.

        Returns a dictionary mapping `subject_id -> assignment["data"]`.
        """
        params: dict[str, Any] = {
            "subject_types": "vocabulary,kana_vocabulary",
            "hidden": "false",
        }
        if levels:
            params["levels"] = ",".join(str(lvl) for lvl in sorted(set(levels)))
        if unlocked_only:
            params["unlocked"] = "true"
        if min_srs_stage is not None:
            if min_srs_stage < 0 or min_srs_stage > 9:
                raise ValueError("min_srs_stage must be between 0 and 9.")
            stages = list(range(min_srs_stage, 10))
            params["srs_stages"] = ",".join(str(stage) for stage in stages)

        raw_assignments, _ = self.fetch_paginated("/assignments", params=params)
        assignments_by_subject: dict[int, dict[str, Any]] = {}
        for item in raw_assignments:
            data = item.get("data") or {}
            subject_id = data.get("subject_id")
            if subject_id is not None:
                assignments_by_subject[int(subject_id)] = data
        return assignments_by_subject
