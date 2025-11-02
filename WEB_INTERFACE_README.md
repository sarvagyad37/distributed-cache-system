# Web Interface

Flask-based web UI for the distributed file storage system.

---

## Features

- File upload with automatic sharding
- File listing and management
- Download files
- Search by filename
- Delete files
- Real-time cluster status
- System metrics dashboard

Dark mode interface with Tailwind CSS.

---

## Setup

```bash
# Ensure system is running
./start_full_system.sh

# Or start manually
python3 web_app.py

# Access at: http://localhost:8080
```

---

## Configuration

Environment variables (optional):

```bash
export SUPERNODE_ADDRESS="localhost:9000"
export PORT=8080
export SECRET_KEY="your-secret-key"
```

---

## Architecture

```
Web Browser (HTTP)
    ↓
Flask App (web_app.py)
    ↓ (gRPC)
SuperNode
    ↓
Cluster Nodes
```

Web app communicates with SuperNode via gRPC. SuperNode coordinates cluster operations.

---

## Endpoints

- `GET /` - Homepage
- `GET /upload` - Upload form
- `POST /upload` - Handle upload
- `GET /files` - List files
- `POST /files` - Get file list for user
- `GET /download` - Download file
- `GET /search` - Search form
- `POST /search` - Search for file
- `POST /delete` - Delete file
- `GET /dashboard` - Metrics dashboard
- `GET /status` - Cluster status (JSON)
- `GET /metrics` - Prometheus metrics

---

## Monitoring

Dashboard displays real-time metrics:
- Cache hit rate
- Request counts
- System health
- Active nodes

Prometheus metrics exported at `/metrics`.

---

## Troubleshooting

**Can't connect to cluster:**
- Verify SuperNode running
- Check web_app.py logs
- Review logs: `tail -f web_app.log`

**Upload fails:**
- Check disk space
- Verify MongoDB connection
- Review node logs

**Dashboard shows offline:**
- Check SuperNode logs
- Verify gRPC ports
- Restart services

---

## Development

```bash
export FLASK_ENV=development
python3 web_app.py
```

For production, use WSGI server like Gunicorn.
