"""Unit tests for sentence extraction and JSON/CSV serialization."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from wanikani_exporter.extractor import (
    extract_sentence_records,
    load_existing_audio_map,
    load_sentences,
    save_sentences_csv,
    save_sentences_json,
)


def _sample_subjects() -> list[dict]:
    return [
        {
            "id": 2467,
            "object": "vocabulary",
            "data": {
                "level": 1,
                "characters": "大人",
                "hidden_at": None,
                "document_url": "https://www.wanikani.com/vocabulary/%E5%A4%A7%E4%BA%BA",
                "meanings": [
                    {"meaning": "Adult", "primary": True, "accepted_answer": True},
                    {"meaning": "Mature", "primary": False, "accepted_answer": True},
                ],
                "readings": [
                    {"reading": "おとな", "primary": True, "accepted_answer": True},
                ],
                "parts_of_speech": ["noun"],
                "context_sentences": [
                    {
                        "ja": "もう大人なんだから、ちゃんとしなさい。",
                        "en": "You are an adult now, so behave yourself.",
                    },
                    {
                        "ja": "大人のふりをする。",
                        "en": "To act like an adult.",
                    },
                ],
            },
        },
        {
            "id": 9200,
            "object": "kana_vocabulary",
            "data": {
                "level": 2,
                "characters": "これ",
                "hidden_at": None,
                "document_url": "https://www.wanikani.com/vocabulary/%E3%81%93%E3%82%8C",
                "meanings": [
                    {"meaning": "This", "primary": True, "accepted_answer": True},
                ],
                "parts_of_speech": ["pronoun"],
                "context_sentences": [
                    {
                        "ja": "これはペンです。",
                        "en": "This is a pen.",
                    }
                ],
            },
        },
    ]


class TestExtractor(unittest.TestCase):
    def test_extracts_vocabulary_and_kana_vocabulary_records(self) -> None:
        records = extract_sentence_records(_sample_subjects())
        self.assertEqual(len(records), 3)

        first = records[0]
        self.assertEqual(first.sentence_id, "wk_2467_0")
        self.assertEqual(first.subject_id, 2467)
        self.assertEqual(first.sentence_index, 0)
        self.assertEqual(first.characters, "大人")
        self.assertEqual(first.primary_reading, "おとな")
        self.assertEqual(first.all_readings, ["おとな"])
        self.assertEqual(first.primary_meaning, "Adult")
        self.assertEqual(first.all_meanings, ["Adult", "Mature"])
        self.assertEqual(first.parts_of_speech, ["noun"])
        self.assertEqual(first.level, 1)
        self.assertEqual(first.sentence_ja, "もう大人なんだから、ちゃんとしなさい。")
        self.assertEqual(first.sentence_en, "You are an adult now, so behave yourself.")
        self.assertEqual(first.sentence_audio, "")

        # kana_vocabulary uses characters as reading
        kana_rec = records[2]
        self.assertEqual(kana_rec.sentence_id, "wk_9200_0")
        self.assertEqual(kana_rec.characters, "これ")
        self.assertEqual(kana_rec.primary_reading, "これ")
        self.assertEqual(kana_rec.all_readings, ["これ"])

    def test_filters_by_assignments_and_preserves_existing_audio(self) -> None:
        assignments = {2467: {"subject_id": 2467, "srs_stage": 5}}
        existing_audio = {"wk_2467_0": "[sound:wk_2467_0.mp3]"}

        records = extract_sentence_records(
            _sample_subjects(),
            assignments_by_subject=assignments,
            filter_by_assignments=True,
            existing_audio_by_id=existing_audio,
        )
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0].srs_stage, 5)
        self.assertEqual(records[0].srs_stage_name(), "guru")
        self.assertEqual(records[0].sentence_audio, "[sound:wk_2467_0.mp3]")
        self.assertEqual(records[1].sentence_audio, "")

    def test_json_and_csv_round_trip(self) -> None:
        records = extract_sentence_records(
            _sample_subjects(),
            assignments_by_subject={2467: {"subject_id": 2467, "srs_stage": 6}},
            existing_audio_by_id={"wk_2467_0": "[sound:wk_2467_0.mp3]"},
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = Path(tmpdir) / "sentences.json"
            csv_path = Path(tmpdir) / "sentences.csv"

            save_sentences_json(records, json_path)
            save_sentences_csv(records, csv_path)

            loaded_json = load_sentences(json_path)
            loaded_csv = load_sentences(csv_path)

            self.assertEqual(loaded_json, records)
            self.assertEqual(loaded_csv, records)
            self.assertEqual(
                load_existing_audio_map(json_path),
                {"wk_2467_0": "[sound:wk_2467_0.mp3]"},
            )


if __name__ == "__main__":
    unittest.main()
