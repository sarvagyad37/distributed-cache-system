import redis
import ast

_redis_port = 6379

r = redis.StrictRedis(host='localhost', port=_redis_port, db=0)

#metadata = {"username_filename" : [clusterName, clusterReplica]}
def saveMetaData(username, filename, clusterName, clusterReplica):
    key = username + "_" + filename
    r.set(key,str([clusterName,clusterReplica]))

def parseMetaData(username, filename):
    key = username + "_" + filename
    return ast.literal_eval(r.get(key).decode('utf-8'))

def keyExists(key):
    return r.exists(key)

def deleteEntry(key):
    r.delete(key)

def getUserFiles(username):
    result = r.get(username)
    if result:
        return result.decode('utf-8')
    return "[]"

def saveUserFile(username, filename):
    user_key = username
    if(keyExists(user_key)):
        l=ast.literal_eval(r.get(user_key).decode('utf-8'))
        if filename not in l:
            l.append(filename)
            r.set(user_key,str(l))
    else:
        r.set(user_key,str([filename]))
