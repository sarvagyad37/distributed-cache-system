#!/usr/bin/env python3
"""
Enhanced Stress Testing Script for Distributed File Storage System
Author: Sarvagya Dwivedi

Comprehensive stress test with metrics collection
Realistic 80/20 access patterns for cache testing
Run with: locust -f stress_test.py --host=http://localhost:8080 --users 50 --spawn-rate 5 --run-time 5m
For 1M requests: --users 200 --spawn-rate 10 --run-time 30m
"""

import os
import time
import random
import string
from locust import HttpUser, task, between, events

# Configuration
BASE_URL = os.environ.get('BASE_URL', 'http://localhost:8080')
USERNAME = os.environ.get('TEST_USERNAME', 'stress_test_user')

def generate_random_filename(extension='txt'):
    """Generate a random filename"""
    random_str = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return f"stress_test_{random_str}.{extension}"

def generate_test_file(size_kb=100):
    """Generate a test file of specified size"""
    content = 'x' * (size_kb * 1024)
    return content.encode('utf-8')

class FileStorageUser(HttpUser):
    """Locust user class for comprehensive stress testing"""
    wait_time = between(0.5, 2)  # Faster wait time for stress testing
    
    def on_start(self):
        """Called when a user starts"""
        self.username = f"{USERNAME}_{random.randint(1000, 9999)}"
        self.uploaded_files = []
        self.popular_files = []  # 20% most popular files (80/20 rule)
        self.upload_count = 0
        self.download_count = 0
    
    @task(10)  # Highest priority - uploads
    def upload_file(self):
        """Upload a file - triggers replication, sharding, load balancing"""
        filename = generate_random_filename()
        # Varied file sizes to test sharding
        file_size = random.choice([10, 50, 100, 500, 1000, 5000])  # KB
        file_content = generate_test_file(size_kb=file_size)
        
        files = {'file': (filename, file_content, 'application/octet-stream')}
        data = {'username': self.username}
        
        with self.client.post('/upload', files=files, data=data, 
                             catch_response=True, name='upload') as response:
            if response.status_code == 200 or response.status_code == 302:
                response.success()
                self.uploaded_files.append(filename)
                # Make 20% of files "popular" (will be accessed 80% of time)
                if random.random() < 0.2:
                    self.popular_files.append(filename)
                self.upload_count += 1
            else:
                response.failure(f"Upload failed with status {response.status_code}")
    
    @task(8)  # High priority - downloads (tests cache with 80/20 rule)
    def download_file(self):
        """Download a file - tests cache hit/miss with realistic 80/20 pattern"""
        if not self.uploaded_files:
            return
        
        # Real-world pattern: 80% of traffic = 20% of popular files
        if random.random() < 0.8 and self.popular_files:
            # 80% chance to download popular file (cache should hit)
            filename = random.choice(self.popular_files)
        else:
            # 20% chance to download random file (may cache miss)
            filename = random.choice(self.uploaded_files)
        
        params = {'username': self.username, 'filename': filename}
        
        with self.client.get('/download', params=params, 
                            catch_response=True, name='download', stream=True) as response:
            if response.status_code == 200:
                response.success()
                self.download_count += 1
            else:
                response.failure(f"Download failed with status {response.status_code}")
    
    @task(5)  # Medium priority - list files
    def list_files(self):
        """List files"""
        params = {'username': self.username}
        with self.client.get('/files', params=params, 
                            catch_response=True, name='list_files') as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"List failed with status {response.status_code}")
    
    @task(3)  # Lower priority - search
    def search_file(self):
        """Search for a file"""
        if not self.uploaded_files:
            return
        
        filename = random.choice(self.uploaded_files)
        data = {'username': self.username, 'filename': filename}
        
        with self.client.post('/search', data=data, 
                             catch_response=True, name='search') as response:
            if response.status_code == 200 or response.status_code == 302:
                response.success()
            else:
                response.failure(f"Search failed with status {response.status_code}")
    
    @task(2)  # Lower priority - delete
    def delete_file(self):
        """Delete a file"""
        if not self.uploaded_files:
            return
        
        filename = random.choice(self.uploaded_files)
        data = {'username': self.username, 'filename': filename}
        
        with self.client.post('/delete', data=data, 
                             catch_response=True, name='delete') as response:
            if response.status_code == 200 or response.status_code == 302:
                response.success()
                # Remove from list if deleted
                if filename in self.uploaded_files:
                    self.uploaded_files.remove(filename)
            else:
                response.failure(f"Delete failed with status {response.status_code}")
    
    @task(1)  # Lowest priority - status check
    def check_status(self):
        """Check cluster status"""
        with self.client.get('/status', catch_response=True, name='status') as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Status check failed with status {response.status_code}")

@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """Called when test starts"""
    print("\n" + "="*70)
    print("STRESS TEST STARTED")
    print("="*70)
    print(f"Target: {BASE_URL}")
    print(f"Metrics available at: http://localhost:8080/metrics")
    print(f"Dashboard: http://localhost:8080/dashboard")
    print(f"Prometheus: http://localhost:9090")
    print(f"Grafana: http://localhost:3000")
    print("="*70 + "\n")

@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Called when test stops"""
    print("\n" + "="*70)
    print("STRESS TEST COMPLETED")
    print("="*70)
    print("\nCheck metrics at:")
    print("  - Prometheus: http://localhost:9090")
    print("  - Grafana: http://localhost:3000")
    print("  - Web Dashboard: http://localhost:8080/dashboard")
    print("="*70 + "\n")

if __name__ == '__main__':
    print(f"\nStarting stress test against {BASE_URL}")
    print("\nRun with Locust:")
    print("  Basic: locust -f stress_test.py --host=http://localhost:8080")
    print("  Stress: locust -f stress_test.py --host=http://localhost:8080 --users 50 --spawn-rate 5 --run-time 5m")
    print("\nOr use run_stress_test.sh script")
    print("\nThen open: http://localhost:8089")
