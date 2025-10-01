import pickle
import sys
import threading

import numpy as np

import flcommon
import time_logger
from config import ServerConfig

config = ServerConfig(int(sys.argv[1]))

f_to_i_v = np.vectorize(flcommon.f_to_i)
i_to_f_v = np.vectorize(flcommon.i_to_f)

from flask import Flask, request

api = Flask(__name__)

clients_secret = []
aggregation_lock = threading.Lock()

total_download_cost = 0
total_upload_cost = 0


@api.route('/recv', methods=['POST'])
def recv():
    my_thread = threading.Thread(target=recv_thread, args=(clients_secret, request.data, request.remote_addr))
    my_thread.start()
    return {"response": "ok"}


def recv_thread(clients_secret: list, data, address):
    global total_download_cost
    total_download_cost += len(data)

    time_logger.server_start()

    print(f"[SECRET] Secret of {address} received. len(data): {len(data)}")
    secret = pickle.loads(data)
    
    # Critical section: protect shared state with lock
    with aggregation_lock:
        clients_secret.append(secret)
        
        print(f"[SECRET] Secret received successfully. Total received: {len(clients_secret)}/{config.number_of_clients}")

        # Only process when we have enough clients (handle duplicates gracefully)
        if len(clients_secret) < config.number_of_clients:
            return
            
        # Process only the first N clients to handle duplicates
        current_batch = clients_secret[:config.number_of_clients] 
        clients_secret.clear()  # Clear all to prevent reprocessing
        
        model = {}
        for client_index in range(len(current_batch)):
            for layer_index in range(len(current_batch[0])):
                current_batch[client_index][layer_index] = f_to_i_v(
                    i_to_f_v(current_batch[client_index][layer_index]) / np.float32(config.number_of_clients))

        for layer_index in range(len(current_batch[0])):
            secrets_summation = np.zeros(shape=current_batch[0][layer_index].shape, dtype=np.uint64)
            for client_index in range(config.number_of_clients):
                secrets_summation += current_batch[client_index][layer_index]
            model[layer_index] = secrets_summation
        pickled_model = pickle.dumps(model)
        flcommon.broadcast_to_clients(pickled_model, config, lead_server=False)

        global total_upload_cost
        total_upload_cost += len(pickled_model) * config.number_of_clients

        print(f"[DOWNLOAD] Total download cost so far: {total_download_cost}")
        print(f"[UPLOAD] Total upload cost so far: {total_upload_cost}")

        print(f"********************** [ROUND] Round completed **********************")

    time_logger.server_idle()


api.run(host=config.server_address, port=int(config.server_base_port) + int(sys.argv[1]), debug=False, use_reloader=False, threaded=True)