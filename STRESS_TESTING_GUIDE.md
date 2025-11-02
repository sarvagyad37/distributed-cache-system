# Complete Stress Testing Guide with Prometheus & Grafana
# Author: Sarvagya Dwivedi

## Overview

This guide covers comprehensive stress testing of the Distributed File Storage System using Prometheus for metrics collection and Grafana for visualization.

## Prerequisites

1. Docker Desktop installed and running
2. Web app running on port 8080
3. Distributed system (Redis, SuperNode, cluster nodes) running
4. Python virtual environment activated

## Quick Start

### 1. Setup Prometheus & Grafana

```bash
./setup_stress_test.sh
```

This will:
- Start Prometheus (port 9090)
- Start Grafana (port 3000)
- Verify all services are running

### 2. Configure Grafana

1. Open http://localhost:3000
2. Login with: `admin` / `admin`
3. Add Prometheus data source:
   - URL: `http://prometheus:9090` (if using Docker)
   - Or: `http://host.docker.internal:9090` (if web app runs outside Docker)
4. Import dashboard:
   - Click "+" → "Import"
   - Upload `grafana_dashboard.json`
   - Select Prometheus data source

### 3. Run Stress Test

**Option A: Quick Runner (Recommended)**
```bash
./run_stress_test.sh
```

**Option B: Manual Locust**
```bash
# Basic test
locust -f stress_test.py --host=http://localhost:8080

# Medium stress
locust -f stress_test.py --host=http://localhost:8080 --users 20 --spawn-rate 3 --run-time 5m --headless

# Heavy stress
locust -f stress_test.py --host=http://localhost:8080 --users 50 --spawn-rate 5 --run-time 10m --headless
```

## Metrics Available

### Cache Metrics
- `cache_hits_total` - Total cache hits
- `cache_misses_total` - Total cache misses
- `cache_size` - Current cache size
- `cache_capacity` - Cache capacity

### Replication Metrics
- `replicated_chunks_total` - Total chunks replicated
- `replicated_files_total` - Total files replicated
- `replication_attempts_total{status}` - Replication attempts by status
- `replication_duration_seconds` - Replication duration histogram

### Load Balancing Metrics
- `load_balancing_decisions_total` - Total load balancing decisions
- `node_selection_count{node_ip}` - Node selection count per node
- `load_balancing_duration_seconds` - Load balancing duration histogram

### Fault Tolerance Metrics
- `active_nodes_count` - Current active nodes
- `total_nodes_count` - Total configured nodes
- `node_failures_total` - Total node failures
- `node_recoveries_total` - Total node recoveries
- `raft_leader_changes_total` - RAFT leader changes
- `raft_elections_total` - RAFT elections

### Sharding Metrics
- `shards_created_total` - Total shards created
- `shard_size_bytes` - Shard size distribution histogram

### System Health Metrics
- `heartbeat_checks_total` - Total heartbeat checks
- `heartbeat_failures_total` - Heartbeat failures
- `metadata_replications_total{status}` - Metadata replications by status

### HTTP Metrics
- `http_requests_total{method,endpoint,status}` - HTTP request counters
- `http_request_duration_seconds` - HTTP request duration histogram
- `file_uploads_total{status}` - File upload counters
- `file_downloads_total{status}` - File download counters
- `file_searches_total{status}` - File search counters
- `file_deletes_total{status}` - File delete counters

## Prometheus Queries

### Cache Hit Rate
```promql
rate(cache_hits_total[5m]) / (rate(cache_hits_total[5m]) + rate(cache_misses_total[5m])) * 100
```

### Replication Success Rate
```promql
rate(replication_attempts_total{status="success"}[5m]) / rate(replication_attempts_total[5m]) * 100
```

### P95 Response Time
```promql
histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m]))
```

### Requests Per Second
```promql
rate(http_requests_total[5m])
```

### Active Nodes
```promql
active_nodes_count
```

## Monitoring During Stress Test

### Web Dashboard
- URL: http://localhost:8080/dashboard
- Real-time metrics visualization
- Updates every 3 seconds

### Prometheus
- URL: http://localhost:9090
- Query metrics directly
- Check targets: http://localhost:9090/targets
- Graph queries: http://localhost:9090/graph

### Grafana
- URL: http://localhost:3000
- Comprehensive dashboard with all metrics
- Pre-configured panels for:
  - Cache performance
  - Replication metrics
  - Load balancing
  - Fault tolerance
  - File operations
  - System health

## Stress Test Scenarios

### Light Load
- 10 concurrent users
- 2 users/second spawn rate
- 2 minute duration
- Good for initial testing

### Medium Load
- 20 concurrent users
- 3 users/second spawn rate
- 5 minute duration
- Tests normal operations

### Heavy Load
- 50 concurrent users
- 5 users/second spawn rate
- 10 minute duration
- Tests system limits

### Custom Load
- Configure users, spawn rate, and duration as needed
- Monitor metrics during test
- Adjust based on system capacity

## Analyzing Results

### Key Metrics to Watch

1. **Cache Hit Rate**: Should increase as files are accessed
2. **Response Time**: P95 should stay reasonable (< 1s)
3. **Error Rate**: Should be minimal (< 1%)
4. **Replication Success**: Should be > 95%
5. **Node Failures**: Should be 0 in healthy system
6. **Load Balancing**: Decisions should distribute evenly

### Performance Indicators

- **Good**: Cache hit rate > 50%, P95 < 500ms, error rate < 1%
- **Acceptable**: Cache hit rate > 30%, P95 < 1s, error rate < 5%
- **Poor**: Cache hit rate < 20%, P95 > 2s, error rate > 10%

## Troubleshooting

### Prometheus not scraping
- Check targets: http://localhost:9090/targets
- Verify web app is running: http://localhost:8080/metrics
- Check Prometheus config: `prometheus.yml`

### Grafana shows no data
- Verify Prometheus data source is configured
- Check Prometheus is scraping targets
- Verify metrics exist: http://localhost:9090/graph

### Metrics not updating
- Check web app is running
- Verify metrics endpoint: http://localhost:8080/metrics
- Check Prometheus scrape interval (should be 5s)

## Cleanup

```bash
# Stop Prometheus and Grafana
docker-compose down

# Stop web app
pkill -f web_app.py
```

## Next Steps

1. Run stress tests at different intensities
2. Monitor metrics in Grafana
3. Analyze performance bottlenecks
4. Optimize system based on findings
5. Document findings and create report
