#!/usr/bin/env bash
set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

if [ ! -d "backend/.venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv backend/.venv
    ./backend/.venv/bin/pip install --upgrade pip
    ./backend/.venv/bin/pip install -r backend/requirements.txt
fi

echo "Starting YTM Sync backend service on 127.0.0.1:8765..."
export PYTHONPATH="$DIR/backend"
exec "$DIR/backend/.venv/bin/python3" -m ytm_service.main
