from concurrent import futures

from threading import Thread
import os
import grpc
import sys
sys.path.append('../generated')
sys.path.append('../utils')
sys.path.append('../proto')
import db
import fileService_pb2_grpc
import fileService_pb2
import heartbeat_pb2_grpc
import heartbeat_pb2
import time
import yaml
import threading
import hashlib
from ShardingHandler import ShardingHandler
from DownloadHelper import DownloadHelper
from DeleteHelper import DeleteHelper
from HybridLRUCache import HybridLRUCache

# Add utils path BEFORE importing metrics
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
utils_path = os.path.join(parent_dir, 'utils')
if utils_path not in sys.path:
    sys.path.insert(0, utils_path)

# Import metrics - NO FALLBACK, fail fast if import fails
from system_metrics import metrics, REPLICATED_CHUNKS, REPLICATED_FILES

UPLOAD_SHARD_SIZE = 50*1024*1024

#
#   *** FileServer Service : FileServer service as per fileService.proto file. ***
#   *** This class implements all the required methods to serve the user requests. *** 
#
class FileServer(fileService_pb2_grpc.FileserviceServicer):
    def __init__(self, hostname, server_port, activeNodesChecker, shardingHandler, superNodeAddress, lru_capacity=50):
        self.serverPort = server_port
        self.serverAddress = hostname+":"+server_port
        self.activeNodesChecker = activeNodesChecker
        self.shardingHandler = shardingHandler
        self.hostname = hostname
        self.lru_capacity = lru_capacity
        # Enterprise-level Hybrid LRU + LFU cache
        # Combines recency (LRU) and frequency (LFU) with score-based eviction
        # Frequency weight: 0.6, Recency weight: 0.4
        self.lru = HybridLRUCache(capacity=lru_capacity, frequency_weight=0.6, recency_weight=0.4)
        self.superNodeAddress = superNodeAddress
        # Pure HybridLRU cache - cache ALL files on first access
        # Eviction handled by HybridLRU's score-based algorithm
        # No pre-cache filtering needed - HybridLRU manages everything
        # Initialize cache metrics
        metrics.set_cache_capacity(lru_capacity)
        metrics.set_cache_size(len(self.lru))
        # CRITICAL FIX: Use getTotalActiveNodeCount() to include current node
        # This ensures active node count never drops to 0
        metrics.set_active_nodes(activeNodesChecker.getTotalActiveNodeCount())
    
    #
    #   This service gets invoked when user uploads a new file.
    #
    def UploadFile(self, request_iterator, context):
        print("Inside Server method ---------- UploadFile")
        data=bytes("",'utf-8')
        username, filename = "", ""
        totalDataSize=0
        active_ip_channel_dict = self.activeNodesChecker.getActiveChannels()

        # list to store the info related to file location.
        metaData=[]

        # Check if this is a chunk storage operation (from another leader) or full file upload (from SuperNode/client)
        # Distinguish by: if replicaNode is set, it's a chunk being stored by another leader node
        first_request = None
        is_chunk_storage = False
        try:
            first_request = next(request_iterator)
            # If replicaNode is set (not empty), this is a chunk being stored by another leader node
            # Full file uploads from SuperNode have replicaNode = ""
            if hasattr(first_request, 'replicaNode') and first_request.replicaNode != "":
                is_chunk_storage = True
            username, filename = first_request.username, first_request.filename
        except StopIteration:
            return fileService_pb2.ack(success=False, message="No data received")

        # If this is chunk storage (from another leader), save directly without sharding
        if is_chunk_storage:
            print("Saving chunk data (from another leader node)")
            sequenceNumberOfChunk = first_request.seqNo if hasattr(first_request, 'seqNo') else 0
            dataToBeSaved = first_request.data
            
            # Process remaining chunks
            for request in request_iterator:
                dataToBeSaved += request.data
            
            key = username + "_" + filename + "_" + str(sequenceNumberOfChunk)
            db.setData(key, dataToBeSaved)
            
            # Handle replication if needed (replicaNode contains the replica node address)
            if first_request.replicaNode != "":
                print("Sending replication to ", first_request.replicaNode)
                active_ip_channel_dict = self.activeNodesChecker.getActiveChannels()
                replica_channel = active_ip_channel_dict[first_request.replicaNode]
                t1 = Thread(target=self.replicateChunkData, args=(replica_channel, dataToBeSaved, username, filename, sequenceNumberOfChunk ,))
                t1.start()
                metrics.record_replication('attempt')
                REPLICATED_CHUNKS.inc()
            
            return fileService_pb2.ack(success=True, message="Saved")

        # If the node is the leader of the cluster and this is a full file upload
        if(int(db.get("primaryStatus"))==1):
            print("Inside primary upload")
            currDataSize = 0
            currDataBytes = bytes("",'utf-8')
            seqNo=1
            
            # Step 1:
            # Get 2 least loaded nodes based on the CPU stats. 
            # 'Node' is where the actual data goes and 'node_replica' is where replica will go.
            start_time = time.time()
            node, node_replica = self.getLeastLoadedNode()
            load_balance_duration = time.time() - start_time
            metrics.record_load_balance_decision(node if node != -1 else "none")
            metrics.record_load_balance_duration(load_balance_duration)

            if(node==-1):
                # No OTHER nodes available for sharding (current node is active, but we need other nodes for distribution)
                total_nodes = self.activeNodesChecker.getTotalActiveNodeCount()
                other_nodes = self.activeNodesChecker.getOtherActiveNodeCount()
                return fileService_pb2.ack(success=False, message=f"Error Saving File. No other nodes available for sharding. (Total active: {total_nodes}, Other nodes: {other_nodes})")

            # Step 2: 
            # Check whether file already exists, if yes then return with message 'File already exists'.
            # First request already read, check file existence
            print("Key is-----------------", username+"_"+filename)
            if(self.fileExists(username, filename)==1):
                print("sending neg ack")
                return fileService_pb2.ack(success=False, message="File already exists for this user. Please rename or delete file first.")
            
            # Step 3: Process chunks - start with first chunk we already read
            currDataSize = sys.getsizeof(first_request.data)
            currDataBytes = first_request.data
            
            # Process remaining chunks
            for request in request_iterator:
                
                # Continue processing remaining chunks
                if((currDataSize + sys.getsizeof(request.data)) > UPLOAD_SHARD_SIZE):
                    response = self.sendDataToDestination(currDataBytes, node, node_replica, username, filename, seqNo, active_ip_channel_dict[node])
                    metaData.append([node, seqNo, node_replica])
                    # Track shard creation
                    metrics.record_shard_creation(len(currDataBytes))
                    currDataBytes = request.data
                    currDataSize = sys.getsizeof(request.data)
                    seqNo+=1
                    start_time = time.time()
                    node, node_replica = self.getLeastLoadedNode()
                    load_balance_duration = time.time() - start_time
                    metrics.record_load_balance_decision(node if node != -1 else "none")
                    metrics.record_load_balance_duration(load_balance_duration)
                    # CRITICAL: Guard check - if no nodes available during chunking, abort upload
                    if(node == -1):
                        total_nodes = self.activeNodesChecker.getTotalActiveNodeCount()
                        other_nodes = self.activeNodesChecker.getOtherActiveNodeCount()
                        return fileService_pb2.ack(success=False, message=f"Error Saving File. No nodes available for sharding during chunk processing. (Total active: {total_nodes}, Other nodes: {other_nodes})")
                else:
                    currDataSize+= sys.getsizeof(request.data)
                    currDataBytes+=request.data

            if(currDataSize>0):
                # CRITICAL: Guard check before accessing active_ip_channel_dict[node]
                if(node == -1):
                    total_nodes = self.activeNodesChecker.getTotalActiveNodeCount()
                    other_nodes = self.activeNodesChecker.getOtherActiveNodeCount()
                    return fileService_pb2.ack(success=False, message=f"Error Saving File. No nodes available for final chunk. (Total active: {total_nodes}, Other nodes: {other_nodes})")
                response = self.sendDataToDestination(currDataBytes, node, node_replica, username, filename, seqNo, active_ip_channel_dict[node])
                metaData.append([node, seqNo, node_replica])
                # Track shard creation
                metrics.record_shard_creation(len(currDataBytes))

            # Step 4: 
            # Save the metadata on the primary node after the completion of sharding.
            if(response.success):
                db.saveMetaData(username, filename, metaData)
                db.saveUserFile(username, filename)
                # Track file replication
                REPLICATED_FILES.inc()

            # Step 5:
            # Make a gRPC call to replicate the matadata on all the other nodes.
            # Run in background thread to avoid blocking the response
            t = Thread(target=self.saveMetadataOnAllNodes, args=(username, filename, metaData))
            t.start()
            metrics.record_metadata_replication('success')

            return fileService_pb2.ack(success=True, message="Saved")

        # If the node is not the leader.
        else:
            print("Saving the data on my local db")
            sequenceNumberOfChunk = 0
            dataToBeSaved = bytes("",'utf-8')

            # Gather all the data from gRPC stream
            for request in request_iterator:
                username, filename, sequenceNumberOfChunk = request.username, request.filename, request.seqNo
                dataToBeSaved+=request.data
            key = username + "_" + filename + "_" + str(sequenceNumberOfChunk)

            # Save the data in local DB.
            db.setData(key, dataToBeSaved)

            # After saving the chunk in the local DB, make a gRPC call to save the replica of the chunk on different 
            # node only if the replicaNode is present.
            if(request.replicaNode!=""):
                print("Sending replication to ", request.replicaNode)
                active_ip_channel_dict = self.activeNodesChecker.getActiveChannels()
                replica_channel = active_ip_channel_dict[request.replicaNode]
                t1 = Thread(target=self.replicateChunkData, args=(replica_channel, dataToBeSaved, username, filename, sequenceNumberOfChunk ,))
                t1.start()
                metrics.record_replication('attempt')
                REPLICATED_CHUNKS.inc()
                # stub = fileService_pb2_grpc.FileserviceStub(replica_channel)
                # response = stub.UploadFile(self.sendDataInStream(dataToBeSaved, username, filename, sequenceNumberOfChunk, ""))

            return fileService_pb2.ack(success=True, message="Saved")

    def replicateChunkData(self, replica_channel, dataToBeSaved, username, filename, sequenceNumberOfChunk):
        start_time = time.time()
        try:
            stub = fileService_pb2_grpc.FileserviceStub(replica_channel)
            response = stub.UploadFile(self.sendDataInStream(dataToBeSaved, username, filename, sequenceNumberOfChunk, ""), timeout=20)
            duration = time.time() - start_time
            metrics.record_replication_duration(duration, 'chunk')
            if response.success:
                metrics.record_replication('success')
                REPLICATED_CHUNKS.inc()
            else:
                metrics.record_replication('failed')
        except Exception as e:
            metrics.record_replication('error')
            print(f"Replication error: {e}")

    # This helper method is responsible for sending the data to destination node through gRPC stream.
    def sendDataToDestination(self, currDataBytes, node, nodeReplica, username, filename, seqNo, channel):
        if(node==self.serverAddress):
            key = username + "_" + filename + "_" + str(seqNo)
            db.setData(key, currDataBytes)
            # Create a success response for local save
            response = fileService_pb2.ack(success=True, message="Saved locally")
            # Replication should NOT block the response - run in background thread
            if(nodeReplica!=""):
                print("Sending replication to ", nodeReplica)
                active_ip_channel_dict = self.activeNodesChecker.getActiveChannels()
                replica_channel = active_ip_channel_dict[nodeReplica]
                # Run replication in background thread to avoid blocking
                t = Thread(target=self.replicateChunkData, args=(replica_channel, currDataBytes, username, filename, seqNo))
                t.start()
                metrics.record_replication('attempt')
            return response
        else:
            print("Sending the UPLOAD_SHARD_SIZE to node :", node)
            try:
                stub = fileService_pb2_grpc.FileserviceStub(channel)
                response = stub.UploadFile(self.sendDataInStream(currDataBytes, username, filename, seqNo, nodeReplica), timeout=20)
                print("Response from uploadFile: ", response.message)
                return response
            except Exception as e:
                print(f"Error sending to node {node}: {e}")
                return fileService_pb2.ack(success=False, message=f"Error sending to node: {str(e)}")

    # This helper method actually makes chunks of less than 4MB and streams them through gRPC.
    # 4 MB is the max data packet size in gRPC while sending. That's why it is necessary. 
    def sendDataInStream(self, dataBytes, username, filename, seqNo, replicaNode):
        chunk_size = 4000000
        start, end = 0, chunk_size
        while(True):
            chunk = dataBytes[start:end]
            if(len(chunk)==0): break
            start=end
            end += chunk_size
            yield fileService_pb2.FileData(username=username, filename=filename, data=chunk, seqNo=seqNo, replicaNode=replicaNode)

    #
    #   This service gets invoked when user requests an uploaded file.
    #
    def DownloadFile(self, request, context):

        print("Inside Download")

        # If the node is the leader of the cluster. 
        if(int(db.get("primaryStatus"))==1):
            
            print("Inside primary download")
            
            # Check if file exists
            if(self.fileExists(request.username, request.filename)==0):
                print("File does not exist")
                yield fileService_pb2.FileData(username = request.username, filename = request.filename, data=bytes("",'utf-8'), seqNo = 0)
                return

            # If the file is present in cache then just fetch it and return. No need to go to individual node.
            cache_key = request.username + "_" + request.filename
            if cache_key in self.lru:
                print("Fetching data from Cache (Hybrid LRU+LFU)")
                metrics.record_cache_hit()  # Track cache hit
                # Access from cache - HybridLRU automatically updates frequency and recency via __getitem__
                CHUNK_SIZE=4000000
                filePath = self.lru[cache_key]  # This triggers __getitem__ which updates frequency/recency
                outfile = filePath
                
                with open(outfile, 'rb') as infile:
                    while True:
                        chunk = infile.read(CHUNK_SIZE)
                        if not chunk: break
                        yield fileService_pb2.FileData(username=request.username, filename=request.filename, data=chunk, seqNo=1)
            
            # If the file is not present in the cache, then fetch it from the individual node.
            else:
                metrics.record_cache_miss()  # Track cache miss
                print("Fetching the metadata")

                # Step 1: get metadata i.e. the location of chunks.
                metaData = db.parseMetaData(request.username, request.filename)

                print(metaData)
                
                #Step 2: make gRPC calls and get the fileData from all the nodes.
                downloadHelper = DownloadHelper(self.hostname, self.serverPort, self.activeNodesChecker)
                data = downloadHelper.getDataFromNodes(request.username, request.filename, metaData)
                print("Sending the data to client")

                #Step 3: send the file to supernode using gRPC streaming.
                chunk_size = 4000000
                start, end = 0, chunk_size
                while(True):
                    chunk = data[start:end]
                    if(len(chunk)==0): break
                    start=end
                    end += chunk_size
                    yield fileService_pb2.FileData(username = request.username, filename = request.filename, data=chunk, seqNo = request.seqNo)
                
                # Step 4: OPTIMIZATION - Async cache write (non-blocking)
                # Start background thread to cache file without blocking client response
                print(f"Starting async cache write (HybridLRU eviction handles priority)")
                threading.Thread(
                    target=self.saveInCache,
                    args=(request.username, request.filename, data),
                    daemon=True
                ).start()
                metrics.set_cache_size(len(self.lru))  # Update cache size metric

        # If the node is not the leader, then just fetch the fileChunk from the local db and stream it back to leader.
        else:
            key = request.username + "_" + request.filename + "_" + str(request.seqNo)
            print(key)
            data = db.getFileData(key)
            chunk_size = 4000000
            start, end = 0, chunk_size
            while(True):
                chunk = data[start:end]
                if(len(chunk)==0): break
                start=end
                end += chunk_size
                yield fileService_pb2.FileData(username = request.username, filename = request.filename, data=chunk, seqNo = request.seqNo)

    # This service is responsible fetching all the files.
    def FileList(self, request, context):
        print("File List Called")
        userFiles = db.getUserFiles(request.username)
        return fileService_pb2.FileListResponse(Filenames=str(userFiles))
    
    # This helper method checks whether the file is present in db or not.
    def fileExists(self, username, filename):
        print("isFile Present", db.keyExists(username + "_" + filename))
        return db.keyExists(username + "_" + filename)
    
    # This helper method returns 2 least loaded nodes from the cluster.
    def getLeastLoadedNode(self):
        print("Ready to enter sharding handler")
        node, node_replica = self.shardingHandler.leastUtilizedNode()
        print("Least loaded node is :", node)
        print("Replica node - ", node_replica)
        return node, node_replica

    # This helper method replicates the metadata on all nodes.
    def saveMetadataOnAllNodes(self, username, filename, metadata):
        print("saveMetadataOnAllNodes")
        active_ip_channel_dict = self.activeNodesChecker.getActiveChannels()
        uniqueFileName = username + "_" + filename
        success_count = 0
        failure_count = 0
        for ip, channel in active_ip_channel_dict.items():
            if(self.isChannelAlive(channel)):
                try:
                    stub = fileService_pb2_grpc.FileserviceStub(channel)
                    response = stub.MetaDataInfo(fileService_pb2.MetaData(filename=uniqueFileName, seqValues=str(metadata).encode('utf-8')), timeout=5)
                    print(response.message)
                    if response.success:
                        success_count += 1
                    else:
                        failure_count += 1
                except Exception as e:
                    failure_count += 1
                    print(f"Metadata replication error to {ip}: {e}")
        
        # Track metadata replication metrics
        if success_count > 0:
            for _ in range(success_count):
                metrics.record_metadata_replication('success')
        if failure_count > 0:
            for _ in range(failure_count):
                metrics.record_metadata_replication('failed')

    # This service is responsible for saving the metadata on local db.
    def MetaDataInfo(self, request, context):
        print("Inside Metadatainfo")
        fileName = request.filename
        seqValues = request.seqValues
        db.saveMetaDataOnOtherNodes(fileName, seqValues)
        ack_message = "Successfully saved the metadata on " + self.serverAddress
        return fileService_pb2.ack(success=True, message=ack_message)

    # This helper method checks whethere created channel is alive or not
    def isChannelAlive(self, channel):
        metrics.record_heartbeat_check()
        try:
            grpc.channel_ready_future(channel).result(timeout=1)
            return True
        except grpc.FutureTimeoutError:
            metrics.record_heartbeat_failure()
            #print("Connection timeout. Unable to connect to port ")
            return False
    
    # This helper method is responsible for updating the cache for faster lookup.
    # Enterprise-level Hybrid LRU + LFU caching with score-based eviction
    # Cache ALL files immediately - HybridLRU's eviction handles priority
    def saveInCache(self, username, filename, data):
        cache_key = username+"_"+filename
        # HybridLRU automatically evicts lowest-score items when capacity is reached
        # Create cache directory if it doesn't exist
        cache_dir = 'cache'
        if not os.path.exists(cache_dir):
            os.makedirs(cache_dir)
        
        filePath = os.path.join(cache_dir, cache_key)
        
        # Check if file already exists in cache (update access tracking)
        if cache_key in self.lru:
            # File already cached, update it (this updates frequency/recency)
            self.lru[cache_key] = filePath
            return
        
        # Save file to cache
        with open(filePath, 'wb') as saveFile:
            saveFile.write(data)
        
        # Add to HybridLRU cache
        # This will automatically evict lowest-score item if at capacity
        # Eviction is based on: (frequency × 0.6) + (recency × 0.4)
        self.lru[cache_key] = filePath
        
        # Clean up old cache files if cache is evicted
        # HybridLRU evicts based on score, so we need to clean up orphaned files
        if len(self.lru) > self.lru_capacity:
            # Get all keys currently in cache
            current_keys = set(self.lru.keys())
            # Clean up files that were evicted
            if os.path.exists(cache_dir):
                for cached_file in os.listdir(cache_dir):
                    cached_key = cached_file  # Filename is the cache key
                    if cached_key not in current_keys:
                        # File was evicted from cache, delete it
                        try:
                            os.remove(os.path.join(cache_dir, cached_file))
                        except:
                            pass

    # This service is responsible for sending the whole cluster stats to superNode
    def getClusterStats(self, request, context):
        print("Inside getClusterStats")
        active_ip_channel_dict = self.activeNodesChecker.getActiveChannels()
        total_cpu_usage, total_disk_space, total_used_mem = 0.0,0.0,0.0
        total_nodes = 0
        for ip, channel in active_ip_channel_dict.items():
            if(self.isChannelAlive(channel)):
                stub = heartbeat_pb2_grpc.HearBeatStub(channel)
                stats = stub.isAlive(heartbeat_pb2.NodeInfo(ip="", port=""))
                total_cpu_usage = float(stats.cpu_usage)
                total_disk_space = float(stats.disk_space)
                total_used_mem = float(stats.used_mem)
                total_nodes+=1

        if(total_nodes==0):
            return fileService_pb2.ClusterStats(cpu_usage = str(100.00), disk_space = str(100.00), used_mem = str(100.00))

        return fileService_pb2.ClusterStats(cpu_usage = str(total_cpu_usage/total_nodes), disk_space = str(total_disk_space/total_nodes), used_mem = str(total_used_mem/total_nodes))

    # This service is responsible for sending the leader info to superNode as soon as leader changes.
    def getLeaderInfo(self, request, context):
        channel = grpc.insecure_channel('{}'.format(self.superNodeAddress))
        stub = fileService_pb2_grpc.FileserviceStub(channel)
        response = stub.getLeaderInfo(fileService_pb2.ClusterInfo(ip = self.hostname, port= self.serverPort, clusterName="team1"))
        print(response.message)

    #
    #   This service gets invoked when user deletes a file.
    #
    def FileDelete(self, request, data):
        username = request.username
        filename = request.filename

        if(int(db.get("primaryStatus"))==1):

            if(self.fileExists(username, filename)==0):
                print("File does not exist")
                return fileService_pb2.ack(success=False, message="File does not exist")

            print("Fetching metadata from leader")
            metadata = db.parseMetaData(request.username, request.filename)
            print("Successfully retrieved metadata from leader")

            deleteHelper = DeleteHelper(self.hostname, self.serverPort, self.activeNodesChecker)
            deleteHelper.deleteFileChunksAndMetaFromNodes(username, filename, metadata)

            return fileService_pb2.ack(success=True, message="Successfully deleted file from the cluster")

        else:
            seqNo = -1

            try:
                seqNo = request.seqNo
            except:
                return fileService_pb2.ack(success=False, message="Internal Error")

            metaDataKey = username+"_"+filename 
            dataChunkKey = username+"_"+filename+"_"+str(seqNo)

            if(db.keyExists(metaDataKey)==1):
                print("FileDelete: Deleting the metadataEntry from local db :")
                db.deleteEntry(metaDataKey)
            if(db.keyExists(dataChunkKey)):
                print("FileDelete: Deleting the data chunk from local db: ")
                db.deleteEntry(dataChunkKey)

            return fileService_pb2.ack(success=True, message="Successfully deleted file from the cluster")

    #
    #   This service gets invoked when user wants to check if the file is present.
    #
    def FileSearch(self, request, data):
        username, filename = request.username, request.filename

        if(self.fileExists(username, filename)==1):
            return fileService_pb2.ack(success=True, message="File exists in the cluster.")
        else:
            return fileService_pb2.ack(success=False, message="File does not exist in the cluster.")

    #
    #   This service gets invoked when user wants to update a file.
    #
    def UpdateFile(self, request_iterator, context):
        
        username, filename = "", ""
        fileData = bytes("",'utf-8')

        for request in request_iterator:
            fileData+=request.data
            username, filename = request.username, request.filename

        def getFileChunks(fileDataBytes):
            # Maximum chunk size that can be sent
            CHUNK_SIZE=4000000
            start, end = 0, CHUNK_SIZE
            while True:
                chunk = fileDataBytes[start:end]
                if len(chunk) == 0:
                    break
                yield fileService_pb2.FileData(username=username, filename=filename, data=chunk, seqNo=1)
                start = end
                end += CHUNK_SIZE

        if(int(db.get("primaryStatus"))==1):
            channel = grpc.insecure_channel('{}'.format(self.serverAddress))
            stub = fileService_pb2_grpc.FileserviceStub(channel)

            response1 = stub.FileDelete(fileService_pb2.FileInfo(username=username, filename=filename))

            if(response1.success):
                response2 = stub.UploadFile(getFileChunks(fileData))
                if(response2.success):
                    return fileService_pb2.ack(success=True, message="File successfully updated.")
                else:
                    return fileService_pb2.ack(success=False, message="Internal error.")
            else:
                return fileService_pb2.ack(success=False, message="Internal error.")
        else:
            return fileService_pb2.ack(success=False, message="Only leader can update files.")





            












            




        



    

