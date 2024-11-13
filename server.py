import argparse
import atexit
import selectors
import socket
import struct
import sys
from collections import deque

from shared import *

class Game:
    class IllegalMove(Exception): pass
    class InvalidMove(Exception): pass
    class Unauthorized(Exception): pass

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
            sys.stderr.write(f'host session {session} rejoined game {self}\n')
            self.host_session = session
        elif self.guest_id == -1:
            sys.stderr.write(f'guest {session} joined, readied unready game {self}\n')
            self.guest_id = id
            self.guest_session = session
            self.start()
        elif id == self.guest_id:
            sys.stderr.write(f'guest session {session} rejoined game {self}\n')
            self.guest_session = session
        else:
            return False # unauthorized user
        return True

    # place a piece at coord for player
    def move(self, player_id: int, moveX: int, moveY: int):
        if moveX > 7 or moveY > 7: raise Game.IllegalMove

        if player_id == self.guest_id:
            if not self.turn % 2:
                raise Game.InvalidMove
            color = COLOR.BLACK
        elif player_id == self.host_id:
            if self.turn % 2:
                raise Game.InvalidMove
            color = COLOR.WHITE
        else:
            raise Game.Unauthorized

        self.board_state[moveY][moveX] = color
        self.turn += 1

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

# HANDLERS =============================================================================================================

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

class MoveHandler(Handler):
    @staticmethod
    def len(protocol_version) -> int: return 1

    @staticmethod
    def handle(server: 'Server', session: Session, message: bytes) -> bytes:
        msg = message[0]

        moveY = msg & 15
        msg >>= 4
        moveX = msg & 15

        plr = session.user_id
        game = session.game
        try: game.move(plr, moveX, moveY)
        except Game.IllegalMove:
            status = STATUS.ILLEGAL
        except Game.InvalidMove: # it's not their turn
            status = STATUS.INVALID
        except Game.Unauthorized: # if this happens, JOIN is broken
            raise Game.Unauthorized('FATAL: Somehow, a session got assigned a game which they aren\'t part of')
        else:
            status = STATUS.OK

            if session.user_id == game.guest_id:
                opp_id = game.host_id
                opp = game.host_session
            else:
                opp_id = game.guest_id
                opp = game.guest_session

            # push gamestate to opponent when we move
            opp.send(PushPreamble(PUSH.GAMESTATE).pack() + game.push_gamestate(opp_id))

        return ResponsePreamble(ACTION.MOVE, status).pack() + game.push_gamestate(plr)

# MAIN SERVER CLASS ====================================================================================================

class Server:
    handlers = {
        ACTION.HELLO: HelloHandler,
        ACTION.JOIN: JoinHandler,
        ACTION.MOVE: MoveHandler,
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
    def start(self, port = 9999):
        atexit.register(self.stop)

        self.main_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.main_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.main_sock.bind(('0.0.0.0', port))
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

    parser = argparse.ArgumentParser()
    parser.add_argument(
        '-p', '--port',
        help='specify the port to host the server on',
        type=int,
        default=9999,
    )
    args = parser.parse_args()

    try: server.start(port=args.port)
    except KeyboardInterrupt:
        sys.stderr.write('killed by KeyboardInterrupt\n')
        exit(0)
