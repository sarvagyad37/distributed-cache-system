from concurrent import futures

import grpc
import sys
import os
sys.path.append('../generated')
sys.path.append('../utils')
import db
import fileService_pb2_grpc
import fileService_pb2
import heartbeat_pb2_grpc
import heartbeat_pb2
import time
import yaml
import threading
import hashlib

# Add utils path BEFORE importing metrics
current_dir = os.path.dirname(os.path.abspath(__file__))
utils_path = current_dir
if utils_path not in sys.path:
    sys.path.insert(0, utils_path)

# Import metrics - NO FALLBACK, fail fast if import fails
from system_metrics import metrics

#
#   *** ShardingHandler Utility : Helper class to get least loaded nodes. ***
#
class ShardingHandler():
    def __init__(self, activeNodesChecker):
        self.activeNodesChecker = activeNodesChecker
        # Always get fresh channel dict for each operation to avoid stale data

    def leastUtilizedNode(self):
        print("Inside leastUtilizedNode method")
        return self.leastUtilizedNodeHelper()
    
    # This method is responsible for finding 2 least loaded nodes from cluster.
    # This method makes gRPC calls to each node in the cluster asking for the CPU stats and based on that it decides the 2 least loaded nodes.
    def leastUtilizedNodeHelper(self):
        # Get fresh active channels to avoid stale data
        active_ip_channel_dict = self.activeNodesChecker.getActiveChannels()
        start_time = time.time()
        minVal, minVal2 = 301.00, 301.00
        leastLoadedNode, leastLoadedNode2 = "",""
        for ip, channel in active_ip_channel_dict.items():
            if(self.isChannelAlive(channel)):
                stub = heartbeat_pb2_grpc.HearBeatStub(channel)
                stats = stub.isAlive(heartbeat_pb2.NodeInfo(ip="", port=""))
                total = float(stats.cpu_usage) + float(stats.disk_space) + float(stats.used_mem)
                if ((total/3)<minVal):
                   minVal2 = minVal
                   minVal = total/3
                   leastLoadedNode2 = leastLoadedNode
                   leastLoadedNode = ip
                elif((total/3)<minVal2):
                   minVal2 = total/3
                   leastLoadedNode2 = ip

        duration = time.time() - start_time
        metrics.record_load_balance_duration(duration)
        
        if(leastLoadedNode==""):
            return -1, ""
        
        # Track load balancing decision
        metrics.record_load_balance_decision(leastLoadedNode)
        if leastLoadedNode2:
            metrics.record_load_balance_decision(leastLoadedNode2)
        
        return leastLoadedNode, leastLoadedNode2

    # This method checks whether the channel is alive or not.
    def isChannelAlive(self, channel):
        try:
            grpc.channel_ready_future(channel).result(timeout=1)
        except grpc.FutureTimeoutError:
            return False
        return True