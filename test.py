import unittest

import server
import client
from shared import *

class MockConn:
    def __init__(self, fd: int):
        self.fd = fd
        self.i = bytes()
        self.o = bytes()

    def recv(self, n: int) -> bytes:
        msg = self.i[:n]
        self.i = self.i[n:]
        return msg

    def send(self, msg: bytes) -> int:
        self.o += msg
        return len(msg)

    def fileno(self): return self.fd

class TestResponsePreamble(unittest.TestCase):
    def test_typebit_status(self):
        for status in STATUS:
            x = ResponsePreamble(ACTION.HELLO, status)
            self.assertEqual(x.status & 128, 0)
            self.assertEqual(x.status, status)
            y = ResponsePreamble.unpack(x.pack())
            self.assertEqual(x.status, y.status)

    def test_action(self):
        for action in ACTION:
            x = ResponsePreamble(action)
            self.assertEqual(x.action, action)
            y = ResponsePreamble.unpack(x.pack())
            self.assertEqual(x.action, y.action)

class TestPushPreamble(unittest.TestCase):
    def test_typebit_type(self):
        for type in PUSH:
            x = PushPreamble(type)
            self.assertEqual(x.type & 32768, 0)
            self.assertEqual(x.type, type)

    def test_endian(self):
        x = PushPreamble(PUSH.GAMESTATE)
        self.assertEqual(x.pack(), bytes((0x0, 0x2)))

class TestServerHello(unittest.TestCase):
    def test_ok(self):
        s = server.Server()
        mc = MockConn(fd = 1)
        mc.i = client.HelloAction(s.max_version, 0x486).serialize()

        session = s.new_session(mc)
        s.cb_handle(session)
        session.flush()

        self.assertEqual(mc.o, struct.pack('!BBH', STATUS.OK, ACTION.HELLO, s.max_version))
        self.assertEqual(s.sessions[1], session)

    def test_multiple(self):
        s = server.Server()
        mc = MockConn(fd = 1)
        mc2 = MockConn(fd = 2)
        mc.i = client.HelloAction(s.max_version, 0x486).serialize()
        mc2.i = client.HelloAction(s.max_version, 0x1134).serialize()

        session = s.new_session(mc)
        session2 = s.new_session(mc2)
        s.cb_handle(session)
        s.cb_handle(session2)
        session.flush()
        session2.flush()

        self.assertEqual(mc.o, struct.pack('!BBH', STATUS.OK, ACTION.HELLO, s.max_version))
        self.assertEqual(mc2.o, struct.pack('!BBH', STATUS.OK, ACTION.HELLO, s.max_version))
        self.assertEqual(s.sessions[1], session)
        self.assertEqual(s.sessions[2], session2)
        self.assertNotEqual(s.sessions[1], s.sessions[2])

    def test_too_new(self):
        s = server.Server()
        mc = MockConn(fd = 1)
        mc.i = client.HelloAction(s.max_version + 1, 0x486).serialize()
        session = s.new_session(mc)

        s.cb_handle(session)
        session.flush()

        self.assertEqual(mc.o, struct.pack('!BBH', STATUS.OK, ACTION.HELLO, s.max_version))

    def test_too_old(self):
        s = server.Server()
        mc = MockConn(fd = 1)
        mc.i = client.HelloAction(s.min_version, 0x486).serialize()
        session = s.new_session(mc)

        s.min_version = s.max_version = s.max_version + 1

        s.cb_handle(session)
        session.flush()

        self.assertEqual(mc.o, struct.pack('!BBH', STATUS.UNSUPPORTED, ACTION.HELLO, s.min_version))

    def test_too_short(self):
        s = server.Server()
        mc = MockConn(fd = 1)

        session = s.new_session(mc)

        for size in (3,2,1):
            mc.i = client.HelloAction(s.min_version, 0x486).serialize()[:size]
            s.cb_handle(session)
            session.flush()

            response = ResponsePreamble.unpack(mc.o)
            self.assertEqual(response, ResponsePreamble(ACTION.HELLO, STATUS.BAD_FORMAT))

    def test_dup(self):
        s = server.Server()
        mc = MockConn(fd = 1)

        session = s.new_session(mc)

        mc.i = client.HelloAction(s.min_version, 0x486).serialize()
        mc.i += mc.i
        s.cb_handle(session)
        session.flush()
        self.assertEqual(len(mc.i), 0) # ensure cb_handle consumed the whole input buffer

        mc.o = mc.o[4:] # skip first OK response
        response = ResponsePreamble.unpack(mc.o)
        self.assertEqual(response, ResponsePreamble(ACTION.HELLO, STATUS.INVALID))
        self.assertEqual(mc.o[2:], struct.pack('!I', 0x486))

    def test_sock_panic(self):
        s = server.Server()
        mc = MockConn(fd = 1)

        session = s.new_session(mc)

        mc.i = client.HelloAction(s.min_version, 0x486).serialize()
        mc.i += client.HelloAction(s.min_version, 0x1134).serialize()
        s.cb_handle(session)
        session.flush()
        self.assertEqual(len(mc.i), 0) # ensure cb_handle consumed the whole input buffer

        mc.o = mc.o[4:] # skip first OK response
        response = ResponsePreamble.unpack(mc.o)
        self.assertEqual(response, ResponsePreamble(ACTION.HELLO, STATUS.INVALID))
        self.assertEqual(mc.o[2:], struct.pack('!I', 0x486))

class TestClientHello(unittest.TestCase):
    def test_ok(self):
        c = client.Client()
        sock = c.sock = MockConn(1)

        c.send_action(client.HelloAction(server.Server.max_version, 0x486))
        c.flush()

        sock.i = struct.pack('!BBH', STATUS.OK, ACTION.HELLO, server.Server.max_version)
        c.handle()
        self.assertEqual(c.protocol_version, server.Server.max_version)
        self.assertEqual(c.user_id, 0x486)

    def test_too_new(self):
        c = client.Client()
        sock = c.sock = MockConn(1)

        client.Client.min_protocol = server.Server.max_version + 1
        c.send_action(client.HelloAction(c.min_protocol, 0x486))

        sock.i = struct.pack('!BBH', STATUS.OK, ACTION.HELLO, server.Server.max_version)
        try: c.handle()
        except client.HelloAction.Unsupported: pass
        else: self.fail()

    def test_too_short(self):
        c = client.Client()
        sock = c.sock = MockConn(1)

        for size in (3,2,1):
            c.send_action(client.HelloAction(0, 0x486))
            sock.i = struct.pack('!BBH', STATUS.OK, ACTION.HELLO, server.Server.max_version)[:size]
            try: c.handle()
            except client.BadMessage: pass
            else: self.fail()

        for size in (5,4,3,2,1):
            c.send_action(client.HelloAction(0, 0x486))
            sock.i = struct.pack('!BBI', STATUS.INVALID, ACTION.HELLO, 0x486)[:size]
            try: c.handle()
            except client.BadMessage: pass
            else: self.fail()

    # duplicate HELLO should fail silently if server already knows us
    def test_dup(self):
        c = client.Client()
        sock = c.sock = MockConn(1)

        c.send_action(client.HelloAction(0, 0x486))
        c.send_action(client.HelloAction(0, 0x486))
        sock.i = struct.pack('!BBH', STATUS.OK, ACTION.HELLO, server.Server.max_version)
        sock.i += struct.pack('!BBI', STATUS.INVALID, ACTION.HELLO, 0x486)

        c.handle()
        self.assertEqual(len(sock.i), 0) # ensure handle consumed the whole input buffer
        self.assertEqual(c.user_id, 0x486)
        self.assertEqual(c.protocol_version, server.Server.max_version)

    # server thinks duplicate HELLO, but it's stuck with someone else's session on our socket
    def test_socketpanic(self):
        c = client.Client()
        sock = c.sock = MockConn(1)

        c.send_action(client.HelloAction(0, 0x486))
        sock.i += struct.pack('!BBI', STATUS.INVALID, ACTION.HELLO, 0x1134)
        try: c.handle()
        except client.HelloAction.SocketPanic: pass
        else: self.fail()

    # ensure response handling in same order as requests
    def test_handle_order(self):
        c = client.Client()
        sock = c.sock = MockConn(1)

        c.send_action(client.HelloAction(0, 0x486))
        c.send_action(client.HelloAction(0, 0x1134))
        sock.i += struct.pack('!BBI', STATUS.INVALID, ACTION.HELLO, 0x486)
        sock.i += struct.pack('!BBI', STATUS.INVALID, ACTION.HELLO, 0x1134)
        c.handle()
        # these will both fail silently.  If they throw SocketPanic, response order is wrong

class TestServerGamestate(unittest.TestCase):
    def test_boardstate(self):
        b = BoardState(False)
        # x marks the spot
        for i in range(8):
            b.state[i][i] = SQUARE.WHITE
        for i in range(8):
            b.state[i][7-i] = SQUARE.BLACK

        c = BoardState.unpack(b.pack())
        self.assertEqual(b.state, c.state)

    def test_pushstate(self):
        mc = MockConn(fd = 1)
        sess = server.Session(mc)
        g = server.Game(sess, 2)
        g.host_id = 0 # white
        g.guest_id = 1 # black
        bs = g.board_state.pack()

        #player is white, cannot move, turn 1
        expected = 0b10000001.to_bytes() + bs
        self.assertEqual(g.push_gamestate(0), expected)

        #player is black, can move, turn 1
        expected = 0b01000001.to_bytes() + bs
        self.assertEqual(g.push_gamestate(1), expected)

        g.turn = 2
        #player is white, can move, turn 2
        expected = 0b11000010.to_bytes() + bs
        self.assertEqual(g.push_gamestate(0), expected)

        #player is black, cannot move, turn 2
        expected = 0b00000010.to_bytes() + bs
        self.assertEqual(g.push_gamestate(1), expected)

        #up to 60 moves are theoretically possible
        for g.turn in range(3,61):
            gsW = g.push_gamestate(0)[0]
            gsB = g.push_gamestate(1)[0]
            self.assertTrue(gsW & g.turn == g.turn)
            self.assertTrue(gsB & g.turn == g.turn)
            self.assertTrue(gsW & 128)
            self.assertFalse(gsB & 128)
            if g.turn % 2:
                self.assertTrue(gsB & 64)
                self.assertFalse(gsW & 64)
            else:
                self.assertTrue(gsW & 64)
                self.assertFalse(gsB & 64)

class TestServerJoin(unittest.TestCase):
    bs = b'\x00\x00\x00\x00\x00\x00\x02\x40\x01\x80\x00\x00\x00\x00\x00\x00' # initial board state (packed)
    gs_white = b'\x81' + bs
    gs_black = b'\x41' + bs
    def test_join_invalid(self):
        s = server.Server()
        mc = MockConn(fd = 1)
        sess = s.new_session(mc)

        mc.i = struct.pack('!BI', ACTION.JOIN, 0x2)
        s.cb_handle(sess)
        sess.flush()

        self.assertEqual(mc.o, ResponsePreamble(ACTION.JOIN, STATUS.INVALID).pack())
        self.assertEqual(sess.game, None)

    def test_create_private(self):
        s = server.Server()
        mc = MockConn(fd = 1)
        sess = s.new_session(mc)
        sess.user_id = 0x486
        self.assertEqual(sess.game, None)

        mc.i = struct.pack('!BI', ACTION.JOIN, 0x1)
        s.cb_handle(sess)
        sess.flush()

        expected = ResponsePreamble(ACTION.JOIN).pack() + struct.pack('!I', 2) + self.gs_white
        self.assertEqual(mc.o, expected)
        self.assertEqual(len(s.matchmaking_queue), 0)
        self.assertEqual(len(s.games), 1)

        game = s.games[0]
        self.assertEqual(game.id, 2)
        self.assertEqual(game.host_id, 0x486)
        self.assertEqual(game.guest_id, -1)
        self.assertIs(game.host_session, sess)
        self.assertEqual(game.guest_session, None)
        self.assertIs(sess.game, game)

    def test_join_private(self):
        s = server.Server()
        mcW = MockConn(fd = 1)
        sessW = s.new_session(mcW)
        sessW.user_id = 0x486

        mcB = MockConn(fd = 1)
        sessB = s.new_session(mcB)
        sessB.user_id = 0x1134

        mcW.i = struct.pack('!BI', ACTION.JOIN, 0x1)
        s.cb_handle(sessW)
        sessW.flush()

        mcB.i = struct.pack('!BI', ACTION.JOIN, 0x2)
        s.cb_handle(sessB)
        sessB.flush()

        _expected = ResponsePreamble(ACTION.JOIN).pack() + struct.pack('!I', 2)
        expectedW = _expected + self.gs_white
        expectedB = _expected + self.gs_black
        self.assertEqual(mcW.o, expectedW)
        self.assertEqual(mcB.o, expectedB)
        self.assertEqual(len(s.games), 1)
        self.assertEqual(len(s.matchmaking_queue), 0)

        game = s.games[0]
        self.assertEqual(game.id, 2)
        self.assertEqual(game.host_id, 0x486)
        self.assertEqual(game.guest_id, 0x1134)
        self.assertIs(game.host_session, sessW)
        self.assertIs(game.guest_session, sessB)
        self.assertIs(sessW.game, game)
        self.assertIs(sessB.game, game)

    def test_join_matchmaking(self):
        s = server.Server()
        mcW = MockConn(fd = 1)
        sessW = s.new_session(mcW)
        sessW.user_id = 0x486
        mcB = MockConn(fd = 1)
        sessB = s.new_session(mcB)
        sessB.user_id = 0x1134

        self.assertEqual(len(s.games), 0)
        self.assertEqual(len(s.matchmaking_queue), 0)

        mcW.i = struct.pack('!BI', ACTION.JOIN, 0x0)
        s.cb_handle(sessW)
        sessW.flush()

        self.assertEqual(len(s.games), 1)
        self.assertEqual(len(s.matchmaking_queue), 1)

        mcB.i = struct.pack('!BI', ACTION.JOIN, 0x0)
        s.cb_handle(sessB)
        sessB.flush()

        self.assertEqual(len(s.games), 1)
        self.assertEqual(len(s.matchmaking_queue), 0)

        _expected = ResponsePreamble(ACTION.JOIN).pack() + struct.pack('!I', 2)
        expectedW = _expected + self.gs_white
        expectedB = _expected + self.gs_black
        self.assertEqual(mcW.o, expectedW)
        self.assertEqual(mcB.o, expectedB)

        game = s.games[0]
        self.assertEqual(game.id, 2)
        self.assertEqual(game.host_id, 0x486)
        self.assertEqual(game.guest_id, 0x1134)
        self.assertIs(game.host_session, sessW)
        self.assertIs(game.guest_session, sessB)
        self.assertIs(sessW.game, game)
        self.assertIs(sessB.game, game)

    def test_unauthorized_join(self):
        s = server.Server()
        mc = MockConn(fd = 1)
        sess = s.new_session(mc)

        sess.user_id = 0x486
        s.games.append(server.Game(sess, 2))
        s.games[0].guest_id = 0x487

        sess2 = s.new_session(mc)
        sess2.user_id = 0x1134
        mc.i = struct.pack('!BI', ACTION.JOIN, 0x2)
        s.cb_handle(sess2)
        sess2.flush()

        expected = ResponsePreamble(ACTION.JOIN, STATUS.UNAUTHORIZED).pack()
        self.assertEqual(mc.o, expected)

        game = s.games[0]
        self.assertEqual(sess2.game, None)

    def test_rejoin(self):
        s = server.Server()
        mcW = MockConn(fd = 1)
        sessW = s.new_session(mcW)
        sessW.user_id = 0x486

        mcB = MockConn(fd = 1)
        sessB = s.new_session(mcB)
        sessB.user_id = 0x1134

        # pretend game is already ready
        s.games.append(server.Game(sessW, 2))
        s.games[0].guest_id = 0x1134

        mcW.i = struct.pack('!BI', ACTION.JOIN, 0x2)
        s.cb_handle(sessW)
        sessW.flush()

        mcB.i = struct.pack('!BI', ACTION.JOIN, 0x2)
        s.cb_handle(sessB)
        sessB.flush()

        _expected = ResponsePreamble(ACTION.JOIN).pack() + struct.pack('!I', 2)
        expectedW = _expected + self.gs_white
        expectedB = _expected + self.gs_black
        self.assertEqual(mcW.o, expectedW)
        self.assertEqual(mcB.o, expectedB)

        game = s.games[0]
        self.assertEqual(game.id, 2)
        self.assertEqual(game.host_id, 0x486)
        self.assertEqual(game.guest_id, 0x1134)
        self.assertIs(game.host_session, sessW)
        self.assertIs(game.guest_session, sessB)
        self.assertIs(sessW.game, game)
        self.assertIs(sessB.game, game)

class TestClientJoin(unittest.TestCase):
    bs = BoardState()
    bsp = bs.pack()

    def test_invalid(self):
        c = client.Client()
        sock = c.sock = MockConn(1)

        c.send_action(client.JoinAction(c.max_protocol, 2))
        c.flush()

        sock.i = ResponsePreamble(ACTION.JOIN, STATUS.INVALID).pack()
        try: c.handle()
        except client.Action.Invalid: pass
        else: self.fail()

    def test_create_private(self):
        c = client.Client()
        sock = c.sock = MockConn(1)

        c.send_action(client.JoinAction(c.max_protocol, 1))
        c.flush()

        sock.i = ResponsePreamble(ACTION.JOIN).pack() + struct.pack('!I', 2)
        sock.i += 0b10000001.to_bytes() + self.bsp
        c.handle()

        self.assertEqual(c.game_id, 2)
        self.assertEqual(c.game_state.color, COLOR.WHITE)
        self.assertEqual(c.game_state.can_move, False)
        self.assertEqual(c.game_state.turn, 1)
        self.assertEqual(c.game_state.board_state, self.bs)

    def test_join_private(self):
        c = client.Client()
        sock = c.sock = MockConn(1)

        c.send_action(client.JoinAction(c.max_protocol, 2))
        c.flush()

        sock.i = ResponsePreamble(ACTION.JOIN).pack() + struct.pack('!I', 2)
        sock.i += 0b01000001.to_bytes() + self.bsp
        c.handle()

        self.assertEqual(c.game_id, 2)
        self.assertEqual(c.game_state.color, COLOR.BLACK)
        self.assertEqual(c.game_state.can_move, True)
        self.assertEqual(c.game_state.turn, 1)
        self.assertEqual(c.game_state.board_state, self.bs)

    def test_unauthorized(self):
        c = client.Client()
        sock = c.sock = MockConn(1)

        c.send_action(client.JoinAction(c.max_protocol, 2))
        c.flush()

        sock.i = ResponsePreamble(ACTION.JOIN, STATUS.UNAUTHORIZED).pack()
        try: c.handle()
        except client.Action.Unauthorized: pass
        else: self.fail()

class TestServerMove(unittest.TestCase):
    # white cannot move on an odd turn (also before the game has started)
    def test_move_invalid(self):
        s = server.Server()

        mcW = MockConn(fd = 1)
        sessW = s.new_session(mcW)
        sessW.user_id = 0x486

        game = s.new_game(sessW)
        sessW.game = game

        mcW.i = ACTION.MOVE.to_bytes() + b'\x32' # move to D,3
        s.cb_handle(sessW)
        sessW.flush()

        bs = BoardState()
        iswhite = 1 << 7
        canmove = 0 << 6
        turn = 1
        state = (iswhite | canmove | turn).to_bytes()
        expectedW = ResponsePreamble(ACTION.MOVE, STATUS.INVALID).pack() + state + bs.pack()
        self.assertEqual(mcW.o, expectedW)

    def test_move_illegal(self):
        # out-of-bounds moves are illegal
        s = server.Server()

        mcW = MockConn(fd = 1)
        sessW = s.new_session(mcW)
        sessW.user_id = 0x486

        mcB = MockConn(fd = 1)
        sessB = s.new_session(mcB)
        sessB.user_id = 0x1134

        game = s.new_game(sessW)
        game.guest_id = sessB.user_id
        sessB.game = sessW.game = game

        mcB.i = ACTION.MOVE.to_bytes() + b'\x88' # move to (OOB) I,9
        s.cb_handle(sessB)
        sessB.flush()

        bs = BoardState()
        iswhite = 0 << 7
        canmove = 1 << 6
        turn = 1
        state = (iswhite | canmove | turn).to_bytes()
        expectedB = ResponsePreamble(ACTION.MOVE, STATUS.ILLEGAL).pack() + state + bs.pack()

        self.assertEqual(mcB.o, expectedB)


    def test_move_valid(self):
        s = server.Server()

        mcW = MockConn(fd = 1)
        sessW = s.new_session(mcW)
        sessW.user_id = 0x486

        mcB = MockConn(fd = 1)
        sessB = s.new_session(mcB)
        sessB.user_id = 0x1134

        game = s.new_game(sessW)
        game.guest_id = sessB.user_id
        sessB.game = sessW.game = game

        mcB.i = ACTION.MOVE.to_bytes() + b'\x32' # move to D,3
        s.cb_handle(sessB)
        sessB.flush()
        sessW.flush()

        bs = BoardState()
        bs[2][3] = COLOR.BLACK

        # verify move response to black
        iswhite = 0 << 7
        canmove = 0 << 6
        turn = 2
        state = (iswhite | canmove | turn).to_bytes()
        expectedB = ResponsePreamble(ACTION.MOVE).pack() + state + bs.pack()
        self.assertEqual(mcB.o, expectedB)

        # verify state push to white
        iswhite = 1 << 7
        canmove = 1 << 6
        turn = 2
        state = (iswhite | canmove | turn).to_bytes()
        expectedW = PushPreamble(PUSH.GAMESTATE).pack() + state + bs.pack()
        self.assertEqual(mcW.o, expectedW)

unittest.main()
