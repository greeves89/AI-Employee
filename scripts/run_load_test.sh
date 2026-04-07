#!/bin/bash
# AI-Employee Load Test Runner
# Usage: ./scripts/run_load_test.sh [locust|k6] [host]

set -e

TOOL="${1:-locust}"
HOST="${2:-http://localhost:8000}"
RESULTS_DIR="results"

mkdir -p "$RESULTS_DIR"

echo "=== AI-Employee Load Test ==="
echo "Tool: $TOOL"
echo "Target: $HOST"
echo ""

# Quick health check
if ! curl -sf "$HOST/health" > /dev/null 2>&1; then
    echo "ERROR: $HOST/health is not reachable."
    echo "Start the platform first: docker compose up -d"
    exit 1
fi
echo "Health check passed."
echo ""

case "$TOOL" in
    locust)
        # Install if needed
        pip install -q locust 2>/dev/null || pip3 install -q locust 2>/dev/null

        echo "Starting Locust (headless, 10 users, 2 min)..."
        echo "For interactive mode: locust -f scripts/load_test.py --host $HOST"
        echo ""

        locust -f scripts/load_test.py \
            --host "$HOST" \
            --headless \
            -u 10 -r 2 \
            --run-time 2m \
            --csv "$RESULTS_DIR/locust" \
            --html "$RESULTS_DIR/locust_report.html" \
            2>&1

        echo ""
        echo "Results saved to:"
        echo "  $RESULTS_DIR/locust_report.html"
        echo "  $RESULTS_DIR/locust_stats.csv"
        ;;

    k6)
        if ! command -v k6 &> /dev/null; then
            echo "k6 not found. Install: brew install k6"
            exit 1
        fi

        echo "Starting k6 (staged ramp-up, ~7 min)..."
        echo ""

        k6 run \
            -e BASE_URL="$HOST" \
            --out json="$RESULTS_DIR/k6_results.json" \
            scripts/load_test_k6.js \
            2>&1

        echo ""
        echo "Results saved to: $RESULTS_DIR/k6_results.json"
        ;;

    *)
        echo "Unknown tool: $TOOL (use 'locust' or 'k6')"
        exit 1
        ;;
esac

echo ""
echo "=== Done ==="
