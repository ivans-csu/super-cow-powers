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

unittest.main()
