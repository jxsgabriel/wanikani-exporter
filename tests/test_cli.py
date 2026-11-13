"""Unit tests for the CLI argument parser and command execution."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock

from wanikani_exporter.cli import build_parser, main, parse_levels, run_download
from wanikani_exporter.wk_client import WaniKaniClient


class TestCLI(unittest.TestCase):
    def test_parse_levels(self) -> None:
        self.assertIsNone(parse_levels(None))
        self.assertIsNone(parse_levels("  "))
        self.assertEqual(parse_levels("1-3,5,8-9"), [1, 2, 3, 5, 8, 9])

        with self.assertRaises(ValueError):
            parse_levels("0-5")
        with self.assertRaises(ValueError):
            parse_levels("10-5")
        with self.assertRaises(ValueError):
            parse_levels("61")

    def test_download_and_build_deck_end_to_end(self) -> None:
        mock_client = MagicMock(spec=WaniKaniClient)
        mock_client.fetch_vocabulary_subjects.return_value = [
            {
                "id": 2467,
                "object": "vocabulary",
                "data": {
                    "level": 1,
                    "characters": "大人",
                    "hidden_at": None,
                    "document_url": "https://www.wanikani.com/vocabulary/%E5%A4%A7%E4%BA%BA",
                    "meanings": [
                        {"meaning": "Adult", "primary": True, "accepted_answer": True}
                    ],
                    "readings": [
                        {"reading": "おとな", "primary": True, "accepted_answer": True}
                    ],
                    "parts_of_speech": ["noun"],
                    "context_sentences": [
                        {
                            "ja": "もう大人なんだから、ちゃんとしなさい。",
                            "en": "You are an adult now, so behave yourself.",
                        }
                    ],
                },
            }
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            json_out = tmp_path / "sentences.json"
            csv_out = tmp_path / "sentences.csv"
            apkg_out = tmp_path / "deck.apkg"

            parser = build_parser()
            dl_args = parser.parse_args(
                [
                    "download",
                    "--levels",
                    "1-3",
                    "--json-out",
                    str(json_out),
                    "--csv-out",
                    str(csv_out),
                    "--no-cache",
                ]
            )
            records = run_download(dl_args, client=mock_client)
            self.assertEqual(len(records), 1)
            self.assertTrue(json_out.is_file())
            self.assertTrue(csv_out.is_file())

            exit_code = main(
                [
                    "build-deck",
                    "--input",
                    str(json_out),
                    "--output",
                    str(apkg_out),
                    "--group-by-level",
                ]
            )
            self.assertEqual(exit_code, 0)
            self.assertTrue(apkg_out.is_file())


if __name__ == "__main__":
    unittest.main()
