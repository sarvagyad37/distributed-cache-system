#!/bin/bash
# Complete System Startup Script (Fixed)
# Author: Sarvagya Dwivedi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🚀 STARTING COMPLETE DISTRIBUTED FILE STORAGE SYSTEM"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Activate virtual environment
if [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
    echo -e "${GREEN}✅ Virtual environment activated${NC}"
else
    echo -e "${RED}❌ Virtual environment not found!${NC}"
    echo "   Please create it: python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi

# Function to check if port is in use
check_port() {
    if lsof -Pi :$1 -sTCP:LISTEN -t >/dev/null 2>&1; then
        return 0
    else
        return 1
    fi
}

# 1. Check/Start Redis
echo "📦 Step 1: Checking Redis..."
if ! pgrep -x "redis-server" > /dev/null; then
    echo -e "${YELLOW}⚠️  Redis not running. Starting Redis...${NC}"
    redis-server --daemonize yes
    sleep 2
else
    echo -e "${GREEN}✅ Redis is running${NC}"
fi

# 2. Start SuperNode
echo ""
echo "📦 Step 2: Starting SuperNode..."
if check_port 9000; then
    echo -e "${YELLOW}⚠️  Port 9000 already in use. SuperNode might already be running.${NC}"
else
    cd SuperNode
    nohup ../venv/bin/python superNode.py > ../logs/supernode.log 2>&1 &
    SUPERNODE_PID=$!
    echo -e "${GREEN}✅ SuperNode started (PID: $SUPERNODE_PID)${NC}"
    cd ..
    sleep 3
fi

# Create logs directory
mkdir -p logs

# 3. Start Cluster Nodes
echo ""
echo "📦 Step 3: Starting Cluster Nodes..."
NODES=("one" "two" "three")
NODE_PIDS=()

for node in "${NODES[@]}"; do
    # Extract port from config.yaml - handle both formats
    PORT=$(grep -A 5 "^${node}:" config.yaml | grep "server_port" | awk '{print $NF}' | tr -d ':')
    
    if [ -z "$PORT" ]; then
        # Fallback: use default ports
        case $node in
            "one") PORT=3100 ;;
            "two") PORT=4000 ;;
            "three") PORT=5100 ;;
        esac
    fi
    
    if check_port $PORT; then
        echo -e "${YELLOW}⚠️  Port $PORT already in use. Node $node might already be running.${NC}"
    else
        echo "Starting node: $node (port: $PORT)"
        nohup venv/bin/python server.py $node > logs/node_${node}.log 2>&1 &
        PID=$!
        NODE_PIDS+=($PID)
        echo -e "${GREEN}✅ Node $node started (PID: $PID)${NC}"
        sleep 2
    fi
done

# 4. Wait for system to stabilize
echo ""
echo "⏳ Waiting for system to stabilize..."
sleep 5

# 5. Verify everything is running
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ SYSTEM STATUS CHECK"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Check Redis
if pgrep -x "redis-server" > /dev/null; then
    echo -e "${GREEN}✅ Redis: Running${NC}"
else
    echo -e "${RED}❌ Redis: Not running${NC}"
fi

# Check SuperNode
if check_port 9000; then
    echo -e "${GREEN}✅ SuperNode: Running on port 9000${NC}"
else
    echo -e "${RED}❌ SuperNode: Not running on port 9000${NC}"
fi

# Check Cluster Nodes
for node in "${NODES[@]}"; do
    PORT=$(grep -A 5 "^${node}:" config.yaml | grep "server_port" | awk '{print $NF}' | tr -d ':')
    if [ -z "$PORT" ]; then
        case $node in
            "one") PORT=3100 ;;
            "two") PORT=4000 ;;
            "three") PORT=5100 ;;
        esac
    fi
    if check_port $PORT; then
        echo -e "${GREEN}✅ Node $node: Running on port $PORT${NC}"
    else
        echo -e "${RED}❌ Node $node: Not running on port $PORT${NC}"
        echo "   Check logs: tail -f logs/node_${node}.log"
    fi
done

# Check Web App
if check_port 8080; then
    echo -e "${GREEN}✅ Web App: Running on port 8080${NC}"
else
    echo -e "${YELLOW}⚠️  Web App: Not running (start with: python web_app.py)${NC}"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📊 ACCESS URLS"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "  Web Interface:    http://localhost:8080"
echo "  Web Dashboard:    http://localhost:8080/dashboard"
echo "  Metrics:          http://localhost:8080/metrics"
echo "  Prometheus:       http://localhost:9090"
echo "  Grafana:          http://localhost:3000"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🛑 TO STOP ALL SERVICES"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "  pkill -f 'server.py|superNode.py'"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Verify metrics endpoint
echo "🔍 Checking metrics endpoint..."
sleep 2
if curl -s http://localhost:8080/api/system-metrics > /dev/null 2>&1; then
    ACTIVE_NODES=$(curl -s http://localhost:8080/api/system-metrics | python3 -c "import sys, json; print(json.load(sys.stdin).get('active_nodes', 0))" 2>/dev/null || echo "0")
    if [ "$ACTIVE_NODES" -gt "0" ]; then
        echo -e "${GREEN}✅ Metrics working! Active nodes: $ACTIVE_NODES${NC}"
    else
        echo -e "${YELLOW}⚠️  Metrics endpoint responding but no active nodes yet. Wait a few seconds...${NC}"
    fi
else
    echo -e "${YELLOW}⚠️  Web app not running. Start it with: python3 web_app.py${NC}"
fi

echo ""
echo "✅ Startup complete!"
echo ""
