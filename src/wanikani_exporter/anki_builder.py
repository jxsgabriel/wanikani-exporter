"""Build TTS-ready Anki decks (.apkg) from WaniKani vocabulary sentence records."""

from __future__ import annotations

import hashlib
import html
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import time
from typing import Any
import zipfile

from wanikani_exporter.models import VocabSentenceRecord

try:
    import genanki  # type: ignore[import-not-found]
except ImportError:
    genanki = None


WANIKANI_SENTENCE_MODEL_ID = 1607392319
WANIKANI_BASE_DECK_ID = 2059400110
WANIKANI_MODEL_NAME = "WaniKani Context Sentence"

MODEL_FIELDS: list[str] = [
    "SentenceID",
    "SentenceJA",
    "SentenceJAHighlighted",
    "SentenceEN",
    "SentenceAudio",
    "Vocabulary",
    "Reading",
    "Meaning",
    "PartsOfSpeech",
    "Level",
    "DocumentURL",
]

FRONT_TEMPLATE = """\
<div class="wk-card">
  <div class="wk-badge">WaniKani Level {{Level}}</div>
  <div class="sentence-ja">{{SentenceJAHighlighted}}</div>
</div>
"""

BACK_TEMPLATE = """\
{{FrontSide}}

<hr id="answer">

<div class="wk-card wk-back">
  {{#SentenceAudio}}
  <div class="sentence-audio">{{SentenceAudio}}</div>
  {{/SentenceAudio}}

  <div class="sentence-en">{{SentenceEN}}</div>

  <div class="vocab-box">
    <div class="vocab-header">
      <span class="vocab-characters">{{Vocabulary}}</span>
      {{#Reading}}<span class="vocab-reading">【{{Reading}}】</span>{{/Reading}}
    </div>
    <div class="vocab-meaning"><strong>Meaning:</strong> {{Meaning}}</div>
    {{#PartsOfSpeech}}
    <div class="vocab-pos"><strong>Part of speech:</strong> {{PartsOfSpeech}}</div>
    {{/PartsOfSpeech}}
    {{#DocumentURL}}
    <div class="vocab-link">
      <a href="{{DocumentURL}}" target="_blank" rel="noopener noreferrer">View on WaniKani</a>
    </div>
    {{/DocumentURL}}
  </div>
</div>
"""

CARD_CSS = """\
.card {
  font-family: "Hiragino Kaku Gothic ProN", "Noto Sans JP", "Yu Gothic", "Meiryo", sans-serif;
  font-size: 22px;
  line-height: 1.6;
  text-align: center;
  color: #1f2937;
  background-color: #f9fafb;
  padding: 20px;
}

.nightMode.card {
  color: #f3f4f6;
  background-color: #111827;
}

.wk-card {
  max-width: 680px;
  margin: 0 auto;
}

.wk-badge {
  display: inline-block;
  font-size: 12px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: #ffffff;
  background-color: #a855f7;
  padding: 3px 10px;
  border-radius: 9999px;
  margin-bottom: 18px;
}

.sentence-ja {
  font-size: 28px;
  line-height: 1.5;
  margin: 12px 0;
}

.vocab-hl {
  color: #9333ea;
  font-weight: 700;
  text-decoration: underline;
  text-underline-offset: 4px;
}

.nightMode .vocab-hl {
  color: #c084fc;
}

.sentence-audio {
  margin: 10px 0;
}

.sentence-en {
  font-size: 20px;
  color: #374151;
  margin: 16px 0 22px 0;
}

.nightMode .sentence-en {
  color: #d1d5db;
}

.vocab-box {
  text-align: left;
  background: #ffffff;
  border: 1px solid #e5e7eb;
  border-left: 4px solid #a855f7;
  border-radius: 8px;
  padding: 14px 18px;
  font-size: 16px;
}

.nightMode .vocab-box {
  background: #1f2937;
  border-color: #374151;
  border-left-color: #c084fc;
}

.vocab-header {
  font-size: 22px;
  margin-bottom: 6px;
}

.vocab-characters {
  font-weight: 700;
}

.vocab-reading {
  color: #6b7280;
}

.nightMode .vocab-reading {
  color: #9ca3af;
}

.vocab-meaning,
.vocab-pos {
  margin-top: 4px;
}

.vocab-link {
  margin-top: 8px;
  font-size: 14px;
}

.vocab-link a {
  color: #9333ea;
  text-decoration: none;
}

.nightMode .vocab-link a {
  color: #c084fc;
}
"""

_BASE91_TABLE = [
    "a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k", "l", "m", "n", "o", "p",
    "q", "r", "s", "t", "u", "v", "w", "x", "y", "z", "A", "B", "C", "D", "E", "F",
    "G", "H", "I", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "U", "V",
    "W", "X", "Y", "Z", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "!", "#",
    "$", "%", "&", "(", ")", "*", "+", ",", "-", ".", "/", ":", ";", "<", "=", ">",
    "?", "@", "[", "]", "^", "_", "`", "{", "|", "}", "~",
]

SOUND_TAG_RE = re.compile(r"\[sound:([^\]]+)\]")


def deterministic_guid(*values: Any) -> str:
    """Compute a deterministic base91 GUID identical to `genanki.guid_for(*values)`."""
    if genanki is not None:
        return str(genanki.guid_for(*values))

    hash_str = "__".join(str(v) for v in values)
    digest = hashlib.sha256(hash_str.encode("utf-8")).digest()
    hash_int = int.from_bytes(digest[:8], byteorder="big")
    rv_reversed: list[str] = []
    while hash_int > 0:
        rv_reversed.append(_BASE91_TABLE[hash_int % len(_BASE91_TABLE)])
        hash_int //= len(_BASE91_TABLE)
    return "".join(reversed(rv_reversed))


def deterministic_deck_id(deck_name: str) -> int:
    """Derive a stable 31-bit integer deck ID for a deck or subdeck name."""
    if deck_name == "WaniKani Context Sentences":
        return WANIKANI_BASE_DECK_ID
    digest = hashlib.sha256(deck_name.encode("utf-8")).digest()
    return (int.from_bytes(digest[:4], byteorder="big") & 0x7FFFFFFF) or 1


def build_note_fields(
    record: VocabSentenceRecord,
    highlight_vocab: bool = True,
) -> list[str]:
    """Build the 11 ordered Anki Note field values for a `VocabSentenceRecord`.

    Note: `SentenceJA` is always kept strictly plain text (no HTML markup) so that
    future TTS tools can consume it cleanly.
    """
    readings_str = ", ".join(record.all_readings) if record.all_readings else record.primary_reading
    meanings_str = ", ".join(record.all_meanings) if record.all_meanings else record.primary_meaning
    pos_str = ", ".join(record.parts_of_speech)

    return [
        record.sentence_id,
        record.sentence_ja,
        record.render_sentence_ja_highlighted(highlight=highlight_vocab),
        html.escape(record.sentence_en),
        record.sentence_audio,
        html.escape(record.characters),
        html.escape(readings_str),
        html.escape(meanings_str),
        html.escape(pos_str),
        str(record.level),
        record.document_url,
    ]


def build_note_tags(record: VocabSentenceRecord) -> list[str]:
    """Build the Anki tags list for a `VocabSentenceRecord`."""
    tags = [
        "wanikani",
        f"wk::level::{record.level:02d}",
    ]
    srs_name = record.srs_stage_name()
    if srs_name:
        tags.append(f"wk::srs::{srs_name}")
    return tags


def extract_media_filenames(records: list[VocabSentenceRecord], media_dir: Path | None) -> list[Path]:
    """Find existing audio files in `media_dir` referenced by `[sound:...]` tags."""
    if media_dir is None or not media_dir.exists():
        return []

    found_paths: list[Path] = []
    seen: set[str] = set()
    for record in records:
        if not record.sentence_audio:
            continue
        for match in SOUND_TAG_RE.findall(record.sentence_audio):
            filename = Path(match).name
            if filename in seen:
                continue
            candidate = media_dir / filename
            if candidate.is_file():
                seen.add(filename)
                found_paths.append(candidate)
    return found_paths


def build_anki_deck(
    records: list[VocabSentenceRecord],
    output_path: Path,
    deck_name: str = "WaniKani Context Sentences",
    group_by_level: bool = False,
    highlight_vocab: bool = True,
    media_dir: Path | None = None,
) -> Path:
    """Create an Anki `.apkg` package from a list of `VocabSentenceRecord` items."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    media_files = extract_media_filenames(records, media_dir)

    if genanki is not None:
        return _build_with_genanki(
            records=records,
            output_path=output_path,
            deck_name=deck_name,
            group_by_level=group_by_level,
            highlight_vocab=highlight_vocab,
            media_files=media_files,
        )

    return _build_with_sqlite(
        records=records,
        output_path=output_path,
        deck_name=deck_name,
        group_by_level=group_by_level,
        highlight_vocab=highlight_vocab,
        media_files=media_files,
    )


def _build_with_genanki(
    records: list[VocabSentenceRecord],
    output_path: Path,
    deck_name: str,
    group_by_level: bool,
    highlight_vocab: bool,
    media_files: list[Path],
) -> Path:
    """Build the `.apkg` package using the `genanki` library."""
    model = genanki.Model(
        WANIKANI_SENTENCE_MODEL_ID,
        WANIKANI_MODEL_NAME,
        fields=[{"name": name} for name in MODEL_FIELDS],
        templates=[
            {
                "name": "Context Sentence Recognition",
                "qfmt": FRONT_TEMPLATE,
                "afmt": BACK_TEMPLATE,
            }
        ],
        css=CARD_CSS,
    )

    decks_by_name: dict[str, Any] = {}

    for record in records:
        target_deck_name = (
            f"{deck_name}::Level {record.level:02d}" if group_by_level else deck_name
        )
        if target_deck_name not in decks_by_name:
            decks_by_name[target_deck_name] = genanki.Deck(
                deterministic_deck_id(target_deck_name),
                target_deck_name,
            )

        note = genanki.Note(
            model=model,
            fields=build_note_fields(record, highlight_vocab=highlight_vocab),
            tags=build_note_tags(record),
            guid=deterministic_guid(record.sentence_id),
        )
        decks_by_name[target_deck_name].add_note(note)

    if not decks_by_name:
        decks_by_name[deck_name] = genanki.Deck(
            deterministic_deck_id(deck_name),
            deck_name,
        )

    package = genanki.Package(list(decks_by_name.values()))
    if media_files:
        package.media_files = [str(p) for p in media_files]
    package.write_to_file(str(output_path))
    return output_path


_ANKI_SCHEMA_SQL = """\
CREATE TABLE col (
    id              integer primary key,
    crt             integer not null,
    mod             integer not null,
    scm             integer not null,
    ver             integer not null,
    dty             integer not null,
    usn             integer not null,
    ls              integer not null,
    conf            text not null,
    models          text not null,
    decks           text not null,
    dconf           text not null,
    tags            text not null
);
CREATE TABLE notes (
    id              integer primary key,
    guid            text not null,
    mid             integer not null,
    mod             integer not null,
    usn             integer not null,
    tags            text not null,
    flds            text not null,
    sfld            integer not null,
    csum            integer not null,
    flags           integer not null,
    data            text not null
);
CREATE TABLE cards (
    id              integer primary key,
    nid             integer not null,
    did             integer not null,
    ord             integer not null,
    mod             integer not null,
    usn             integer not null,
    type            integer not null,
    queue           integer not null,
    due             integer not null,
    ivl             integer not null,
    factor          integer not null,
    reps            integer not null,
    lapses          integer not null,
    left            integer not null,
    odue            integer not null,
    odid            integer not null,
    flags           integer not null,
    data            text not null
);
CREATE TABLE revlog (
    id              integer primary key,
    cid             integer not null,
    usn             integer not null,
    ease            integer not null,
    ivl             integer not null,
    lastIvl         integer not null,
    factor          integer not null,
    time            integer not null,
    type            integer not null
);
CREATE TABLE graves (
    usn             integer not null,
    oid             integer not null,
    type            integer not null
);
CREATE INDEX ix_notes_usn on notes (usn);
CREATE INDEX ix_cards_usn on cards (usn);
CREATE INDEX ix_revlog_usn on revlog (usn);
CREATE INDEX ix_cards_nid on cards (nid);
CREATE INDEX ix_cards_sched on cards (did, queue, due);
CREATE INDEX ix_revlog_cid on revlog (cid);
CREATE INDEX ix_notes_csum on notes (csum);
"""


def _field_checksum(val: str) -> int:
    """Compute Anki's 32-bit SHA1 field checksum."""
    digest = hashlib.sha1(val.encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def _build_with_sqlite(
    records: list[VocabSentenceRecord],
    output_path: Path,
    deck_name: str,
    group_by_level: bool,
    highlight_vocab: bool,
    media_files: list[Path],
) -> Path:
    """Fallback `.apkg` builder using Python's built-in `sqlite3` and `zipfile`."""
    now_sec = int(time.time())
    now_ms = now_sec * 1000

    decks_json: dict[str, Any] = {
        "1": {
            "id": 1,
            "name": "Default",
            "mod": now_sec,
            "usn": 0,
            "lrnToday": [0, 0],
            "revToday": [0, 0],
            "newToday": [0, 0],
            "timeToday": [0, 0],
            "collapsed": False,
            "browserCollapsed": False,
            "desc": "",
            "dyn": 0,
            "conf": 1,
            "extendNew": 10,
            "extendRev": 50,
        }
    }

    def _ensure_deck(target_name: str) -> int:
        did = deterministic_deck_id(target_name)
        key = str(did)
        if key not in decks_json:
            decks_json[key] = {
                "id": did,
                "name": target_name,
                "mod": now_sec,
                "usn": -1,
                "lrnToday": [0, 0],
                "revToday": [0, 0],
                "newToday": [0, 0],
                "timeToday": [0, 0],
                "collapsed": False,
                "browserCollapsed": False,
                "desc": "",
                "dyn": 0,
                "conf": 1,
                "extendNew": 10,
                "extendRev": 50,
            }
        return did

    if not records:
        _ensure_deck(deck_name)

    models_json = {
        str(WANIKANI_SENTENCE_MODEL_ID): {
            "id": WANIKANI_SENTENCE_MODEL_ID,
            "name": WANIKANI_MODEL_NAME,
            "type": 0,
            "mod": now_sec,
            "usn": -1,
            "sortf": 0,
            "did": deterministic_deck_id(deck_name),
            "tmpls": [
                {
                    "name": "Context Sentence Recognition",
                    "ord": 0,
                    "qfmt": FRONT_TEMPLATE,
                    "afmt": BACK_TEMPLATE,
                    "bqfmt": "",
                    "bafmt": "",
                    "did": None,
                    "bfont": "",
                    "bsize": 0,
                }
            ],
            "flds": [
                {
                    "name": field_name,
                    "ord": idx,
                    "sticky": False,
                    "rtl": False,
                    "font": "Arial",
                    "size": 20,
                    "media": [],
                }
                for idx, field_name in enumerate(MODEL_FIELDS)
            ],
            "css": CARD_CSS,
            "latexPre": "",
            "latexPost": "",
            "latexsvg": False,
            "req": [[0, "all", [0]]],
            "tags": [],
            "vers": [],
        }
    }

    conf_json = {
        "nextPos": 1,
        "estTimes": True,
        "activeDecks": [1],
        "sortType": "noteFld",
        "timeLim": 0,
        "sortBackwards": False,
        "addToCur": True,
        "curDeck": 1,
        "newBury": True,
        "newSpread": 0,
        "dueCounts": True,
        "curModel": str(WANIKANI_SENTENCE_MODEL_ID),
        "collapseTime": 1200,
    }

    dconf_json = {
        "1": {
            "id": 1,
            "mod": 0,
            "name": "Default",
            "usn": 0,
            "maxTaken": 60,
            "autoplay": True,
            "timer": 0,
            "replayq": True,
            "new": {
                "bury": True,
                "delays": [1, 10],
                "initialFactor": 2500,
                "ints": [1, 4, 7],
                "order": 1,
                "perDay": 20,
                "separate": True,
            },
            "lapse": {
                "delays": [10],
                "leechAction": 0,
                "leechFails": 8,
                "minInt": 1,
                "mult": 0,
            },
            "rev": {
                "bury": True,
                "ease4": 1.3,
                "fuzz": 0.05,
                "ivlFct": 1,
                "maxIvl": 36500,
                "minSpace": 1,
                "perDay": 100,
            },
            "dyn": False,
        }
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "collection.anki2"
        conn = sqlite3.connect(str(db_path))
        try:
            cursor = conn.cursor()
            cursor.executescript(_ANKI_SCHEMA_SQL)

            note_rows: list[tuple[Any, ...]] = []
            card_rows: list[tuple[Any, ...]] = []

            for idx, record in enumerate(records):
                target_deck_name = (
                    f"{deck_name}::Level {record.level:02d}"
                    if group_by_level
                    else deck_name
                )
                did = _ensure_deck(target_deck_name)

                fields = build_note_fields(record, highlight_vocab=highlight_vocab)
                tags = build_note_tags(record)
                tags_str = " " + " ".join(tags) + " " if tags else ""
                flds_str = "\x1f".join(fields)
                guid = deterministic_guid(record.sentence_id)
                note_id = now_ms + idx * 2 + 1
                card_id = now_ms + idx * 2 + 2

                note_rows.append(
                    (
                        note_id,
                        guid,
                        WANIKANI_SENTENCE_MODEL_ID,
                        now_sec,
                        -1,
                        tags_str,
                        flds_str,
                        fields[0],
                        _field_checksum(fields[0]),
                        0,
                        "",
                    )
                )
                card_rows.append(
                    (
                        card_id,
                        note_id,
                        did,
                        0,
                        now_sec,
                        -1,
                        0,
                        0,
                        idx + 1,
                        0,
                        0,
                        0,
                        0,
                        0,
                        0,
                        0,
                        0,
                        "",
                    )
                )

            cursor.execute(
                "INSERT INTO col VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    1,
                    now_sec,
                    now_ms,
                    now_ms,
                    11,
                    0,
                    0,
                    0,
                    json.dumps(conf_json),
                    json.dumps(models_json),
                    json.dumps(decks_json),
                    json.dumps(dconf_json),
                    "{}",
                ),
            )
            cursor.executemany(
                "INSERT INTO notes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                note_rows,
            )
            cursor.executemany(
                "INSERT INTO cards VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                card_rows,
            )
            conn.commit()
        finally:
            conn.close()

        media_manifest: dict[str, str] = {}
        with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.write(db_path, arcname="collection.anki2")
            for media_idx, media_path in enumerate(media_files):
                index_key = str(media_idx)
                media_manifest[index_key] = media_path.name
                zf.write(media_path, arcname=index_key)
            zf.writestr("media", json.dumps(media_manifest))

    return output_path
