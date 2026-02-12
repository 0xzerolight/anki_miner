"""Console presenter for CLI output."""

from anki_miner.models import (
    ProcessingResult,
    TokenizedWord,
    ValidationResult,
)


class ConsolePresenter:
    """Present output to console (CLI implementation)."""

    def show_info(self, message: str) -> None:
        """Display an informational message."""
        print(message)

    def show_success(self, message: str) -> None:
        """Display a success message."""
        print(f"[OK] {message}")

    def show_warning(self, message: str) -> None:
        """Display a warning message."""
        print(f"[WARN] {message}")

    def show_error(self, message: str) -> None:
        """Display an error message."""
        print(f"[ERROR] {message}")

    def show_validation_result(self, result: ValidationResult) -> None:
        """Display the result of system validation."""
        print("\nValidation Results:")
        print(f"  {'[OK]' if result.ankiconnect_ok else '[FAIL]'} AnkiConnect")
        print(f"  {'[OK]' if result.ffmpeg_ok else '[FAIL]'} ffmpeg")
        print(f"  {'[OK]' if result.deck_exists else '[FAIL]'} Anki Deck")
        print(f"  {'[OK]' if result.note_type_exists else '[FAIL]'} Note Type")

        if result.issues:
            print("\nIssues:")
            for issue in result.issues:
                print(f"  {issue}")

        if result.all_passed:
            print("\n[OK] All validations passed")
        else:
            print("\n[FAIL] Some validations failed")

    def show_processing_result(self, result: ProcessingResult) -> None:
        """Display the result of processing an episode."""
        print("\nProcessing Complete:")
        print(f"  Total words found: {result.total_words_found}")
        print(f"  New words found: {result.new_words_found}")
        print(f"  Cards created: {result.cards_created}")
        print(f"  Time elapsed: {result.elapsed_time:.1f}s")

        if result.errors:
            print("\nErrors:")
            for error in result.errors:
                print(f"  {error}")

    def show_word_preview(self, words: list[TokenizedWord]) -> None:
        """Display a preview of discovered words."""
        print(f"\nWord Preview ({len(words)} words):")
        print("=" * 60)

        for i, word in enumerate(words[:20], 1):  # Show first 20
            print(f"{i:2d}. {word.lemma:15s} ({word.reading})")

        if len(words) > 20:
            print(f"... and {len(words) - 20} more words")


class ConsoleProgressCallback:
    """Console implementation of progress callback."""

    def __init__(self):
        """Initialize the progress callback."""
        self.total = 0
        self.current = 0
        self.description = ""

    def on_start(self, total: int, description: str) -> None:
        """Called when an operation starts."""
        self.total = total
        self.current = 0
        self.description = description
        print(f"\n{description}...")

    def on_progress(self, current: int, item_description: str) -> None:
        """Called when an item is processed."""
        self.current = current
        print(f"  [{current}/{self.total}] {item_description}")

    def on_complete(self) -> None:
        """Called when an operation completes."""
        print(f"  [OK] Complete: {self.current}/{self.total}")

    def on_error(self, item_description: str, error_message: str) -> None:
        """Called when an item fails."""
        print(f"  [ERROR] {item_description}: {error_message}")
