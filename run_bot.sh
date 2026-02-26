#!/bin/bash
# ─── AI Stock Analyst — Background Service Launcher ───────────
# Runs the Flask app + trading bots as a background process.
# Survives terminal close, screen sleep, SSH disconnect.
#
# Usage:
#   ./run_bot.sh start    — Start the app in the background
#   ./run_bot.sh stop     — Stop the background app
#   ./run_bot.sh restart  — Restart the app
#   ./run_bot.sh status   — Check if running
#   ./run_bot.sh logs     — Tail the live log
# ──────────────────────────────────────────────────────────────

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$APP_DIR/.bot.pid"
LOG_FILE="$APP_DIR/logs/bot.log"
PYTHON="python3"

# Ensure logs directory exists
mkdir -p "$APP_DIR/logs"

start_app() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            echo "Already running (PID $PID)"
            echo "Use: ./run_bot.sh logs   to tail the log"
            echo "     ./run_bot.sh stop   to stop"
            return 1
        else
            rm -f "$PID_FILE"
        fi
    fi

    echo "Starting AI Stock Analyst..."
    cd "$APP_DIR"

    # Rotate log if > 10MB
    if [ -f "$LOG_FILE" ] && [ $(stat -f%z "$LOG_FILE" 2>/dev/null || stat --printf="%s" "$LOG_FILE" 2>/dev/null) -gt 10485760 ]; then
        mv "$LOG_FILE" "$LOG_FILE.$(date +%Y%m%d_%H%M%S).bak"
        echo "Log rotated."
    fi

    # Start with nohup — survives terminal close and screen sleep
    nohup "$PYTHON" app.py >> "$LOG_FILE" 2>&1 &
    APP_PID=$!
    echo "$APP_PID" > "$PID_FILE"

    sleep 2
    if kill -0 "$APP_PID" 2>/dev/null; then
        echo "Started successfully (PID $APP_PID)"
        echo "App URL:  http://127.0.0.1:5001"
        echo "Log file: $LOG_FILE"
        echo ""
        echo "The bot will keep running even if you:"
        echo "  - Close the terminal"
        echo "  - Close the browser"
        echo "  - Let your screen sleep"
        echo ""
        echo "Use ./run_bot.sh logs   to watch live activity"
        echo "Use ./run_bot.sh stop   to shut down"
    else
        echo "Failed to start. Check log: $LOG_FILE"
        rm -f "$PID_FILE"
        return 1
    fi
}

stop_app() {
    if [ ! -f "$PID_FILE" ]; then
        echo "Not running (no PID file)"
        # Also try to kill by port
        PORT_PID=$(lsof -ti:5001 2>/dev/null)
        if [ -n "$PORT_PID" ]; then
            echo "Found process on port 5001 (PID $PORT_PID) — stopping..."
            kill "$PORT_PID" 2>/dev/null
            sleep 1
            kill -9 "$PORT_PID" 2>/dev/null
            echo "Stopped."
        fi
        return 0
    fi

    PID=$(cat "$PID_FILE")
    echo "Stopping (PID $PID)..."

    kill "$PID" 2>/dev/null
    sleep 2

    if kill -0 "$PID" 2>/dev/null; then
        echo "Graceful stop failed — force killing..."
        kill -9 "$PID" 2>/dev/null
        sleep 1
    fi

    # Also clean up any child processes on port 5001
    PORT_PID=$(lsof -ti:5001 2>/dev/null)
    if [ -n "$PORT_PID" ]; then
        kill -9 "$PORT_PID" 2>/dev/null
    fi

    rm -f "$PID_FILE"
    echo "Stopped."
}

check_status() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            UPTIME=$(ps -o etime= -p "$PID" 2>/dev/null | xargs)
            echo "Running (PID $PID, uptime: $UPTIME)"
            echo "App URL: http://127.0.0.1:5001"
            return 0
        else
            echo "PID file exists but process is dead — cleaning up"
            rm -f "$PID_FILE"
            return 1
        fi
    else
        echo "Not running"
        return 1
    fi
}

tail_logs() {
    if [ ! -f "$LOG_FILE" ]; then
        echo "No log file yet. Start the app first: ./run_bot.sh start"
        return 1
    fi
    echo "Tailing $LOG_FILE (Ctrl+C to stop watching)..."
    echo "──────────────────────────────────────────────"
    tail -f "$LOG_FILE"
}

case "${1:-}" in
    start)
        start_app
        ;;
    stop)
        stop_app
        ;;
    restart)
        stop_app
        sleep 1
        start_app
        ;;
    status)
        check_status
        ;;
    logs|log)
        tail_logs
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status|logs}"
        echo ""
        echo "  start    Start the app in the background"
        echo "  stop     Stop the background app"
        echo "  restart  Restart the app"
        echo "  status   Check if running"
        echo "  logs     Tail the live log output"
        ;;
esac
