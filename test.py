import unittest

import server
from shared import *

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

unittest.main()
