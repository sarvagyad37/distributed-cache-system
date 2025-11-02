# How to Run

Setup and run instructions for the distributed file storage system.

---

## Prerequisites

- Python 3.8+
- Redis (localhost:6379)
- MongoDB (localhost:27017) - for metadata if needed

---

## Installation

```bash
cd Distributed-File-Storage-System-master

# Virtual environment
python3 -m venv venv
source venv/bin/activate

# Dependencies
pip install -r requirements.txt

# Start Redis
redis-server --daemonize yes --port 6379
```

---

## Running the System

### Automated Startup

```bash
./start_full_system.sh
```

Starts SuperNode, cluster nodes, and web interface automatically.

### Manual Startup

**Terminal 1 - SuperNode:**
```bash
cd SuperNode
python3 superNode.py
```

**Terminal 2 - Node 1:**
```bash
python3 server.py one
```

**Terminal 3 - Node 2:**
```bash
python3 server.py two
```

**Terminal 4 - Node 3:**
```bash
python3 server.py three
```

**Terminal 5 - Web Interface:**
```bash
python3 web_app.py
```

---

## Verification

1. Check web interface: http://localhost:8080
2. Dashboard should show "Online" status
3. Try uploading a file
4. Check logs for errors

---

## Monitoring

**Setup Prometheus and Grafana:**
```bash
./setup_stress_test.sh
# Or: docker-compose up -d
```

**Access:**
- Prometheus: http://localhost:9090
- Grafana: http://localhost:3000
- Web Dashboard: http://localhost:8080/dashboard

---

## Load Testing

```bash
source venv/bin/activate

# Quick test (5 minutes)
locust -f stress_test.py --host=http://localhost:8080 --users 50 --spawn-rate 5 --run-time 5m

# Full test (1M requests, 30 minutes)
locust -f stress_test.py --host=http://localhost:8080 --users 200 --spawn-rate 10 --run-time 30m --headless

# Interactive UI
locust -f stress_test.py --host=http://localhost:8080
# Then: http://localhost:8089
```

---

## Configuration

Edit `config.yaml` for cluster setup:

```yaml
one:
  hostname: localhost
  server_port: 3100
  primary: 1
  raft_port: 3101

LRUCapacity: 10000
UPLOAD_SHARD_SIZE: 52428800  # 50MB
super_node_address: "localhost:9000"
```

---

## Troubleshooting

**System won't start:**
```bash
pkill -f "python.*server.py|python.*superNode.py"
lsof -i :9000  # SuperNode
lsof -i :3100  # Node 1
./start_full_system.sh
```

**Redis issues:**
```bash
pgrep redis-server
redis-server --daemonize yes --port 6379
redis-cli ping  # Should return PONG
```

**Upload fails:**
- Check disk space
- Verify MongoDB connection
- Review node logs

**Dashboard offline:**
- Check SuperNode logs
- Verify gRPC ports
- Restart web interface

**Low performance:**
- Verify cache capacity (10K)
- Check Redis is running
- Monitor with Prometheus

---

## Quick Reference

```bash
# Start everything
./start_full_system.sh

# Stop everything
pkill -f "python.*server.py|python.*superNode.py"

# View logs
tail -f logs/node_one.log

# Check status
curl http://localhost:8080/status

# Test upload
python client.py upload test.txt
```

---

## Expected Behavior

With proper configuration:
- Cache hit rate: 85-95%
- Upload/download functional
- Search working
- Dashboard shows metrics
- Stress tests complete without crashes
