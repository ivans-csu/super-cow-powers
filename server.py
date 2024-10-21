import struct
import selectors
import socket
import sys
from collections import namedtuple

from shared import *

class Game:
    # TODO: associate with sessions
    PLAYER = IntEnum('PLAYER', 'HOST GUEST', start=0)
    def __init__(self, creator_id, game_id):
        self.id = game_id
        self.turn = 1
        self.player_ids = [creator_id, None]

    def __str__(self):
        return f'id:{self.id},turn:{self.turn},players:{str(self.player_ids)}'

    # notify the game creator of the started match
    def start(self):
        print('game started', self, file=sys.stderr)
        # TODO

class Session():
    def __init__(self, sock):
        self.sock = sock
        self.protocol = 0
        self.user_id = None
        self.game = None
        self.write_buf = bytes()

    def send(self, message: bytes):
        self.write_buf += message

    def flush(self):
        try:
            sent = self.sock.send(self.write_buf)
            self.write_buf = self.write_buf[sent:]
        except BlockingIOError:
            return

# Handler interface:
# len(protocol_version) yields the number of bytes to be consumed to decode this message type for the specified protocol version
# handle(server, session, message) decode the message according to the protocol version of the session, perform the action, return a response

class HelloHandler:
    @staticmethod
    def len(protocol: int) -> int: return 6

    @staticmethod
    def handle(server, session: Session, message: bytes) -> bytes:
        max_version, user_id = struct.unpack('!HI', message)

        if max_version < server.min_version:
            preamble = ResponsePreamble(ACTION.HELLO, STATUS.UNSUPPORTED).raw
            message = struct.pack('!H', server.min_version)
            return preamble + message

        version = min(server.max_version, max_version)
        session.protocol = version
        session.user_id = user_id

        preamble = ResponsePreamble(ACTION.HELLO).raw
        message = struct.pack('!H', version)

        print(f'new session for user {session.user_id} protocol {session.protocol}', file=sys.stderr)
        return preamble + message

class Server:
    handlers = {
        ACTION.HELLO: HelloHandler,
    }

    min_version = 0
    max_version = 0

    def __init__(self):
        self.games = list()
        self.matchmaking_queue = list()
        self.sessions = dict() # map player socket fds to user_ids and games
        self.main_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sel = selectors.DefaultSelector()

    # main loop for listening as a TCP server.  blocks.
    def start(self, address = '', port = 9999):
        self.main_sock.bind((address, port))
        self.main_sock.setblocking(False)
        self.main_sock.listen()
        self.sel.register(self.main_sock, selectors.EVENT_READ, self.cb_connect)
        print(f'listening on {self.main_sock.getsockname()}', file=sys.stderr)

        try:
            while True:
                events = self.sel.select()
                for key, mask in events:
                    callback = key.data
                    if key.fd in self.sessions:
                        session = self.sessions[key.fd]
                    else:
                        session = Session(key.fileobj)
                        self.sessions[key.fd] = session
                    if mask & selectors.EVENT_READ:
                        callback(session)
                    if mask & selectors.EVENT_WRITE:
                        if session.write_buf:
                            session.flush()
        except KeyboardInterrupt:
            sys.stderr.write(f'released {self.main_sock.getsockname()}\n')
            self.main_sock.close()

    # accept a new TCP connection
    def cb_connect(self, session: Session):
        sock, addr = session.sock.accept()
        sock.setblocking(False)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 1)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 60)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 5)
        print('new connection from:', addr, file=sys.stderr)
        self.sel.register(sock, selectors.EVENT_WRITE | selectors.EVENT_READ, self.cb_handle)

    # handle an action message from a session
    def cb_handle(self, session: Session) :
        try:
            preamble = session.sock.recv(1)
        except ConnectionResetError:
            sys.stderr.write(f'CONNECTION RESET FOR USER {session.user_id}\n')
            self.disconnect(session)
            return
        except BlockingIOError:
            return

        # client disconnected
        if not preamble:
            self.disconnect(session)
            return

        action = preamble[0]

        try: handler = Server.handlers[ACTION(action)]
        except:
            sys.stderr.write(f'UNSUPPORTED ACTION: {action}\n')
            session.send(ResponsePreamble(action, STATUS.UNSUPPORTED).raw)
            return

        msg_len = handler.len(session.protocol)
        message = session.sock.recv(msg_len)
        if len(message) < msg_len:
            session.send(ResponsePreamble(action, STATUS.BAD_FORMAT).raw)
            return

        response = handler.handle(self, session, message)
        session.send(response)

    def disconnect(self, session: Session):
        print(f'user {session.user_id} hung up.', file=sys.stderr)
        if session.sock.fileno() in self.sessions:
            del(self.sessions[session.sock.fileno()])
        self.sel.unregister(session.sock)
        session.sock.close()
        # TODO: remove user session from game

    def newGame(self, user_id):
        game_id = len(self.games)
        game = Game(user_id, game_id)
        self.games.append(game)
        self.matchmaking_queue.append(game)
        print('new game added to matchmaking:', game, file=sys.stderr)
        return game

if __name__ == '__main__':
    server = Server()

    argc = len(sys.argv)
    if argc == 1:
        server.start()
    elif argc == 3:
        server.start(sys.argv[1], int(sys.argv[2]))
    else:
        print('usage: server.py <listen address> <listen port>', file=sys.stderr)
        exit(1)
