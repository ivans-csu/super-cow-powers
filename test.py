import unittest

import server
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
            self.assertEqual(x.status() & 128, 0)
            self.assertEqual(x.status(), status)

    def test_action(self):
        for action in ACTION:
            x = ResponsePreamble(action)
            self.assertEqual(x.action(), action)

class TestPushPreamble(unittest.TestCase):
    def test_typebit_type(self):
        for type in PUSH:
            x = PushPreamble(type)
            self.assertEqual(x.type() & 32768, 0)
            self.assertEqual(x.type(), type)

    def test_endian(self):
        x = PushPreamble(PUSH.GAMESTATE)
        self.assertEqual(x.raw, bytes((0x0, 0x2)))

class TestServerHello(unittest.TestCase):
    def test_ok(self):
        s = server.Server()
        mc = MockConn(fd = 1)
        mc.i = struct.pack('!BHI', ACTION.HELLO, s.max_version, 0x486)

        session = s.new_session(mc)
        s.cb_handle(session)
        session.flush()

        self.assertEqual(mc.o, struct.pack('!BBH', STATUS.OK, ACTION.HELLO, s.max_version))
        self.assertEqual(s.sessions[1], session)

    def test_multiple(self):
        s = server.Server()
        mc = MockConn(fd = 1)
        mc2 = MockConn(fd = 2)
        mc.i = struct.pack('!BHI', ACTION.HELLO, s.max_version, 0x486)
        mc2.i = struct.pack('!BHI', ACTION.HELLO, s.max_version, 0x1134)

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
        mc.i = struct.pack('!BHI', ACTION.HELLO, s.max_version + 1, 0x486)
        session = s.new_session(mc)

        s.cb_handle(session)
        session.flush()

        self.assertEqual(mc.o, struct.pack('!BBH', STATUS.OK, ACTION.HELLO, s.max_version))

    def test_too_old(self):
        s = server.Server()
        mc = MockConn(fd = 1)
        mc.i = struct.pack('!BHI', ACTION.HELLO, s.min_version, 0x486)
        session = s.new_session(mc)

        s.min_version = s.max_version = s.max_version + 1

        s.cb_handle(session)
        session.flush()

        self.assertEqual(mc.o, struct.pack('!BBH', STATUS.UNSUPPORTED, ACTION.HELLO, s.min_version))

unittest.main()
