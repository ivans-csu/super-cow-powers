import atexit
import selectors
import socket
import struct
import sys
from collections import deque
from shared import *


# interface
class Action:
    class Unready(Exception): pass
    class BadStatus(Exception): pass

    # ctor is non-standardized, should contain protocol version and fields to init for a request message

    # provide a mapping into the ACTION enum
    type: ACTION

    # returns number of bytes to be consumed to decode a response message for the specified protocol version and status code
    def len(self, status: STATUS) -> int: ...

    # return the on-the-wire packed message for this Action request
    def serialize(self) -> bytes: ...

    # unpack the response message, update internal state
    def parse_response(self, status: STATUS, message: bytes): ...

    # once we've parsed the response, perform whatever internal client state manipulation is appropriate
    # raises ActionUnreadyException if called before parse_response
    def finish(self, client): ...

class HelloAction(Action):
    type = ACTION.HELLO

    class Unsupported(Exception): ...
    class SocketPanic(Exception): ...

    def __init__(self, max_protocol: int, user_id: int):
        self.protocol = max_protocol
        self.user_id = user_id
        self.ready = False

    def len(self, status):
        if status == STATUS.INVALID: return 4
        else: return 2

    def serialize(self):
        return struct.pack('!BHI', ACTION.HELLO, self.protocol, self.user_id)

    def parse_response(self, status: STATUS, message: bytes):
        if status == STATUS.OK:
            self.protocol = struct.unpack('!H', message)[0]
            if self.protocol < Client.min_protocol: raise self.Unsupported
        elif status == STATUS.UNSUPPORTED:
            self.protocol = struct.unpack('!H', message)[0]
            raise self.Unsupported
        elif status == STATUS.INVALID:
            user_id = struct.unpack('!I', message)[0]
            if user_id != self.user_id:
                raise self.SocketPanic('PANIC! Server reports socket already in use by another user!  This is a critical server bug!')
            # else ignore
        else: raise Action.BadStatus(status)
        self.ready = True

    def finish(self, client):
        if not self.ready: raise Action.Unready
        client.protocol_version = self.protocol
        client.user_id = self.user_id
        sys.stderr.write(f'new session established. user {self.user_id} protocol {self.protocol}\n')

class BadMessage(Exception): ...
class Invalid(Exception): ...

class Client:
    min_protocol = 0
    max_protocol = 0

    def __init__(self):
        self.protocol_version = -1
        self.sel = selectors.DefaultSelector()
        self.sock: socket.socket
        self.user_id = -1
        self.waiting_actions: dict[ACTION, deque[Action]] = {}
        self.writebuffer = bytes()
        for action in ACTION:
            self.waiting_actions[action] = deque()

    # MAIN LOOP
    def start(self, address: str = '', port: int = 9999):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        self.sock.connect((address, port))
        sys.stderr.write(f'connected to {self.sock.getpeername()}\n')
        self.sock.setblocking(False)
        self.sel.register(self.sock, selectors.EVENT_READ | selectors.EVENT_WRITE)

        self.send_action(HelloAction(self.max_protocol, 0x0486))

        while True:

            # interactive client code should be called from here

            events = self.sel.select()
            for _, mask in events:
                if mask & selectors.EVENT_READ:
                    self.handle()
                if mask & selectors.EVENT_WRITE:
                    self.flush()

    def handle(self):
        try:
            preamble = self.sock.recv(2)
        except BlockingIOError:
            return
        if not preamble:
            sys.stderr.write('server disconnected\n')
            exit(0)
        elif len(preamble) < 2: raise BadMessage

        # process entire input buffer
        while preamble:
            if preamble[0] & 128 == 0: # action
                status = preamble[0]
                action = preamble[1]

                try: action_handler = self.waiting_actions[ACTION(action)].pop()
                except ValueError:
                    raise
                    # TODO: handle OOB action number
                except IndexError:
                    raise
                    # TODO: handle response for nonexistent request

                try: msg_len = action_handler.len(STATUS(status))
                except ValueError:
                    raise
                    # TODO: handle OOB status code

                message = self.sock.recv(msg_len)
                if len(message) < msg_len:
                    raise BadMessage('unexpected end of message')

                try: action_handler.parse_response(STATUS(status), message)
                except Action.BadStatus:
                    raise
                    # TODO: handle
                except HelloAction.Unsupported:
                    raise
                    # TODO: handle
                try: action_handler.finish(self)
                except Action.Unready:
                    raise
                    # TODO: handle
            else: # push
                pass

            try:
                preamble = self.sock.recv(2)
            except BlockingIOError:
                return
            if not preamble:
                break
            elif len(preamble) < 2: raise BadMessage

    def send_action(self, action: Action):
        self.waiting_actions[action.type].append(action)
        self.writebuffer += action.serialize()

    def flush(self):
        if self.writebuffer:
            sent = self.sock.send(self.writebuffer)
            self.writebuffer = self.writebuffer[sent:]

    def stop(self):
        sys.stderr.write(f'released {self.sock.getsockname()}\n')
        self.sock.close()

if __name__ == '__main__':
    client = Client()

    atexit.register(client.stop)

    try:
        argc = len(sys.argv)
        if argc == 1:
            client.start()
        elif argc == 3:
            client.start(sys.argv[1], int(sys.argv[2]))
        else:
            sys.stderr.write('usage: client.py <server address> <server port>\n')
            exit(1)

    except KeyboardInterrupt:
        sys.stderr.write('killed by KeyboardInterrupt\n')
        exit(0)
