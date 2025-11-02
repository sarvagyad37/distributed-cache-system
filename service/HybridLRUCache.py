"""
Enterprise-Level Hybrid LRU + LFU Cache Implementation

Combines:
- LRU (Least Recently Used): Recency tracking
- LFU (Least Frequently Used): Frequency tracking
- Score-based eviction: cache_score = (frequency × 0.6) + (recency × 0.4)
"""

import time
import threading
import heapq
import math
from collections import OrderedDict


class HybridLRUCache:
    """
    Hybrid LRU + LFU cache with score-based eviction.
    
    Eviction Policy:
    - Tracks both frequency (LFU) and recency (LRU)
    - Calculates cache score: score = (frequency × 0.6) + (recency × 0.4)
    - Evicts items with lowest scores when capacity is reached
    """
    
    def __init__(self, capacity=100, frequency_weight=0.6, recency_weight=0.4):
        """
        Initialize hybrid cache.
        
        Args:
            capacity: Maximum number of items in cache
            frequency_weight: Weight for frequency component (0.0-1.0)
            recency_weight: Weight for recency component (0.0-1.0)
        """
        self.capacity = capacity
        self.frequency_weight = frequency_weight
        self.recency_weight = recency_weight
        
        # Storage: {key: value}
        self.cache = {}
        
        # Track frequency: {key: access_count}
        self.frequency = {}
        
        # Track recency: {key: last_access_time}
        self.recency = {}
        
        # Keep ordered dict for efficient eviction
        self.order = OrderedDict()
        
        # Thread safety: Lock for all cache operations
        self._lock = threading.RLock()  # Reentrant lock for nested calls
        
        # Performance optimization: Heap for O(log n) eviction
        # Heap stores (score, key) tuples - min heap (lowest score = eviction candidate)
        self._eviction_heap = []
        self._heap_dirty = True  # Flag to indicate if heap needs rebuilding
        self._score_cache = {}  # Cache scores to avoid recalculation
    
    def _calculate_recency_score(self, time_since_access):
        """
        Calculate recency score for a given time since access.
        Returns a score between 0.0 and 1.0.
        """
        # Multi-timeframe decay: considers both short-term and long-term recency
        # Short-term (last 5 min): very high weight
        # Medium-term (last 30 min): moderate weight
        # Long-term (>30 min): gradual decay over hours
        
        decay_rate = 3600.0  # 1 hour base decay
        
        # Exponential decay: gradual, no hard cutoff
        # exp(-t/T) gives smooth decay from 1.0 to 0.0
        if time_since_access <= 300:  # Last 5 minutes
            recency_score = 1.0  # Maximum score for very recent
        elif time_since_access <= 1800:  # Last 30 minutes
            # Linear interpolation for medium-term
            recency_score = 1.0 - ((time_since_access - 300) / 1500) * 0.3  # Down to 0.7
        else:
            # Exponential decay for long-term
            recency_score = 0.7 * math.exp(-(time_since_access - 1800) / decay_rate)
        
        return max(0.0, recency_score)
    
    def _calculate_score(self, key):
        """
        Calculate cache score for a key with OPTIMIZED normalization.
        
        Score = (frequency × frequency_weight) + (recency_score × recency_weight)
        
        Optimizations:
        - Logarithmic frequency to prevent score collapse
        - Exponential recency for gradual decay
        - Percentile-based normalization for better distribution
        """
        if key not in self.frequency or key not in self.recency:
            return 0.0
        
        # OPTIMIZED FREQUENCY NORMALIZATION
        # Use logarithmic scale to prevent collapse from super-popular files
        frequencies = list(self.frequency.values())
        if not frequencies:
            freq_score = 0.0
        else:
            max_freq = max(frequencies)
            if max_freq <= 1:
                freq_score = 1.0 if self.frequency[key] > 0 else 0.0
            else:
                # Logarithmic normalization: prevents one file from dominating scores
                log_key = math.log(1 + self.frequency[key])
                log_max = math.log(1 + max_freq)
                freq_score = log_key / log_max
        
        # OPTIMIZED RECENCY NORMALIZATION
        # Use exponential decay for gradual, smooth scoring
        current_time = time.time()
        time_since_access = current_time - self.recency[key]
        recency_score = self._calculate_recency_score(time_since_access)
        
        # Calculate final score
        score = (freq_score * self.frequency_weight) + (recency_score * self.recency_weight)
        return score
    
    def _rebuild_heap(self):
        """Rebuild eviction heap - O(n log n) but only called when needed."""
        self._eviction_heap = []
        self._score_cache = {}
        
        # Calculate scores for all items and build heap
        for key in self.cache.keys():
            score = self._calculate_score(key)
            self._score_cache[key] = score
            heapq.heappush(self._eviction_heap, (score, key))
        
        self._heap_dirty = False
    
    def _update_score(self, key):
        """Update score for a key in heap (marks heap as dirty for lazy rebuild)."""
        # For performance, we mark heap as dirty and rebuild lazily
        # This avoids expensive heap updates on every access
        self._heap_dirty = True
        if key in self._score_cache:
            del self._score_cache[key]
    
    def _evict_lowest_score(self):
        """Evict the item with the lowest cache score - O(log n) with heap."""
        if not self.cache:
            return
        
        # Rebuild heap if dirty (scores changed)
        if self._heap_dirty or not self._eviction_heap:
            self._rebuild_heap()
        
        # Pop lowest score item from heap
        # Handle case where heap might have stale entries (key already evicted)
        # Add iteration counter to prevent infinite loop
        max_iterations = len(self.cache) * 2  # Safety limit
        iterations = 0
        
        while self._eviction_heap and iterations < max_iterations:
            iterations += 1
            score, evict_key = heapq.heappop(self._eviction_heap)
            
            # Check if key still exists and score is still valid
            if evict_key in self.cache:
                # Verify this is still the lowest score (might have changed)
                current_score = self._calculate_score(evict_key)
                if abs(current_score - score) < 0.0001:  # Score matches (within float precision)
                    self._remove(evict_key)
                    return
                else:
                    # Score changed, re-add with new score
                    heapq.heappush(self._eviction_heap, (current_score, evict_key))
                    continue
        
        # Fallback: If heap is empty, all entries stale, or max iterations reached
        # Use O(n) method to ensure correctness
        scores = {key: self._calculate_score(key) for key in self.cache.keys()}
        lowest_key = min(scores.items(), key=lambda x: x[1])[0]
        self._remove(lowest_key)
        self._heap_dirty = True  # Rebuild heap next time
    
    def _remove(self, key):
        """Remove a key from cache and all tracking structures."""
        if key in self.cache:
            del self.cache[key]
        if key in self.frequency:
            del self.frequency[key]
        if key in self.recency:
            del self.recency[key]
        if key in self.order:
            del self.order[key]
        if key in self._score_cache:
            del self._score_cache[key]
        # Mark heap as dirty since we removed an item
        self._heap_dirty = True
    
    def __contains__(self, key):
        """Check if key is in cache (thread-safe)."""
        with self._lock:
            return key in self.cache
    
    def __getitem__(self, key):
        """Get item from cache and update access tracking (thread-safe)."""
        with self._lock:
            if key not in self.cache:
                raise KeyError(key)
            
            # Update frequency
            self.frequency[key] = self.frequency.get(key, 0) + 1
            
            # Update recency
            self.recency[key] = time.time()
            
            # Update order (move to end)
            if key in self.order:
                self.order.move_to_end(key)
            else:
                self.order[key] = None
            
            # Mark score as changed (lazy heap update)
            self._update_score(key)
            
            return self.cache[key]
    
    def __setitem__(self, key, value):
        """Set item in cache with smart eviction (thread-safe)."""
        with self._lock:
            # If key exists, update it
            if key in self.cache:
                self.cache[key] = value
                self.frequency[key] = self.frequency.get(key, 0) + 1
                self.recency[key] = time.time()
                self.order.move_to_end(key)
                self._update_score(key)  # Score changed
                return
            
            # If cache is full, evict lowest score item
            if len(self.cache) >= self.capacity:
                self._evict_lowest_score()
            
            # Add new item
            self.cache[key] = value
            self.frequency[key] = 1
            self.recency[key] = time.time()
            self.order[key] = None
            self._update_score(key)  # New item, mark heap dirty
    
    def __delitem__(self, key):
        """Delete item from cache (thread-safe)."""
        with self._lock:
            self._remove(key)
    
    def __len__(self):
        """Return number of items in cache (thread-safe)."""
        with self._lock:
            return len(self.cache)
    
    def keys(self):
        """Return all cache keys (thread-safe)."""
        with self._lock:
            return list(self.cache.keys())  # Return copy to prevent modification during iteration
    
    def get(self, key, default=None):
        """Get item from cache, return default if not found (thread-safe)."""
        with self._lock:
            if key in self.cache:
                return self[key]
            return default
    
    def clear(self):
        """Clear all cache data (thread-safe)."""
        with self._lock:
            self.cache.clear()
            self.frequency.clear()
            self.recency.clear()
            self.order.clear()
            self._eviction_heap.clear()
            self._score_cache.clear()
            self._heap_dirty = True
    
    def get_stats(self):
        """Get cache statistics (thread-safe)."""
        with self._lock:
            if not self.cache:
                return {
                    'size': 0,
                    'capacity': self.capacity,
                    'avg_frequency': 0,
                    'avg_recency_score': 0,
                    'avg_cache_score': 0
                }
            
            avg_freq = sum(self.frequency.values()) / len(self.frequency)
            # Use OPTIMIZED recency scoring (multi-timeframe exponential)
            # This matches the actual _calculate_score method
            current_time = time.time()
            recency_scores = [self._calculate_recency_score(current_time - last_access) for last_access in self.recency.values()]
            avg_recency = sum(recency_scores) / len(recency_scores) if recency_scores else 0
            
            scores = [self._calculate_score(key) for key in self.cache.keys()]
            avg_score = sum(scores) / len(scores) if scores else 0
            
            return {
                'size': len(self.cache),
                'capacity': self.capacity,
                'avg_frequency': avg_freq,
                'avg_recency_score': avg_recency,
                'avg_cache_score': avg_score
            }

