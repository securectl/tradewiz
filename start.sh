#!/usr/bin/env bash
# Start the AI Stock Analyst server

DIR="$(cd "$(dirname "$0")" && pwd)"
PIDFILE="$DIR/.app.pid"

if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "Server is already running (PID $(cat "$PIDFILE"))"
    exit 1
fi

cd "$DIR"

# Activate virtual environment if present
if [ -d "venv" ]; then
    source venv/bin/activate
elif [ -d ".venv" ]; then
    source .venv/bin/activate
fi

echo "Starting AI Stock Analyst on port 5001..."
PYTHON=$(command -v python3 || command -v python)
nohup "$PYTHON" app.py > "$DIR/app.log" 2>&1 &
APP_PID=$!
echo "$APP_PID" > "$PIDFILE"
echo "Server started (PID $APP_PID). Logs: $DIR/app.log"
