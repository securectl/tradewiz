#!/usr/bin/env bash
# Stop the AI Stock Analyst server

DIR="$(cd "$(dirname "$0")" && pwd)"
PIDFILE="$DIR/.app.pid"

if [ ! -f "$PIDFILE" ]; then
    echo "No PID file found. Server may not be running."
    exit 1
fi

PID=$(cat "$PIDFILE")

if kill -0 "$PID" 2>/dev/null; then
    echo "Stopping AI Stock Analyst (PID $PID)..."
    kill "$PID"
    # Wait up to 10 seconds for graceful shutdown
    for i in $(seq 1 10); do
        if ! kill -0 "$PID" 2>/dev/null; then
            break
        fi
        sleep 1
    done
    # Force kill if still running
    if kill -0 "$PID" 2>/dev/null; then
        echo "Graceful shutdown timed out. Force killing..."
        kill -9 "$PID"
    fi
    echo "Server stopped."
else
    echo "Process $PID is not running."
fi

rm -f "$PIDFILE"
