from enum import IntEnum
import struct

class ACTION(IntEnum):
    HELLO = 0
    JOIN = 1
    MOVE = 2

class PUSH(IntEnum):
    CONNECT = 0
    DCONNECT = 1
    GAMESTATE = 2

class STATUS(IntEnum):
    OK = 0
    BAD_FORMAT = 1
    ILLEGAL = 2
    INVALID = 3
    UNSUPPORTED = 4
    UNAUTHORIZED = 5

class ResponsePreamble:
    def __init__(self, action: int, status: STATUS = STATUS.OK):
        assert(status < 128)
        self.action = action
        self.status = status

    @staticmethod
    def unpack(msg: bytes):
        status, action = struct.unpack('BB', msg)
        return ResponsePreamble(action, status)

    def pack(self) -> bytes:
        return bytes((self.status, self.action))

class PushPreamble:
    def __init__(self, type: PUSH):
        assert(type < 32768)
        self.type = type

    @staticmethod
    def unpack(msg: bytes):
        return PushPreamble(struct.unpack('!H', msg)[0])

    def pack(self) -> bytes:
        return struct.pack('!H', self.type)
