# Distributed File Storage System

Author: Sarvagya Dwivedi  
License: MIT

---

## Overview

A distributed file storage system built with gRPC, implementing RAFT consensus, automatic replication, and hybrid LRU+LFU caching. Handles 1M+ requests with 85-95% cache hit rate.

**Architecture:**
```
Web Interface (Flask)
    ↓
SuperNode (coordinator)
    ↓
Cluster Nodes (RAFT consensus)
```

**Features:**
- File upload/download with automatic sharding (50MB chunks)
- Distributed storage across least-loaded nodes
- RAFT consensus for leader election
- Automatic replication and failover
- Hybrid LRU+LFU cache (85-95% hit rate)
- Real-time monitoring (Prometheus, Grafana)

---

## Quick Start

**Requirements:**
- Python 3.8+
- Redis (localhost:6379)
- 8GB+ RAM recommended

**Installation:**
```bash
git clone <repository-url>
cd Distributed-File-Storage-System-master

python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt

# Start Redis
redis-server --daemonize yes --port 6379
```

**Configuration:**

Edit `config.yaml` to configure cluster nodes.

**Run:**

```bash
# Automated startup
./start_full_system.sh

# Manual startup
# Terminal 1: cd SuperNode && python3 superNode.py
# Terminal 2: python3 server.py one
# Terminal 3: python3 server.py two
# Terminal 4: python3 server.py three
# Terminal 5: python3 web_app.py
```

**Access:**
- Web Interface: http://localhost:8080
- Prometheus: http://localhost:9090
- Grafana: http://localhost:3000

---

## Features

**Operations:**
1. Upload - 50MB sharding, distributed storage
2. Download - Parallel retrieval, chunk reassembly
3. Search - Fast metadata lookup
4. List - User file listing
5. Delete - Distributed deletion
6. Update - Atomic updates

**Cache Algorithm:**
- Hybrid LRU+LFU with score-based eviction
- Logarithmic frequency normalization
- Multi-timeframe exponential recency decay
- Async cache writes
- O(log n) heap-based eviction
- Capacity: 10,000 files

**RAFT Consensus:**
- Leader election
- Metadata consistency
- Automatic failover

---

## Performance

| Metric | Value |
|--------|-------|
| Cache Hit Rate | 85-95% |
| Capacity | 10K files |
| Latency | 60-120ms |
| Throughput | +30% vs baseline |
| Stress Test | 1M+ requests |

---

## Load Testing

```bash
# Quick test (5 minutes)
locust -f stress_test.py --host=http://localhost:8080 \
  --users 50 --spawn-rate 5 --run-time 5m

# Full test (1M requests, 30 minutes)
locust -f stress_test.py --host=http://localhost:8080 \
  --users 200 --spawn-rate 10 --run-time 30m --headless

# Interactive UI
locust -f stress_test.py --host=http://localhost:8080
# http://localhost:8089
```

---

## Troubleshooting

**System startup issues:**
```bash
pkill -f "python.*server.py|python.*superNode.py|python.*web_app.py"
lsof -i :9000  # SuperNode
lsof -i :3100  # Node 1
./start_full_system.sh
```

**Redis connection:**
```bash
pgrep redis-server
redis-cli ping  # Should return PONG
```

**Cache performance:**
- Verify cache capacity matches working set
- Use 80/20 access pattern in tests
- Monitor eviction rates

**Latency:**
- Check network between nodes
- Monitor disk I/O
- Review memory pressure

---

## Technology Stack

- Python 3.8+
- gRPC + Protocol Buffers
- Flask
- Redis
- Prometheus + Grafana
- Locust
- pysyncobj (RAFT)
- ThreadPoolExecutor

---

## Project Structure

```
server.py              # Cluster node server
web_app.py             # Flask web interface
client.py              # CLI client
config.yaml            # Configuration
stress_test.py         # Locust stress tests

proto/                 # gRPC definitions
generated/             # Generated code

service/
  ├── FileServer.py    # File operations
  ├── HeartbeatService.py
  └── HybridLRUCache.py

utils/
  ├── ActiveNodesChecker.py
  ├── ShardingHandler.py
  ├── Raft.py
  └── system_metrics.py

SuperNode/             # Coordinator
templates/             # Web UI templates
```

---

## Documentation

- [HOW_TO_RUN.md](HOW_TO_RUN.md) - Detailed setup instructions
- [PROJECT_UPDATES.md](PROJECT_UPDATES.md) - Development notes and learnings
- [WEB_INTERFACE_README.md](WEB_INTERFACE_README.md) - Web interface documentation
- [STRESS_TESTING_GUIDE.md](STRESS_TESTING_GUIDE.md) - Load testing guide

---

## Key Learnings

- Observability: Metrics > logs for production systems
- Testing: Use realistic access patterns (80/20 rule)
- Concurrency: Thread pool sizing critical at scale
- Caching: Smooth functions outperform hard cutoffs
- Resilience: Plan for failure at every layer

See [PROJECT_UPDATES.md](PROJECT_UPDATES.md) for detailed analysis.
