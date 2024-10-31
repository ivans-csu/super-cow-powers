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

class TestServerJoin(unittest.TestCase):
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

        expected = ResponsePreamble(ACTION.JOIN).pack() + struct.pack('!I', 2)
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
        mc = MockConn(fd = 1)
        sess = s.new_session(mc)
        sess.user_id = 0x486

        mc2 = MockConn(fd = 1)
        sess2 = s.new_session(mc2)
        sess2.user_id = 0x1134

        mc.i = struct.pack('!BI', ACTION.JOIN, 0x1)
        s.cb_handle(sess)
        sess.flush()

        mc2.i = struct.pack('!BI', ACTION.JOIN, 0x2)
        s.cb_handle(sess2)
        sess2.flush()

        expected = ResponsePreamble(ACTION.JOIN).pack() + struct.pack('!I', 2)
        self.assertEqual(mc.o, expected)
        self.assertEqual(mc2.o, expected)
        self.assertEqual(len(s.games), 1)
        self.assertEqual(len(s.matchmaking_queue), 0)

        game = s.games[0]
        self.assertEqual(game.id, 2)
        self.assertEqual(game.host_id, 0x486)
        self.assertEqual(game.guest_id, 0x1134)
        self.assertIs(game.host_session, sess)
        self.assertIs(game.guest_session, sess2)
        self.assertIs(sess.game, game)
        self.assertIs(sess2.game, game)

    def test_join_matchmaking(self):
        s = server.Server()
        mc = MockConn(fd = 1)
        sess = s.new_session(mc)
        sess.user_id = 0x486
        mc2 = MockConn(fd = 1)
        sess2 = s.new_session(mc2)
        sess2.user_id = 0x1134

        self.assertEqual(len(s.games), 0)
        self.assertEqual(len(s.matchmaking_queue), 0)

        mc.i = struct.pack('!BI', ACTION.JOIN, 0x0)
        s.cb_handle(sess)
        sess.flush()

        self.assertEqual(len(s.games), 1)
        self.assertEqual(len(s.matchmaking_queue), 1)

        mc2.i = struct.pack('!BI', ACTION.JOIN, 0x0)
        s.cb_handle(sess2)
        sess2.flush()

        self.assertEqual(len(s.games), 1)
        self.assertEqual(len(s.matchmaking_queue), 0)

        expected = ResponsePreamble(ACTION.JOIN).pack() + struct.pack('!I', 2)
        self.assertEqual(mc.o, expected)
        self.assertEqual(mc2.o, expected)

        game = s.games[0]
        self.assertEqual(game.id, 2)
        self.assertEqual(game.host_id, 0x486)
        self.assertEqual(game.guest_id, 0x1134)
        self.assertIs(game.host_session, sess)
        self.assertIs(game.guest_session, sess2)
        self.assertIs(sess.game, game)
        self.assertIs(sess2.game, game)

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
        mc = MockConn(fd = 1)
        sess = s.new_session(mc)
        sess.user_id = 0x486

        mc2 = MockConn(fd = 1)
        sess2 = s.new_session(mc2)
        sess2.user_id = 0x1134

        # pretend game is already ready
        s.games.append(server.Game(sess, 2))
        s.games[0].guest_id = 0x1134

        mc.i = struct.pack('!BI', ACTION.JOIN, 0x2)
        s.cb_handle(sess)
        sess.flush()

        mc2.i = struct.pack('!BI', ACTION.JOIN, 0x2)
        s.cb_handle(sess2)
        sess2.flush()

        expected = ResponsePreamble(ACTION.JOIN).pack() + struct.pack('!I', 2)
        self.assertEqual(mc.o, expected)
        self.assertEqual(mc2.o, expected)

        game = s.games[0]
        self.assertEqual(game.id, 2)
        self.assertEqual(game.host_id, 0x486)
        self.assertEqual(game.guest_id, 0x1134)
        self.assertIs(game.host_session, sess)
        self.assertIs(game.guest_session, sess2)
        self.assertIs(sess.game, game)
        self.assertIs(sess2.game, game)

unittest.main()
