from concurrent import futures

import grpc
import sys
sys.path.append("../generated")
sys.path.append("../utils")
import db
import fileService_pb2_grpc
import fileService_pb2
import heartbeat_pb2_grpc
import heartbeat_pb2
import time
import yaml
import threading
import hashlib
from Raft import Raft
from pysyncobj import SyncObj, replicated
import os

# Add utils path BEFORE importing metrics
current_dir = os.path.dirname(os.path.abspath(__file__))
utils_path = current_dir
if utils_path not in sys.path:
    sys.path.insert(0, utils_path)

# Import metrics - NO FALLBACK, fail fast if import fails
from system_metrics import metrics

#
#   Raft Utility : Helper class to start the raft service.
#
class RaftHelper():
    def __init__(self, hostname, server_port, raft_port, activeNodesChecker, superNodeAddress):
        self.activeNodesChecker = activeNodesChecker
        self.serverAddress = hostname + ":" + raft_port
        self.raft_port = raft_port
        self.superNodeAddress = superNodeAddress
        self.hostname = hostname
        self.serverPort = server_port

    #
    #  A thread will start for this method. This method keeps updating the primaryStatus field in db
    #  whenever leader goes down.
    #  Also this method is responisble for sending the newly elected leader info to SuperNode.
    #
    def startRaftServer(self):
        time.sleep(2)  # Reduced from 4 to 2 seconds
        print("------------------------------Starting Raft Server-------------------------------------")
        otherNodes = self.getListOfOtherNodes(self.activeNodesChecker.getAllAvailableIPAddresses())

        for node in otherNodes:
            print(node)

        otherNodes.remove(self.serverAddress)

        raftInstance = Raft(self.serverAddress, otherNodes)
        print("Raft utility has been started")

        n = 0
        old_value = -1
        isLeaderUpdated = False
        
        # Check if already primary and register immediately
        if int(db.get("primaryStatus")) == 1:
            print("Already primary, registering with SuperNode immediately...")
            self.sendLeaderInfoToSuperNode()
            isLeaderUpdated = True
        
        while True:
            time.sleep(0.5)
            if raftInstance.getCounter() != old_value:
                old_value = raftInstance.getCounter()
            if raftInstance._getLeader() is None:
                # CRITICAL FIX: Check total active nodes (including self) to ensure at least 1
                # This prevents issues when node count temporarily drops to 0
                total_active_nodes = self.activeNodesChecker.getTotalActiveNodeCount()
                # We can always be leader if we're the only node (total_active_nodes >= 1)
                # or if we have other nodes to coordinate with
                if total_active_nodes >= 1:  # Minimum 1 (ourselves) is always present
                    if not isLeaderUpdated:
                        print("Since the leader is None, declaring myself the leader:", self.serverAddress)
                        print(f"   Total active nodes: {total_active_nodes} (ensuring minimum of 1)")
                        db.setData("primaryStatus", 1)
                        self.sendLeaderInfoToSuperNode()
                        isLeaderUpdated = True
                continue
            n += 1
            # Check more frequently - every 5 iterations (2.5 seconds) instead of 20 (10 seconds)
            if n % 5 == 0:
                isLeader = raftInstance._isLeader()
                if n % 20 == 0:  # Print debug info every 20 iterations
                    print("===================================")
                    print("Am I the leader?", isLeader)
                    print("Current Leader running at address:", raftInstance._getLeader())
                self.updatePrimaryStatus(isLeader, raftInstance)

    def getListOfOtherNodes(self, AllAvailableIPAddresses):
        allavailableIps = self.activeNodesChecker.getAllAvailableIPAddresses()
        raftNodes = []
        for ip in allavailableIps:
            ip, port = ip.split(":")
            raftNodes.append(ip+":"+self.raft_port)
        return raftNodes

    # Method to update the primaryStatus flag in db and also to send newly elected leader info to supernode
    def updatePrimaryStatus(self, isLeader, raftInstance):
        isPrimary = int(db.get("primaryStatus"))

        if(isPrimary==1):
            self.sendLeaderInfoToSuperNode()

        if (raftInstance._getLeader() is None):
            db.setData("primaryStatus", 1)
            self.sendLeaderInfoToSuperNode()
        elif(isLeader and isPrimary==0):
            db.setData("primaryStatus", 1)
            self.sendLeaderInfoToSuperNode()
            metrics.record_raft_leader_change()
            metrics.record_raft_election()
        elif(not isLeader and isPrimary==1):
            db.setData("primaryStatus", 0)
            metrics.record_raft_leader_change()

    # Method to send newly elected leader info to supernode
    def sendLeaderInfoToSuperNode(self):        
        try:
            channel = grpc.insecure_channel('{}'.format(self.superNodeAddress))
            stub = fileService_pb2_grpc.FileserviceStub(channel)
            response = stub.getLeaderInfo(fileService_pb2.ClusterInfo(ip = self.hostname, port= self.serverPort, clusterName="team1"))
            print(f"✅ Successfully registered leader with SuperNode: {self.hostname}:{self.serverPort}")
            print(f"   Response: {response.message}")
            channel.close()
        except Exception as e:
            print(f"❌ Not able to connect to supernode at {self.superNodeAddress}: {e}")
            # Don't silently fail - this is critical

        
        


