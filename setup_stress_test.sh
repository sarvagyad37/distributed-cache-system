#!/bin/bash
# Complete Stress Testing Script with Prometheus & Grafana
# Author: Sarvagya Dwivedi

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🚀 DISTRIBUTED FILE STORAGE - COMPLETE STRESS TEST SETUP"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    echo "❌ Docker is not running!"
    echo ""
    echo "Please start Docker Desktop first, then run this script again."
    echo ""
    exit 1
fi

echo "✅ Docker is running"
echo ""

# Check if web app is running
if ! curl -s http://localhost:8080/metrics > /dev/null 2>&1; then
    echo "⚠️  Web app is not running on port 8080"
    echo "   Please start it first:"
    echo "   cd $SCRIPT_DIR && source venv/bin/activate && python3 web_app.py &"
    echo ""
    read -p "Continue anyway? (y/n) " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
else
    echo "✅ Web app is running on port 8080"
fi

echo ""

# Start Prometheus and Grafana
echo "📊 Starting Prometheus and Grafana..."
docker-compose down > /dev/null 2>&1 || true
docker-compose up -d

echo ""
echo "⏳ Waiting for services to start..."
sleep 5

# Check Prometheus
if curl -s http://localhost:9090/-/healthy > /dev/null 2>&1; then
    echo "✅ Prometheus is running at http://localhost:9090"
else
    echo "⏳ Prometheus is starting..."
    sleep 5
fi

# Check Grafana
if curl -s http://localhost:3000/api/health > /dev/null 2>&1; then
    echo "✅ Grafana is running at http://localhost:3000"
else
    echo "⏳ Grafana is starting..."
    sleep 5
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📊 SETUP COMPLETE!"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "🌐 Access Points:"
echo "   📈 Prometheus:    http://localhost:9090"
echo "   📊 Grafana:       http://localhost:3000 (admin/admin)"
echo "   🎯 Web Dashboard: http://localhost:8080/dashboard"
echo "   📉 Metrics:       http://localhost:8080/metrics"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🎯 NEXT STEPS:"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "1. Configure Grafana:"
echo "   - Login to http://localhost:3000 (admin/admin)"
echo "   - Add Prometheus data source: http://prometheus:9090"
echo "   - Import dashboard from: grafana_dashboard.json"
echo ""
echo "2. Run Stress Test:"
echo "   Basic:   locust -f stress_test.py --host=http://localhost:8080"
echo "   Medium:  locust -f stress_test.py --host=http://localhost:8080 --users 20 --spawn-rate 3 --run-time 5m"
echo "   Heavy:   locust -f stress_test.py --host=http://localhost:8080 --users 50 --spawn-rate 5 --run-time 10m"
echo ""
echo "3. Monitor Metrics:"
echo "   - Watch Prometheus queries: http://localhost:9090/graph"
echo "   - View Grafana dashboard: http://localhost:3000"
echo "   - Check web dashboard: http://localhost:8080/dashboard"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Check if Grafana needs Prometheus data source setup
echo "🔍 Checking Prometheus connectivity..."
if curl -s http://localhost:9090/api/v1/targets | grep -q "UP"; then
    UP_TARGETS=$(curl -s http://localhost:9090/api/v1/targets | grep -o '"health":"up"' | wc -l | tr -d ' ')
    echo "✅ Prometheus is scraping $UP_TARGETS target(s)"
else
    echo "⚠️  Prometheus targets may not be up yet. Check: http://localhost:9090/targets"
fi

echo ""
echo "✨ Ready for stress testing!"
echo ""

