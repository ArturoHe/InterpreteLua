import math
import re

from LuaParser import LuaParser
from LuaParserVisitor import LuaParserVisitor


class BreakSignal(Exception):
    pass


class GotoSignal(Exception):
    def __init__(self, label):
        self.label = label


class ReturnSignal(Exception):
    def __init__(self, values):
        self.values = values


class YieldSignal(Exception):
    def __init__(self, values):
        self.values = values


class LuaError(Exception):
    def __init__(self, value=None):
        self.value = value
        super().__init__(str(value))


ESCAPES = {
    'a': '\a', 'b': '\b', 'f': '\f', 'n': '\n', 'r': '\r',
    't': '\t', 'v': '\v', '\\': '\\', '"': '"', "'": "'", '\n': '\n',
}


class LuaTable:
    def __init__(self):
        self.data = {}
        self.metatable = None

    def get(self, key):
        if key in self.data:
            return self.data[key]
        meta = self.metatable
        if isinstance(meta, LuaTable):
            index = meta.data.get('__index')
            if isinstance(index, LuaTable):
                return index.get(key)
            if index is not None:
                # si __index es función, la llama el visitor
                return ('__call_index__', index, self, key)
        return None

    def set(self, key, value):
        meta = self.metatable
        if key not in self.data and isinstance(meta, LuaTable):
            newindex = meta.data.get('__newindex')
            if isinstance(newindex, LuaTable):
                return newindex.set(key, value)
            if newindex is not None:
                return ('__call_newindex__', newindex, self, key, value)

        if value is None:
            self.data.pop(key, None)
        else:
            self.data[key] = value
        return None

    def length(self):
        # ojo: desde 1 y corta en el primer hueco
        n = 0
        while n + 1 in self.data:
            n += 1
        return n

    def __repr__(self):
        return f'table: {id(self)}'


class LuaFunction:
    def __init__(self, params, body, scopes, vararg=False):
        self.params = params
        self.body = body
        self.scopes = list(scopes)
        self.vararg = vararg


class LuaThread:
    def __init__(self, func, vm):
        self.func = func
        self.vm = vm
        self.status = 'suspended'
        self.history = []
        self.yields = 0

    def __repr__(self):
        return f'thread: {id(self)}'


class LuaUserdata:
    def __init__(self, name='userdata'):
        self.name = name

    def __repr__(self):
        return f'userdata: {id(self)}'


ARITH_MM = {
    '+': '__add', '-': '__sub', '*': '__mul', '/': '__div',
    '//': '__idiv', '%': '__mod', '^': '__pow',
    '&': '__band', '|': '__bor', '~': '__bxor', '<<': '__shl', '>>': '__shr',
}


class EvalVisitor(LuaParserVisitor):
    def __init__(self):
        self.scopes = [{}]
        self.current_coro = None
        self._load_stdlib()

    def _load_stdlib(self):
        g = self.scopes[0]
        g.update({
            'print': self._print,
            'tonumber': self._tonumber,
            'tostring': self._tostring,
            'type': self._type,
            'ipairs': self._ipairs,
            'pairs': self._pairs,
            'error': self._error,
            'pcall': self._pcall,
            'xpcall': self._xpcall,
            'setmetatable': self._setmetatable,
            'getmetatable': self._getmetatable,
            'rawget': lambda t, k: t.data.get(k) if isinstance(t, LuaTable) else None,
            'rawset': self._rawset,
            'collectgarbage': self._collectgarbage,
            'assert': self._assert,
            'next': self._next,
            'select': self._select,
            'load': self._load,
            'loadstring': self._load,
            'unpack': self._table_unpack,
        })

        mathlib = LuaTable()
        mathlib.data.update({
            'abs': abs, 'ceil': math.ceil, 'floor': math.floor,
            'sqrt': math.sqrt, 'sin': math.sin, 'cos': math.cos,
            'tan': math.tan, 'log': math.log, 'exp': math.exp,
            'max': max, 'min': min, 'pi': math.pi, 'huge': float('inf'),
        })
        g['math'] = mathlib

        stringlib = LuaTable()
        stringlib.data.update({
            'len': len,
            'lower': lambda s: str(s).lower(),
            'upper': lambda s: str(s).upper(),
            'rep': lambda s, n, sep='': str(sep).join([str(s)] * int(n)),
            'reverse': lambda s: str(s)[::-1],
            'sub': self._str_sub,
            'find': self._str_find,
            'gsub': self._str_gsub,
            'byte': lambda s, i=1: ord(str(s)[int(i) - 1]) if 1 <= int(i) <= len(str(s)) else None,
            'char': lambda *codes: ''.join(chr(int(c)) for c in codes),
        })
        g['string'] = stringlib

        tablelib = LuaTable()
        tablelib.data.update({
            'insert': self._table_insert,
            'remove': self._table_remove,
            'pack': self._table_pack,
            'unpack': self._table_unpack,
        })
        g['table'] = tablelib

        iolib = LuaTable()
        iolib.data.update({
            'write': self._io_write,
            'stdin': LuaUserdata('stdin'),
            'stdout': LuaUserdata('stdout'),
            'stderr': LuaUserdata('stderr'),
        })
        g['io'] = iolib

        corolib = LuaTable()
        corolib.data.update({
            'create': lambda fn: LuaThread(fn, self),
            'resume': self._coresume,
            'yield': self._coyield,
            'status': lambda th: th.status if isinstance(th, LuaThread) else 'dead',
            'running': lambda: [self.current_coro, self.current_coro is None],
        })
        g['coroutine'] = corolib

        globals_tbl = LuaTable()
        globals_tbl.data = g
        g['_G'] = globals_tbl

    def _to_str(self, value):
        if isinstance(value, LuaTable) and isinstance(value.metatable, LuaTable):
            handler = value.metatable.data.get('__tostring')
            if handler is not None:
                return str(self.call(handler, [value]))
        if value is None:
            return 'nil'
        if value is True:
            return 'true'
        if value is False:
            return 'false'
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)

    def _print(self, *args):
        print('\t'.join(self._to_str(a) for a in args))

    def _io_write(self, *args):
        print(''.join(self._to_str(a) for a in args), end='')

    def _tonumber(self, value, base=10):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
        if not isinstance(value, str):
            return None

        text = value.strip()
        lower = text.lower()
        try:
            if base != 10:
                return int(text, int(base))
            if lower.startswith(('0x', '-0x')):
                if '.' in text or 'p' in lower:
                    return float.fromhex(text)
                return int(text, 16)
            return float(text) if ('.' in text or 'e' in lower) else int(text)
        except ValueError:
            return None

    def _tostring(self, value):
        return self._to_str(value)

    def _type(self, value):
        if value is None:
            return 'nil'
        if isinstance(value, bool):
            return 'boolean'
        if isinstance(value, (int, float)):
            return 'number'
        if isinstance(value, str):
            return 'string'
        if isinstance(value, LuaTable):
            return 'table'
        if isinstance(value, LuaThread):
            return 'thread'
        if isinstance(value, LuaUserdata):
            return 'userdata'
        if isinstance(value, LuaFunction) or callable(value):
            return 'function'
        return 'userdata'

    def _table_insert(self, tbl, pos_or_val, value=None):
        if value is None:
            tbl.set(tbl.length() + 1, pos_or_val)
            return

        pos = int(pos_or_val)
        for i in range(tbl.length(), pos - 1, -1):
            tbl.set(i + 1, tbl.get(i))
        tbl.set(pos, value)

    def _table_remove(self, tbl, pos=None):
        n = tbl.length()
        if n == 0:
            return None

        pos = n if pos is None else int(pos)
        removed = tbl.get(pos)
        for i in range(pos, n):
            tbl.set(i, tbl.get(i + 1))
        tbl.set(n, None)
        return removed

    def _ipairs(self, tbl):
        def step(t, i):
            i = int(i) + 1
            value = t.get(i)
            return None if value is None else [i, value]

        return [step, tbl, 0]

    def _pairs(self, tbl):
        # copia de claves, por si cambian durante el for
        keys = list(tbl.data.keys())

        def step(t, key):
            if not keys:
                return None
            if key is None:
                nxt = keys[0]
            else:
                try:
                    pos = keys.index(key) + 1
                except ValueError:
                    return None
                if pos >= len(keys):
                    return None
                nxt = keys[pos]
            return [nxt, t.get(nxt)]

        return [step, tbl, None]

    def _str_sub(self, s, i, j=None):
        s = str(s)
        n = len(s)
        i = int(i)
        j = n if j is None else int(j)

        if i < 0:
            i = max(1, n + i + 1)
        if j < 0:
            j = n + j + 1
        i = max(1, i)
        j = min(n, j)

        if i > j:
            return ''
        return s[i - 1:j]

    def _str_find(self, s, pattern, init=1):
        s = str(s)
        pattern = str(pattern)
        idx = s.find(pattern, int(init) - 1)
        if idx == -1:
            return None
        return [idx + 1, idx + len(pattern)]

    def _str_gsub(self, s, pattern, repl, n=None):
        s = str(s)
        pattern = str(pattern)
        repl = str(repl)
        limit = 0 if n is None else int(n)

        if limit == 0:
            return [s.replace(pattern, repl), s.count(pattern)]
        return [s.replace(pattern, repl, limit), min(limit, s.count(pattern))]

    def _unwind(self, depth):
        # pcall deja entornos a medio abrir
        del self.scopes[depth:]

    def _error(self, msg='error', level=1):
        raise LuaError(msg)

    def _pcall(self, func, *args):
        depth = len(self.scopes)
        try:
            result = self.call(func, list(args))
            return [True] + (result if isinstance(result, list) else [result])
        except LuaError as err:
            self._unwind(depth)
            return [False, err.value]
        except Exception as err:
            self._unwind(depth)
            return [False, str(err)]

    def _xpcall(self, func, msgh, *args):
        depth = len(self.scopes)
        try:
            result = self.call(func, list(args))
            return [True] + (result if isinstance(result, list) else [result])
        except LuaError as err:
            self._unwind(depth)
            return [False, self.call(msgh, [err.value])]
        except Exception as err:
            self._unwind(depth)
            return [False, self.call(msgh, [str(err)])]

    def _setmetatable(self, tbl, mt):
        if isinstance(tbl, LuaTable):
            tbl.metatable = mt
        return tbl

    def _getmetatable(self, obj):
        return obj.metatable if isinstance(obj, LuaTable) else None

    def _rawset(self, tbl, k, v):
        if isinstance(tbl, LuaTable):
            if v is None:
                tbl.data.pop(k, None)
            else:
                tbl.data[k] = v
        return tbl

    def _live_ids(self):
        # gc casero: alcanzables desde los entornos
        live = set()
        pending = [v for scope in self.scopes for k, v in scope.items() if k != '__to_close__']

        while pending:
            obj = pending.pop()
            if obj is None or isinstance(obj, (int, float, str, bool)):
                continue
            if id(obj) in live:
                continue
            live.add(id(obj))

            if isinstance(obj, LuaTable):
                mode = ''
                if isinstance(obj.metatable, LuaTable):
                    mode = str(obj.metatable.data.get('__mode', ''))
                    pending.append(obj.metatable)
                for k, v in obj.data.items():
                    if 'k' not in mode:
                        pending.append(k)
                    if 'v' not in mode:
                        pending.append(v)
            elif isinstance(obj, LuaFunction):
                for scope in obj.scopes:
                    pending.extend(scope.values())

        return live

    def _collectgarbage(self, opt='collect', arg=None):
        if opt not in ('collect', None):
            return 0

        alive = self._live_ids()
        seen = set()
        pending = [v for scope in self.scopes for k, v in scope.items() if k != '__to_close__']

        while pending:
            obj = pending.pop()
            if not isinstance(obj, LuaTable) or id(obj) in seen:
                continue
            seen.add(id(obj))

            mode = ''
            if isinstance(obj.metatable, LuaTable):
                mode = str(obj.metatable.data.get('__mode', ''))
            if mode:
                for key, value in list(obj.data.items()):
                    weak_key = ('k' in mode and isinstance(key, (LuaTable, LuaFunction, LuaThread, LuaUserdata))
                                and id(key) not in alive)
                    weak_value = ('v' in mode and isinstance(value, (LuaTable, LuaFunction, LuaThread, LuaUserdata))
                                  and id(value) not in alive)
                    if weak_key or weak_value:
                        del obj.data[key]

            pending.extend(v for v in obj.data.values() if isinstance(v, LuaTable))

        return 0

    def _coresume(self, thread, *args):
        if not isinstance(thread, LuaThread):
            return [False, 'coroutine expected']
        if thread.status == 'dead':
            return [False, 'cannot resume dead coroutine']

        thread.history.append(list(args))
        thread.yields = 0

        caller = self.current_coro
        if caller is not None:
            caller.status = 'normal'
        self.current_coro = thread
        thread.status = 'running'

        try:
            result = self.call(thread.func, thread.history[0])
            thread.status = 'dead'
            values = result if isinstance(result, list) else ([] if result is None else [result])
            return [True] + values
        except YieldSignal as signal:
            thread.status = 'suspended'
            return [True] + signal.values
        except LuaError as err:
            thread.status = 'dead'
            return [False, err.value]
        except Exception as err:
            thread.status = 'dead'
            return [False, str(err)]
        finally:
            self.current_coro = caller
            if caller is not None:
                caller.status = 'running'

    def _coyield(self, *args):
        # el retorno lo pone el resume siguiente
        thread = self.current_coro
        if thread is None:
            raise LuaError('attempt to yield from outside a coroutine')

        thread.yields += 1
        if thread.yields < len(thread.history):
            values = thread.history[thread.yields]
            return values[0] if len(values) == 1 else (values or None)
        raise YieldSignal(list(args))

    def _assert(self, cond, msg='assertion failed!', *extra):
        if not self.is_truthy(cond):
            raise LuaError(msg)
        if extra:
            return [cond, msg] + list(extra)
        if msg != 'assertion failed!':
            return [cond, msg]
        return cond

    def _next(self, tbl, key=None):
        if not isinstance(tbl, LuaTable):
            return None
        keys = list(tbl.data.keys())
        if not keys:
            return None
        if key is None:
            nxt = keys[0]
        else:
            try:
                pos = keys.index(key) + 1
            except ValueError:
                return None
            if pos >= len(keys):
                return None
            nxt = keys[pos]
        return [nxt, tbl.get(nxt)]

    def _select(self, index, *args):
        if index == '#':
            return len(args)
        idx = int(index)
        if idx < 0:
            idx = len(args) + idx + 1
        return list(args[idx - 1:])

    def _load(self, chunk, chunkname='=(load)', mode='bt', env=None):
        from antlr4 import CommonTokenStream, InputStream
        from antlr4.atn.PredictionMode import PredictionMode
        from LuaLexer import LuaLexer

        try:
            if callable(chunk) or isinstance(chunk, LuaFunction):
                pieces = []
                while True:
                    piece = self.call(chunk, [])
                    if piece is None or piece == '':
                        break
                    pieces.append(str(piece))
                source = ''.join(pieces)
            else:
                source = str(chunk)

            stream = CommonTokenStream(LuaLexer(InputStream(source)))
            parser = LuaParser(stream)
            parser._interp.predictionMode = PredictionMode.SLL
            try:
                tree = parser.start_()
            except Exception:
                stream.reset()
                parser.reset()
                parser._interp.predictionMode = PredictionMode.LL
                tree = parser.start_()

            target = env if isinstance(env, LuaTable) else self.scopes[0]
            return LuaFunction([], tree.chunk().block(), [target])
        except Exception as err:
            return [None, str(err)]

    def _table_pack(self, *args):
        tbl = LuaTable()
        for i, value in enumerate(args, start=1):
            tbl.set(i, value)
        tbl.set('n', len(args))
        return tbl

    def _table_unpack(self, tbl, i=1, j=None):
        if not isinstance(tbl, LuaTable):
            return []
        i = int(i)
        j = tbl.length() if j is None else int(j)
        return [tbl.get(k) for k in range(i, j + 1)]

    def get_var(self, name):
        for scope in reversed(self.scopes):
            if isinstance(scope, LuaTable):
                value = self._resolve_index(scope.get(name))
                if value is not None:
                    return value
            elif name in scope and name != '__to_close__':
                return scope[name]
        return None

    def set_var(self, name, value, is_local=False):
        if is_local:
            self.scopes[-1][name] = value
            return

        for scope in reversed(self.scopes):
            if isinstance(scope, LuaTable):
                self._resolve_newindex(scope.set(name, value))
                return
            if name in scope and name != '__to_close__':
                scope[name] = value
                return

        if isinstance(self.scopes[0], LuaTable):
            self.scopes[0].set(name, value)
        else:
            self.scopes[0][name] = value

    def is_truthy(self, value):
        # ojo: 0 y "" son verdad
        return value is not False and value is not None

    def _num(self, value):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
        if isinstance(value, str):
            parsed = self._tonumber(value)
            if parsed is not None:
                return parsed
        return 0

    def _int(self, value):
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        raise LuaError('number has no integer representation')

    def _mm(self, a, b, name):
        for obj in (a, b):
            if isinstance(obj, LuaTable) and isinstance(obj.metatable, LuaTable):
                method = obj.metatable.data.get(name)
                if method is not None:
                    return method
        return None

    def _resolve_index(self, value):
        if isinstance(value, tuple) and len(value) == 4 and value[0] == '__call_index__':
            _, handler, table, key = value
            return self.call(handler, [table, key])
        return value

    def _resolve_newindex(self, result):
        if isinstance(result, tuple) and len(result) == 5 and result[0] == '__call_newindex__':
            _, handler, table, key, value = result
            self.call(handler, [table, key, value])

    def call(self, func, args):
        if isinstance(func, LuaFunction):
            frame = {'__to_close__': []}
            for i, param in enumerate(func.params):
                frame[param] = args[i] if i < len(args) else None

            if func.vararg:
                extra = list(args[len(func.params):])
                frame['...'] = extra
                frame['arg'] = self._table_pack(*extra)
            else:
                frame['...'] = []

            previous = self.scopes
            base = list(func.scopes) if func.scopes else [previous[0]]
            self.scopes = base + [frame]
            error = None
            try:
                self.visitChildren(func.body)
                return None
            except ReturnSignal as ret:
                if len(ret.values) == 1:
                    return ret.values[0]
                return ret.values or None
            except LuaError as err:
                error = err.value
                raise
            finally:
                for name in reversed(frame.get('__to_close__', [])):
                    value = frame.get(name)
                    if isinstance(value, LuaTable) and isinstance(value.metatable, LuaTable):
                        closer = value.metatable.data.get('__close')
                        if closer is not None:
                            self.call(closer, [value, error])
                self.scopes = previous

        if isinstance(func, LuaTable) and isinstance(func.metatable, LuaTable):
            handler = func.metatable.data.get('__call')
            if handler is not None:
                return self.call(handler, [func] + list(args))

        if callable(func):
            return func(*args)

        return None

    def visitBlock(self, ctx):
        self.scopes.append({'__to_close__': []})
        error = None
        try:
            stats = ctx.stat()
            retstat = ctx.retstat()

            labels = {}
            for pos, stat in enumerate(stats):
                if stat.label():
                    labels[stat.label().NAME().getText()] = pos

            pos = 0
            while pos < len(stats):
                try:
                    self.visit(stats[pos])
                    pos += 1
                except GotoSignal as jump:
                    if jump.label not in labels:
                        raise
                    pos = labels[jump.label]

            if retstat:
                return self.visit(retstat)
            return None
        except LuaError as err:
            error = err.value
            raise
        finally:
            scope = self.scopes.pop()
            for name in reversed(scope.get('__to_close__', [])):
                value = scope.get(name)
                if isinstance(value, LuaTable) and isinstance(value.metatable, LuaTable):
                    closer = value.metatable.data.get('__close')
                    if closer is not None:
                        self.call(closer, [value, error])

    def visitRetstat(self, ctx):
        raise ReturnSignal(self._eval_list(ctx.explist()) if ctx.explist() else [])

    def visitStat(self, ctx):
        head = ctx.getChild(0).getText()

        if head == 'return':
            raise ReturnSignal(self._eval_list(ctx.explist()) if ctx.explist() else [])
        if head == 'goto':
            raise GotoSignal(ctx.NAME().getText())
        if head == ';':
            return None
        if head == 'break':
            raise BreakSignal()

        if head == 'local' and ctx.getChild(1).getText() == 'function':
            name = ctx.NAME().getText()
            self.set_var(name, None, is_local=True)
            self.set_var(name, self._make_function(ctx.funcbody()), is_local=True)
            return None

        if head == 'local':
            if ctx.attnamelist():
                children = list(ctx.attnamelist().getChildren())
                names = []
                to_close = []
                i = 0
                while i < len(children):
                    node = children[i]
                    if node.getText() != ',':
                        names.append(node.getText())
                        if i + 1 < len(children) and isinstance(children[i + 1], LuaParser.AttribContext):
                            if 'close' in children[i + 1].getText():
                                to_close.append(node.getText())
                            i += 1
                    i += 1

                values = self._eval_list(ctx.explist()) if ctx.explist() else []
                for i, name in enumerate(names):
                    self.set_var(name, values[i] if i < len(values) else None, is_local=True)
                    if name in to_close:
                        self.scopes[-1].setdefault('__to_close__', []).append(name)
            return None

        if head == 'function':
            funcname = ctx.funcname()
            names = [n.getText() for n in funcname.NAME()]
            is_method = ':' in funcname.getText()
            func = self._make_function(ctx.funcbody(), with_self=is_method)

            if len(names) == 1:
                self.set_var(names[0], func)
            else:
                target = self.get_var(names[0])
                for part in names[1:-1]:
                    target = target.get(part)
                if isinstance(target, LuaTable):
                    target.set(names[-1], func)
            return None

        if head == 'do':
            return self.visit(ctx.block(0))

        if head == 'if':
            for test, block in zip(ctx.exp(), ctx.block()):
                if self.is_truthy(self.visit(test)):
                    return self.visit(block)
            blocks = ctx.block()
            if len(blocks) > len(ctx.exp()):
                return self.visit(blocks[-1])
            return None

        if head == 'while':
            try:
                while self.is_truthy(self.visit(ctx.exp(0))):
                    self.visit(ctx.block(0))
            except BreakSignal:
                pass
            return None

        if head == 'repeat':
            try:
                while True:
                    # el until ve las locales de adentro
                    self.scopes.append({'__to_close__': []})
                    try:
                        self.visitChildren(ctx.block(0))
                        finished = self.is_truthy(self.visit(ctx.exp(0)))
                    finally:
                        self.scopes.pop()
                    if finished:
                        break
            except BreakSignal:
                pass
            return None

        if head == 'for' and ctx.getChild(2).getText() == '=':
            name = ctx.NAME().getText()
            current = self.visit(ctx.exp(0))
            limit = self.visit(ctx.exp(1))
            step = self.visit(ctx.exp(2)) if len(ctx.exp()) == 3 else 1

            try:
                while (step > 0 and current <= limit) or (step < 0 and current >= limit):
                    self.scopes.append({name: current, '__to_close__': []})
                    try:
                        self.visitChildren(ctx.block(0))
                    finally:
                        self.scopes.pop()
                    current += step
            except BreakSignal:
                pass
            return None

        if head == 'for':
            var_names = [n.getText() for n in ctx.namelist().NAME()]
            control = self._eval_list(ctx.explist())
            iterator = control[0] if len(control) > 0 else None
            state = control[1] if len(control) > 1 else None
            key = control[2] if len(control) > 2 else None

            try:
                while True:
                    result = self.call(iterator, [state, key])
                    values = result if isinstance(result, list) else ([] if result is None else [result])
                    if not values or values[0] is None:
                        break
                    key = values[0]

                    frame = {'__to_close__': []}
                    for i, var_name in enumerate(var_names):
                        frame[var_name] = values[i] if i < len(values) else None
                    self.scopes.append(frame)
                    try:
                        self.visitChildren(ctx.block(0))
                    finally:
                        self.scopes.pop()
            except BreakSignal:
                pass
            return None

        if ctx.varlist() and ctx.explist():
            values = self._eval_list(ctx.explist())
            for i, var in enumerate(ctx.varlist().var()):
                self._assign(var, values[i] if i < len(values) else None)
            return None

        if ctx.functioncall():
            return self.visit(ctx.functioncall())

        return self.visitChildren(ctx)

    def _make_function(self, body_ctx, with_self=False):
        params = ['self'] if with_self else []
        vararg = False

        parlist = body_ctx.parlist()
        if parlist:
            if '...' in parlist.getText():
                vararg = True
            if parlist.namelist():
                params += [n.getText() for n in parlist.namelist().NAME()]

        return LuaFunction(params, body_ctx.block(), self.scopes, vararg)

    def _eval_list(self, explist_ctx):
        exps = explist_ctx.exp()
        values = []
        for i, exp in enumerate(exps):
            value = self.visit(exp)
            if i == len(exps) - 1 and isinstance(value, list):
                values.extend(value)
            else:
                values.append(value[0] if isinstance(value, list) and value else value)
        return values

    def _assign(self, var_ctx, value):
        if var_ctx.getChildCount() == 1:
            self.set_var(var_ctx.getText(), value)
            return

        container = self.visit(var_ctx.prefixexp())
        if var_ctx.getChild(1).getText() == '[':
            key = self.visit(var_ctx.exp())
        else:
            key = var_ctx.NAME().getText()

        if isinstance(container, LuaTable):
            self._resolve_newindex(container.set(key, value))

    def visitExplist(self, ctx):
        return [self.visit(e) for e in ctx.exp()]

    def visitExp(self, ctx):
        count = ctx.getChildCount()

        if count == 1:
            node = ctx.getChild(0)
            text = node.getText()
            if text == 'nil':
                return None
            if text == 'true':
                return True
            if text == 'false':
                return False
            if text == '...':
                return self.get_var('...') or []
            return self.visit(node)

        if count == 2:
            op = ctx.getChild(0).getText()
            value = self.visit(ctx.exp(0))

            if op == '-':
                handler = self._mm(value, None, '__unm')
                if handler is not None:
                    return self.call(handler, [value, value])
                return -self._num(value)
            if op == 'not':
                return not self.is_truthy(value)
            if op == '#':
                handler = self._mm(value, None, '__len')
                if handler is not None:
                    return self.call(handler, [value])
                if isinstance(value, LuaTable):
                    return value.length()
                return len(value) if value is not None else 0
            if op == '~':
                return ~int(self._num(value))

        if count == 3:
            if ctx.getChild(0).getText() == '(':
                return self.visit(ctx.exp(0))

            op = ctx.getChild(1).getText()
            left = self.visit(ctx.exp(0))

            if op == 'and':
                return self.visit(ctx.exp(1)) if self.is_truthy(left) else left
            if op == 'or':
                return left if self.is_truthy(left) else self.visit(ctx.exp(1))

            right = self.visit(ctx.exp(1))

            if op in ARITH_MM:
                handler = self._mm(left, right, ARITH_MM[op])
                if handler is not None:
                    return self.call(handler, [left, right])

            if op in ('&', '|', '~', '<<', '>>'):
                return self._bitwise(op, left, right)
            if op in ('+', '-', '*', '/', '//', '%', '^'):
                return self._arith(op, left, right)

            if op == '..':
                handler = self._mm(left, right, '__concat')
                if handler is not None:
                    return self.call(handler, [left, right])
                return self._to_str(left) + self._to_str(right)

            if op == '==':
                if self._both_tables(left, right):
                    handler = self._mm(left, right, '__eq')
                    if handler is not None:
                        return self.is_truthy(self.call(handler, [left, right]))
                return left == right
            if op == '~=':
                if self._both_tables(left, right):
                    handler = self._mm(left, right, '__eq')
                    if handler is not None:
                        return not self.is_truthy(self.call(handler, [left, right]))
                return left != right
            if op == '<':
                handler = self._mm(left, right, '__lt')
                if handler is not None:
                    return self.is_truthy(self.call(handler, [left, right]))
                return left < right
            if op == '<=':
                handler = self._mm(left, right, '__le')
                if handler is not None:
                    return self.is_truthy(self.call(handler, [left, right]))
                handler = self._mm(right, left, '__lt')
                if handler is not None:
                    return not self.is_truthy(self.call(handler, [right, left]))
                return left <= right
            if op == '>':
                handler = self._mm(right, left, '__lt')
                if handler is not None:
                    return self.is_truthy(self.call(handler, [right, left]))
                return left > right
            if op == '>=':
                handler = self._mm(right, left, '__le')
                if handler is not None:
                    return self.is_truthy(self.call(handler, [right, left]))
                handler = self._mm(left, right, '__lt')
                if handler is not None:
                    return not self.is_truthy(self.call(handler, [left, right]))
                return left >= right

        return self.visitChildren(ctx)

    def _both_tables(self, a, b):
        return isinstance(a, LuaTable) and isinstance(b, LuaTable) and a is not b

    def _bitwise(self, op, left, right):
        a = self._int(left)
        b = self._int(right)

        if op == '&':
            return a & b
        if op == '|':
            return a | b
        if op == '~':
            return a ^ b
        if op == '<<':
            if b < 0:
                return 0 if -b >= 64 else a >> -b
            return 0 if b >= 64 else (a << b) & 0xFFFFFFFFFFFFFFFF

        if b < 0:
            return 0 if -b >= 64 else (a << -b) & 0xFFFFFFFFFFFFFFFF
        return 0 if b >= 64 else a >> b

    def _arith(self, op, left, right):
        a = self._num(left)
        b = self._num(right)

        if op == '+':
            return a + b
        if op == '-':
            return a - b
        if op == '*':
            return a * b
        if op == '/':
            # no explota, da inf/nan
            if b == 0:
                if a == 0:
                    return float('nan')
                return float('inf') if a > 0 else float('-inf')
            return float(a) / float(b)
        if op == '//':
            return a // b
        if op == '%':
            return a % b
        return float(a ** b)

    def visitTableconstructor(self, ctx):
        tbl = LuaTable()
        if not ctx.fieldlist():
            return tbl

        fields = ctx.fieldlist().field()
        index = 1
        for pos, field in enumerate(fields):
            is_last = pos == len(fields) - 1

            if field.getChild(0).getText() == '[':
                tbl.set(self.visit(field.exp(0)), self._single(field.exp(1)))
            elif field.getChildCount() == 3 and field.getChild(1).getText() == '=':
                tbl.set(field.NAME().getText(), self._single(field.exp(0)))
            else:
                value = self.visit(field.exp(0))
                if isinstance(value, list):
                    if is_last:
                        for item in value:
                            tbl.set(index, item)
                            index += 1
                    else:
                        tbl.set(index, value[0] if value else None)
                        index += 1
                else:
                    tbl.set(index, value)
                    index += 1

        return tbl

    def _single(self, exp_ctx):
        value = self.visit(exp_ctx)
        if isinstance(value, list):
            return value[0] if value else None
        return value

    def visitNumber(self, ctx):
        text = ctx.getText()
        if text.startswith(('0x', '0X')):
            if '.' in text or 'p' in text or 'P' in text:
                return float.fromhex(text)
            return int(text, 16)
        if '.' in text or 'e' in text or 'E' in text:
            return float(text)
        return int(text)

    def visitString(self, ctx):
        text = ctx.getText()

        if not text.startswith(('"', "'")):
            if text.startswith('['):
                match = re.match(r'^\[(=*)\[\r?\n?(.*)\]\1\]$', text, re.DOTALL)
                if match:
                    return match.group(2)
                return text[2:-2]
            return text

        value = text[1:-1]
        value = re.sub(r'\\z\s*', '', value)
        value = re.sub(r'\\u\{([0-9a-fA-F]+)\}', lambda m: chr(int(m.group(1), 16)), value)
        value = re.sub(r'\\(\d{1,3})', lambda m: chr(int(m.group(1), 10)), value)

        def replace(match):
            seq = match.group(1)
            if seq.startswith('x') and len(seq) == 3:
                return chr(int(seq[1:], 16))
            return ESCAPES.get(seq, seq)

        return re.sub(r'\\(x[0-9a-fA-F]{2}|.)', replace, value)

    def _chain(self, ctx):
        # a.b[1]:f(x), se resuelve de izquierda a derecha
        nodes = list(ctx.getChildren())

        first = nodes[0]
        if first.getText() == '(':
            current = self.visit(nodes[1])
            pos = 3
        elif isinstance(first, (LuaParser.VarContext, LuaParser.PrefixexpContext, LuaParser.FunctioncallContext)):
            current = self.visit(first)
            pos = 1
        else:
            current = self.get_var(first.getText())
            pos = 1

        while pos < len(nodes):
            node = nodes[pos]
            text = node.getText()

            if text == '[':
                current = self._index(current, self.visit(nodes[pos + 1]))
                pos += 3
            elif text == '.':
                current = self._index(current, nodes[pos + 1].getText())
                pos += 2
            elif text == ':':
                name = nodes[pos + 1].getText()
                if isinstance(current, LuaTable):
                    method = self._resolve_index(current.get(name))
                elif isinstance(current, str):
                    method = self.scopes[0]['string'].get(name)
                else:
                    method = None
                current = self.call(method, [current] + self.visit(nodes[pos + 2]))
                pos += 3
            elif isinstance(node, LuaParser.ArgsContext):
                current = self.call(current, self.visit(node))
                pos += 1
            else:
                pos += 1

        return current

    def _index(self, obj, key):
        if not isinstance(obj, LuaTable):
            return None
        return self._resolve_index(obj.get(key))

    def visitVar(self, ctx):
        return self._chain(ctx)

    def visitPrefixexp(self, ctx):
        return self._chain(ctx)

    def visitFunctioncall(self, ctx):
        return self._chain(ctx)

    def visitFunctiondef(self, ctx):
        return self._make_function(ctx.funcbody())

    def visitArgs(self, ctx):
        if ctx.explist():
            return self._eval_list(ctx.explist())
        if ctx.tableconstructor():
            return [self.visit(ctx.tableconstructor())]
        if ctx.string():
            return [self.visit(ctx.string())]
        return []
