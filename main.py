import os
import sys
from antlr4 import InputStream, CommonTokenStream
from antlr4.atn.PredictionMode import PredictionMode
from LuaLexer import LuaLexer
from LuaParser import LuaParser
from EvalVisitor import EvalVisitor


def main():
    sys.stdin.reconfigure(encoding='utf-8')
    codigo_lua = sys.stdin.read()

    input_stream = InputStream(codigo_lua)
    lexer = LuaLexer(input_stream)
    stream = CommonTokenStream(lexer)
    parser = LuaParser(stream)

    parser._interp.predictionMode = PredictionMode.SLL
    try:
        tree = parser.start_()
    except Exception:
        stream.reset()
        parser.reset()
        parser._interp.predictionMode = PredictionMode.LL
        tree = parser.start_()

    visitor = EvalVisitor()
    visitor.visit(tree)
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()