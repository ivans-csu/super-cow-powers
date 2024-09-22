# Statement of Work

## Project Title 

Networked Othello
 
## Team

Super Cow Powers (Ben Perry & Ivan Stanton)

## Objective

To implement a networked Othello/Reversi game in Go with a command line interface.

## Scope

Inclusions:
* A simple client-server application protocol for exchanging moves in and synchronizing turn-based games
* An implementation of this protocol using the Go programming language and its network stack
* A client for this protocol which implements the rules of Othello.

Exclusions:
* Clients for other games
* Server functionality for non-turn-based games
* Anti-cheat functionality

## Deliverables

* Documentation of the game message protocol
* A simple server program which can relay messages between clients
* An implementation of Othello rules
* A client script that uses a CLI
* A presentation outlining the project and its progress

## Timeline

Key milestones:
* Server complete
* Client can connect to server
* Client can synchronize with another client
* Client is fully functional with a terminal user interface

Task breakdown:
* Protocol design (8 hours)
* Server programming (8 hours)
* Basic client programming (4 hours)
* TUI programming (2 hours)
* Client-server tests (4 hours)
* Game rules implementation (12 hours)
* Game tests (4 hours)

## Technical requirements

Hardware:
* Three computers (may be virtual machines), each with a network card

Software:
* A Unix-like operating system
* Go standard libraries

## Assumptions

* The CSU servers can be used for development and testing of the server protocol.
* Go's standard network libraries will be sufficient to implement this program.

## Roles and Responsibilities

* Both team members will share programming and design responsibilities according to their availability and skills.
* Major decisions will be agreed upon by both team members.

## Communication plan

* Decision-making will be done over Discord messages and during conversations after class.
* Longer meetings will be planned ahead of time and most likely take place over Discord.
