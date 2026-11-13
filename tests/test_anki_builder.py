"""Unit tests for the Anki `.apkg` deck builder."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zipfile

from wanikani_exporter.anki_builder import (
    MODEL_FIELDS,
    WANIKANI_SENTENCE_MODEL_ID,
    build_anki_deck,
    build_note_fields,
    build_note_tags,
    deterministic_guid,
)
from wanikani_exporter.models import VocabSentenceRecord


def _sample_records() -> list[VocabSentenceRecord]:
    return [
        VocabSentenceRecord(
            sentence_id="wk_2467_0",
            subject_id=2467,
            sentence_index=0,
            characters="大人",
            primary_reading="おとな",
            all_readings=["おとな"],
            primary_meaning="Adult",
            all_meanings=["Adult", "Mature"],
            parts_of_speech=["noun"],
            level=1,
            sentence_ja="もう大人なんだから、ちゃんとしなさい。",
            sentence_en="You are an adult now, so behave yourself.",
            document_url="https://www.wanikani.com/vocabulary/%E5%A4%A7%E4%BA%BA",
            srs_stage=5,
            sentence_audio="[sound:wk_2467_0.mp3]",
        ),
        VocabSentenceRecord(
            sentence_id="wk_9200_0",
            subject_id=9200,
            sentence_index=0,
            characters="これ",
            primary_reading="これ",
            all_readings=["これ"],
            primary_meaning="This",
            all_meanings=["This"],
            parts_of_speech=["pronoun"],
            level=2,
            sentence_ja="これはペンです。",
            sentence_en="This is a pen.",
            document_url="https://www.wanikani.com/vocabulary/%E3%81%93%E3%82%8C",
            srs_stage=None,
            sentence_audio="",
        ),
    ]


class TestAnkiBuilder(unittest.TestCase):
    def test_deterministic_guid_is_stable(self) -> None:
        guid1 = deterministic_guid("wk_2467_0")
        guid2 = deterministic_guid("wk_2467_0")
        guid_other = deterministic_guid("wk_2467_1")
        self.assertEqual(guid1, guid2)
        self.assertNotEqual(guid1, guid_other)
        self.assertTrue(len(guid1) > 5)

    def test_build_note_fields_keeps_sentence_ja_plain_for_tts(self) -> None:
        rec = _sample_records()[0]
        fields = build_note_fields(rec, highlight_vocab=True)
        self.assertEqual(len(fields), len(MODEL_FIELDS))
        self.assertEqual(fields[0], "wk_2467_0")
        # SentenceJA must have no HTML so future TTS engines read it cleanly
        self.assertEqual(fields[1], "もう大人なんだから、ちゃんとしなさい。")
        self.assertNotIn("<span", fields[1])
        # SentenceJAHighlighted has the highlight span
        self.assertIn('<span class="vocab-hl">大人</span>', fields[2])
        self.assertEqual(fields[4], "[sound:wk_2467_0.mp3]")
        self.assertEqual(fields[5], "大人")
        self.assertEqual(fields[6], "おとな")
        self.assertEqual(fields[7], "Adult, Mature")

    def test_build_note_tags(self) -> None:
        rec = _sample_records()[0]
        tags = build_note_tags(rec)
        self.assertEqual(tags, ["wanikani", "wk::level::01", "wk::srs::guru"])

    def test_build_anki_deck_creates_valid_apkg_with_subdecks_and_media(self) -> None:
        records = _sample_records()
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            media_dir = tmp_path / "audio"
            media_dir.mkdir()
            audio_file = media_dir / "wk_2467_0.mp3"
            audio_file.write_bytes(b"FAKE_MP3_DATA")

            apkg_path = tmp_path / "output" / "test_deck.apkg"
            build_anki_deck(
                records=records,
                output_path=apkg_path,
                deck_name="WaniKani Context Sentences",
                group_by_level=True,
                highlight_vocab=True,
                media_dir=media_dir,
            )

            self.assertTrue(apkg_path.is_file())
            with zipfile.ZipFile(apkg_path, "r") as zf:
                names = set(zf.namelist())
                self.assertIn("media", names)
                self.assertTrue(
                    "collection.anki2" in names or "collection.anki21" in names
                )
                media_map = json.loads(zf.read("media").decode("utf-8"))
                self.assertIn("wk_2467_0.mp3", media_map.values())

                db_member = (
                    "collection.anki2"
                    if "collection.anki2" in names
                    else "collection.anki21"
                )
                extracted_db = tmp_path / "collection.anki2"
                extracted_db.write_bytes(zf.read(db_member))

                conn = sqlite3.connect(str(extracted_db))
                try:
                    cursor = conn.cursor()
                    cursor.execute("SELECT guid, flds, tags FROM notes ORDER BY id")
                    note_rows = cursor.fetchall()
                    self.assertEqual(len(note_rows), 2)
                    self.assertEqual(note_rows[0][0], deterministic_guid("wk_2467_0"))
                    note_0_fields = note_rows[0][1].split("\x1f")
                    self.assertEqual(len(note_0_fields), 11)
                    self.assertEqual(note_0_fields[0], "wk_2467_0")
                    self.assertEqual(note_0_fields[4], "[sound:wk_2467_0.mp3]")

                    cursor.execute("SELECT models, decks FROM col")
                    models_str, decks_str = cursor.fetchone()
                    models_dict = json.loads(models_str)
                    self.assertIn(str(WANIKANI_SENTENCE_MODEL_ID), models_dict)
                    decks_dict = json.loads(decks_str)
                    deck_names = {d["name"] for d in decks_dict.values()}
                    self.assertIn("WaniKani Context Sentences::Level 01", deck_names)
                    self.assertIn("WaniKani Context Sentences::Level 02", deck_names)
                finally:
                    conn.close()


if __name__ == "__main__":
    unittest.main()
