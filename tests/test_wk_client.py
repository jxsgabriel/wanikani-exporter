"""Unit tests for the WaniKani API v2 client."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock

from wanikani_exporter.wk_client import (
    WaniKaniAPIError,
    WaniKaniClient,
    _SimpleResponse,
)


def _mock_response(
    status_code: int = 200,
    json_data: dict | None = None,
    headers: dict[str, str] | None = None,
) -> _SimpleResponse:
    return _SimpleResponse(
        status_code=status_code,
        text=json.dumps(json_data or {}),
        headers=headers or {},
    )


class TestWaniKaniClient(unittest.TestCase):
    def test_rejects_empty_token(self) -> None:
        with self.assertRaises(ValueError):
            WaniKaniClient("   ")

    def test_fetch_paginated_follows_next_url(self) -> None:
        session = MagicMock()
        session.headers = {}
        page1 = _mock_response(
            200,
            {
                "object": "collection",
                "data_updated_at": "2026-01-01T00:00:00.000000Z",
                "pages": {
                    "next_url": "https://api.wanikani.com/v2/subjects?page_after_id=10"
                },
                "data": [{"id": 10, "data": {"level": 1, "characters": "一"}}],
            },
        )
        page2 = _mock_response(
            200,
            {
                "object": "collection",
                "data_updated_at": "2026-01-02T00:00:00.000000Z",
                "pages": {"next_url": None},
                "data": [{"id": 11, "data": {"level": 1, "characters": "二"}}],
            },
        )
        session.get.side_effect = [page1, page2]

        client = WaniKaniClient("test-token", session=session)
        items, updated_at = client.fetch_paginated(
            "/subjects", params={"types": "vocabulary"}
        )

        self.assertEqual(len(items), 2)
        self.assertEqual([item["id"] for item in items], [10, 11])
        self.assertEqual(updated_at, "2026-01-02T00:00:00.000000Z")
        self.assertEqual(session.get.call_count, 2)
        # First request has params; second uses next_url with params=None
        self.assertEqual(
            session.get.call_args_list[0].kwargs["params"], {"types": "vocabulary"}
        )
        self.assertIsNone(session.get.call_args_list[1].kwargs["params"])

    def test_retries_on_429_rate_limit(self) -> None:
        session = MagicMock()
        session.headers = {}
        rate_limited = _mock_response(
            429,
            {"error": "Rate limit exceeded"},
            headers={"Retry-After": "2"},
        )
        success = _mock_response(
            200,
            {
                "object": "collection",
                "pages": {"next_url": None},
                "data": [{"id": 2467, "data": {"level": 1, "characters": "大人"}}],
            },
        )
        session.get.side_effect = [rate_limited, success]
        slept: list[float] = []

        client = WaniKaniClient(
            "test-token",
            session=session,
            sleep_fn=slept.append,
        )
        items, _ = client.fetch_paginated("/subjects")
        self.assertEqual(len(items), 1)
        self.assertEqual(slept, [2.0])

    def test_raises_api_error_on_unauthorized(self) -> None:
        session = MagicMock()
        session.headers = {}
        session.get.return_value = _mock_response(401, {"error": "Unauthorized"})

        client = WaniKaniClient("bad-token", session=session)
        with self.assertRaises(WaniKaniAPIError) as ctx:
            client.fetch_paginated("/subjects")
        self.assertEqual(ctx.exception.status_code, 401)

    def test_fetch_vocabulary_subjects_uses_incremental_cache(self) -> None:
        session = MagicMock()
        session.headers = {}
        initial_resp = _mock_response(
            200,
            {
                "object": "collection",
                "data_updated_at": "2026-01-01T00:00:00Z",
                "pages": {"next_url": None},
                "data": [
                    {
                        "id": 2467,
                        "data": {"level": 1, "characters": "大人", "hidden_at": None},
                    }
                ],
            },
        )
        incremental_resp = _mock_response(
            200,
            {
                "object": "collection",
                "data_updated_at": "2026-01-05T00:00:00Z",
                "pages": {"next_url": None},
                "data": [
                    {
                        "id": 2468,
                        "data": {"level": 1, "characters": "一人", "hidden_at": None},
                    }
                ],
            },
        )
        session.get.side_effect = [initial_resp, incremental_resp]

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "cache.json"
            client = WaniKaniClient("test-token", session=session)

            first = client.fetch_vocabulary_subjects(
                levels=[1], use_cache=True, cache_path=cache_path
            )
            self.assertEqual(len(first), 1)
            self.assertTrue(cache_path.exists())

            second = client.fetch_vocabulary_subjects(
                levels=[1], use_cache=True, cache_path=cache_path
            )
            self.assertEqual(len(second), 2)
            second_call_params = session.get.call_args_list[1].kwargs["params"]
            self.assertEqual(
                second_call_params.get("updated_after"), "2026-01-01T00:00:00Z"
            )

    def test_fetch_assignments_filters_by_min_srs_stage(self) -> None:
        session = MagicMock()
        session.headers = {}
        session.get.return_value = _mock_response(
            200,
            {
                "object": "collection",
                "pages": {"next_url": None},
                "data": [
                    {
                        "id": 999,
                        "data": {"subject_id": 2467, "srs_stage": 5},
                    }
                ],
            },
        )

        client = WaniKaniClient("test-token", session=session)
        assignments = client.fetch_assignments(
            levels=[1, 2], unlocked_only=True, min_srs_stage=5
        )
        self.assertIn(2467, assignments)
        self.assertEqual(assignments[2467]["srs_stage"], 5)
        call_params = session.get.call_args.kwargs["params"]
        self.assertEqual(call_params["srs_stages"], "5,6,7,8,9")
        self.assertEqual(call_params["unlocked"], "true")


if __name__ == "__main__":
    unittest.main()
