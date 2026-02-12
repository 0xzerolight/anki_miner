"""Main CLI entry point for anki_miner."""

import argparse
import sys

from anki_miner import __version__
from anki_miner.cli.commands import create_shortcut, mine, mine_folder


def main():
    """Main CLI entry point with subcommands."""
    parser = argparse.ArgumentParser(
        prog="anki_miner",
        description="Tool for Japanese vocabulary mining from anime subtitles",
        epilog="Use 'anki_miner <command> --help' for command-specific help",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # anki_miner mine <video> <subtitle>
    mine_parser = subparsers.add_parser(
        "mine",
        help="Mine vocabulary from single episode",
        description="Extract Japanese vocabulary from anime subtitles and create Anki cards",
    )
    mine_parser.add_argument("video", help="Path to video file")
    mine_parser.add_argument("subtitle", help="Path to subtitle file (.ass, .srt, .ssa)")
    mine_parser.add_argument(
        "--preview",
        action="store_true",
        help="Preview words without creating cards",
    )
    mine_parser.add_argument(
        "--offset",
        type=float,
        default=0.0,
        help="Subtitle timing offset in seconds (negative = earlier, positive = later)",
    )

    # anki_miner mine-folder <folder>
    folder_parser = subparsers.add_parser(
        "mine-folder",
        help="Mine vocabulary from folder of episodes",
        description="Batch process entire folder of video/subtitle pairs",
    )
    folder_parser.add_argument("folder", help="Path to folder containing episodes")
    folder_parser.add_argument(
        "--preview",
        action="store_true",
        help="Preview words without creating cards",
    )
    folder_parser.add_argument(
        "--offset",
        type=float,
        default=0.0,
        help="Subtitle timing offset in seconds",
    )

    # anki_miner create-shortcut
    subparsers.add_parser(
        "create-shortcut",
        help="Create a desktop shortcut for the GUI",
        description="Create a desktop/app menu shortcut for Anki Miner GUI",
    )

    args = parser.parse_args()

    # Dispatch to appropriate command
    if args.command == "mine":
        return mine.mine_command(args)
    elif args.command == "mine-folder":
        return mine_folder.mine_folder_command(args)
    elif args.command == "create-shortcut":
        return create_shortcut.create_shortcut_command(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
