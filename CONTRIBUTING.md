# Contributing to Anki Miner

This covers how to report bugs, suggest features, and submit code changes.

## Reporting Bugs

Open an issue on [GitHub Issues](https://github.com/0xzerolight/anki_miner/issues) with:

- Your OS and Python version
- Steps to reproduce the issue
- Expected vs actual behavior
- Any error messages or logs

## Suggesting Features

Open a feature request on [GitHub Issues](https://github.com/0xzerolight/anki_miner/issues) describing:

- What you'd like to see added
- Why it would be useful
- Any implementation ideas you have

## Development Setup

```bash
# Clone the repository
git clone https://github.com/0xzerolight/anki_miner.git
cd anki_miner

# Create a virtual environment
python -m venv venv
source venv/bin/activate  # Linux/macOS
# or: venv\Scripts\activate  # Windows

# Install in development mode with dev dependencies
pip install -e ".[dev]"

# Set up pre-commit hooks (runs black and ruff before each commit)
pre-commit install
```

## Code Style

This project uses the following tools, configured in `pyproject.toml`:

- **[black](https://black.readthedocs.io/)** — Code formatting (100 character line length)
- **[ruff](https://docs.astral.sh/ruff/)** — Linting
- **[mypy](https://mypy.readthedocs.io/)** — Type checking

Before submitting a PR, ensure your code passes:

```bash
# Format code
black .

# Lint
ruff check .

# Type check
mypy anki_miner
```

## Running Tests

```bash
# Run all tests with coverage
pytest

# Run a specific test file
pytest tests/unit/test_word_filter.py

# Run with verbose output
pytest -v
```

## Pull Request Process

1. Fork the repository and create a feature branch from `main`
2. Make your changes with clear, descriptive commit messages
3. Add tests for new functionality
4. Ensure all tests pass and code style checks are clean
5. Open a pull request with a description of what you changed and why
