# WaniKani Exporter

Download vocabulary context sentences from the [WaniKani API v2](https://docs.api.wanikani.com/20170710/) into structured local sentence lists (`JSON` / `CSV`) and generate TTS-ready Anki decks (`.apkg`).

## Features

- **Two-Stage Pipeline**:
  1. `download`: Fetches vocabulary (`vocabulary` and `kana_vocabulary`) and context sentences from WaniKani API v2, with support for level filtering (`--levels`), user assignment / SRS filtering (`--unlocked-only`, `--min-srs-stage`), rate-limit handling, and incremental local caching.
  2. `build-deck`: Builds a native `.apkg` Anki package from the saved `sentences.json` or `sentences.csv` list.
  3. `all`: Runs both steps in one command.
- **Idempotent Re-Imports**: Every card uses a deterministic Anki Note GUID derived from `wk_<subject_id>_<sentence_index>`, so re-importing updated `.apkg` files updates cards in place without resetting your Anki review history.
- **TTS-Ready Card Design**:
  - `SentenceJA` is stored as 100% plain Japanese text (HTML highlighting is isolated in `SentenceJAHighlighted`).
  - Includes a dedicated `SentenceAudio` field wrapped in a conditional `{{#SentenceAudio}}...{{/SentenceAudio}}` block and optional `--media-dir` bundling for future TTS audio files.

## Setup

1. Copy `.env.example` to `.env` and add your WaniKani v2 Personal Access Token:
   ```bash
   cp .env.example .env
   ```
2. Install the package (or run directly via `PYTHONPATH=src python3 -m wanikani_exporter`):
   ```bash
   pip install -e ".[dev]"
   ```

## Usage

### 1. Download Vocabulary Context Sentences (`JSON` & `CSV`)

```bash
# Download all levels
wk-export download

# Download only levels 1-10
wk-export download --levels 1-10

# Download only vocabulary you have unlocked on WaniKani
wk-export download --unlocked-only

# Download only vocabulary at Guru I (SRS stage 5) or higher
wk-export download --min-srs-stage 5
```

This writes:
- `data/sentences.json`
- `data/sentences.csv`

### 2. Build an Anki Deck (`.apkg`) from the Sentence List

```bash
# Build single deck from data/sentences.json -> output/wanikani_sentences.apkg
wk-export build-deck

# Group cards into hierarchical subdecks by WaniKani level
wk-export build-deck --group-by-level

# Bundle future TTS audio files referenced by sentence_audio tags
wk-export build-deck --media-dir data/audio
```

### 3. Run Both Steps End-to-End

```bash
wk-export all --levels 1-5 --group-by-level
```

## Anki Note Fields (11 Fields)

| # | Field Name | Description |
| :- | :--- | :--- |
| 1 | `SentenceID` | Deterministic ID (`wk_<subject_id>_<sentence_index>`) |
| 2 | `SentenceJA` | Plain Japanese context sentence (no HTML) |
| 3 | `SentenceJAHighlighted` | Japanese sentence with `<span class="vocab-hl">` around target word |
| 4 | `SentenceEN` | English translation of the context sentence |
| 5 | `SentenceAudio` | Reserved for future TTS `[sound:...]` tag (empty by default) |
| 6 | `Vocabulary` | Target vocabulary word (kanji/kana) |
| 7 | `Reading` | Kana reading(s) |
| 8 | `Meaning` | English meaning(s) |
| 9 | `PartsOfSpeech` | Grammatical part(s) of speech |
| 10 | `Level` | WaniKani level (`1`–`60`) |
| 11 | `DocumentURL` | Permalink to WaniKani vocabulary page |

## Running Tests

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```
