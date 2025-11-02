"""
System Metrics Collector
Exposes real metrics from the distributed file storage system
Author: Sarvagya Dwivedi
"""

from prometheus_client import Counter, Histogram, Gauge, Info
import threading
import time

# Cache Metrics
CACHE_HITS = Counter('cache_hits_total', 'Total cache hits')
CACHE_MISSES = Counter('cache_misses_total', 'Total cache misses')
CACHE_SIZE = Gauge('cache_size', 'Current number of items in cache')
CACHE_CAPACITY = Gauge('cache_capacity', 'Maximum cache capacity')

# Replication Metrics
REPLICATION_ATTEMPTS = Counter('replication_attempts_total', 'Total replication attempts', ['status'])
REPLICATION_DURATION = Histogram('replication_duration_seconds', 'Replication duration', ['operation'])
REPLICATED_CHUNKS = Counter('replicated_chunks_total', 'Total chunks replicated')
REPLICATED_FILES = Counter('replicated_files_total', 'Total files replicated')

# Load Balancing Metrics
LOAD_BALANCING_DECISIONS = Counter('load_balancing_decisions_total', 'Load balancing decisions made')
NODE_SELECTION_COUNT = Counter('node_selection_count', 'Node selection count', ['node_ip'])
LOAD_BALANCING_DURATION = Histogram('load_balancing_duration_seconds', 'Time to select least loaded node')

# Fault Tolerance Metrics
NODE_FAILURES = Counter('node_failures_total', 'Total node failures detected')
NODE_RECOVERIES = Counter('node_recoveries_total', 'Total node recoveries')
ACTIVE_NODES = Gauge('active_nodes_count', 'Current number of active nodes')
TOTAL_NODES = Gauge('total_nodes_count', 'Total configured nodes')
RAFT_LEADER_CHANGES = Counter('raft_leader_changes_total', 'Total RAFT leader changes')
RAFT_ELECTIONS = Counter('raft_elections_total', 'Total RAFT elections')

# Sharding Metrics
SHARDS_CREATED = Counter('shards_created_total', 'Total shards created')
SHARD_SIZE = Histogram('shard_size_bytes', 'Size of shards created', buckets=[1024, 10240, 102400, 1048576, 10485760, 52428800])

# Metadata Replication Metrics
METADATA_REPLICATIONS = Counter('metadata_replications_total', 'Total metadata replications', ['status'])

# System Health Metrics
HEARTBEAT_CHECKS = Counter('heartbeat_checks_total', 'Total heartbeat checks performed')
HEARTBEAT_FAILURES = Counter('heartbeat_failures_total', 'Heartbeat failures detected')

# Thread-safe metrics storage
class SystemMetrics:
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(SystemMetrics, cls).__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self.cache_hits = 0
        self.cache_misses = 0
        self.active_nodes = 0
        self.total_nodes = 0
        self.replication_count = 0
        self.failed_nodes = 0
        self.recovered_nodes = 0
        self.load_balance_decisions = 0
        self.shards_created = 0
        self._initialized = True
    
    def record_cache_hit(self):
        self.cache_hits += 1
        CACHE_HITS.inc()
    
    def record_cache_miss(self):
        self.cache_misses += 1
        CACHE_MISSES.inc()
    
    def set_cache_size(self, size):
        CACHE_SIZE.set(size)
    
    def set_cache_capacity(self, capacity):
        CACHE_CAPACITY.set(capacity)
    
    def record_replication(self, status='success'):
        self.replication_count += 1
        REPLICATION_ATTEMPTS.labels(status=status).inc()
    
    def record_replication_duration(self, duration, operation='chunk'):
        REPLICATION_DURATION.labels(operation=operation).observe(duration)
    
    def record_node_failure(self):
        self.failed_nodes += 1
        NODE_FAILURES.inc()
    
    def record_node_recovery(self):
        self.recovered_nodes += 1
        NODE_RECOVERIES.inc()
    
    def set_active_nodes(self, count):
        self.active_nodes = count
        ACTIVE_NODES.set(count)
    
    def set_total_nodes(self, count):
        self.total_nodes = count
        TOTAL_NODES.set(count)
    
    def record_load_balance_decision(self, node_ip):
        self.load_balance_decisions += 1
        LOAD_BALANCING_DECISIONS.inc()
        NODE_SELECTION_COUNT.labels(node_ip=node_ip).inc()
    
    def record_load_balance_duration(self, duration):
        LOAD_BALANCING_DURATION.observe(duration)
    
    def record_shard_creation(self, size_bytes):
        self.shards_created += 1
        SHARDS_CREATED.inc()
        SHARD_SIZE.observe(size_bytes)
    
    def record_raft_leader_change(self):
        RAFT_LEADER_CHANGES.inc()
    
    def record_raft_election(self):
        RAFT_ELECTIONS.inc()
    
    def record_heartbeat_check(self):
        HEARTBEAT_CHECKS.inc()
    
    def record_heartbeat_failure(self):
        HEARTBEAT_FAILURES.inc()
    
    def record_metadata_replication(self, status='success'):
        METADATA_REPLICATIONS.labels(status=status).inc()
    
    def get_cache_hit_rate(self):
        total = self.cache_hits + self.cache_misses
        return (self.cache_hits / total * 100) if total > 0 else 0
    
    def get_stats(self):
        return {
            'cache_hits': self.cache_hits,
            'cache_misses': self.cache_misses,
            'cache_hit_rate': self.get_cache_hit_rate(),
            'active_nodes': self.active_nodes,
            'total_nodes': self.total_nodes,
            'replication_count': self.replication_count,
            'failed_nodes': self.failed_nodes,
            'recovered_nodes': self.recovered_nodes,
            'load_balance_decisions': self.load_balance_decisions,
            'shards_created': self.shards_created
        }

# Global metrics instance
metrics = SystemMetrics()

