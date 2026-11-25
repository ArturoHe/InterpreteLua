import sys
from typing import TextIO

from antlr4 import InputStream, Lexer

LBRACKET = ord('[')
RBRACKET = ord(']')
EQUALS = ord('=')
NEWLINE = ord('\n')


class LuaLexerBase(Lexer):
    def __init__(self, input: InputStream, output: TextIO = sys.stdout):
        super().__init__(input, output)
        self.start_line = 0
        self.start_col = 0

    def HandleComment(self):
        self.start_line = self.line
        self.start_col = self.column - 2
        stream = self._input

        # comentario largo
        if stream.LA(1) == LBRACKET:
            sep = self.skip_sep(stream)
            if sep >= 2:
                self.read_long_string(stream, sep)
                return

        while stream.LA(1) != NEWLINE and stream.LA(1) != -1:
            self._interp.consume(stream)

    def read_long_string(self, stream, sep):
        done = False
        self._interp.consume(stream)

        while not done:
            c = stream.LA(1)
            if c == -1:
                done = True
            elif c == RBRACKET:
                if self.skip_sep(stream) == sep:
                    self._interp.consume(stream)
                    done = True
            elif stream.LA(1) == -1:
                done = True
            else:
                self._interp.consume(stream)

    def skip_sep(self, stream):
        count = 0
        first = stream.LA(1)
        self._interp.consume(stream)

        while stream.LA(1) == EQUALS:
            self._interp.consume(stream)
            count += 1

        if stream.LA(1) == first:
            count += 2
        elif count == 0:
            count = 1
        else:
            count = 0

        return count

    def IsLine1Col0(self):
        return self._input.index == 1
