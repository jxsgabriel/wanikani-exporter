"""Command-line interface for downloading WaniKani context sentences and building Anki decks."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
from typing import Sequence

from wanikani_exporter.anki_builder import build_anki_deck
from wanikani_exporter.extractor import (
    extract_sentence_records,
    load_existing_audio_map,
    load_sentences,
    save_sentences_csv,
    save_sentences_json,
)
from wanikani_exporter.models import VocabSentenceRecord
from wanikani_exporter.wk_client import DEFAULT_CACHE_PATH, WaniKaniAPIError, WaniKaniClient


def _load_dotenv_file(dotenv_path: Path = Path(".env")) -> None:
    """Load key=value pairs from `.env` into `os.environ` if not already set."""
    try:
        from dotenv import load_dotenv  # type: ignore[import-not-found]

        load_dotenv(dotenv_path=dotenv_path, override=False)
        return
    except ImportError:
        pass

    if not dotenv_path.is_file():
        return

    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def parse_levels(levels_arg: str | None) -> list[int] | None:
    """Parse a level specification such as '1-10' or '1,3,5-7' into a sorted list of ints."""
    if not levels_arg or not levels_arg.strip():
        return None

    levels: set[int] = set()
    for part in levels_arg.split(","):
        token = part.strip()
        if not token:
            continue
        if "-" in token:
            start_str, _, end_str = token.partition("-")
            try:
                start = int(start_str.strip())
                end = int(end_str.strip())
            except ValueError as exc:
                raise ValueError(f"Invalid level range '{token}'.") from exc
            if start > end:
                raise ValueError(f"Invalid level range '{token}': start > end.")
            for lvl in range(start, end + 1):
                if lvl < 1 or lvl > 60:
                    raise ValueError(f"WaniKani level {lvl} out of range (1-60).")
                levels.add(lvl)
        else:
            try:
                lvl = int(token)
            except ValueError as exc:
                raise ValueError(f"Invalid level number '{token}'.") from exc
            if lvl < 1 or lvl > 60:
                raise ValueError(f"WaniKani level {lvl} out of range (1-60).")
            levels.add(lvl)

    return sorted(levels) if levels else None


def _add_download_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--api-token",
        default=None,
        help="WaniKani API v2 token (defaults to WANIKANI_API_TOKEN env var or .env).",
    )
    parser.add_argument(
        "--levels",
        default=None,
        help="Filter by WaniKani levels, e.g. '1-10' or '1,2,5-8' (default: all 1-60).",
    )
    parser.add_argument(
        "--unlocked-only",
        action="store_true",
        help="Only export vocabulary unlocked by the user (queries /v2/assignments).",
    )
    parser.add_argument(
        "--min-srs-stage",
        type=int,
        default=None,
        help="Only export vocabulary at or above this SRS stage (1=Apprentice 1, 5=Guru 1, 9=Burned).",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=Path("data/sentences.json"),
        help="Output path for the JSON sentence list (default: data/sentences.json).",
    )
    parser.add_argument(
        "--csv-out",
        type=Path,
        default=Path("data/sentences.csv"),
        help="Output path for the CSV sentence list (default: data/sentences.csv).",
    )
    parser.add_argument(
        "--cache-file",
        type=Path,
        default=DEFAULT_CACHE_PATH,
        help=f"Path to raw subjects cache file (default: {DEFAULT_CACHE_PATH}).",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable reading/writing the local WaniKani subjects cache.",
    )


def _add_build_deck_arguments(
    parser: argparse.ArgumentParser,
    include_input_arg: bool = True,
) -> None:
    if include_input_arg:
        parser.add_argument(
            "--input",
            type=Path,
            default=Path("data/sentences.json"),
            help="Path to input sentence list (.json or .csv, default: data/sentences.json).",
        )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output/wanikani_sentences.apkg"),
        help="Output path for the generated Anki package (default: output/wanikani_sentences.apkg).",
    )
    parser.add_argument(
        "--deck-name",
        default="WaniKani Context Sentences",
        help="Base Anki deck name (default: 'WaniKani Context Sentences').",
    )
    parser.add_argument(
        "--group-by-level",
        action="store_true",
        help="Organize cards into hierarchical subdecks by WaniKani level.",
    )
    parser.add_argument(
        "--highlight-vocab",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Highlight target vocabulary word on the front of the card (default: enabled).",
    )
    parser.add_argument(
        "--media-dir",
        type=Path,
        default=None,
        help="Optional directory containing audio files referenced by sentence_audio tags.",
    )


def build_parser() -> argparse.ArgumentParser:
    """Create the top-level CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="wk-export",
        description="Download WaniKani vocabulary context sentences and generate TTS-ready Anki decks.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    download_parser = subparsers.add_parser(
        "download",
        help="Download vocabulary context sentences from WaniKani API v2 to JSON/CSV.",
    )
    _add_download_arguments(download_parser)

    build_parser_sub = subparsers.add_parser(
        "build-deck",
        help="Generate an Anki .apkg deck from an exported JSON or CSV sentence list.",
    )
    _add_build_deck_arguments(build_parser_sub, include_input_arg=True)

    all_parser = subparsers.add_parser(
        "all",
        help="Download vocabulary context sentences from WaniKani and immediately build the Anki deck.",
    )
    _add_download_arguments(all_parser)
    _add_build_deck_arguments(all_parser, include_input_arg=False)

    return parser


def run_download(
    args: argparse.Namespace,
    client: WaniKaniClient | None = None,
) -> list[VocabSentenceRecord]:
    """Execute the `download` step and write JSON and CSV sentence lists."""
    levels = parse_levels(args.levels)

    if client is None:
        _load_dotenv_file()
        token = args.api_token or os.environ.get("WANIKANI_API_TOKEN", "")
        if not token.strip():
            raise ValueError(
                "Missing WaniKani API token. Pass --api-token or set WANIKANI_API_TOKEN."
            )
        client = WaniKaniClient(api_token=token)

    subjects = client.fetch_vocabulary_subjects(
        levels=levels,
        use_cache=not args.no_cache,
        cache_path=args.cache_file,
    )

    filter_by_assignments = bool(args.unlocked_only or args.min_srs_stage is not None)
    assignments_by_subject = None
    if filter_by_assignments:
        assignments_by_subject = client.fetch_assignments(
            levels=levels,
            unlocked_only=args.unlocked_only,
            min_srs_stage=args.min_srs_stage,
        )

    existing_audio = load_existing_audio_map(args.json_out)
    if not existing_audio and args.csv_out:
        existing_audio = load_existing_audio_map(args.csv_out)

    records = extract_sentence_records(
        subjects=subjects,
        assignments_by_subject=assignments_by_subject,
        filter_by_assignments=filter_by_assignments,
        existing_audio_by_id=existing_audio,
    )

    if args.json_out:
        save_sentences_json(records, args.json_out)
        print(f"Saved {len(records)} context sentences to {args.json_out}")
    if args.csv_out:
        save_sentences_csv(records, args.csv_out)
        print(f"Saved {len(records)} context sentences to {args.csv_out}")

    return records


def run_build_deck(
    args: argparse.Namespace,
    records: list[VocabSentenceRecord] | None = None,
) -> Path:
    """Execute the `build-deck` step from in-memory records or `--input` file."""
    if records is None:
        input_path: Path = args.input
        if not input_path.exists():
            raise FileNotFoundError(
                f"Sentence list file not found: {input_path}. Run 'wk-export download' first."
            )
        records = load_sentences(input_path)

    output_path = build_anki_deck(
        records=records,
        output_path=args.output,
        deck_name=args.deck_name,
        group_by_level=args.group_by_level,
        highlight_vocab=args.highlight_vocab,
        media_dir=args.media_dir,
    )
    print(f"Built Anki deck with {len(records)} cards at {output_path}")
    return output_path


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "download":
            run_download(args)
        elif args.command == "build-deck":
            run_build_deck(args)
        elif args.command == "all":
            records = run_download(args)
            run_build_deck(args, records=records)
        else:
            parser.print_help()
            return 1
    except (ValueError, FileNotFoundError, WaniKaniAPIError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
