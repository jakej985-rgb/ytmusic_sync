#!/usr/bin/env bash
set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

# Start backend in background if not already running
if ! curl -s http://127.0.0.1:8765/api/status >/dev/null 2>&1; then
    echo "Starting local backend service..."
    ./run_backend.sh &
    BACKEND_PID=$!
    trap "kill $BACKEND_PID 2>/dev/null || true" EXIT
    sleep 2
fi

echo "Launching YTM Sync desktop app..."
cd app
flutter run -d linux
