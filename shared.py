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
        self.raw = bytes((status, action))

    def status(self) -> STATUS: return STATUS(self.raw[0])

    def action(self) -> ACTION: return ACTION(self.raw[1])

class PushPreamble:
    def __init__(self, type: PUSH):
        assert(type < 32768)
        self.raw = struct.pack('!H', type)

    def type(self) -> PUSH: return PUSH(struct.unpack('!H', self.raw)[0])
