# Contributing to pyNetScope

Thank you for your interest in contributing to pyNetScope! This document provides guidelines and instructions to help you get started.

## Code of Conduct
Please be respectful and professional in all interactions.

## Local Setup
1. Clone the repository.
2. Create and activate a virtual environment.
3. Install pyNetScope in editable mode with development dependencies:
   ```bash
   pip install -e ".[dev]"
   ```

## Development Workflow
- **Formatting**: Run `ruff format .` to format the code.
- **Linting**: Run `ruff check .` to check for syntax and style issues.
- **Type Checking**: Run `mypy pynetscope` or `pyright` to run type checkers.
- **Tests**: Run `pytest` to execute the test suite. Make sure all tests pass before submitting a pull request.

## Submitting Pull Requests
1. Create a branch for your changes: `git checkout -b feature/my-feature`.
2. Implement your changes and add tests.
3. Run formatting, linting, type checks, and tests.
4. Push your branch and open a PR against the `main` branch.
