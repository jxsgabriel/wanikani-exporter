"""Extract vocabulary context sentences from WaniKani payloads and read/write JSON & CSV."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from wanikani_exporter.models import VocabSentenceRecord, make_sentence_id


CSV_FIELDNAMES = [
    "sentence_id",
    "subject_id",
    "sentence_index",
    "characters",
    "primary_reading",
    "all_readings",
    "primary_meaning",
    "all_meanings",
    "parts_of_speech",
    "level",
    "sentence_ja",
    "sentence_en",
    "document_url",
    "srs_stage",
    "sentence_audio",
]


def extract_sentence_records(
    subjects: list[dict[str, Any]],
    assignments_by_subject: dict[int, dict[str, Any]] | None = None,
    filter_by_assignments: bool = False,
    existing_audio_by_id: dict[str, str] | None = None,
) -> list[VocabSentenceRecord]:
    """Convert raw WaniKani subject objects into flat `VocabSentenceRecord` entries.

    Args:
        subjects: Raw subject objects from `/v2/subjects`.
        assignments_by_subject: Optional mapping of `subject_id -> assignment["data"]`.
        filter_by_assignments: When True, only subjects present in `assignments_by_subject`
            are included in the output.
        existing_audio_by_id: Optional mapping of `sentence_id -> sentence_audio` to preserve
            previously populated TTS audio tags across re-exports.
    """
    records: list[VocabSentenceRecord] = []
    audio_map = existing_audio_by_id or {}

    for subject in subjects:
        subject_id = int(subject["id"])
        if filter_by_assignments:
            if not assignments_by_subject or subject_id not in assignments_by_subject:
                continue

        data = subject.get("data") or {}
        if data.get("hidden_at") is not None:
            continue

        context_sentences = data.get("context_sentences") or []
        if not context_sentences:
            continue

        characters = str(data.get("characters") or "").strip()
        level = int(data.get("level", 1))
        document_url = str(data.get("document_url") or "").strip()
        parts_of_speech = [
            str(pos).strip()
            for pos in (data.get("parts_of_speech") or [])
            if str(pos).strip()
        ]

        # Extract meanings (primary first, then accepted secondary meanings)
        meanings_raw = data.get("meanings") or []
        primary_meanings = [
            str(m["meaning"]).strip()
            for m in meanings_raw
            if m.get("primary") and m.get("meaning")
        ]
        secondary_meanings = [
            str(m["meaning"]).strip()
            for m in meanings_raw
            if not m.get("primary")
            and m.get("accepted_answer", True)
            and m.get("meaning")
        ]
        all_meanings = primary_meanings + secondary_meanings
        primary_meaning = primary_meanings[0] if primary_meanings else (
            all_meanings[0] if all_meanings else ""
        )

        # Extract readings (vocabulary has `readings`; kana_vocabulary uses `characters`)
        readings_raw = data.get("readings") or []
        if readings_raw:
            primary_readings = [
                str(r["reading"]).strip()
                for r in readings_raw
                if r.get("primary") and r.get("reading")
            ]
            secondary_readings = [
                str(r["reading"]).strip()
                for r in readings_raw
                if not r.get("primary")
                and r.get("accepted_answer", True)
                and r.get("reading")
            ]
            all_readings = primary_readings + secondary_readings
            primary_reading = primary_readings[0] if primary_readings else (
                all_readings[0] if all_readings else characters
            )
        else:
            primary_reading = characters
            all_readings = [characters] if characters else []

        srs_stage: int | None = None
        if assignments_by_subject and subject_id in assignments_by_subject:
            raw_stage = assignments_by_subject[subject_id].get("srs_stage")
            if raw_stage is not None:
                srs_stage = int(raw_stage)

        for idx, ctx in enumerate(context_sentences):
            sentence_ja = str(ctx.get("ja") or "").strip()
            sentence_en = str(ctx.get("en") or "").strip()
            if not sentence_ja and not sentence_en:
                continue

            sentence_id = make_sentence_id(subject_id, idx)
            sentence_audio = audio_map.get(sentence_id, "")

            records.append(
                VocabSentenceRecord(
                    sentence_id=sentence_id,
                    subject_id=subject_id,
                    sentence_index=idx,
                    characters=characters,
                    primary_reading=primary_reading,
                    all_readings=all_readings,
                    primary_meaning=primary_meaning,
                    all_meanings=all_meanings,
                    parts_of_speech=parts_of_speech,
                    level=level,
                    sentence_ja=sentence_ja,
                    sentence_en=sentence_en,
                    document_url=document_url,
                    srs_stage=srs_stage,
                    sentence_audio=sentence_audio,
                )
            )

    return records


def save_sentences_json(records: list[VocabSentenceRecord], path: Path) -> None:
    """Write sentence records to a UTF-8 JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [record.to_dict() for record in records]
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_sentences_json(path: Path) -> list[VocabSentenceRecord]:
    """Load sentence records from a UTF-8 JSON file."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"Expected a JSON array of sentence records in {path}")
    return [VocabSentenceRecord.from_dict(item) for item in raw]


def save_sentences_csv(records: list[VocabSentenceRecord], path: Path) -> None:
    """Write sentence records to a UTF-8 CSV file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()
        for record in records:
            row = record.to_dict()
            row["all_readings"] = "; ".join(record.all_readings)
            row["all_meanings"] = "; ".join(record.all_meanings)
            row["parts_of_speech"] = "; ".join(record.parts_of_speech)
            row["srs_stage"] = "" if record.srs_stage is None else str(record.srs_stage)
            writer.writerow(row)


def load_sentences_csv(path: Path) -> list[VocabSentenceRecord]:
    """Load sentence records from a UTF-8 CSV file."""
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        return [VocabSentenceRecord.from_dict(row) for row in reader]


def load_sentences(path: Path) -> list[VocabSentenceRecord]:
    """Load sentence records from either a `.json` or `.csv` file based on suffix."""
    suffix = path.suffix.lower()
    if suffix == ".json":
        return load_sentences_json(path)
    if suffix in (".csv", ".tsv"):
        return load_sentences_csv(path)
    raise ValueError(
        f"Unsupported sentence list file format '{path.suffix}'. Use .json or .csv."
    )


def load_existing_audio_map(path: Path | None) -> dict[str, str]:
    """Load any existing `sentence_id -> sentence_audio` mappings from an existing output file."""
    if path is None or not path.exists():
        return {}
    try:
        records = load_sentences(path)
        return {
            rec.sentence_id: rec.sentence_audio
            for rec in records
            if rec.sentence_audio
        }
    except Exception:
        return {}
