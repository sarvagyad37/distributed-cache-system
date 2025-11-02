import sys
import os
sys.path.append('../generated')
sys.path.append('../utils')
import db
import time
import grpc
import threading

# Add utils path BEFORE importing metrics
current_dir = os.path.dirname(os.path.abspath(__file__))
utils_path = current_dir
if utils_path not in sys.path:
    sys.path.insert(0, utils_path)

# Import metrics - NO FALLBACK, fail fast if import fails
from system_metrics import metrics

#
#   *** ActiveNodesChecker Utility : Helper class to keep track of active nodes. ***
#
class ActiveNodesChecker():

    def __init__(self, current_node_address=None):
        # Thread safety: Protect shared data structures
        self._lock = threading.RLock()  # Reentrant lock for nested calls
        
        # Channel management: Map channels to IPs and track active channels
        self.channel_ip_map = {}  # {channel: ip_address}
        self.active_ip_channel_dict = {}  # {ip_address: channel} - only active OTHER nodes
        self._previous_active_count = 0
        
        # CRITICAL: Store current node address - this node is ALWAYS active if we're running
        self.current_node_address = current_node_address
        
        # Initialize node count metrics IMMEDIATELY to prevent 0-state
        try:
            total_nodes = len(self.getAllAvailableIPAddresses())
            metrics.set_total_nodes(total_nodes)
            # CRITICAL: Set to 1 immediately if we have current_node_address
            # This prevents any window where count could be 0
            initial_count = 1 if self.current_node_address else 0
            metrics.set_active_nodes(initial_count)
            if self.current_node_address:
                print(f"✅ Initialized ActiveNodesChecker - Current node: {self.current_node_address}, Initial active count: {initial_count}")
        except Exception as e:
            print(f"⚠️ Warning: Failed to initialize node metrics: {e}")
            # Even on error, ensure count is at least 1 if we have a node address
            if self.current_node_address:
                metrics.set_active_nodes(1)

    #
    #  A thread will start for this method. This method keeps updating the active_ip_channel_dict map.
    #
    def readAvailableIPAddresses(self):
        print("Inside readAvailableIPAddresses")

        # Read all the available IP addresses from iptable.txt
        ip_addresses = self.getAllAvailableIPAddresses()

        # Update total nodes count
        total_nodes = len(ip_addresses)
        metrics.set_total_nodes(total_nodes)
        print(f"Total nodes configured: {total_nodes}")

        # Create channels with all the IP addresses
        self.createChannelListForAvailableIPs(ip_addresses)
        db.setData("ip_addresses", self.getStringFromIPAddressesList(ip_addresses))

        while True:
            time.sleep(0.5)
            ip_addresses=[]

            try:
                ip_addresses_old = self.getIPAddressListFromString(db.getData("ip_addresses"))
            except:
                db.setData("ip_addresses","")

            ip_addresses = self.getAllAvailableIPAddresses()
            
            # Update total nodes if changed
            total_nodes = len(ip_addresses)
            metrics.set_total_nodes(total_nodes)
            
            db.setData("ip_addresses", self.getStringFromIPAddressesList(ip_addresses))

            # If there is any addition or deletion of node then create a new channel for that and update {channel, ip} map.
            if(ip_addresses != ip_addresses_old):
                self.createChannelListForAvailableIPs(ip_addresses)
                print(f"Node list changed. Total nodes: {total_nodes}")

            # Update the active {IP, channel} map
            self.heartBeatChecker()

    # This method return a list of IP Addresses present in iptable.txt
    # NOTE: Current node is included in the list (for total node count), but will be filtered out
    # when creating channels to avoid self-connection
    def getAllAvailableIPAddresses(self):
        ip_addresses=[]
        try:
            with open('iptable.txt') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):  # Skip empty lines and comments
                        ip_addresses.append(line.split()[0])
        except FileNotFoundError:
            # Try iptable_local.txt as fallback
            try:
                with open('iptable_local.txt') as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#'):  # Skip empty lines and comments
                            ip_addresses.append(line.split()[0])
            except FileNotFoundError:
                print("Warning: Neither iptable.txt nor iptable_local.txt found")
        return ip_addresses

    def getIPAddressListFromString(self, ipAddresses):
        result = []
        if ipAddresses=="":  return result
        return ipAddresses.split(',')
    
    def getStringFromIPAddressesList(self, ipAddressList):
        ipAddressString = ""
        for ipAddress in ipAddressList:
            ipAddressString+=ipAddress+","
        ipAddressString = ipAddressString[:-1]
        return ipAddressString

    #Create Channel:IP HashMap
    # CRITICAL: Exclude current node to avoid self-connection and double-counting
    # IMPROVED: Reuse existing channels and properly close removed channels
    def createChannelListForAvailableIPs(self, ip_addresses):
        with self._lock:
            # Track channels we need to keep
            new_channel_map = {}
            ip_addresses_to_add = set()
            
            # Filter out current node first
            filtered_addresses = []
            for ip_address in ip_addresses:
                if self.current_node_address and ip_address == self.current_node_address:
                    continue
                filtered_addresses.append(ip_address)
                ip_addresses_to_add.add(ip_address)
            
            # Reuse existing channels for IPs we already have
            channels_to_close = []
            for channel, existing_ip in list(self.channel_ip_map.items()):
                if existing_ip in ip_addresses_to_add:
                    # Reuse existing channel
                    new_channel_map[channel] = existing_ip
                    ip_addresses_to_add.discard(existing_ip)
                else:
                    # Mark for cleanup - channel no longer needed
                    channels_to_close.append(channel)
            
            # Create new channels for new IPs
            for ip_address in ip_addresses_to_add:
                try:
                    channel = grpc.insecure_channel('{}'.format(ip_address))
                    new_channel_map[channel] = ip_address
                except Exception as e:
                    print(f"⚠️ Failed to create channel to {ip_address}: {e}")
            
            # Close old channels that are no longer needed
            for channel in channels_to_close:
                try:
                    channel.close()
                except Exception:
                    pass  # Ignore errors during cleanup
            
            # Replace the channel map atomically
            self.channel_ip_map = new_channel_map
            
            # Remove any IPs from active dict that are no longer in our channel map
            active_ips_to_remove = []
            for ip in self.active_ip_channel_dict:
                if ip not in [existing_ip for existing_ip in new_channel_map.values()]:
                    active_ips_to_remove.append(ip)
            
            for ip in active_ips_to_remove:
                del self.active_ip_channel_dict[ip]

    # This method keeps updating the active channels based on their aliveness.
    # It removes the channels from the list if the node is down.
    # CRITICAL: This node is ALWAYS counted as active if running.
    # IMPROVED: Thread-safe with proper locking and better error handling
    def heartBeatChecker(self):
        with self._lock:
            previous_count = len(self.active_ip_channel_dict)
            
            # CRITICAL FIX: Explicitly remove current node from active dict if it somehow got in
            # This prevents double-counting (current node should never be in active_ip_channel_dict)
            if self.current_node_address and self.current_node_address in self.active_ip_channel_dict:
                print(f"⚠️ WARNING: Current node {self.current_node_address} found in active channels, removing to prevent double-counting")
                del self.active_ip_channel_dict[self.current_node_address]
            
            # Check each channel's aliveness
            channels_to_check = list(self.channel_ip_map.items())  # Snapshot to avoid iteration issues
        
        # Perform channel checks outside lock to avoid blocking
        # (isChannelAlive might take time)
        channel_status = {}
        for channel, ip_address in channels_to_check:
            # Double-check: Never add current node to active dict
            if self.current_node_address and ip_address == self.current_node_address:
                continue
            channel_status[ip_address] = self.isChannelAlive(channel)
        
        # Update active dict with lock held
        with self._lock:
            for ip_address, is_alive in channel_status.items():
                if is_alive:
                    if ip_address not in self.active_ip_channel_dict:
                        # Find the channel for this IP
                        for ch, ip in self.channel_ip_map.items():
                            if ip == ip_address:
                                self.active_ip_channel_dict[ip_address] = ch
                                break
                else:
                    if ip_address in self.active_ip_channel_dict:
                        del self.active_ip_channel_dict[ip_address]
            
            # CRITICAL FIX: Always count current node as active (since we're running this code)
            # This ensures active node count NEVER drops to 0, preventing Raft and cache issues
            current_count = len(self.active_ip_channel_dict)
            
            if self.current_node_address:
                # Count = other active nodes + this node (always 1)
                # Since we filter out current node above, this is always accurate
                total_active_count = current_count + 1
            else:
                total_active_count = current_count
            
            # Set metrics immediately - this ensures count is always correct
            metrics.set_active_nodes(total_active_count)
            
            # Update metrics when count changes (using total count including current node)
            if previous_count != current_count:
                prev_total = previous_count + (1 if self.current_node_address else 0)
                print(f"📊 Active nodes changed: {prev_total} -> {total_active_count} (other nodes: {previous_count} -> {current_count})")
                if total_active_count < prev_total:
                    metrics.record_node_failure()
                elif total_active_count > prev_total:
                    metrics.record_node_recovery()
    
    # This method checks whether the channel is alive or not.
    def isChannelAlive(self, channel):
        metrics.record_heartbeat_check()
        try:
            grpc.channel_ready_future(channel).result(timeout=1)
            return True
        except grpc.FutureTimeoutError:
            metrics.record_heartbeat_failure()
            return False

    # This method returns a map of active {ip, channel} for OTHER nodes only.
    # Used for communication with other nodes in the cluster.
    # The current node is NOT included as you don't send messages to yourself.
    # IMPROVED: Thread-safe with minimal lock contention (fast path for reads)
    def getActiveChannels(self):
        # Fast path: acquire lock only to get reference and create copy
        # This minimizes lock hold time which is critical for hot paths
        with self._lock:
            # Create shallow copy of dict (channels are immutable references)
            # This is fast and prevents external modification
            return dict(self.active_ip_channel_dict)
    
    # CRITICAL: Returns total active node count including current node
    # This ensures count NEVER drops to 0 when current node is running
    # MUST be used for all count checks, metrics, and Raft operations
    # IMPROVED: Thread-safe with minimal lock contention (fast path)
    def getTotalActiveNodeCount(self):
        # Fast path: just get length, minimal lock time
        with self._lock:
            other_nodes_count = len(self.active_ip_channel_dict)
        # Calculate outside lock (current_node_address is immutable after init)
        if self.current_node_address:
            return other_nodes_count + 1  # Always include current node
        return other_nodes_count
    
    # CRITICAL: Returns count of OTHER active nodes (excluding current node)
    # Only use this for load balancing decisions where you need OTHER nodes
    # For total cluster count, use getTotalActiveNodeCount()
    # IMPROVED: Thread-safe with minimal lock contention (fast path)
    def getOtherActiveNodeCount(self):
        # Fast path: just get length, minimal lock time
        with self._lock:
            return len(self.active_ip_channel_dict)