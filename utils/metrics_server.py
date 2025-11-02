"""
Prometheus Metrics HTTP Server for Backend Nodes
Exposes metrics endpoint for Prometheus scraping
Author: Sarvagya Dwivedi
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
import threading

class MetricsHandler(BaseHTTPRequestHandler):
    """HTTP handler for Prometheus metrics endpoint"""
    
    def do_GET(self):
        if self.path == '/metrics':
            self.send_response(200)
            self.send_header('Content-Type', CONTENT_TYPE_LATEST)
            self.end_headers()
            self.wfile.write(generate_latest())
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        # Suppress HTTP server logs
        pass

def start_metrics_server(port=8000):
    """Start HTTP server for Prometheus metrics"""
    server = HTTPServer(('0.0.0.0', port), MetricsHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"✅ Metrics server started on port {port}")
    return server

