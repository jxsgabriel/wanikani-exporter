"""Data models for WaniKani vocabulary context sentence records."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import html
from typing import Any


SRS_STAGE_NAMES: dict[int, str] = {
    0: "initiate",
    1: "apprentice",
    2: "apprentice",
    3: "apprentice",
    4: "apprentice",
    5: "guru",
    6: "guru",
    7: "master",
    8: "enlightened",
    9: "burned",
}


def make_sentence_id(subject_id: int, sentence_index: int) -> str:
    """Create a deterministic sentence ID used for Anki GUIDs and future TTS filenames."""
    return f"wk_{subject_id}_{sentence_index}"


@dataclass
class VocabSentenceRecord:
    """Represents a single WaniKani vocabulary context sentence and its vocabulary metadata."""

    sentence_id: str
    subject_id: int
    sentence_index: int
    characters: str
    primary_reading: str
    all_readings: list[str] = field(default_factory=list)
    primary_meaning: str = ""
    all_meanings: list[str] = field(default_factory=list)
    parts_of_speech: list[str] = field(default_factory=list)
    level: int = 1
    sentence_ja: str = ""
    sentence_en: str = ""
    document_url: str = ""
    srs_stage: int | None = None
    sentence_audio: str = ""

    def srs_stage_name(self) -> str | None:
        """Return the human-readable SRS tier name (e.g. 'apprentice', 'guru') if present."""
        if self.srs_stage is None:
            return None
        return SRS_STAGE_NAMES.get(self.srs_stage)

    def render_sentence_ja_highlighted(self, highlight: bool = True) -> str:
        """Return HTML-escaped Japanese sentence with optional target vocabulary highlighting."""
        escaped_sentence = html.escape(self.sentence_ja)
        if not highlight or not self.characters:
            return escaped_sentence
        escaped_vocab = html.escape(self.characters)
        if escaped_vocab in escaped_sentence:
            return escaped_sentence.replace(
                escaped_vocab,
                f'<span class="vocab-hl">{escaped_vocab}</span>',
            )
        return escaped_sentence

    def to_dict(self) -> dict[str, Any]:
        """Serialize record to a JSON-compatible dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VocabSentenceRecord:
        """Deserialize record from a dictionary (JSON or parsed CSV row)."""
        subject_id = int(data["subject_id"])
        sentence_index = int(data["sentence_index"])
        sentence_id = str(
            data.get("sentence_id") or make_sentence_id(subject_id, sentence_index)
        )

        def _parse_list(val: Any) -> list[str]:
            if isinstance(val, list):
                return [str(x).strip() for x in val if str(x).strip()]
            if isinstance(val, str):
                return [x.strip() for x in val.split(";") if x.strip()]
            return []

        raw_srs = data.get("srs_stage")
        srs_stage: int | None
        if raw_srs is None or raw_srs == "":
            srs_stage = None
        else:
            srs_stage = int(raw_srs)

        all_readings = _parse_list(data.get("all_readings", []))
        primary_reading = str(data.get("primary_reading", "")).strip()
        if not all_readings and primary_reading:
            all_readings = [primary_reading]

        all_meanings = _parse_list(data.get("all_meanings", []))
        primary_meaning = str(data.get("primary_meaning", "")).strip()
        if not all_meanings and primary_meaning:
            all_meanings = [primary_meaning]

        return cls(
            sentence_id=sentence_id,
            subject_id=subject_id,
            sentence_index=sentence_index,
            characters=str(data.get("characters", "")),
            primary_reading=primary_reading,
            all_readings=all_readings,
            primary_meaning=primary_meaning,
            all_meanings=all_meanings,
            parts_of_speech=_parse_list(data.get("parts_of_speech", [])),
            level=int(data.get("level", 1)),
            sentence_ja=str(data.get("sentence_ja", "")),
            sentence_en=str(data.get("sentence_en", "")),
            document_url=str(data.get("document_url", "")),
            srs_stage=srs_stage,
            sentence_audio=str(data.get("sentence_audio", "") or ""),
        )
