# Blink one-time install

Use these commands once on a fresh Ubuntu/Debian machine.

## 1) Clone the repository

```bash
cd ~
git clone <your-blink-repo-url> blink
cd blink
```

## 2) Install system packages

```bash
sudo apt-get update
sudo apt-get install -y git python3 python3-venv python3-pip
```

## 3) Create and activate virtualenv

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

## 4) Install Blink (editable + dev tools)

This installs the CLI, crawler stack, Starlette/uvicorn for `blink serve`, and test extras in one step:

```bash
python -m pip install -e ".[dev]"
```

Production-style install without dev tools (still includes `blink serve`):

```bash
python -m pip install .
```

## 5) Install Playwright browser/runtime dependencies

```bash
playwright install --with-deps chromium
```

After this one-time setup, jump to the workflow commands in `README.md`.
