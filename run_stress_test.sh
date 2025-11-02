#!/bin/bash
# Quick Stress Test Runner - FIXED
# Author: Sarvagya Dwivedi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🎯 STRESS TEST SETUP CHECK"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Check if web app is running
if ! curl -s http://localhost:8080/metrics > /dev/null 2>&1; then
    echo "❌ Web app is not running!"
    echo "   Please start it first:"
    echo "   cd $SCRIPT_DIR && source venv/bin/activate && python3 web_app.py &"
    exit 1
fi

# Check if backend nodes are running
BACKEND_NODES=0

# Check if nodes are running on their ports
if lsof -Pi :3100 -sTCP:LISTEN >/dev/null 2>&1; then
    BACKEND_NODES=$((BACKEND_NODES + 1))
fi
if lsof -Pi :4000 -sTCP:LISTEN >/dev/null 2>&1; then
    BACKEND_NODES=$((BACKEND_NODES + 1))
fi
if lsof -Pi :5100 -sTCP:LISTEN >/dev/null 2>&1; then
    BACKEND_NODES=$((BACKEND_NODES + 1))
fi

# Also try to get from Prometheus if available
if curl -s http://localhost:9090/api/v1/query?query=max\(active_nodes_count\{job=~\"cluster-node.*\"\}\) >/dev/null 2>&1; then
    PROMETHEUS_NODES=$(curl -s "http://localhost:9090/api/v1/query?query=max(active_nodes_count{job=~\"cluster-node.*\"})" 2>/dev/null | python3 -c "import sys, json; data=json.load(sys.stdin); results=data.get('data',{}).get('result',[]); print(results[0].get('value',['?','0'])[1]) if results else print('0')" 2>/dev/null || echo "0")
    if [ "$PROMETHEUS_NODES" != "0" ] && [ "$PROMETHEUS_NODES" != "?" ]; then
        BACKEND_NODES=$PROMETHEUS_NODES
    fi
fi

if [ "$BACKEND_NODES" -eq "0" ]; then
    echo "⚠️  WARNING: No backend nodes detected!"
    echo "   The distributed system backend may not be running."
    echo "   Run: ./start_full_system.sh"
    echo ""
    read -p "Continue anyway? (y/n): " continue_choice
    if [ "$continue_choice" != "y" ]; then
        exit 1
    fi
else
    echo "✅ Backend nodes detected: $BACKEND_NODES"
fi

echo ""
echo "Select stress test intensity:"
echo "  1) Light   - 10 users, 2 spawn rate, 2 minutes"
echo "  2) Medium  - 20 users, 3 spawn rate, 5 minutes"
echo "  3) Heavy   - 50 users, 5 spawn rate, 10 minutes"
echo "  4) Custom  - Configure your own"
echo ""
read -p "Choice [1-4]: " choice

case $choice in
    1)
        USERS=10
        SPAWN_RATE=2
        RUN_TIME=2m
        ;;
    2)
        USERS=20
        SPAWN_RATE=3
        RUN_TIME=5m
        ;;
    3)
        USERS=50
        SPAWN_RATE=5
        RUN_TIME=10m
        ;;
    4)
        read -p "Users: " USERS
        read -p "Spawn rate: " SPAWN_RATE
        read -p "Run time (e.g., 5m): " RUN_TIME
        ;;
    *)
        echo "Invalid choice"
        exit 1
        ;;
esac

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🚀 STARTING STRESS TEST"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "Configuration:"
echo "  Users: $USERS"
echo "  Spawn Rate: $SPAWN_RATE users/sec"
echo "  Duration: $RUN_TIME"
echo ""
echo "Monitor at:"
echo "  📊 Web Dashboard: http://localhost:8080/dashboard"
echo "  📈 Prometheus:     http://localhost:9090"
echo "  📉 Grafana:        http://localhost:3000/d/distributed-file-storage-57033343"
echo ""
echo "⚠️  IMPORTANT: Keep Grafana dashboard open to watch metrics change!"
echo ""
echo "Press Ctrl+C to stop the test"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

source venv/bin/activate

locust -f stress_test.py \
    --host=http://localhost:8080 \
    --users $USERS \
    --spawn-rate $SPAWN_RATE \
    --run-time $RUN_TIME \
    --headless \
    --html=stress_test_report.html \
    --csv=stress_test_results

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ STRESS TEST COMPLETED"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "📊 Reports generated:"
echo "  - HTML Report: stress_test_report.html"
echo "  - CSV Results: stress_test_results_*.csv"
echo ""
echo "Check final metrics at:"
echo "  - Prometheus: http://localhost:9090"
echo "  - Grafana: http://localhost:3000"
echo ""
