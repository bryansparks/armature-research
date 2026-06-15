# Contributing to Research-Project

Thanks for your interest! This project is an [Armature](https://github.com/bryansparks/armature) showcase workflow, and contributions that improve the workflow quality, documentation, or add new research capabilities are welcome.

## Ways to Contribute

- **Bug reports**: Open an issue with a clear description and steps to reproduce.
- **Feature ideas**: Open an issue with the `enhancement` label.
- **Code changes**: Fork, branch, and submit a pull request.

## Development Setup

1. Clone the repo and install dependencies:
   ```bash
   pip install -e ".[dev]"
   ```
2. Copy `.env.example` to `.env` and add your API keys.
3. Run a test workflow:
   ```bash
   armature run workflows/research-analyst.yaml --input "topic=your research topic"
   ```

## Pull Request Guidelines

- Keep changes focused — one feature or fix per PR.
- Update documentation (README, docstrings) as needed.
- Run `pytest` before submitting — all tests should pass.

## Code Style

- Follow the existing patterns in the codebase.
- Python: type hints, docstrings, and `ruff` formatting.
- YAML: match the existing workflow structure and commenting style.

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](./LICENSE).