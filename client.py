import argparse
import atexit
import os
import selectors
import socket
import struct
import sys
from collections import deque
from shared import *
import ui

DEBUG = os.environ.get('DEBUG', None)
NOUI = os.environ.get('NOUI', None)

class Action:
    class Unready(Exception): pass
    class BadStatus(Exception): pass
    class Ignore(Exception): pass # parse_response failed, but can be safely ignored. do not call fin
    class Unauthorized(Exception): pass
    class Invalid(Exception): pass

    # ctor is non-standardized, should contain protocol version and fields to init for a request message

    # provide a mapping into the ACTION enum
    type: ACTION

    # returns number of bytes to be consumed to decode a response message for the specified protocol version and status code
    def len(self, status: STATUS) -> int: ...

    # return the on-the-wire packed message for this Action request
    def serialize(self) -> bytes: ...

    # unpack the response message, update internal state
    def parse_response(self, status: STATUS|int, message: bytes): ...

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

    def parse_response(self, status: STATUS|int, message: bytes):
        if status == STATUS.OK:
            self.protocol = struct.unpack('!H', message)[0]
            if self.protocol < Client.min_protocol: raise self.Unsupported
        elif status == STATUS.UNSUPPORTED:
            self.protocol = struct.unpack('!H', message)[0]
            raise self.Unsupported
        elif status == STATUS.INVALID:
            user_id = struct.unpack('!I', message)[0]
            if DEBUG: sys.stderr.write(f'client: server reported duplicate HELLO\n')
            if user_id != self.user_id: raise self.SocketPanic('PANIC! Server reports socket already in use by another user!  This is a critical server bug!')
            else: raise Action.Ignore # don't call finish(), fail silently on duplicate HELLO for same user
        else:
            if type(status) == STATUS: raise Action.BadStatus(status.name)
            raise Action.BadStatus(status)
        self.ready = True
        ui.push_event(ui.PrintEvent('connected to server!', '@'))

    def finish(self, client):
        if not self.ready: raise Action.Unready
        client.protocol_version = self.protocol
        client.user_id = self.user_id
        if DEBUG: sys.stderr.write(f'client: new session established. user {self.user_id} protocol {self.protocol}\n')

class JoinAction(Action):
    type = ACTION.JOIN

    def __init__(self, protocol_version:int, game_id:int):
        self.protocol = protocol_version
        self.game_id = game_id
        self.ready = False

    def serialize(self):
        return struct.pack('!BI', ACTION.JOIN, self.game_id)

    def len(self, status):
        if status == STATUS.OK: return 21
        else: return 0

    def parse_response(self, status: STATUS|int, message: bytes):
        if status != STATUS.OK:
            if status == STATUS.UNAUTHORIZED:
                if DEBUG: raise Action.Unauthorized('Server reports user is not permitted to join this game.')
                ui.push_event(ui.ErrorEvent('Unauthorized; You are not a participant in this game'))
                return
            elif status == STATUS.INVALID:
                if DEBUG: raise Action.Invalid('Server reports game does not exist.')
                ui.push_event(ui.ErrorEvent('No such game exists'))
                return
            else:
                if type(status) == STATUS: raise Action.BadStatus(status.name)
                else: raise Action.BadStatus(status)

        self.game_id = struct.unpack('!I', message[:4])[0]
        self.game_state = GameState.unpack(message[4:])
        self.ready = True

    def finish(self, client: 'Client'):
        if not self.ready:
            if DEBUG: raise Action.Unready
            else: return # silently remove unfinished actions when not debugging
        client.game_id = self.game_id
        client.game_state = self.game_state

        if DEBUG: sys.stderr.write(f'client: user {client.user_id} joined game {self.game_id}\n')
        if self.game_state.color == COLOR.WHITE:
            uimessage = 'Matchmaking in progress. Once found, your opponent will make the first move.'
        else: uimessage = ''

        ui.push_event(ui.JoinEvent(self.game_id, self.game_state, uimessage))

class MoveAction(Action):
    type = ACTION.MOVE

    def __init__(self, protocol: int, x: int, y: int):
        self.ready = False
        self.protocol = protocol
        self.x = x
        self.y = y

    def serialize(self) -> bytes:
        return struct.pack('BB', ACTION.MOVE, (self.x << 4) | (self.y & 15))

    def len(self, status): return 17

    def parse_response(self, status: STATUS|int, message: bytes):
        self.status = status
        self.game_state = GameState.unpack(message)
        self.ready = True

    def finish(self, client: 'Client'):
        if not self.ready: raise Action.Unready
        client.game_state = self.game_state

        match self.status:
            case STATUS.INVALID:
                uimessage = 'It is not your turn to move!'
            case STATUS.ILLEGAL:
                uimessage = 'Move is not legal'
            case _:
                uimessage = ''
        ui.push_event(ui.GamestateEvent(self.game_state, uimessage))

class BadMessage(Exception): ...

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
        self.game_id = -1
        self.game_state = GameState(color=None, turn=-1, can_move=False, board_state=None)


    def cb_stdin(self, _):
        i = os.read(1, 128)
        i = i[:-1]

        ui.parse(self, input=i.decode())

    def cb_handle(self, mask):
        if mask & selectors.EVENT_READ:
            try: self.handle()
            except ConnectionError as e:
                sys.stderr.write(f'CONNECTION ERROR: {e}\n')
                self.disconnect()
                exit(1)
        if mask & selectors.EVENT_WRITE:
            try: self.flush()
            except ConnectionError as e:
                sys.stderr.write(f'CONNECTION ERROR: {e}\n')
                self.disconnect()
                exit(1)

    # MAIN LOOP
    def start(self, address: str = '', port: int = 9999):
        atexit.register(self.stop)

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        self.sock.connect((address, port))
        if DEBUG: sys.stderr.write(f'client: connected to {self.sock.getpeername()}\n')
        self.sock.setblocking(False)
        self.sel.register(self.sock, selectors.EVENT_READ | selectors.EVENT_WRITE, data=self.cb_handle)
        if not NOUI: self.sel.register(sys.stdin, selectors.EVENT_READ, data=self.cb_stdin)

        self.send_action(HelloAction(self.max_protocol, self.sock.getsockname()[1]))
        ui.push_event(ui.PrintEvent('welcome!'))
        ui.push_event(ui.PrintEvent('connecting to server...'))

        while True:
            if not NOUI: ui.handle_events()

            events = self.sel.select()
            for key, mask in events:
                key.data(mask)

    def handle(self):
        try:
            preamble = self.sock.recv(2)
        except BlockingIOError:
            return
        if not preamble:
            if DEBUG: sys.stderr.write('server disconnected\n')
            if not NOUI:
                ui.clear_line()
                ui.ErrorEvent('Lost connection to server').handle()
            exit(1)

        # process entire input buffer
        while preamble:
            if len(preamble) < 2: raise BadMessage

            if preamble[0] & 128 == 0: # action
                status = preamble[0]
                action = preamble[1]

                try: action_handler = self.waiting_actions[ACTION(action)].popleft()
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
                except HelloAction.Ignore: pass # don't finish()
                else:
                    try: action_handler.finish(self)
                    except Action.Unready:
                        raise
                        # TODO: handle

            else: # push
                push_type = PushPreamble.unpack(preamble).type

                if push_type == PUSH.GAMESTATE:
                    message = self.sock.recv(17)
                    if len(message) < 17:
                        raise BadMessage('unexpected end of message')

                    self.game_state = GameState.unpack(message)
                    ui.push_event(ui.GamestateEvent(self.game_state))
                elif push_type == PUSH.DCONNECT:
                    ui.push_event(ui.PrintEvent('opponent is now away', '@'))
                elif push_type == PUSH.WIN:
                    ui.push_event(ui.GameOverEvent('Congratulations, you WON the match!'))
                elif push_type == PUSH.LOSE:
                    ui.push_event(ui.GameOverEvent('Shucks, you LOST the match.'))
                elif push_type == PUSH.TIE:
                    ui.push_event(ui.GameOverEvent('This match ended in a tie!'))
                else:
                    if DEBUG: print(f'client: got unhandled PUSH type "{push_type}"', file=sys.stderr)

            try: preamble = self.sock.recv(2)
            except BlockingIOError:
                return

    def join(self, game_id: int):
        self.send_action(JoinAction(self.max_protocol, game_id))

    def move(self, x:int, y:int):
        self.send_action(MoveAction(self.protocol_version, x, y))

    def disconnect(self):
        try: sn = self.sock.getsockname()
        except: sn = self.sock.fileno()
        if DEBUG: sys.stderr.write(f'client: released {sn}\n')
        self.sock.close()

    def send_action(self, action: Action):
        self.waiting_actions[action.type].append(action)
        self.writebuffer += action.serialize()

    def flush(self):
        if self.writebuffer:
            sent = self.sock.send(self.writebuffer)
            self.writebuffer = self.writebuffer[sent:]

    def stop(self):
        self.disconnect()

if __name__ == '__main__':
    client = Client()

    parser = argparse.ArgumentParser()
    parser.add_argument(
        '-i', '--ip',
        help='specify the ip address of the server',
        default='localhost',
    )
    parser.add_argument(
        '-p', '--port',
        help='specify the port of the server',
        type=int,
        default=9999,
    )
    args = parser.parse_args()

    try: client.start(args.ip, args.port)
    except KeyboardInterrupt:
        sys.stderr.write('killed by KeyboardInterrupt\n')
        exit(0)
