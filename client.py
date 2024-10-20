import selectors
import socket
import struct
import sys
from shared import *
from queue import Queue

class ActionUnreadyException(Exception):
    pass

class HelloAction:
    def __init__(self, max_protocol: int, user_id: int):
        self.protocol = max_protocol
        self.user_id = user_id
        self.ready = False

    @staticmethod
    def len(protocol_version): return 2

    def message(self):
        return struct.pack('!BHI', ACTION.HELLO, self.protocol, self.user_id)

    def parse_response(self, protocol: int, message: bytes):
        self.protocol = struct.unpack('!H', message)[0]
        assert self.protocol >= Client.min_protocol # TODO: handle this properly
        self.ready = True

    def act(self, client):
        if not self.ready:
            raise ActionUnreadyException
        client.protocol_version = self.protocol
        client.user_id = self.user_id
        sys.stderr.write(f'new session established. user {self.user_id} protocol {self.protocol}\n')

class Client:
    min_protocol = 0
    max_protocol = 0

    def __init__(self):
        self.sel = selectors.DefaultSelector()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.writebuffer = bytes()
        self.actions = Queue()
        self.protocol_version = None
        self.user_id = None

    # MAIN LOOP
    def start(self, address: str = '', port: int = 9999):
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        self.sock.connect((address, port))
        sys.stderr.write(f'connected to {self.sock.getpeername()}\n')
        self.sock.setblocking(False)
        self.sel.register(self.sock, selectors.EVENT_READ | selectors.EVENT_WRITE)

        self.send_action(HelloAction(self.max_protocol, 0x0486))

        try:
            while True:
                events = self.sel.select()
                for key, mask in events:
                    if mask & selectors.EVENT_READ:
                        self.handle()
                    if mask & selectors.EVENT_WRITE:
                        if self.writebuffer:
                            sent = self.sock.send(self.writebuffer)
                            self.writebuffer = self.writebuffer[sent:]
                # interactive client code gets called here
        except KeyboardInterrupt:
            sys.stderr.write(f'released {self.sock.getsockname()}\n')
            self.sock.close()

    def handle(self):
        try:
            preamble = self.sock.recv(2)
        except BlockingIOError:
            return

        if not preamble:
            sys.stderr.write('server disconnected\n')
            exit(0)

        if preamble[0] < 128:
            status = preamble[0]
            action = preamble[1]
            try:
                handler = self.actions.get()
            except:
                return
                # TODO: handle this

            if status == STATUS.OK:
                msg_len = handler.len(self.protocol_version)
                message = self.sock.recv(msg_len)
                handler.parse_response(self.protocol_version, message)
                handler.act(self)
        else:
            pass

    def parse(self, message: bytes):
        pass

    def send_action(self, action):
        self.actions.put(action)
        self.writebuffer += action.message()

if __name__ == '__main__':
    client = Client()

    argc = len(sys.argv)
    if argc == 1:
        client.start()
    elif argc == 3:
        client.start(sys.argv[1], int(sys.argv[2]))
    else:
        print('usage: client.py <server address> <server port>', file=sys.stderr)
        exit(1)
