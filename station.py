import sys
import socket
import pickle
import select
import ipaddress
import time
import threading
from collections import deque
from utils import *
from datetime import datetime, timedelta

class Station:
    def __init__(self, interface_file, routingtable_file, hostname_file, is_router=True):
        self.is_router = is_router
        self.hostname_mapping = load_json_file(hostname_file)
        self.reverse_hostname_mapping = {v:k for k, v in self.hostname_mapping.items()}
        self.station_info = load_json_file(interface_file)
        self.routing_table = load_json_file(routingtable_file)
        self.arp_table = {}
        self.pending_queue = deque()
        self.LENGTH = 4096
        self.all_connections = set()
        self.socket_to_ip = {}
        self.ip_to_socket = {}

        self.timeout = 60  # Set the timeout in seconds
        self.time_table = {}
        self.close_socks = None

    def add_time(self, key):
        # Call this as soon as a connection is established or updated
        now_time = datetime.now()
        self.time_table[str(key)] = {'sock': key, 'time': now_time}

    def update_time(self, key):
        # Call this as soon as a connection is established or updated
        now_time = datetime.now()
        self.time_table[str(key)] = {'sock': key, 'time': now_time}

    def check_time(self):
        keys_to_remove = []
        return_keys = []

        for key, value in self.time_table.items():
            time_difference = datetime.now() - value['time']

            # To remove entries where the time difference exceeds the timeout
            if time_difference > timedelta(seconds=self.timeout):
                keys_to_remove.append(key)

        # Remove entries from the time table
        for key in keys_to_remove:
            return_keys.append(self.time_table[key]['sock'])
            del self.time_table[key]
            print("As time difference is {}. Removed entry for key: {}".format(time_difference, key))
        self.close_socks = return_keys

    def connect_to_lans(self):
        # print(self.station_info["stations"])
        for interface in self.station_info["stations"]:
            bridge_name = self.station_info[interface]["lan"]
            #check if there is an active lan with lan_name
            all_bridges = load_json_file('all_lans.json')
            if bridge_name not in all_bridges:
                print("LAN {} is unavailable ...".format(bridge_name))
                print('\n')
                continue
            lan_info = load_json_file('bridge_{}.json'.format(bridge_name))
            bridge_ip = lan_info['ip']
            bridge_port = lan_info['port']
            # print('all info gathered')
            # print('Bridge ip: {} Bridge port: {}'.format(bridge_ip, bridge_port))
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                retries = 5
                wait_time = 2  # seconds
                for _ in range(retries):
                    sock.connect((bridge_ip, int(bridge_port)))
                    response = sock.recv(self.LENGTH)
                    response = pickle.loads(response)
                    if response['message'] == 'accept':
                        print("Connected to {} on interface {}.........".format(bridge_name, interface))
                        self.all_connections.add(sock)
                        hostname,port=sock.getpeername()
                        station_ip = self.station_info[interface]["ip"]
                        self.socket_to_ip['{}:{}'.format(hostname, port)] = station_ip
                        self.ip_to_socket[station_ip] = sock
                        break
                    elif response['message'] == 'reject':
                        print('No free port on the bridge {}. The connection cannot be established...'.format(bridge_name))
                        break
                    else:
                        print("Connection to {} on interface {} rejected..\nRetrying...".format(bridge_name, interface))
                    time.sleep(wait_time)
                    
                    # ready_to_read, _, _ = select.select([s], [], [], wait_time)
                        
            except Exception as e:
                print("Error connecting to {}: {}".format(bridge_name, e))

    def print_tables(self, message):
        if message == 'rt':
                print('============ Printing Routing Table ===========')
                print('Network Address \t Next Hop Address \t Netmask \t Interface to next Hop')
                for k,v in self.routing_table.items():
                    res = '{}'.format(k)
                    for kk, vv in v.items():
                        res = res + '\t {}'.format(vv)
                    print(res)
                print('============ END ===========')
        elif message == 'dns':
            print('=========== Printing DNS Table ===============')
            
            print('Hostname \t IP')
            for k, v in self.hostname_mapping.items():
                print('{k}\t{v}')
            print('============ END ===========')
        elif message == 'arp':
            print('=========== Printing ARP Table ===============')
            print('IP \t MAC')
            for k,v in self.arp_table.items():
                print('{} \t {}'.format(k, v))
            ## implement here 
            print('============ END ===========')
        elif message == 'tt':
            print('=========== Printing Time Table ===============')
            print('\Key\t\t\Time')
            for k,v in self.time_table.items():
                print('{}\t\t{}'.format(v['sock'], v['time']))
            print('============ END ===========')
        else:
            print('Command {} not found'.format(message))
        return
    
    def send_arp(self, arp_msg, sock):
        print('sending {}.....'.format(arp_msg['type']))
        sock.send(pickle.dumps(arp_msg))
    
    def create_ip_packet(self,source_ip, destination_ip, message):
        ip_packet = {
                'source_ip':source_ip,
                'destination_ip': destination_ip, 
                'message': message
            }
        return ip_packet
    
    def create_frame(self, source_mac, destination_mac, ip_packet):
        frame = {
                'type': 'IP',
                'source_mac': source_mac,
                'destination_mac':destination_mac,
                'ip_packet': ip_packet
            }
        return pickle.dumps(frame)
    
    def send_message(self, source_ip, destination_ip, source_mac, destination_mac, message, sock):
        ip_packet = self.create_ip_packet(source_ip, destination_ip, message)
        frame = self.create_frame(source_mac, destination_mac, ip_packet)
        sock.send(frame)
    
    def forward_message(self, messsage, sock):
        sock.send(pickle.dumps(messsage))

    def get_next_interface(self, destination_ip):
        for network_prefix, entry in self.routing_table.items():
            netmask = entry['mask']
            destination_network_prefix = ipaddress.ip_network((destination_ip, netmask), strict=False).network_address
            if destination_network_prefix == ipaddress.ip_address(network_prefix):
                return entry['next_interface'], entry['next_hop']
        #returning default entry       
        entry = self.routing_table['0.0.0.0']
        return entry['next_interface'], entry['next_hop']
 

    def prepare_message(self, destination, message):
        destination_ip = self.hostname_mapping[destination]
        destination_mac = None

        source_name = self.station_info['stations'][0]
        source_ip = self.station_info[source_name]['ip']
        source_mac = self.station_info[source_name]['mac']
        sock = self.ip_to_socket[source_ip]

        #to find if the destination is in the same lan
        next_interface, next_hop_ip = self.get_next_interface(destination_ip)
        next_interface_ip = self.hostname_mapping[next_interface]
        # next_interface_socket = self.ip_to_socket[next_interface_ip]

        #destination in the same lan
        if next_hop_ip == '0.0.0.0':
            if destination_ip in self.arp_table:
                destination_mac = self.arp_table[destination_ip]
                self.send_message(source_ip, destination_ip, source_mac, destination_mac, message, sock)
            else:
                self.pending_queue.append({'source_ip':source_ip ,'destination_ip': destination_ip, 'message':message, 'source_mac': source_mac, 'sock':sock})
                self.send_arp({'source_ip':source_ip ,'destination_ip': destination_ip, 'source_mac': source_mac, 'destination_mac':'ff:ff:ff:ff:ff:ff', 'type':'ARP_request'}, sock)
        
        #destination is in the different lan
        else:
            if next_hop_ip in self.arp_table:
                destination_mac = self.arp_table[next_hop_ip]
                self.send_message(source_ip, destination_ip, source_mac, destination_mac, message, sock)
            
            else:
                self.pending_queue.append({'source_ip':source_ip ,'destination_ip': destination_ip, 'message':message, 'source_mac': source_mac, 'sock':sock})
                #get mac of next hop router
                self.send_arp({'source_ip':source_ip ,'destination_ip': next_hop_ip, 'source_mac': source_mac, 'destination_mac':'ff:ff:ff:ff:ff:ff', 'type':'ARP_request'}, sock)
    
    def process_pending_queue(self):
        new_queue = deque()
        for item in self.pending_queue:
            destination_ip = item['destination_ip']
            next_interface, next_hop_ip = self.get_next_interface(destination_ip)
            #get socket associated with the next interface
            next_interface_ip = self.hostname_mapping[next_interface]
            next_interface_socket = self.ip_to_socket[next_interface_ip]
            #if the destination is in the same lan
            if next_hop_ip == '0.0.0.0':
                destination_mac = self.arp_table.get(destination_ip, None)
            #destination in the different lan
            else:
                destination_mac = self.arp_table.get(next_hop_ip,None)
            
            if destination_mac:
                self.send_message(item['source_ip'], item['destination_ip'], item['source_mac'], destination_mac, item['message'], next_interface_socket)
            else:
                new_queue.append(item)
        self.pending_queue = new_queue
            
    #consult routing table to get next interface
    def process_arp(self, message, sock):
        destination_ip = message['destination_ip']
        source_ip = message['source_ip']
        socket_ip, socket_port = sock.getpeername()
        my_ip = self.socket_to_ip['{}:{}'.format(socket_ip, socket_port)]
        my_name =  self.reverse_hostname_mapping[my_ip]
        my_mac = self.station_info[my_name]['mac']
        
        # if message['source_ip'] not in self.arp_table:
        #     self.arp_table[message['source_ip']] = message['source_mac']

        print('received {} at {}'.format(message['type'], my_name))
        if message['type'] == 'ARP_request':
            
            if my_ip == destination_ip:
                self.send_arp({'source_ip':my_ip ,'destination_ip': source_ip, 'source_mac': my_mac, 'destination_mac':message['source_mac'], 'type':'ARP_response'}, sock) 
           
            else:
                print("Received ARP request destined for different station...dropping.....")
                print(my_ip, destination_ip)
        elif message['type'] == 'ARP_response':
            if my_ip == destination_ip:
                print("Received ARP response...Processing pending queue..")
                self.arp_table[source_ip] = message['source_mac']
                self.process_pending_queue()
            else:
                print("Received ARP response destined for different station.......")

    def process_frame(self, message, sock):
        
        socket_ip, socket_port = sock.getpeername()
        my_ip = self.socket_to_ip['{}:{}'.format(socket_ip, socket_port)]
        my_name =  self.reverse_hostname_mapping[my_ip]
        ip_packet = message['ip_packet']
        destination_ip = ip_packet['destination_ip']
        source_ip = ip_packet['source_ip']
        print('Received Message {} at {}'.format(ip_packet['message'], my_name))
        if self.is_router:
            print(self.ip_to_socket)
            ########1 dec #######
            #if router received an ip packet then it must forward
            next_interface, next_hop_ip = self.get_next_interface(destination_ip)
            next_interface_ip = self.hostname_mapping[next_interface]
            next_interface_sock = self.ip_to_socket[next_interface_ip]

            #if the packet is destined for host in the next interface
            if next_hop_ip == '0.0.0.0':
                print('Packet is destined for network in the next interface....')
                if destination_ip in self.arp_table:
                    message['destination_mac'] = self.arp_table.get(destination_ip)
                    self.forward_message(message, next_interface_sock)

                else:
                    print('Destination ip not in the arp table....')
                    #add the packet in the pending_queue
                    self.pending_queue.append({'source_ip':source_ip ,'destination_ip': destination_ip, 'message':ip_packet['message'], 'source_mac': message['source_mac'], 'sock':next_interface_sock})

                    #send arp request
                    self.send_arp({'source_ip':next_interface_ip ,'destination_ip': destination_ip, 'source_mac': message['source_mac'], 'destination_mac':'ff:ff:ff:ff:ff:ff', 'type':'ARP_request'}, next_interface_sock)
            
            #packet to be delivered in different lan
            else:
                print('Packet for host in different lan')
                #if mac of next hop present in arp table
                if next_hop_ip in self.arp_table:
                    message['destination_mac'] = self.arp_table[next_hop_ip]
                    self.forward_message(message, next_interface_sock)

                else:
                    #add the packet in the pending_queue
                    self.pending_queue.append({'source_ip':source_ip ,'destination_ip': destination_ip, 'message':ip_packet['message'], 'source_mac': message['source_mac'], 'sock':next_interface_sock})

                    #send arp request
                    self.send_arp({'source_ip':next_interface_ip ,'destination_ip':next_hop_ip , 'source_mac': message['source_mac'], 'destination_mac':'ff:ff:ff:ff:ff:ff', 'type':'ARP_request'}, next_interface_sock)

        else:
            if self.station_info[my_name]['mac'] == message['destination_mac']:
                ip_packet = message['ip_packet']
                print('Received message from station {}'.format(self.reverse_hostname_mapping[ip_packet["source_ip"]]))
                print('Message: {}'.format(ip_packet["message"]))
            else:
                print('Received message destined for different station....dropping...')
    
    def receive_message(self, message, sock):
        message = pickle.loads(message)
        if message['type'] in {'ARP_request', 'ARP_response'}:
            self.process_arp(message, sock)
        elif message['type'] == 'IP':
            self.process_frame(message, sock)

    def handle_input(self):
        # usr_input = sys.stdin.readline()
        # if ';' not in usr_input:
        #     print('Wrong input format...')
        #     return
        # destination, message = usr_input.split(';')
        # print('dest: {}, message: {}'.format(destination, message))

        destination = input()
        message = input("Enter the Message/command: ")
        destination, message = destination.strip(), message.strip()

        if self.is_router and destination != 'cmd':
            print('Router only supports commands....')
        elif destination.lower() == 'cmd':
            self.print_tables(message) 
        elif destination == self.station_info["stations"][0]:
            print('Cannot send message to itself...')
        elif destination not in self.hostname_mapping:
            print('Destination not found....')
        else:
            self.prepare_message(destination, message)
        
    
    #if a bridge is disconnected reomove the connection
    def disconnect_from_lan(self, sock):
        socket_ip,socket_port=sock.getpeername()
        station_ip = self.socket_to_ip['{}:{}'.format(socket_ip, socket_port)]
        self.ip_to_socket.pop(station_ip, None)
        self.socket_to_ip.pop('{}:{}'.format(socket_ip, socket_port), None)
        station_name = self.reverse_hostname_mapping[station_ip]
        print(station_name, '-------------')
        print(self.station_info['stations'])
        self.station_info['stations'].remove(station_name)
        bridge_name = self.station_info[station_name]['lan']
        self.station_info.pop(station_name, None)
        self.all_connections.remove(sock)
        return

    def start(self):
        try:
            #try connecting to lans 
            self.connect_to_lans()
            # print('Enter the message in format: destination_name;message')
            for conn in self.all_connections:
                self.add_time(conn)
            threading.Thread(target=self.check_time).start()
            while True:
                print('\n==== Enter your input ========')
                print("Enter the Destination Name or Type cmd for command: ")
                if len(self.all_connections) == 0:
                    print('No active connections...')
                    break
                self.possible_inputs = [sys.stdin]+list(self.all_connections)
                if self.close_socks:
                    for cs in self.close_socks:
                        self.disconnect_from_lan(cs)
                    break
                # self.possible_inputs = list(self.all_connections)
                read_sockets,_, _ = select.select(self.possible_inputs,[],[])
                for sock in read_sockets:
                # for sock in self.possible_inputs:
                    #if client needs to send message to the server
                    if sock == sys.stdin:
                        self.handle_input()
                    else:
                        # threading.Thread(target=self.handle_input).start()
                        for conn in self.all_connections.copy():
                        #if client receives message from the server
                            if sock == conn:
                                try:
                                    retries = 5
                                    wait_time = 1 
                                    for _ in range(retries):
                                        message = conn.recv(self.LENGTH)
                                        if message:
                                            break
                                        else:
                                            print("Retrying...")
                                            time.sleep(wait_time)
                                except socket.timeout:
                                    pass
                                if message:
                                    self.receive_message(message, sock)
                                    self.update_time(sock)
                                #if message is empty, the server has died
                                else:
                                    self.disconnect_from_lan(sock)
                            # break
                                
        except ConnectionRefusedError:
            print("Connection to the bridge refused. Exiting...")
        except KeyboardInterrupt:
            for conn in self.all_connections.copy():
                self.disconnect_from_lan(conn)
            print('Keyboard Interrupt... Station disconnected from all lans')

    def close(self):
        for sock in self.possible_inputs:
            sock.close()

if __name__ == '__main__':
    assert len(sys.argv) == 5, 'Usage: python station.py -no/route interface routingtable hostname'
    is_router = sys.argv[1] == "-route"

    inerface_file = sys.argv[2]
    routingtable_file = sys.argv[3]
    hostname_file = sys.argv[4]

    # is_router = input("Is this station a router? (y/n): ").lower() == 'y'
    station = Station(inerface_file, routingtable_file, hostname_file, is_router)
    station.start()
    station.close()