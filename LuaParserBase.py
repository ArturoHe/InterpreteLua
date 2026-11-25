import sys

from antlr4 import Parser, TokenStream


class LuaParserBase(Parser):
    debug = False

    def __init__(self, input: TokenStream, output=sys.stdout):
        super().__init__(input, output)

    def IsFunctionCall(self):
        # a.b no es llamada, a.b(x) sí
        if self._input.LT(1).type != self.NAME:
            return False
        return self._input.LT(2).type != self.OP
