import atexit
import selectors
import socket
import struct
import sys
from collections import deque

from shared import *

class Game:
    def __init__(self, creator: 'Session', game_id: int):
        self.id: int = game_id
        self.host_id: int = creator.user_id
        self.guest_id: int = -1
        self.host_session: Session = creator
        self.guest_session: Session = None
        self.turn: int = 1
        self.board_state = BoardState()

    def __repr__(self):
        return f'<id:{self.id},turn:{self.turn},host:{self.host_id},guest:{self.guest_id}>'

    # attach a user session to a game, start the game if unready
    # returns false IFF game is ready and user is not a participant
    def join(self, session: 'Session') -> bool:
        id = session.user_id
        if id == self.host_id:
            sys.stderr.write('host session {session} rejoined game {game}\n')
            self.host_session = session
        elif self.guest_id == -1:
            sys.stderr.write('guest {session} joined, readied unready game {game}\n')
            self.guest_id = id
            self.guest_session = session
            self.start()
        elif id == self.guest_id:
            sys.stderr.write('guest session {session} rejoined game {game}\n')
            self.guest_session = session
        else:
            return False # unauthorized user
        return True

    # notify the game creator of the started match
    def start(self):
        print('game started', self, file=sys.stderr)
        # TODO

    def push_gamestate(self, player_id: int) -> bytes:
        message = bytearray(17)

        if player_id == self.guest_id: # BLACK
            state = 0
            can_move = self.turn % 2
        else: # WHITE
            state = 128
            can_move = (self.turn + 1) % 2
        state |= can_move << 6
        state |= self.turn # assumes turn shall never exceed 63
        message[0] = state
        message[1:] = self.board_state.pack()

        return bytes(message)

class Session:
    def __init__(self, sock):
        self.game: Game = None
        self.user_id = -1
        self.protocol = 0 # init protocol always 0 for HELLO
        self.sock = sock
        self.write_buf = bytes()

    def __repr__(self):
        addr = 'DEAD'
        try:
            addr = self.sock.getpeername()
            addr = addr[0]+':'+addr[1]
        except: pass
        return(f'<fd: {self.sock.fileno()}, addr:{addr}, user:{self.user_id}, prtcl:{self.protocol}>')

    def send(self, message: bytes):
        self.write_buf += message

    def flush(self):
        try:
            sent = self.sock.send(self.write_buf)
            self.write_buf = self.write_buf[sent:]
        except BlockingIOError:
            return

# Handler interface
class Handler:
    # returns the number of bytes to be consumed to decode this message type for the specified protocol version
    @staticmethod
    def len(protocol_version: int) -> int: ...

    # decode the message according to the protocol version of the session
    # perform the action on the server; may mutate internal state
    # return a response
    @staticmethod
    def handle(server: 'Server', session: Session, message: bytes) -> bytes: ...

class HelloHandler(Handler):
    @staticmethod
    def len(protocol_version) -> int: return 6

    @staticmethod
    def handle(server, session, message) -> bytes:
        max_version, user_id = struct.unpack('!HI', message)

        # session already exists for socket!
        if session.user_id != -1:
            sockfd = session.sock.fileno()
            sys.stderr.write(f'duplicate HELLO from {session}\n')
            return ResponsePreamble(ACTION.HELLO, STATUS.INVALID).pack() + \
                    struct.pack('!I', server.sessions[sockfd].user_id)

        if max_version < server.min_version:
            preamble = ResponsePreamble(ACTION.HELLO, STATUS.UNSUPPORTED).pack()
            message = struct.pack('!H', server.min_version)
            return preamble + message

        version = min(server.max_version, max_version)
        session.protocol = version
        session.user_id = user_id

        preamble = ResponsePreamble(ACTION.HELLO).pack()
        message = struct.pack('!H', version)

        print(f'new session for {session}', file=sys.stderr)
        return preamble + message

class JoinHandler(Handler):
    @staticmethod
    def len(protocol_version) -> int: return 4

    @staticmethod
    def handle(server, session, message) -> bytes:
        game_id = struct.unpack('!I', message)[0]

        match game_id:
            case 1: # create private game
                game = server.new_game(session)
            case 0: # join matchmaking
                if server.matchmaking_queue:
                    game = server.matchmaking_queue.popleft()
                    if not game.join(session):
                        raise Exception('FATAL ERROR: matchmaking yielded unauthorized join')
                    # else joined successfully :)
                else:
                    game = server.new_game(session)
                    server.matchmaking_queue.append(game)
            case _:
                game_id -= 2 # offset for reserved 0,1
                if game_id < len(server.games):
                    game = server.games[game_id]
                    if not game.join(session):
                        return ResponsePreamble(ACTION.JOIN, STATUS.UNAUTHORIZED).pack()
                    # else joined successfully :)
                else:
                    return ResponsePreamble(ACTION.JOIN, STATUS.INVALID).pack()

        session.game = game

        preamble = ResponsePreamble(ACTION.JOIN).pack()
        body = struct.pack('!I', game.id)
        return preamble + body + game.push_gamestate(session.user_id)

class Server:
    handlers = {
        ACTION.HELLO: HelloHandler,
        ACTION.JOIN: JoinHandler,
    }

    min_version = 0
    max_version = 0

    def __init__(self):
        self.games: list[Game] = list()
        self.main_sock: socket.socket
        self.matchmaking_queue: deque[Game] = deque()
        self.sel = selectors.DefaultSelector()
        self.sessions = dict() # map player socket fds to user_ids and games

    def new_session(self, conn) -> Session:
        session = Session(conn)
        self.sessions[conn.fileno()] = session
        return session

    # main loop for listening as a TCP server.  blocks.
    def start(self, address = '', port = 9999):
        self.main_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.main_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.main_sock.bind((address, port))
        self.main_sock.setblocking(False)
        self.main_sock.listen()
        self.sel.register(self.main_sock, selectors.EVENT_READ, self.cb_connect)
        print(f'listening on {self.main_sock.getsockname()}', file=sys.stderr)

        while True:
            events = self.sel.select()
            for key, mask in events:
                callback = key.data
                if key.fd in self.sessions:
                    session = self.sessions[key.fd]
                else:
                    session = self.new_session(key.fileobj)

                if mask & selectors.EVENT_READ:
                    try: callback(session)
                    except ConnectionError as e:
                        sys.stderr.write(f'CONNECTION ERROR: {session} -> {e}\n')
                        self.disconnect(session)
                        continue
                if mask & selectors.EVENT_WRITE:
                    if session.write_buf:
                        try: session.flush()
                        except ConnectionError as e:
                            sys.stderr.write(f'CONNECTION ERROR: {session} -> {e}\n')
                            self.disconnect(session)

    def stop(self):
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
        try: preamble = session.sock.recv(1)
        except BlockingIOError:
            return

        # client disconnected
        if not preamble:
            self.disconnect(session)
            return

        bytes_read = 0

        # exhaust the entire input buffer
        while preamble:
            bytes_read += 1
            action = preamble[0]

            try: handler = Server.handlers[ACTION(action)]
            except:
                sys.stderr.write(f'UNSUPPORTED ACTION: {action}\n')
                session.send(ResponsePreamble(action, STATUS.UNSUPPORTED).pack())
                return

            msg_len = handler.len(session.protocol)
            message = session.sock.recv(msg_len)
            nread = len(message)
            bytes_read += nread
            if nread < msg_len:
                session.send(ResponsePreamble(action, STATUS.BAD_FORMAT).pack())
                return

            response = handler.handle(self, session, message)
            session.send(response)

            if bytes_read >= 1400:
                sys.stderr.write(f'DOS protection: {session} sent {bytes_read}/1400 bytes!  Aborting read\n')
                return

            try: preamble = session.sock.recv(1)
            except BlockingIOError:
                return

    def disconnect(self, session: Session):
        print(f'{session} hung up.', file=sys.stderr)
        if session.sock.fileno() in self.sessions:
            del(self.sessions[session.sock.fileno()])
        self.sel.unregister(session.sock)
        session.sock.close()
        # TODO: remove user session from game

    def new_game(self, session: Session) -> Game:
        game_id = 2 + len(self.games)
        game = Game(session, game_id)
        self.games.append(game)
        sys.stderr.write(f'new game created: {game}\n')
        return game

if __name__ == '__main__':
    server = Server()

    atexit.register(server.stop)

    try:
        argc = len(sys.argv)
        if argc == 1:
            server.start()
        elif argc == 3:
            server.start(sys.argv[1], int(sys.argv[2]))
        else:
            print('usage: server.py <listen address> <listen port>', file=sys.stderr)
            exit(1)

    except KeyboardInterrupt:
        sys.stderr.write('killed by KeyboardInterrupt\n')
        exit(0)
