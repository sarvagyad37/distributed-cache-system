from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file
import os
import sys
import grpc
import time
import threading
import requests
from concurrent import futures
from functools import wraps
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from flask import Response

# Add paths for gRPC imports
sys.path.append('./generated')
sys.path.append('./proto')
sys.path.append('./utils')
sys.path.append('./service')

import fileService_pb2_grpc
import fileService_pb2

# Import system metrics
from utils.system_metrics import metrics

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'distributed-file-storage-dev-key-change-in-production')

# Configuration
SUPERNODE_ADDRESS = os.environ.get('SUPERNODE_ADDRESS', 'localhost:9000')
UPLOAD_FOLDER = 'files'
DOWNLOAD_FOLDER = 'downloads'
ALLOWED_EXTENSIONS = set()  # Allow all file types

# Ensure directories exist
for folder in [UPLOAD_FOLDER, DOWNLOAD_FOLDER]:
    os.makedirs(folder, exist_ok=True)

# Prometheus Metrics
REQUEST_COUNT = Counter(
    'http_requests_total',
    'Total HTTP requests',
    ['method', 'endpoint', 'status']
)

REQUEST_DURATION = Histogram(
    'http_request_duration_seconds',
    'HTTP request duration',
    ['method', 'endpoint']
)

UPLOAD_COUNT = Counter(
    'file_uploads_total',
    'Total file uploads',
    ['status']
)

DOWNLOAD_COUNT = Counter(
    'file_downloads_total',
    'Total file downloads',
    ['status']
)

SEARCH_COUNT = Counter(
    'file_searches_total',
    'Total file searches',
    ['status']
)

DELETE_COUNT = Counter(
    'file_deletes_total',
    'Total file deletes',
    ['status']
)

ACTIVE_CONNECTIONS = Gauge(
    'active_connections',
    'Active gRPC connections'
)

CONNECTION_POOL_SIZE = Gauge(
    'connection_pool_size',
    'Size of gRPC connection pool'
)

RETRY_ATTEMPTS = Counter(
    'grpc_retry_attempts_total',
    'Total gRPC retry attempts',
    ['operation', 'result']
)

FILE_SIZE = Histogram(
    'file_size_bytes',
    'File size distribution',
    ['operation']
)

# Connection Pool Manager for gRPC channels
class GrpcConnectionPool:
    """Thread-safe connection pool for gRPC channels"""
    def __init__(self, address, max_pool_size=5, min_pool_size=2):
        self.address = address
        self.max_pool_size = max_pool_size
        self.min_pool_size = min_pool_size
        self._lock = threading.Lock()
        self._channels = []
        self._active_count = 0
        
    def get_channel(self):
        """Get a channel from pool or create new one"""
        with self._lock:
            # Try to reuse existing channel
            if self._channels:
                channel = self._channels.pop()
                try:
                    # Quick check if channel is still usable
                    grpc.channel_ready_future(channel).result(timeout=0.1)
                    self._active_count += 1
                    CONNECTION_POOL_SIZE.set(len(self._channels))
                    return channel
                except:
                    # Channel is dead, close it and create new one
                    try:
                        channel.close()
                    except:
                        pass
                    pass
            
            # Create new channel if pool is not full
            if self._active_count < self.max_pool_size:
                channel = grpc.insecure_channel(
                    self.address,
                    options=[
                        ('grpc.keepalive_time_ms', 30000),
                        ('grpc.keepalive_timeout_ms', 5000),
                        ('grpc.keepalive_permit_without_calls', True),
                        ('grpc.http2.max_pings_without_data', 0),
                        ('grpc.http2.min_time_between_pings_ms', 10000),
                        ('grpc.http2.min_ping_interval_without_data_ms', 300000),
                    ]
                )
                # Quick connect check
                try:
                    grpc.channel_ready_future(channel).result(timeout=0.5)
                except:
                    try:
                        channel.close()
                    except:
                        pass
                    raise
                
                self._active_count += 1
                ACTIVE_CONNECTIONS.inc()
                CONNECTION_POOL_SIZE.set(len(self._channels))
                return channel
            else:
                # Pool is full, wait briefly and try to get from pool
                return None
    
    def return_channel(self, channel):
        """Return channel to pool"""
        if not channel:
            return
        
        with self._lock:
            if self._active_count > 0:
                self._active_count -= 1
            
            # Check if channel is still usable before returning to pool
            try:
                grpc.channel_ready_future(channel).result(timeout=0.1)
                if len(self._channels) < self.max_pool_size:
                    self._channels.append(channel)
                    CONNECTION_POOL_SIZE.set(len(self._channels))
                    return
            except:
                pass
            
            # Channel is dead or pool is full, close it
            try:
                channel.close()
                ACTIVE_CONNECTIONS.dec()
            except:
                pass
            CONNECTION_POOL_SIZE.set(len(self._channels))
    
    def close_all(self):
        """Close all channels in pool"""
        with self._lock:
            for channel in self._channels:
                try:
                    channel.close()
                except:
                    pass
            self._channels.clear()
            self._active_count = 0
            CONNECTION_POOL_SIZE.set(0)

# Global connection pool
_connection_pool = None
_pool_lock = threading.Lock()

def get_connection_pool():
    """Get or create connection pool (singleton)"""
    global _connection_pool
    if _connection_pool is None:
        with _pool_lock:
            if _connection_pool is None:
                _connection_pool = GrpcConnectionPool(SUPERNODE_ADDRESS)
    return _connection_pool

def get_stub():
    """Get gRPC stub from connection pool with retry"""
    pool = get_connection_pool()
    max_retries = 3
    
    for attempt in range(max_retries):
        try:
            channel = pool.get_channel()
            if channel:
                return fileService_pb2_grpc.FileserviceStub(channel), channel
            else:
                # Pool exhausted, wait briefly and retry
                time.sleep(0.1 * (attempt + 1))
        except Exception as e:
            RETRY_ATTEMPTS.labels(operation='get_channel', result='failed').inc()
            if attempt < max_retries - 1:
                time.sleep(0.1 * (attempt + 1))
            else:
                ACTIVE_CONNECTIONS.set(0)
                return None, None
    
    ACTIVE_CONNECTIONS.set(0)
    return None, None

def retry_grpc_call(max_retries=3, base_delay=0.1, max_delay=2.0):
    """Decorator for retrying gRPC calls with exponential backoff"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries):
                try:
                    result = func(*args, **kwargs)
                    if attempt > 0:
                        RETRY_ATTEMPTS.labels(operation=func.__name__, result='success').inc()
                    return result
                except grpc.RpcError as e:
                    last_exception = e
                    # Don't retry on certain errors
                    if e.code() in [grpc.StatusCode.INVALID_ARGUMENT, grpc.StatusCode.NOT_FOUND, grpc.StatusCode.PERMISSION_DENIED]:
                        raise
                    
                    if attempt < max_retries - 1:
                        # Exponential backoff with jitter
                        delay = min(base_delay * (2 ** attempt), max_delay)
                        time.sleep(delay)
                        RETRY_ATTEMPTS.labels(operation=func.__name__, result='retry').inc()
                    else:
                        RETRY_ATTEMPTS.labels(operation=func.__name__, result='failed').inc()
                except Exception as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        delay = min(base_delay * (2 ** attempt), max_delay)
                        time.sleep(delay)
                        RETRY_ATTEMPTS.labels(operation=func.__name__, result='retry').inc()
                    else:
                        RETRY_ATTEMPTS.labels(operation=func.__name__, result='failed').inc()
            
            # All retries failed
            raise last_exception
        return wrapper
    return decorator

def allowed_file(filename):
    return '.' in filename or True  # Allow all files

@app.route('/metrics')
def metrics_endpoint():
    """Prometheus metrics endpoint"""
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)

@app.route('/api/system-metrics')
def system_metrics_api():
    """API endpoint for system metrics - queries Prometheus for backend metrics"""
    try:
        import requests
        import json
        
        # Get web app metrics
        web_stats = metrics.get_stats()
        
        # Query Prometheus for backend node metrics
        prometheus_url = os.environ.get('PROMETHEUS_URL', 'http://localhost:9090')
        
        backend_metrics = {}
        import urllib.parse
        
        try:
            # Query active_nodes_count from backend nodes (URL encode the query)
            query = 'max(active_nodes_count{job=~"cluster-node.*"})'
            encoded_query = urllib.parse.quote(query)
            response = requests.get(
                f'{prometheus_url}/api/v1/query?query={encoded_query}',
                timeout=2
            )
            if response.status_code == 200:
                data = response.json()
                if data.get('status') == 'success' and data.get('data', {}).get('result'):
                    backend_metrics['active_nodes_count'] = float(data['data']['result'][0]['value'][1])
                else:
                    backend_metrics['active_nodes_count'] = 0
            else:
                backend_metrics['active_nodes_count'] = 0
        except Exception as e:
            print(f"Error querying active_nodes_count: {e}")
            backend_metrics['active_nodes_count'] = 0
        
        try:
            # Query total_nodes_count from backend nodes (URL encode the query)
            query = 'max(total_nodes_count{job=~"cluster-node.*"})'
            encoded_query = urllib.parse.quote(query)
            response = requests.get(
                f'{prometheus_url}/api/v1/query?query={encoded_query}',
                timeout=2
            )
            if response.status_code == 200:
                data = response.json()
                if data.get('status') == 'success' and data.get('data', {}).get('result'):
                    backend_metrics['total_nodes_count'] = float(data['data']['result'][0]['value'][1])
                else:
                    backend_metrics['total_nodes_count'] = 0
            else:
                backend_metrics['total_nodes_count'] = 0
        except Exception as e:
            print(f"Error querying total_nodes_count: {e}")
            backend_metrics['total_nodes_count'] = 0
        
        # Query all backend metrics from Prometheus
        backend_metric_queries = {
            'cache_hits_total': 'sum(cache_hits_total{job=~"cluster-node.*"})',
            'cache_misses_total': 'sum(cache_misses_total{job=~"cluster-node.*"})',
            'cache_size': 'max(cache_size{job=~"cluster-node.*"})',
            'cache_capacity': 'max(cache_capacity{job=~"cluster-node.*"})',
            'replicated_chunks_total': 'sum(replicated_chunks_total{job=~"cluster-node.*"})',
            'replicated_files_total': 'sum(replicated_files_total{job=~"cluster-node.*"})',
            'load_balancing_decisions_total': 'sum(load_balancing_decisions_total{job=~"cluster-node.*"})',
            'shards_created_total': 'sum(shards_created_total{job=~"cluster-node.*"})',
            'node_failures_total': 'sum(node_failures_total{job=~"cluster-node.*"})',
            'node_recoveries_total': 'sum(node_recoveries_total{job=~"cluster-node.*"})',
            'raft_leader_changes_total': 'sum(raft_leader_changes_total{job=~"cluster-node.*"})',
            'raft_elections_total': 'sum(raft_elections_total{job=~"cluster-node.*"})',
            'heartbeat_checks_total': 'sum(heartbeat_checks_total{job=~"cluster-node.*"})',
            'heartbeat_failures_total': 'sum(heartbeat_failures_total{job=~"cluster-node.*"})',
        }
        
        for metric_name, query in backend_metric_queries.items():
            try:
                encoded_query = urllib.parse.quote(query)
                response = requests.get(
                    f'{prometheus_url}/api/v1/query?query={encoded_query}',
                    timeout=2
                )
                if response.status_code == 200:
                    data = response.json()
                    if data.get('status') == 'success' and data.get('data', {}).get('result'):
                        backend_metrics[metric_name] = float(data['data']['result'][0]['value'][1])
                    else:
                        backend_metrics[metric_name] = 0
                else:
                    backend_metrics[metric_name] = 0
            except Exception as e:
                backend_metrics[metric_name] = 0
        
        # Query metadata_replications_total with labels
        try:
            query = 'sum(metadata_replications_total{job=~"cluster-node.*"}) by (status)'
            encoded_query = urllib.parse.quote(query)
            response = requests.get(
                f'{prometheus_url}/api/v1/query?query={encoded_query}',
                timeout=2
            )
            if response.status_code == 200:
                data = response.json()
                if data.get('status') == 'success' and data.get('data', {}).get('result'):
                    metadata_repl = {'success': 0, 'failed': 0}
                    for result in data['data']['result']:
                        status = result.get('metric', {}).get('status', 'unknown')
                        value = float(result['value'][1])
                        if status in metadata_repl:
                            metadata_repl[status] = value
                    backend_metrics['metadata_replications_success'] = metadata_repl['success']
                    backend_metrics['metadata_replications_failed'] = metadata_repl['failed']
        except:
            backend_metrics['metadata_replications_success'] = 0
            backend_metrics['metadata_replications_failed'] = 0
        
        # Query replication_attempts_total with labels
        try:
            query = 'sum(replication_attempts_total{job=~"cluster-node.*"}) by (status)'
            encoded_query = urllib.parse.quote(query)
            response = requests.get(
                f'{prometheus_url}/api/v1/query?query={encoded_query}',
                timeout=2
            )
            if response.status_code == 200:
                data = response.json()
                if data.get('status') == 'success' and data.get('data', {}).get('result'):
                    for result in data['data']['result']:
                        status = result.get('metric', {}).get('status', 'unknown')
                        value = float(result['value'][1])
                        key = f'replication_attempts_total{{status="{status}"}}'
                        backend_metrics[key] = value
        except Exception as e:
            print(f"Error querying replication_attempts_total: {e}")
        
        # Query load_balancing_duration_seconds histogram (average)
        try:
            query = 'avg(rate(load_balancing_duration_seconds_sum{job=~"cluster-node.*"}[5m])) / avg(rate(load_balancing_duration_seconds_count{job=~"cluster-node.*"}[5m]))'
            encoded_query = urllib.parse.quote(query)
            response = requests.get(
                f'{prometheus_url}/api/v1/query?query={encoded_query}',
                timeout=2
            )
            if response.status_code == 200:
                data = response.json()
                if data.get('status') == 'success' and data.get('data', {}).get('result'):
                    avg_duration = float(data['data']['result'][0]['value'][1])
                    backend_metrics['load_balancing_duration_seconds_avg'] = avg_duration * 1000  # Convert to ms
                else:
                    backend_metrics['load_balancing_duration_seconds_avg'] = 0
            else:
                backend_metrics['load_balancing_duration_seconds_avg'] = 0
        except Exception as e:
            print(f"Error querying load_balancing_duration_seconds: {e}")
            backend_metrics['load_balancing_duration_seconds_avg'] = 0
        
        # Query web app file operation metrics (file_searches_total, file_downloads_total, etc.)
        # Read directly from web app's Prometheus client instead of querying Prometheus
        web_app_metrics = {}
        try:
            # Get metrics directly from Prometheus client
            from prometheus_client import generate_latest
            from prometheus_client.parser import text_string_to_metric_families
            
            metrics_text = generate_latest().decode('utf-8')
            # text_string_to_metric_families expects a string, not StringIO
            for family in text_string_to_metric_families(metrics_text):
                if family.name in ['file_uploads_total', 'file_downloads_total', 'file_searches_total', 'file_deletes_total']:
                    for sample in family.samples:
                        # sample format: (name, labels, value)
                        metric_name = sample[0]
                        labels = sample[1]
                        value = sample[2]
                        
                        # Build key like "file_searches_total{status=\"found\"}"
                        label_str = ','.join([f'{k}="{v}"' for k, v in labels.items()])
                        key = f'{metric_name}{{{label_str}}}'
                        web_app_metrics[key] = value
        except Exception as e:
            print(f"Error reading web app metrics directly: {e}")
            # Fallback: query Prometheus
            web_app_metric_queries = {
                'file_uploads_total': 'sum(file_uploads_total{job="web-interface"}) by (status)',
                'file_downloads_total': 'sum(file_downloads_total{job="web-interface"}) by (status)',
                'file_searches_total': 'sum(file_searches_total{job="web-interface"}) by (status)',
                'file_deletes_total': 'sum(file_deletes_total{job="web-interface"}) by (status)',
            }
            
            for metric_name, query in web_app_metric_queries.items():
                try:
                    encoded_query = urllib.parse.quote(query)
                    response = requests.get(
                        f'{prometheus_url}/api/v1/query?query={encoded_query}',
                        timeout=2
                    )
                    if response.status_code == 200:
                        data = response.json()
                        if data.get('status') == 'success' and data.get('data', {}).get('result'):
                            for result in data['data']['result']:
                                status = result.get('metric', {}).get('status', 'unknown')
                                value = float(result['value'][1])
                                key = f'{metric_name}{{status="{status}"}}'
                                web_app_metrics[key] = value
                except Exception as e2:
                    print(f"Error querying {metric_name} from Prometheus: {e2}")
        
        # Merge web app stats, backend metrics, and web app file operation metrics
        web_stats.update(backend_metrics)
        web_stats.update(web_app_metrics)
        
        # Also add metrics object for dashboard compatibility
        web_stats['metrics'] = web_app_metrics
        
        return jsonify(web_stats)
    except Exception as e:
        print(f"Error in system-metrics API: {e}")
        return jsonify({'error': str(e)})

@app.route('/dashboard')
def metrics_dashboard():
    """Metrics visualization dashboard"""
    REQUEST_COUNT.labels(method='GET', endpoint='/dashboard', status='200').inc()
    return render_template('metrics_dashboard.html')

@app.route('/')
def index():
    REQUEST_COUNT.labels(method='GET', endpoint='/', status='200').inc()
    return render_template('index.html')

@app.route('/upload', methods=['GET', 'POST'])
def upload_file():
    if request.method == 'POST':
        REQUEST_COUNT.labels(method='POST', endpoint='/upload', status='200').inc()
        start_time = time.time()
        
        if 'file' not in request.files:
            flash('No file selected', 'error')
            REQUEST_COUNT.labels(method='POST', endpoint='/upload', status='400').inc()
            REQUEST_DURATION.labels(method='POST', endpoint='/upload').observe(time.time() - start_time)
            return redirect(url_for('index'))
        
        file = request.files['file']
        username = request.form.get('username', 'default_user').strip()
        
        if file.filename == '':
            flash('No file selected', 'error')
            REQUEST_COUNT.labels(method='POST', endpoint='/upload', status='400').inc()
            REQUEST_DURATION.labels(method='POST', endpoint='/upload').observe(time.time() - start_time)
            return redirect(url_for('index'))
        
        if not username:
            flash('Username is required', 'error')
            REQUEST_COUNT.labels(method='POST', endpoint='/upload', status='400').inc()
            REQUEST_DURATION.labels(method='POST', endpoint='/upload').observe(time.time() - start_time)
            return redirect(url_for('index'))
        
        # Save file temporarily
        filepath = os.path.join(UPLOAD_FOLDER, file.filename)
        file.save(filepath)
        file_size = os.path.getsize(filepath)
        
        # Upload via gRPC with connection pooling and retry
        stub, channel = get_stub()
        if not stub:
            flash('Unable to connect to supernode. Please try again.', 'error')
            os.remove(filepath)
            UPLOAD_COUNT.labels(status='error').inc()
            REQUEST_DURATION.labels(method='POST', endpoint='/upload').observe(time.time() - start_time)
            return redirect(url_for('index'))
        
        pool = get_connection_pool()
        try:
            # Retry logic with exponential backoff
            # Note: Must recreate generator for each retry since generators can only be iterated once
            response = None
            for attempt in range(3):
                try:
                    def file_chunks():
                        CHUNK_SIZE = 4000000
                        with open(filepath, 'rb') as f:
                            while True:
                                chunk = f.read(CHUNK_SIZE)
                                if not chunk:
                                    break
                                yield fileService_pb2.FileData(username=username, filename=file.filename, data=chunk, seqNo=1)
                    
                    # Add timeout to gRPC call (30 seconds for uploads)
                    response = stub.UploadFile(file_chunks(), timeout=30)
                    break  # Success, exit retry loop
                except grpc.RpcError as e:
                    if attempt < 2 and e.code() in [grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED, grpc.StatusCode.RESOURCE_EXHAUSTED]:
                        # Retry on transient errors
                        time.sleep(0.2 * (attempt + 1))
                        RETRY_ATTEMPTS.labels(operation='upload', result='retry').inc()
                        continue
                    else:
                        raise  # Don't retry on non-transient errors
            
            if response and response.success:
                flash(f'File "{file.filename}" uploaded successfully', 'success')
                UPLOAD_COUNT.labels(status='success').inc()
                FILE_SIZE.labels(operation='upload').observe(file_size)
                os.remove(filepath)  # Clean up temp file only on success
            elif response:
                flash(f'Upload failed: {response.message}', 'error')
                UPLOAD_COUNT.labels(status='failed').inc()
                os.remove(filepath)  # Clean up temp file
            else:
                flash(f'Upload failed: No response from server', 'error')
                UPLOAD_COUNT.labels(status='error').inc()
                os.remove(filepath)
        except grpc.RpcError as e:
            os.remove(filepath)
            error_msg = str(e)
            if "Deadline Exceeded" in error_msg or "timeout" in error_msg.lower():
                flash(f'Upload timeout: The file may be too large or the server is busy. Please try again.', 'error')
            elif e.code() == grpc.StatusCode.UNAVAILABLE:
                flash(f'Service temporarily unavailable. Please try again.', 'error')
            else:
                flash(f'Upload error: {error_msg}', 'error')
            print(f"gRPC error in UploadFile: {e}")
            UPLOAD_COUNT.labels(status='error').inc()
        except Exception as e:
            os.remove(filepath)
            flash(f'Upload error: {str(e)}', 'error')
            print(f"Upload error: {e}")
            import traceback
            traceback.print_exc()
            UPLOAD_COUNT.labels(status='error').inc()
        finally:
            # Return channel to pool instead of closing
            pool.return_channel(channel)
        
        REQUEST_DURATION.labels(method='POST', endpoint='/upload').observe(time.time() - start_time)
        return redirect(url_for('index'))
    
    REQUEST_COUNT.labels(method='GET', endpoint='/upload', status='200').inc()
    return render_template('upload.html')

@app.route('/files')
def list_files():
    REQUEST_COUNT.labels(method='GET', endpoint='/files', status='200').inc()
    start_time = time.time()
    username = request.args.get('username', 'default_user').strip()
    
    stub, channel = get_stub()
    pool = get_connection_pool()
    if not stub:
        flash('Unable to connect to supernode. Please try again.', 'error')
        REQUEST_COUNT.labels(method='GET', endpoint='/files', status='500').inc()
        REQUEST_DURATION.labels(method='GET', endpoint='/files').observe(time.time() - start_time)
        return render_template('files.html', files=[], username=username)
    
    try:
        # Add timeout to gRPC call with retry
        import grpc
        response = None
        for attempt in range(3):
            try:
                response = stub.FileList(fileService_pb2.UserInfo(username=username), timeout=5)
                break
            except grpc.RpcError as e:
                if attempt < 2 and e.code() in [grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED]:
                    time.sleep(0.1 * (attempt + 1))
                    RETRY_ATTEMPTS.labels(operation='list_files', result='retry').inc()
                    continue
                else:
                    raise
        import ast
        import json
        try:
            # Try parsing as Python literal first
            files = ast.literal_eval(response.Filenames)
            # Ensure it's a list
            if not isinstance(files, list):
                files = []
        except (ValueError, SyntaxError):
            # If that fails, try JSON parsing
            try:
                files = json.loads(response.Filenames)
            except (ValueError, TypeError):
                # If both fail, return empty list
                files = []
        except Exception as e:
            print(f"Error parsing FileList response: {e}, response.Filenames={response.Filenames}")
            files = []
    except grpc.RpcError as e:
        flash(f'Error fetching files: {str(e)}', 'error')
        print(f"gRPC error in FileList: {e}")
        files = []
    except Exception as e:
        flash(f'Error fetching files: {str(e)}', 'error')
        print(f"FileList error: {e}")
        import traceback
        traceback.print_exc()
        files = []
    finally:
        # Return channel to pool
        pool.return_channel(channel)
    
    REQUEST_DURATION.labels(method='GET', endpoint='/files').observe(time.time() - start_time)
    return render_template('files.html', files=files, username=username)

@app.route('/download')
def download_file():
    REQUEST_COUNT.labels(method='GET', endpoint='/download', status='200').inc()
    start_time = time.time()
    username = request.args.get('username', 'default_user').strip()
    filename = request.args.get('filename', '').strip()
    
    if not filename:
        flash('Filename is required', 'error')
        REQUEST_COUNT.labels(method='GET', endpoint='/download', status='400').inc()
        return redirect(url_for('list_files', username=username))
    
    stub, channel = get_stub()
    pool = get_connection_pool()
    if not stub:
        flash('Unable to connect to supernode. Please try again.', 'error')
        DOWNLOAD_COUNT.labels(status='error').inc()
        return redirect(url_for('list_files', username=username))
    
    try:
        data = bytes()
        # Add timeout to gRPC call with retry (10 seconds for downloads)
        responses = None
        for attempt in range(3):
            try:
                responses = stub.DownloadFile(fileService_pb2.FileInfo(username=username, filename=filename), timeout=10)
                break
            except grpc.RpcError as e:
                if attempt < 2 and e.code() in [grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED]:
                    time.sleep(0.1 * (attempt + 1))
                    RETRY_ATTEMPTS.labels(operation='download', result='retry').inc()
                    continue
                else:
                    raise
        
        for response in responses:
            data += response.data
        
        if not data:
            flash('File not found or empty', 'error')
            DOWNLOAD_COUNT.labels(status='not_found').inc()
            REQUEST_DURATION.labels(method='GET', endpoint='/download').observe(time.time() - start_time)
            return redirect(url_for('list_files', username=username))
        
        # Save to downloads folder
        download_path = os.path.join(DOWNLOAD_FOLDER, filename)
        with open(download_path, 'wb') as f:
            f.write(data)
        
        FILE_SIZE.labels(operation='download').observe(len(data))
        DOWNLOAD_COUNT.labels(status='success').inc()
        REQUEST_DURATION.labels(method='GET', endpoint='/download').observe(time.time() - start_time)
        return send_file(download_path, as_attachment=True, download_name=filename)
    except grpc.RpcError as e:
        error_msg = f'Download error: {e.code().name} - {e.details()}' if hasattr(e, 'code') else f'Download error: {str(e)}'
        flash(error_msg, 'error')
        DOWNLOAD_COUNT.labels(status='error').inc()
        REQUEST_DURATION.labels(method='GET', endpoint='/download').observe(time.time() - start_time)
        return redirect(url_for('list_files', username=username))
    except Exception as e:
        flash(f'Download error: {str(e)}', 'error')
        DOWNLOAD_COUNT.labels(status='error').inc()
        REQUEST_DURATION.labels(method='GET', endpoint='/download').observe(time.time() - start_time)
        return redirect(url_for('list_files', username=username))
    finally:
        # Return channel to pool
        pool.return_channel(channel)

@app.route('/search', methods=['GET', 'POST'])
def search_file():
    if request.method == 'POST':
        REQUEST_COUNT.labels(method='POST', endpoint='/search', status='200').inc()
        start_time = time.time()
        username = request.form.get('username', 'default_user').strip()
        filename = request.form.get('filename', '').strip()
        
        if not filename:
            flash('Filename is required', 'error')
            REQUEST_COUNT.labels(method='POST', endpoint='/search', status='400').inc()
            return redirect(url_for('search_file'))
        
        stub, channel = get_stub()
        pool = get_connection_pool()
        if not stub:
            flash('Unable to connect to supernode. Please try again.', 'error')
            SEARCH_COUNT.labels(status='error').inc()
            return redirect(url_for('search_file'))
        
        try:
            # Add timeout to gRPC call with retry (5 seconds for searches)
            response = None
            for attempt in range(3):
                try:
                    response = stub.FileSearch(fileService_pb2.FileInfo(username=username, filename=filename), timeout=5)
                    break
                except grpc.RpcError as e:
                    if attempt < 2 and e.code() in [grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED]:
                        time.sleep(0.1 * (attempt + 1))
                        RETRY_ATTEMPTS.labels(operation='search', result='retry').inc()
                        continue
                    else:
                        raise
            if response.success:
                flash(f'File "{filename}" found: {response.message}', 'success')
                SEARCH_COUNT.labels(status='found').inc()
            else:
                flash(f'File "{filename}" not found: {response.message}', 'error')
                SEARCH_COUNT.labels(status='not_found').inc()
        except grpc.RpcError as e:
            error_msg = f'Search error: {e.code().name} - {e.details()}' if hasattr(e, 'code') else f'Search error: {str(e)}'
            flash(error_msg, 'error')
            SEARCH_COUNT.labels(status='error').inc()
        except Exception as e:
            flash(f'Search error: {str(e)}', 'error')
            SEARCH_COUNT.labels(status='error').inc()
        finally:
            # Return channel to pool
            pool.return_channel(channel)
        
        REQUEST_DURATION.labels(method='POST', endpoint='/search').observe(time.time() - start_time)
        return redirect(url_for('search_file'))
    
    REQUEST_COUNT.labels(method='GET', endpoint='/search', status='200').inc()
    return render_template('search.html')

@app.route('/delete', methods=['POST'])
def delete_file():
    REQUEST_COUNT.labels(method='POST', endpoint='/delete', status='200').inc()
    start_time = time.time()
    username = request.form.get('username', 'default_user').strip()
    filename = request.form.get('filename', '').strip()
    
    if not filename:
        flash('Filename is required', 'error')
        REQUEST_COUNT.labels(method='POST', endpoint='/delete', status='400').inc()
        return redirect(url_for('list_files', username=username))
    
    stub, channel = get_stub()
    pool = get_connection_pool()
    if not stub:
        flash('Unable to connect to supernode. Please try again.', 'error')
        DELETE_COUNT.labels(status='error').inc()
        return redirect(url_for('list_files', username=username))
    
    try:
        # Add timeout to gRPC call with retry (5 seconds for deletes)
        response = None
        for attempt in range(3):
            try:
                response = stub.FileDelete(fileService_pb2.FileInfo(username=username, filename=filename), timeout=5)
                break
            except grpc.RpcError as e:
                if attempt < 2 and e.code() in [grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED]:
                    time.sleep(0.1 * (attempt + 1))
                    RETRY_ATTEMPTS.labels(operation='delete', result='retry').inc()
                    continue
                else:
                    raise
        if response.success:
            flash(f'File "{filename}" deleted successfully', 'success')
            DELETE_COUNT.labels(status='success').inc()
        else:
            flash(f'Delete failed: {response.message}', 'error')
            DELETE_COUNT.labels(status='failed').inc()
    except grpc.RpcError as e:
        error_msg = f'Delete error: {e.code().name} - {e.details()}' if hasattr(e, 'code') else f'Delete error: {str(e)}'
        flash(error_msg, 'error')
        DELETE_COUNT.labels(status='error').inc()
    except Exception as e:
        flash(f'Delete error: {str(e)}', 'error')
        DELETE_COUNT.labels(status='error').inc()
    finally:
        # Return channel to pool
        pool.return_channel(channel)
    
    REQUEST_DURATION.labels(method='POST', endpoint='/delete').observe(time.time() - start_time)
    return redirect(url_for('list_files', username=username))

@app.route('/status')
def cluster_status():
    """Get cluster status and metrics"""
    stub, channel = get_stub()
    pool = get_connection_pool()
    if not stub:
        return jsonify({
            'status': 'error',
            'message': 'Unable to connect to supernode'
        }), 503
    
    try:
        # Try to get cluster stats, but it might not be available
        # Return basic connection status for now
        return jsonify({
            'status': 'online',
            'message': 'Connected to supernode'
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500
    finally:
        # Return channel to pool
        pool.return_channel(channel)

@app.route('/api/files/<username>')
def api_list_files(username):
    """API endpoint for file listing"""
    stub, channel = get_stub()
    pool = get_connection_pool()
    if not stub:
        return jsonify({'error': 'Unable to connect to supernode'}), 503
    
    try:
        # Retry logic for API calls
        response = None
        for attempt in range(3):
            try:
                response = stub.FileList(fileService_pb2.UserInfo(username=username), timeout=5)
                break
            except grpc.RpcError as e:
                if attempt < 2 and e.code() in [grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED]:
                    time.sleep(0.1 * (attempt + 1))
                    continue
                else:
                    raise
        import ast
        try:
            files = ast.literal_eval(response.Filenames)
        except:
            files = []
        return jsonify({'files': files})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        # Return channel to pool
        pool.return_channel(channel)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=True)
