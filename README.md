# Networked Othello

This is a simple Othello/Reversi game implemented using Python and sockets.

**How to play:**
1. **Start the server:** Run `python server.py <listen IP address> <port>`
2. **Connect clients:** Run `python client.py <server IP address> <port>`.
3. **Play the game:** Players take turns entering their moves. When all squares contain pieces, the player with the most pieces on the board wins.

**Technologies used:**
* Python
* Sockets

**Additional resources:**
* [Python Documentation](https://docs.python.org/3/)

# Super Cow Powers Othello Protocol Specification v0 (INDEV)
## Preface
  - The overall model is that of "dumb clients" and an "oracle server"; The server is the arbiter of truth and the enforcer of rules, clients may only request state changes
  - two TCP connections are established between two players and a central server
  - TCP stays alive so the server can push state messages to the clients

  - multibyte words are packed big-endian
  - Several protocol message types contain fields which are not aligned to octet boundaries.  Client and server implementations **MUST NOT** reorder these fields based on the endianness of the machine on which they are running

## Potential future additions
  - user authentication
  - user nicknames
  - ingame chat

## Notation & Terminology
### User
  - a **user** is an identity claimed by a connected client
  - users are uniquely identified with a **user id** number from 0 to 2^32-1

### Game
  - a **game** is an association between two users to play one match of Othello, and the current state of that match
  - games are uniquely identified with a **game id** number from 2 to 2^32-1
  - games have **player**s: A user who is participating in that game
  - server maintains a list of **ready**, **unready**, and **completed** games.
    - **ready**: game has two players and may proceed
    - **unready**: game has only one player and cannot proceed
    - **completed**: game has finished, no moves are permitted
  - games have a **host** and a **guest** player:
    - **host:** The player who created a game (the first player)
    - **guest:** The player who joined a game (the second player)

### Message
  - information is exchanged between client and server via **messages**
  - a **preamble** before every message specifies the type of message to be decoded
    - the preamble is/contains a number which maps to a symbolic name via an enum, this is not a plaintext protocol
  - messages are composed of fixed-width **fields** which represent individual data elements
  - fields are packed directly adjacently without any padding
  - fields may be octets, multibyte words, or arbitrary data
  - clients send **action** request messages
  - server sends **state** messages
    - state messages must be one of **action status** or **state push**
      - **action status**: a state message responding to an action message from the client
      - **state push**: a state message pushed to the client, independent of their actions
  - action status message fields may be **conditional**: they may not be present depending on the status code returned

### Session
  - a **session** is an association between a TCP connection, a user, and 1 or fewer games
  - a session is required in order to send any message other than HELLO (the session establishment message)
  - a session may **join** a game
    - the attached client will receive state messages for the game
    - the attached client can send game-specific action messages
  - a session may participate in at most one game at a time
  - a user may not have more than one session joined to the same game
  - a user may have an indefinite number of simultaneous sessions

### Message Notation in This Document:
- each line represents a field, where earlier fields correspond to lower addresses.
- when fields do not align to octet boundaries, earlier fields correspond to more significant (leftward) bits

#### unsigned word
(field name) : (width in bits)

#### signed word
(field name) : s(width in bits)

#### arbitrary data
(field name) : d(width in bits)

#### conditional field
?(field name) : (prefix)(width in bits) : IF (condition)

## Overview
### Client Packets
  - the client sends request packets to the server to perform an **action**
  - The preamble specifies the action type; Action type determines the type of action message; the number of bytes to be read from the TCP stream, and how those data are formatted
  - most actions expect a response from the server via an **action status** message, which includes the success or failure status of the requested action, as well as containing any needed game state should the action succeed
    - any number of state messages may be received following an action request before an action response is received
    - Multiple action requests of the same type may be pending a response at a given time; The above rule applies, however, responses to these messages shall arrive in the order in which they were sent

### Server Packets
  - the server sends state packets to the client to indicate **status** of their request and/or current game **state**
  - The preamble encodes the response type; Response type must be either an **action status** or a **state push**
    - **Action status** preamble specifies the action type to which it is a response, and the success or failure status code of that action.  The message body may be empty, or contain state information depending on the status code.  The client infers the number of bytes to be read from the stream, and the format of the corresponding message body, using the action number and the status.
    - **State push** preamble specifies the type of state message; it determines the number of bytes to be read from the TCP stream, and how those data are formatted
      - a state push may occur at any time, a compliant client must support this
  - all state packets convey the authoritative state of the session and/or the active game associated with this session

### Games
  - users may request to be connected to a random match via the matchmaking system
  - users may request to create a private match which will not be added to matchmaking
  - users may connect to specific games by id provided that they are either unready, or the user is a player of that game
  - The host is always white and the guest is always black.  Since black always moves first, the guest always moves first;  Therefore, the game cannot begin until a guest user joins.
  - players may disconnect and reconnect to a game at any time, the server retains the state of the game indefinitely.

### Matchmaking
  - server maintains a FIFO queue of unready games
  - When a player requests matchmaking, an unready game is dequeued if extant, or a new unready game is created and queued.  The player is then added to the game, which is readied if applicable.

## Status Codes
### 0 - OK

### 1 - BAD_FORMAT
- the message contents cannot be understood

### 2 - ILLEGAL
- the request is for a resource which does not exist
- the request is for a move which violates the rules

### 3 - INVALID
- the action cannot be performed at this time
  - all actions yield this status if a session is not yet established
  - all game actions yield this status if the session is not joined to a game

### 4 - UNSUPPORTED
- the action type is not implemented by the server
- the server does not support any protocol versions the client will accept

### 5 - UNAUTHORIZED
- this user does not have permission to act on this resource

## Actions & Action Responses
### 0 - HELLO
  - negotiate protocol version between client and server.
  - identify the user to the server
  - establish a session

  - HELLO message format shall never change in subsequent protocol revisions

#### request
  - advertises their maximum supported protocol version
  - specifies their user id

#### response
  - status UNSUPPORTED and the minimum protocol version it supports  
  *OR*
  - status INVALID and user id if a session already exists on socket  
  *OR*
  - specifies the protocol version it will use going forwards

  - if the client does not support the server's advertised protocol version, it simply closes the connection

### 1 - JOIN
  - associate the user with a game via matchmaking or specified game id
  - (re)connect the active session to the game

#### request
  - game id field
    - 0 - use matchmaking
      - if unready games are queued for matchmaking, add this player as a guest to the oldest game and dequeue it.
      - if no unready games are queued, create an unready game with this player as the host, and queue it.
    - 1 - create private match
      - An unready game is created, but not added to matchmaking; A guest can only connect with the specific game id.
    - other: join a specified game
        - id corresponds to an unready game:
          - join and ready this unready game
          - dequeue this game if it is in matchmaking.  (this ideally won't occur ever, but must be handled correctly)
        - id corresponds to a ready game:
          - rejoin this game iff user is one of the players

#### response
  - status UNAUTHORIZED if game is ready and user isn't one of the players  
*OR*
  - status INVALID if game does not exist  
*OR*
  - game id
  - complete game state

### 2 - MOVE
  - make a move on the session's active game

#### request
  - coordinate to place a piece on

#### response
  - complete GAMESTATE  
  AND
  - status ILLEGAL if the move violates game rules  
  OR
  - status INVALID if it is not the player's turn  

### 255 - UNSUPPORTED
  - client received a state packet which it does not recognize or cannot understand
  - TBD: serverside remediation

## State Pushes

### 0 - CONNECT
  - inform the host that a guest has joined, and the game is now ready  
  *OR*
  - inform a player that their opponent's session has reconnected to the game

### 1 - DCONNECT
  - inform a player that their opponent's session has disconnected

### 2 - GAMESTATE
  - push indicates the opponent has moved
  - full snapshot of game's state
    - inform the client if they can move
    - inform the client of their color
    - inform the client of the turn number
    - inform the client of the board state
  - indicate if the game is complete
    - if the turn number is odd, the player is black, but they cannot move, the match must be complete
    - if the turn number is even, the player is white, but they cannot move, the match must be complete

## Message Specification
### Client Packet Structure:
  action preamble : 8  
  message body data : *undefined*

### Server Packet Structure:
  state preamble : d16  
  message body data : *undefined*

  - preamble is interpreted differently depending on the value of the most significant bit

#### response type 0 - action status
  response type : 1  
  status : 7  
  action type : 8

#### response type 1 - state push
  response type : 1  
  state message type : 15

### Action / Action Response Messages
#### 0 - HELLO
##### request
  max version number : 16  
  user id : 32
##### response
  ?version number : 16 : status != INVALID  
  ?user id : 32 : status = INVALID  

#### 1 - JOIN
##### request
  game id : 32
##### response
  ?game id : 32 : IF status = OK  
  ?GAMESTATE message : d136 : IF status = OK

#### 2 - MOVE
##### request
  to coordinate X : 4  
  to coordinate Y : 4

##### response
  GAMESTATE message : d136

### State Push Messages
#### 0 - CONNECT
  (empty)

#### 1 - DCONNECT
  (empty)

#### 2 - GAMESTATE
  color : 1  
  can move : 1  
  turn number : 6  
  board state : d128

  - 0 is black, 1 is white
  - board state
    - array of 64 2-bit numbers representing spaces of the board from top left to bottom right
      - packed as though each square were a field; MSB = earlier spaces
    - 0 - empty
    - 1 - black
    - 2 - white
    - 3 - (unused)
