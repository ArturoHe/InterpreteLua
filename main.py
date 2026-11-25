import os
import sys

from antlr4 import CommonTokenStream, InputStream
from antlr4.atn.PredictionMode import PredictionMode

from EvalVisitor import EvalVisitor
from LuaLexer import LuaLexer
from LuaParser import LuaParser


def parse(source):
    tokens = CommonTokenStream(LuaLexer(InputStream(source)))
    parser = LuaParser(tokens)

    # SLL primero; si falla, LL
    parser._interp.predictionMode = PredictionMode.SLL
    try:
        return parser.start_()
    except Exception:
        tokens.reset()
        parser.reset()
        parser._interp.predictionMode = PredictionMode.LL
        return parser.start_()


def main():
    sys.stdin.reconfigure(encoding='utf-8')
    program = sys.stdin.read()

    EvalVisitor().visit(parse(program))

    sys.stdout.flush()
    os._exit(0)


if __name__ == '__main__':
    main()
