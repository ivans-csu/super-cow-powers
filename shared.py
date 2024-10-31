from enum import IntEnum
from typing import Self
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

    def __eq__(self, other):
        return self.action == other.action and self.status == other.status

    def __repr__(self):
        action = ACTION(self.action).name if self.action in iter(ACTION) else self.action
        status = STATUS(self.status).name if self.status in iter(STATUS) else self.status
        return f'ResponsePreamble(action={action}, status={status})'

    @staticmethod
    def unpack(msg: bytes) -> Self:
        msg = msg[:2]
        status, action = struct.unpack('BB', msg)
        return ResponsePreamble(action, status)

    def pack(self) -> bytes:
        return bytes((self.status, self.action))

class PushPreamble:
    def __init__(self, type: PUSH):
        assert(type < 32768)
        self.type = type

    def __eq__(self, other):
        return self.type == other.type

    def __repr__(self):
        type = PUSH(self.push).name if self.push in iter(PUSH) else self.push
        return f'PushPreamble(type={type})'

    @staticmethod
    def unpack(msg: bytes) -> Self:
        msg = msg[:2]
        return PushPreamble(struct.unpack('!H', msg)[0])

    def pack(self) -> bytes:
        return struct.pack('!H', self.type)

class SQUARE(IntEnum):
    EMPTY = 0
    BLACK = 1
    WHITE = 2

class BoardState:
    _MASK = 0x3
    _MAP = b'-@O'

    def __init__(self, new=True):
        self.state: list[list[SQUARE]] = [[SQUARE.EMPTY] * 8 for _ in range(8)]
        if new:
            self.state[3][3] = self.state[4][4] = SQUARE.WHITE
            self.state[3][4] = self.state[4][3] = SQUARE.BLACK

    def __repr__(self):
        output = bytearray(128)
        i = 0
        for row in range(8):
            for col in range(8):
                output[i] = BoardState._MAP[self.state[row][col]]
                output[i+1] = 0x20 # ' '
                i += 2
            output[i-1] = 0x0A # '\n'
        return output.decode('ascii')

    @staticmethod
    def unpack(message: bytes) -> 'BoardState':
        board = BoardState(new=False)
        octet = 0
        for row in range(8):
            for offset in (0,4):
                pack = message[octet]
                for col in range(3,-1,-1):
                    board.state[row][col + offset] = SQUARE(pack & BoardState._MASK)
                    pack >>= 2
                octet += 1
        return board

    def pack(self) -> bytes:
        output = bytearray(16)
        octet = 0
        for row in range(8):
            for offset in (0,4):
                pack = 0
                for col in range(4):
                    pack <<= 2
                    pack |= self.state[row][col + offset]
                output[octet] = pack
                octet += 1
        return bytes(output)
