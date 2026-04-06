#!/bin/bash
#
# Health Check Script for AI Stock Analyst
# Checks all application layers: system resources, Docker containers,
# database, nginx, Flask app, SSL, and external dependencies.
#
# Usage:
#   ./healthcheck.sh           # Full check with colored output
#   ./healthcheck.sh --quiet   # Exit code only (for cron/monitoring)
#   ./healthcheck.sh --fix     # Attempt auto-fix for common issues
#

set -euo pipefail

# --- Config ---
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
DOMAIN="tradewiz.market"
COMPOSE_CMD="docker compose"
APP_CONTAINER="ai_stock_analyst-app-1"
NGINX_CONTAINER="ai_stock_analyst-nginx-1"
DB_CONTAINER="ai_stock_analyst-db-1"
CERTBOT_CONTAINER="ai_stock_analyst-certbot-1"
APP_PORT=5000
LOG_FILE="${APP_DIR}/healthcheck.log"

# Detect docker compose command
if ! $COMPOSE_CMD version &>/dev/null 2>&1; then
    COMPOSE_CMD="docker-compose"
fi

QUIET=false
AUTO_FIX=false
FAIL_COUNT=0
WARN_COUNT=0
TOTAL_CHECKS=0

for arg in "$@"; do
    case "$arg" in
        --quiet|-q) QUIET=true ;;
        --fix|-f)   AUTO_FIX=true ;;
    esac
done

# --- Output helpers ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log() {
    local ts
    ts=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[$ts] $*" >> "$LOG_FILE"
}

pass() {
    TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
    $QUIET || printf "  ${GREEN}[OK]${NC}   %s\n" "$1"
    log "OK: $1"
}

fail() {
    TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
    FAIL_COUNT=$((FAIL_COUNT + 1))
    $QUIET || printf "  ${RED}[FAIL]${NC} %s\n" "$1"
    log "FAIL: $1"
}

warn() {
    TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
    WARN_COUNT=$((WARN_COUNT + 1))
    $QUIET || printf "  ${YELLOW}[WARN]${NC} %s\n" "$1"
    log "WARN: $1"
}

section() {
    $QUIET || printf "\n${BLUE}=== %s ===${NC}\n" "$1"
    log "--- $1 ---"
}

# --- Checks ---

check_system_resources() {
    section "System Resources"

    # Disk usage
    local disk_pct
    disk_pct=$(df / | awk 'NR==2 {gsub(/%/,""); print $5}')
    if [ "$disk_pct" -ge 90 ]; then
        fail "Disk usage critical: ${disk_pct}%"
    elif [ "$disk_pct" -ge 80 ]; then
        warn "Disk usage high: ${disk_pct}%"
    else
        pass "Disk usage: ${disk_pct}%"
    fi

    # Memory
    local mem_pct
    mem_pct=$(free | awk '/Mem:/ {printf "%.0f", $3/$2 * 100}')
    if [ "$mem_pct" -ge 90 ]; then
        fail "Memory usage critical: ${mem_pct}%"
    elif [ "$mem_pct" -ge 80 ]; then
        warn "Memory usage high: ${mem_pct}%"
    else
        pass "Memory usage: ${mem_pct}%"
    fi

    # Load average
    local load cores
    load=$(awk '{print $1}' /proc/loadavg)
    cores=$(nproc)
    local load_int=${load%.*}
    if [ "$load_int" -ge "$((cores * 2))" ]; then
        fail "Load average critical: ${load} (${cores} cores)"
    elif [ "$load_int" -ge "$cores" ]; then
        warn "Load average high: ${load} (${cores} cores)"
    else
        pass "Load average: ${load} (${cores} cores)"
    fi

    # Swap usage
    local swap_total swap_used
    swap_total=$(free | awk '/Swap:/ {print $2}')
    swap_used=$(free | awk '/Swap:/ {print $3}')
    if [ "$swap_total" -gt 0 ]; then
        local swap_pct=$((swap_used * 100 / swap_total))
        if [ "$swap_pct" -ge 50 ]; then
            warn "Swap usage: ${swap_pct}%"
        else
            pass "Swap usage: ${swap_pct}%"
        fi
    fi
}

check_docker() {
    section "Docker Containers"

    if ! docker info &>/dev/null; then
        fail "Docker daemon not running"
        return
    fi
    pass "Docker daemon running"

    local containers=("$APP_CONTAINER" "$NGINX_CONTAINER" "$DB_CONTAINER" "$CERTBOT_CONTAINER")
    local names=("App (gunicorn)" "Nginx" "PostgreSQL" "Certbot")

    for i in "${!containers[@]}"; do
        local cname="${containers[$i]}"
        local label="${names[$i]}"
        local status
        status=$(docker inspect --format='{{.State.Status}}' "$cname" 2>/dev/null || echo "not_found")

        if [ "$status" = "running" ]; then
            # Check for recent restarts
            local started_at
            started_at=$(docker inspect --format='{{.State.StartedAt}}' "$cname" 2>/dev/null)
            local started_epoch
            started_epoch=$(date -d "$started_at" +%s 2>/dev/null || echo 0)
            local now_epoch
            now_epoch=$(date +%s)
            local uptime_min=$(( (now_epoch - started_epoch) / 60 ))

            if [ "$uptime_min" -lt 5 ]; then
                warn "${label}: running but recently restarted (${uptime_min}m ago)"
            else
                pass "${label}: running (uptime: ${uptime_min}m)"
            fi
        elif [ "$status" = "not_found" ]; then
            fail "${label}: container not found"
            if $AUTO_FIX; then
                $QUIET || printf "       Attempting: ${COMPOSE_CMD} up -d\n"
                (cd "$APP_DIR" && $COMPOSE_CMD up -d 2>&1 | tail -5)
            fi
        else
            fail "${label}: status=$status"
            if $AUTO_FIX; then
                $QUIET || printf "       Attempting restart...\n"
                docker restart "$cname" 2>&1 | tail -2
            fi
        fi
    done
}

check_database() {
    section "Database"

    # PostgreSQL health from Docker
    local health
    health=$(docker inspect --format='{{.State.Health.Status}}' "$DB_CONTAINER" 2>/dev/null || echo "unknown")
    if [ "$health" = "healthy" ]; then
        pass "PostgreSQL health: $health"
    else
        fail "PostgreSQL health: $health"
        return
    fi

    # Test actual query
    local result
    result=$(docker exec "$DB_CONTAINER" psql -U stockbot -d stockbot -t -c "SELECT 1" 2>&1)
    if echo "$result" | grep -q "1"; then
        pass "PostgreSQL query test: OK"
    else
        fail "PostgreSQL query test: $result"
    fi

    # Check connection count
    local conn_count
    conn_count=$(docker exec "$DB_CONTAINER" psql -U stockbot -d stockbot -t -c "SELECT count(*) FROM pg_stat_activity WHERE datname='stockbot'" 2>/dev/null | tr -d ' ')
    if [ -n "$conn_count" ] && [ "$conn_count" -ge 80 ]; then
        warn "PostgreSQL connections high: $conn_count"
    elif [ -n "$conn_count" ]; then
        pass "PostgreSQL connections: $conn_count"
    fi

    # Check DB size
    local db_size
    db_size=$(docker exec "$DB_CONTAINER" psql -U stockbot -d stockbot -t -c "SELECT pg_size_pretty(pg_database_size('stockbot'))" 2>/dev/null | tr -d ' ')
    if [ -n "$db_size" ]; then
        pass "Database size: $db_size"
    fi
}

check_nginx() {
    section "Nginx"

    # Test nginx config
    local config_test
    config_test=$(docker exec "$NGINX_CONTAINER" nginx -t 2>&1)
    if echo "$config_test" | grep -q "successful"; then
        pass "Nginx config: valid"
    else
        fail "Nginx config: invalid — $config_test"
    fi

    # Port 80
    if curl -sS -o /dev/null -w "%{http_code}" --max-time 5 http://localhost/ 2>/dev/null | grep -qE "^(301|200)$"; then
        pass "Port 80: responding (HTTP→HTTPS redirect)"
    else
        fail "Port 80: not responding"
    fi

    # Port 443
    local https_code
    https_code=$(curl -sSk -o /dev/null -w "%{http_code}" --max-time 5 https://localhost/ 2>/dev/null)
    if [ "$https_code" = "200" ] || [ "$https_code" = "302" ]; then
        pass "Port 443: responding (HTTP $https_code)"
    else
        fail "Port 443: HTTP $https_code"
    fi
}

check_flask_app() {
    section "Flask Application"

    # Internal health via nginx→app
    local response
    response=$(docker exec "$NGINX_CONTAINER" wget -qO- --timeout=10 "http://app:${APP_PORT}/api/ai-status" 2>&1)
    if echo "$response" | grep -q "configured"; then
        pass "Flask /api/ai-status: OK"
    else
        fail "Flask /api/ai-status: no response or unexpected — $response"
        if $AUTO_FIX; then
            $QUIET || printf "       Attempting app restart...\n"
            docker restart "$APP_CONTAINER" 2>&1 | tail -2
            sleep 10
            response=$(docker exec "$NGINX_CONTAINER" wget -qO- --timeout=10 "http://app:${APP_PORT}/api/ai-status" 2>&1)
            if echo "$response" | grep -q "configured"; then
                $QUIET || printf "       ${GREEN}Fixed: App responding after restart${NC}\n"
            else
                $QUIET || printf "       ${RED}Still failing after restart${NC}\n"
            fi
        fi
        return
    fi

    # Gunicorn workers
    local worker_count
    worker_count=$(docker exec "$APP_CONTAINER" python -c "
import os
pids = []
for p in os.listdir('/proc'):
    if p.isdigit():
        try:
            with open(f'/proc/{p}/cmdline', 'rb') as f:
                cmd = f.read().decode('utf-8', errors='replace')
                if 'gunicorn' in cmd:
                    pids.append(p)
        except: pass
print(len(pids))
" 2>/dev/null)
    if [ -n "$worker_count" ] && [ "$worker_count" -ge 2 ]; then
        pass "Gunicorn processes: $worker_count (master + workers)"
    else
        warn "Gunicorn processes: ${worker_count:-unknown}"
    fi

    # Test critical endpoints
    local endpoints=("/api/ai-status" "/api/me")
    local expected_codes=("200" "401")
    for i in "${!endpoints[@]}"; do
        local ep="${endpoints[$i]}"
        local expect="${expected_codes[$i]}"
        local code
        code=$(curl -sSk -o /dev/null -w "%{http_code}" --max-time 10 "https://localhost${ep}" 2>/dev/null)
        if [ "$code" = "$expect" ]; then
            pass "Endpoint ${ep}: HTTP $code"
        else
            fail "Endpoint ${ep}: expected $expect, got $code"
        fi
    done
}

check_ssl() {
    section "SSL Certificate"

    # Check cert expiry
    local expiry
    expiry=$(echo | openssl s_client -connect localhost:443 -servername "$DOMAIN" 2>/dev/null | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)
    if [ -n "$expiry" ]; then
        local expiry_epoch
        expiry_epoch=$(date -d "$expiry" +%s 2>/dev/null || echo 0)
        local now_epoch
        now_epoch=$(date +%s)
        local days_left=$(( (expiry_epoch - now_epoch) / 86400 ))

        if [ "$days_left" -le 0 ]; then
            fail "SSL cert EXPIRED ($expiry)"
        elif [ "$days_left" -le 14 ]; then
            warn "SSL cert expires in ${days_left} days ($expiry)"
            if $AUTO_FIX; then
                $QUIET || printf "       Attempting certbot renewal...\n"
                docker exec "$CERTBOT_CONTAINER" certbot renew --quiet 2>&1 | tail -3
            fi
        elif [ "$days_left" -le 30 ]; then
            warn "SSL cert expires in ${days_left} days ($expiry)"
        else
            pass "SSL cert valid for ${days_left} days (expires $expiry)"
        fi
    else
        fail "Could not read SSL certificate"
    fi
}

check_external_deps() {
    section "External Dependencies"

    # OpenRouter API
    local or_code
    or_code=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 10 "https://openrouter.ai/api/v1/models" 2>/dev/null)
    if [ "$or_code" = "200" ]; then
        pass "OpenRouter API: reachable"
    else
        warn "OpenRouter API: HTTP $or_code"
    fi

    # Yahoo Finance (via yfinance proxy)
    local yf_code
    yf_code=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 10 "https://query1.finance.yahoo.com/v8/finance/chart/AAPL?range=1d&interval=1d" 2>/dev/null)
    if [ "$yf_code" = "200" ]; then
        pass "Yahoo Finance API: reachable"
    else
        warn "Yahoo Finance API: HTTP $yf_code"
    fi

    # DNS resolution
    if host "$DOMAIN" &>/dev/null; then
        pass "DNS resolution for $DOMAIN: OK"
    else
        fail "DNS resolution for $DOMAIN: FAILED"
    fi
}

check_logs_for_errors() {
    section "Recent Error Analysis"

    # Check for OOM kills
    local oom_count
    oom_count=$(dmesg 2>/dev/null | grep -ci "out of memory" || true)
    oom_count=${oom_count:-0}
    oom_count=$(echo "$oom_count" | tr -d '[:space:]')
    if [ "$oom_count" -gt 0 ]; then
        warn "OOM kills detected in dmesg: $oom_count"
    else
        pass "No OOM kills in dmesg"
    fi

    # App container error count (last 100 lines)
    local error_count
    error_count=$(docker logs "$APP_CONTAINER" --tail=100 2>&1 | grep -ciE "(error|traceback|exception|critical)" || true)
    error_count=$(echo "${error_count:-0}" | tail -1 | tr -d '[:space:]')
    if [ "$error_count" -ge 10 ]; then
        warn "App container: $error_count errors in last 100 log lines"
    elif [ "$error_count" -gt 0 ]; then
        pass "App container: $error_count errors in last 100 log lines"
    else
        pass "App container: no errors in recent logs"
    fi

    # Nginx 5xx errors in last 100 lines
    local nginx_5xx
    nginx_5xx=$(docker logs "$NGINX_CONTAINER" --tail=100 2>&1 | grep -cE '" [5][0-9]{2} ' || true)
    nginx_5xx=$(echo "${nginx_5xx:-0}" | tail -1 | tr -d '[:space:]')
    if [ "$nginx_5xx" -ge 5 ]; then
        warn "Nginx: $nginx_5xx server errors (5xx) in last 100 access log lines"
    else
        pass "Nginx 5xx errors: $nginx_5xx in last 100 lines"
    fi
}

# --- Main ---

$QUIET || printf "${BLUE}AI Stock Analyst — Health Check${NC}\n"
$QUIET || printf "Time: %s\n" "$(date '+%Y-%m-%d %H:%M:%S %Z')"
log "=== Health check started ==="

check_system_resources
check_docker
check_database
check_nginx
check_flask_app
check_ssl
check_external_deps
check_logs_for_errors

# --- Summary ---
$QUIET || printf "\n${BLUE}=== Summary ===${NC}\n"
$QUIET || printf "  Checks: %d  |  Passed: %d  |  Warnings: %d  |  Failed: %d\n" \
    "$TOTAL_CHECKS" \
    "$((TOTAL_CHECKS - FAIL_COUNT - WARN_COUNT))" \
    "$WARN_COUNT" \
    "$FAIL_COUNT"

log "Summary: ${TOTAL_CHECKS} checks, ${FAIL_COUNT} failed, ${WARN_COUNT} warnings"

if [ "$FAIL_COUNT" -gt 0 ]; then
    $QUIET || printf "\n  ${RED}Status: UNHEALTHY${NC}\n\n"
    exit 2
elif [ "$WARN_COUNT" -gt 0 ]; then
    $QUIET || printf "\n  ${YELLOW}Status: DEGRADED${NC}\n\n"
    exit 1
else
    $QUIET || printf "\n  ${GREEN}Status: HEALTHY${NC}\n\n"
    exit 0
fi
