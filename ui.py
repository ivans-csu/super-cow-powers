import sys
from textwrap import wrap
import client
from collections import deque

_mode: deque['_Mode'] = deque()
events: deque['Event'] = deque()

# EVENTS ===============================================================================================================

# event interface; allows client to queue a sequence of ui actions to be executed in order (FIFO)
class Event():
    def handle(self): pass

# called by client to signal to ui that a new gamestate should be printed
class GamestateEvent(Event):
    def __init__(self, gs: client.GameState, message=''):
        self.gs = gs
        self.message = message

    def handle(self):
        print(self.gs)
        if self.message: _print_msg('@', self.message)

# called by client on join, switch to game mode UI
class JoinEvent(GamestateEvent):
    def __init__(self, game_id:int, gs: client.GameState, message=''):
        self.id = game_id
        self.gs = gs
        self.message = message

    def handle(self):
        set_mode(_mode_game)
        super().handle()
        _print_msg('@', f"Game id is '{self.id}'", 'Write this number down if you wish to reconnect to this game later.')

class GameOverEvent(Event):
    def __init__(self, message: str):
        self.message = _msg('@', message)

    def handle(self):
        print(' MATCH OVER '.center(80, '#'), self.message, sep='\n\n')
        _prev_mode()

# an event which prints a string on handle
class GameOverEvent(Event):
    def __init__(self, message: str):
        self.message = _msg('@', message)

    def handle(self):
        print(' MATCH OVER '.center(80, '#'), self.message, '', sep='\n\n')
        _prev_mode()

# an event which prints a string on handle
class PrintEvent(Event):
    def __init__(self, message: str, lead:str = ''):
        if lead:
            self.message = _msg(lead, message)
        else:
            self.message = _msg(message)

    def handle(self): print(self.message)

# an event which prints a string on handle
class ErrorEvent(Event):
    def __init__(self, message: str, lead:str = '@'):
        self.message = _msg(lead, 'ERROR: ' + message + '!')

    def handle(self): print(self.message)

# MODES & COMMANDS =====================================================================================================

# mode interface; implements a prompt for user input, and a parse to parse user input
class _Mode():
    def prompt(self) -> str: return ''

    def parse(self, cl:'client.Client', args:list[str]):
        pass

class _QuitMode(_Mode):
    def prompt(self) -> str:
        return 'are you sure you want to exit the program? y/n'

    def parse(self, cl, args):
        if args[0].lower() == 'y': exit()
        else: _prev_mode()
_mode_quit = _QuitMode()

# commands are used by MenuModes; they represent a command that the user can select from the menu
class _Command():
    def __init__(self, name:str, desc:str, helptext:list[str] = [], args:str = '', act = None):
        self.name = name.lower()
        self.desc = desc
        if not helptext: helptext = [desc]
        self.helptext = helptext
        self.args = args
        if act: self.act = act
        self.abbrev = ''
        for c in name:
            if c.isupper(): self.abbrev += c.lower()
        if not self.abbrev: self.abbrev = name[0]

    def short_use(self) -> str:
        if self.name[:len(self.abbrev)] == self.abbrev:
            return f'({self.abbrev.upper()}){self.name[len(self.abbrev):]}'
        else:
            return f'({self.abbrev.upper()}) {self.name}'

    def short_help(self) -> str:
        if self.args:
            return f"'{self.abbrev}' {self.args} - {self.name}: {self.desc}"
        else:
            return f"'{self.abbrev}' - {self.name}: {self.desc}"

    def act(self, cl:'client.Client', mode:'_MenuMode', args:list[str]):
        pass

# universal commands ---------------------------------------------------------------------------------------------------
_cmd_quit = _Command('quit',
    desc='exit the program',
    act = lambda *_: _mode.append(_mode_quit)
)

def _cmd_help_act(cl:'client.Client', mode:'_MenuMode', args):
    if len(args) > 1 and args[1] in mode.map:
        cmd = mode.map[args[1]]
        _print_msg(f"HELP: '{args[1]}'")
        _prindent(1, 'USAGE:')
        _prindent(2, *[f'{key} {cmd.args}' for key in (cmd.abbrev, cmd.name)])
        _prindent(1, 'DESCRIPTION:')
        _prindent(2, *mode.map[args[1]].helptext)
    else:
        _print_msg('HELP:')
        _prindent(1, *(cmd.short_help() for cmd in mode.cmds))
    sys.stdout.write('\n')
_cmd_help = _Command('help',
    desc='print this list, or get more help for a specific command',
    helptext=['Prints a list of commands and their usage, or provides specific help for a command if one is specified.'],
    act = _cmd_help_act,
    args = '[command]'
)

# menumode provides a standardized prompt with a list of commands, standardized help, standardized parsing
# essentially a list of objects implementing the 'Command' interface
class _MenuMode(_Mode):
    def __init__(self, name:str, commands:list[_Command]):
        self.name = name
        self.cmds:list[_Command] = [_cmd_help, *commands, _cmd_quit]
        self.map = {cmd.abbrev : cmd for cmd in self.cmds}
        self.map |= {cmd.name : cmd for cmd in self.cmds}

    def prompt(self) -> str:
        return f"\r{self.name.upper()} MODE: {', '.join(cmd.short_use() for cmd in self.cmds)}"

    def parse(self, cl:'client.Client', args:list[str]):
        cmd = args[0].lower()
        if not cmd in self.map:
            _print_msg(f"NO SUCH COMMAND: '{cmd}'")
        else:
            self.map[cmd].act(cl, self, args)

# game mode ------------------------------------------------------------------------------------------------------------

class _GameMenuMode(_MenuMode):
    # pseudo-command; no abbrev, we parse input as a move coordinate if it matches no other commands
    class MoveCommand(_Command):
        def __init__(self):
            self.abbrev = ''
            self.name = ''
            pass

        def short_use(self) -> str:
            return '(<row><column>) move'

        def short_help(self) -> str:
            return 'move: enter a coordinate on which to place a piece in the format <column><row>.  EG: a1, h8, etc'

        def act(self, cl:'client.Client', mode:'_MenuMode', args:list[str]):
            cmd = args[0].lower()
            def bad():
                _print_msg(f"'{cmd}' is not a command, and cannot be understood as a move coordinate")

            coord = cmd.encode()

            if len(coord) != 2: bad()
            elif not (b'a'[0] <= coord[0] <= b'h'[0]): bad()
            elif not (b'0'[0] <= coord[1] <= b'9'[0]): bad()
            else:
                x = coord[0] - b'a'[0]
                y = coord[1] - b'1'[0]
                cl.move(x, y)

    cmd_move = MoveCommand()

    def __init__(self):
        _MenuMode.__init__(self, 'game play', commands=[self.cmd_move])

    def parse(self, cl:'client.Client', args:list[str]):
        cmd = args[0].lower()
        if not cmd in self.map:
            self.cmd_move.act(cl, self, args)
        else:
            self.map[cmd].act(cl, self, args)
_mode_game = _GameMenuMode()

# join mode ------------------------------------------------------------------------------------------------------------

class _Mode_Join(_Mode):
    def prompt(self) -> str:
        return 'JOIN:\n' + _indent('>', "'m' to queue for matchmaking,",
           "'p' to create a private match",
           "a game number to (re)join a specific match",
           'any other text to abort'
        ) + '\n'

    def parse(self, cl, args):
        cmd = args[0].lower()
        if cmd == 'm':
            cl.join(0)
        elif cmd == 'p':
            cl.join(1)
        else:
            try:
                assert cmd
                game = int(cmd)
                cl.join(game)
            except:
                _print_msg(f"invalid selection: '{cmd}'")
        _prev_mode()
_mode_join = _Mode_Join()

# normal mode ----------------------------------------------------------------------------------------------------------

def _cmd_join_act(cl, _, args):
    _mode.append(_mode_join)
    args = args[1:]
    if args: _mode[-1].parse(cl, args)
_mode_normal = _MenuMode('normal',
    commands=[
        _Command('join',
            desc='connect to a game',
            args = '[option]',
            helptext=['Connect to a game.',
                'If no arguments are provided, a list of options are provided and an interactive prompt allows you to choose one.',
                'If an argument is provided, join treats it as though you had interactively selected that option'
            ],
            act = _cmd_join_act
        ),
        _Command('rejoin',
            desc='reconnect to the most recent game',
            act = lambda cl, *_: cl.rejoin()
        ),
    ]
)

# UI FUNCTIONS =========================================================================================================

def set_mode(mode: _Mode):
    _mode.append(mode)

def _prev_mode():
    _mode.pop()

# display a helpful onscreen dialog prompt for command keywords
def prompt():
    sys.stdout.write(_mode[-1].prompt() + ' :> ')
    sys.stdout.flush()

# take user input and execute the corresponding command
def parse(cl:'client.Client', input:str):
    args = input.split()
    if not len(args): return
    _mode[-1].parse(cl, args)
    prompt()

def push_event(event): events.append(event)

# clear the current line from the terminal
def clear_line():
    sys.stdout.write('\r\033[J')

def handle_events():
    if events:
        clear_line()
        while events:
            events.popleft().handle()
        prompt()

# helper functions -----------------------------------------------------------------------------------------------------

def _msg(*args) -> str:
    if len(args) > 1 and type(args[0]) is str and len(args[0]) == 1:
        lead = args[0]
        args = args[1:]
    else:
        lead = '~'

    if len(args) > 1 and type(args[0]) is int:
        nspace = args[0]
        args = args[1:]
    else:
        nspace = 0

    lead = f'{" "*nspace} {lead} '
    cont = " " * (nspace + 3)

    subargs = []
    for arg in args:
        for a in arg.split('\n'):
            subargs.extend(wrap(a.strip(), 80 - len(lead)))

    line_one = lead + subargs[0]
    return '\n'.join((line_one, *(cont + arg for arg in subargs[1:])))

def _indent(*args) -> str:
    if len(args) > 1 and type(args[0]) == str and len(args[0]) == 1:
        lead = args[0]
        args = args[1:]
    else:
        lead = '*'

    if len(args) > 1 and type(args[0]) is int:
        nspace = 2 * args[0]
        args = args[1:]
    else:
        nspace = 0

    return '\n'.join(_msg(lead, nspace, arg) for arg in args)

def _print_msg(*args):
    print(_msg(*args))

def _prindent(*args):
    print(_indent(*args))

# INIT =================================================================================================================
_mode.append(_mode_normal)
