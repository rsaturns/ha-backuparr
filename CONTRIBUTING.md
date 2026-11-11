# Contributing

Thanks for helping. Bug reports, ideas and pull requests are all welcome.

## Reporting a problem

Open an issue with the bug report form. Your Home Assistant, Backuparr and
integration versions help, and so does the integration's diagnostics file
(it leaves out Backuparr's address and token). Security problems go through
[private reporting](SECURITY.md) instead.

## Pull requests

- One focused change per pull request, branched from an up-to-date `main`.
- Everything the integration needs stays in `custom_components/backuparr/`;
  no requirements Home Assistant doesn't already have.
- Add or update tests, and run them:

  ```bash
  python3.14 -m venv .venv
  .venv/bin/pip install -r requirements-dev.txt
  .venv/bin/pytest -q
  .venv/bin/ruff check . && .venv/bin/ruff format --check .
  ```

- hassfest and the HACS action run on every pull request.
- Add a line to `CHANGELOG.md` under `## [Unreleased]` if users will notice.
- If AI tools helped, review and test every line, and say so in the pull
  request.

By taking part you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).
