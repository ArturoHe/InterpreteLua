import math
import re
from LuaParser import LuaParser
from LuaParserVisitor import LuaParserVisitor


# --- EXCEPCIONES DE CONTROL DE FLUJO ---
class BreakException(Exception):
    pass


class GotoException(Exception):
    def __init__(self, label):
        self.label = label


class ReturnException(Exception):
    def __init__(self, values):
        self.values = values


class LuaRuntimeError(Exception):
    def __init__(self, message):
        self.message = message
        super().__init__(str(message))


# --- ESTRUCTURAS DE DATOS DE LUA ---
class LuaTable:
    def __init__(self):
        self.data = {}
        self.metatable = None

    def get(self, key):
        if key in self.data:
            return self.data[key]
        if isinstance(self.metatable, LuaTable):
            idx_mm = self.metatable.data.get("__index")
            if isinstance(idx_mm, LuaTable):
                return idx_mm.get(key)
            if idx_mm is not None:
                return ("__call_index__", idx_mm, self, key)
        return None

    def set(self, key, value):
        if key not in self.data and isinstance(self.metatable, LuaTable):
            newidx_mm = self.metatable.data.get("__newindex")
            if isinstance(newidx_mm, LuaTable):
                return newidx_mm.set(key, value)
            if newidx_mm is not None:
                return ("__call_newindex__", newidx_mm, self, key, value)

        if value is None:
            self.data.pop(key, None)
        else:
            self.data[key] = value
        return None

    def length(self):
        i = 1
        while i in self.data:
            i += 1
        return i - 1

    def __repr__(self):
        return f"table: {id(self)}"


class LuaFunction:
    def __init__(self, params, body_ctx, closure_scopes, is_vararg=False):
        self.params = params
        self.body_ctx = body_ctx
        self.closure_scopes = list(closure_scopes)
        self.is_vararg = is_vararg


class _CoroYield(Exception):
    def __init__(self, values):
        self.values = values


class LuaThread:
    def __init__(self, func, visitor):
        self.func = func
        self.visitor = visitor
        self.status = "suspended"
        self.resume_history = []
        self.yield_count = 0

    def __repr__(self):
        return f"thread: {id(self)}"


class LuaUserdata:
    def __init__(self, name="userdata"):
        self.name = name

    def __repr__(self):
        return f"userdata: {id(self)}"


class EvalVisitor(LuaParserVisitor):
    def __init__(self):
        self.scopes = [{}]
        self.current_coro = None
        self._init_stdlib()

    # --- LIBRERÍA ESTÁNDAR DE LUA ---
    def _init_stdlib(self):
        self.scopes[0]["print"] = self._lua_print
        self.scopes[0]["tonumber"] = self._lua_tonumber
        self.scopes[0]["tostring"] = self._lua_tostring
        self.scopes[0]["type"] = self._lua_type
        self.scopes[0]["ipairs"] = self._lua_ipairs
        self.scopes[0]["pairs"] = self._lua_pairs
        self.scopes[0]["error"] = self._lua_error
        self.scopes[0]["pcall"] = self._lua_pcall
        self.scopes[0]["xpcall"] = self._lua_xpcall
        self.scopes[0]["setmetatable"] = self._lua_setmetatable
        self.scopes[0]["getmetatable"] = self._lua_getmetatable
        self.scopes[0]["rawget"] = lambda tbl, k: tbl.data.get(k, None) if isinstance(tbl, LuaTable) else None
        self.scopes[0]["rawset"] = self._lua_rawset
        self.scopes[0]["collectgarbage"] = self._lua_collectgarbage

        # Librería math
        math_tbl = LuaTable()
        math_tbl.set("abs", abs)
        math_tbl.set("ceil", math.ceil)
        math_tbl.set("floor", math.floor)
        math_tbl.set("sqrt", math.sqrt)
        math_tbl.set("sin", math.sin)
        math_tbl.set("cos", math.cos)
        math_tbl.set("tan", math.tan)
        math_tbl.set("log", math.log)
        math_tbl.set("exp", math.exp)
        math_tbl.set("max", max)
        math_tbl.set("min", min)
        math_tbl.set("pi", math.pi)
        math_tbl.set("huge", float("inf"))
        self.scopes[0]["math"] = math_tbl

        # Librería table
        table_tbl = LuaTable()
        table_tbl.set("insert", self._lua_table_insert)
        table_tbl.set("remove", self._lua_table_remove)
        self.scopes[0]["table"] = table_tbl

        # Librería string
        string_tbl = LuaTable()
        string_tbl.set("len", len)
        string_tbl.set("lower", lambda s: str(s).lower())
        string_tbl.set("upper", lambda s: str(s).upper())
        string_tbl.set("rep", lambda s, n, sep="": str(sep).join([str(s)] * int(n)))
        string_tbl.set("reverse", lambda s: str(s)[::-1])
        string_tbl.set("sub", self._lua_string_sub)
        string_tbl.set("find", self._lua_string_find)
        string_tbl.set("gsub", self._lua_string_gsub)
        string_tbl.set("byte", lambda s, i=1: ord(str(s)[int(i) - 1]) if 1 <= int(i) <= len(str(s)) else None)
        string_tbl.set("char", lambda *codes: "".join(chr(int(c)) for c in codes))
        self.scopes[0]["string"] = string_tbl

        # Librería io
        io_tbl = LuaTable()
        io_tbl.set("write", lambda *args: print("".join(self._format_lua_val(a) for a in args), end=""))
        io_tbl.set("stdin", LuaUserdata("stdin"))
        io_tbl.set("stdout", LuaUserdata("stdout"))
        io_tbl.set("stderr", LuaUserdata("stderr"))
        self.scopes[0]["io"] = io_tbl

        # Librería coroutine
        coro_tbl = LuaTable()
        coro_tbl.set("create", lambda fn: LuaThread(fn, self))
        coro_tbl.set("resume", self._lua_coro_resume)
        coro_tbl.set("yield", self._lua_coro_yield)
        coro_tbl.set("status", lambda th: th.status if isinstance(th, LuaThread) else "dead")
        coro_tbl.set("running", lambda: [self.current_coro, self.current_coro is None])
        self.scopes[0]["coroutine"] = coro_tbl

        self.scopes[0]["load"] = self._lua_load
        self.scopes[0]["loadstring"] = self._lua_load
        self.scopes[0]["assert"] = self._lua_assert
        self.scopes[0]["next"] = self._lua_next
        self.scopes[0]["select"] = self._lua_select

        g_tbl = LuaTable()
        g_tbl.data = self.scopes[0]
        self.scopes[0]["_G"] = g_tbl

        table_tbl.set("pack", self._lua_table_pack)
        table_tbl.set("unpack", self._lua_table_unpack)
        self.scopes[0]["unpack"] = self._lua_table_unpack

    def _format_lua_val(self, val):
        if isinstance(val, LuaTable) and isinstance(val.metatable, LuaTable):
            ts = val.metatable.data.get("__tostring")
            if ts is not None:
                return str(self.invoke_callable(ts, [val]))
        if val is None:
            return "nil"
        if val is True:
            return "true"
        if val is False:
            return "false"
        if isinstance(val, float) and val.is_integer():
            return str(int(val))
        return str(val)

    def _lua_print(self, *args):
        print("\t".join(self._format_lua_val(a) for a in args))
        return None

    def _lua_tonumber(self, val, base=10):
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return val
        if isinstance(val, str):
            val = val.strip()
            try:
                if base != 10:
                    return int(val, int(base))
                if val.startswith(('0x', '0X', '-0x', '-0X')):
                    if '.' in val or 'p' in val or 'P' in val:
                        return float.fromhex(val)
                    return int(val, 16)
                return float(val) if ('.' in val or 'e' in val.lower()) else int(val)
            except ValueError:
                return None
        return None

    def _lua_tostring(self, val):
        return self._format_lua_val(val)

    def _lua_type(self, val):
        if val is None:
            return "nil"
        if isinstance(val, bool):
            return "boolean"
        if isinstance(val, (int, float)):
            return "number"
        if isinstance(val, str):
            return "string"
        if isinstance(val, LuaTable):
            return "table"
        if isinstance(val, LuaThread):
            return "thread"
        if isinstance(val, LuaUserdata):
            return "userdata"
        if isinstance(val, LuaFunction) or callable(val):
            return "function"
        return "userdata"

    def _lua_table_insert(self, tbl, pos_or_val, val=None):
        if val is None:
            tbl.set(tbl.length() + 1, pos_or_val)
        else:
            pos = int(pos_or_val)
            n = tbl.length()
            for i in range(n, pos - 1, -1):
                tbl.set(i + 1, tbl.get(i))
            tbl.set(pos, val)

    def _lua_table_remove(self, tbl, pos=None):
        n = tbl.length()
        if n == 0:
            return None
        pos = n if pos is None else int(pos)
        removed = tbl.get(pos)
        for i in range(pos, n):
            tbl.set(i, tbl.get(i + 1))
        tbl.set(n, None)
        return removed

    def _lua_ipairs(self, tbl):
        def _ipairs_next(t, i):
            nxt = int(i) + 1
            val = t.get(nxt)
            if val is None:
                return None
            return [nxt, val]
        return [_ipairs_next, tbl, 0]

    def _lua_pairs(self, tbl):
        keys = list(tbl.data.keys())
        def _pairs_next(t, k):
            if not keys:
                return None
            if k is None:
                nxt_k = keys[0]
            else:
                try:
                    idx = keys.index(k)
                    if idx + 1 >= len(keys):
                        return None
                    nxt_k = keys[idx + 1]
                except ValueError:
                    return None
            return [nxt_k, t.get(nxt_k)]
        return [_pairs_next, tbl, None]

    def _lua_string_sub(self, s, i, j=None):
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
            return ""
        return s[i - 1 : j]

    def _lua_string_find(self, s, pattern, init=1):
        s = str(s)
        idx = s.find(str(pattern), int(init) - 1)
        if idx == -1:
            return None
        return [idx + 1, idx + len(str(pattern))]

    def _lua_string_gsub(self, s, pattern, repl, n=None):
        s = str(s)
        count = 0 if n is None else int(n)
        if count == 0:
            res, subs = s.replace(str(pattern), str(repl)), s.count(str(pattern))
        else:
            res, subs = s.replace(str(pattern), str(repl), count), min(count, s.count(str(pattern)))
        return [res, subs]

    def _lua_error(self, msg="error", level=1):
        raise LuaRuntimeError(msg)

    def _lua_pcall(self, func, *args):
        saved_depth = len(self.scopes)
        try:
            res = self.invoke_callable(func, list(args))
            if isinstance(res, list):
                return [True] + res
            return [True, res]
        except LuaRuntimeError as e:
            while len(self.scopes) > saved_depth:
                self.scopes.pop()
            return [False, e.message]
        except Exception as e:
            while len(self.scopes) > saved_depth:
                self.scopes.pop()
            return [False, str(e)]

    def _lua_xpcall(self, func, msgh, *args):
        saved_depth = len(self.scopes)
        try:
            res = self.invoke_callable(func, list(args))
            if isinstance(res, list):
                return [True] + res
            return [True, res]
        except LuaRuntimeError as e:
            while len(self.scopes) > saved_depth:
                self.scopes.pop()
            handled = self.invoke_callable(msgh, [e.message])
            return [False, handled]
        except Exception as e:
            while len(self.scopes) > saved_depth:
                self.scopes.pop()
            handled = self.invoke_callable(msgh, [str(e)])
            return [False, handled]

    def _lua_setmetatable(self, tbl, mt):
        if isinstance(tbl, LuaTable):
            tbl.metatable = mt
        return tbl

    def _lua_getmetatable(self, obj):
        if isinstance(obj, LuaTable):
            return obj.metatable
        return None

    def _lua_rawset(self, tbl, k, v):
        if isinstance(tbl, LuaTable):
            if v is None:
                tbl.data.pop(k, None)
            else:
                tbl.data[k] = v
        return tbl

    def _collect_live_ids(self):
        live = set()
        stack = []
        for scope in self.scopes:
            for k, v in scope.items():
                if k != "__to_close__":
                    stack.append(v)

        while stack:
            obj = stack.pop()
            if obj is None or isinstance(obj, (int, float, str, bool)):
                continue
            obj_id = id(obj)
            if obj_id in live:
                continue
            live.add(obj_id)

            if isinstance(obj, LuaTable):
                mode = ""
                if isinstance(obj.metatable, LuaTable):
                    mode = str(obj.metatable.data.get("__mode", ""))
                    stack.append(obj.metatable)
                for k, v in obj.data.items():
                    if 'k' not in mode:
                        stack.append(k)
                    if 'v' not in mode:
                        stack.append(v)
            elif isinstance(obj, LuaFunction):
                for sc in obj.closure_scopes:
                    for v in sc.values():
                        stack.append(v)
        return live

    def _lua_collectgarbage(self, opt="collect", arg=None):
        if opt in ("collect", None):
            live_ids = self._collect_live_ids()
            visited = set()
            stack = [v for sc in self.scopes for k, v in sc.items() if k != "__to_close__"]
            while stack:
                obj = stack.pop()
                if not isinstance(obj, LuaTable) or id(obj) in visited:
                    continue
                visited.add(id(obj))
                mode = str(obj.metatable.data.get("__mode", "")) if isinstance(obj.metatable, LuaTable) else ""
                if mode:
                    keys_to_del = []
                    for k, v in list(obj.data.items()):
                        k_dead = ('k' in mode) and isinstance(k, (LuaTable, LuaFunction, LuaThread, LuaUserdata)) and (id(k) not in live_ids)
                        v_dead = ('v' in mode) and isinstance(v, (LuaTable, LuaFunction, LuaThread, LuaUserdata)) and (id(v) not in live_ids)
                        if k_dead or v_dead:
                            keys_to_del.append(k)
                    for k in keys_to_del:
                        obj.data.pop(k, None)
                for k, v in obj.data.items():
                    if isinstance(v, LuaTable):
                        stack.append(v)
        return 0

    def _lua_coro_resume(self, th, *args):
        if not isinstance(th, LuaThread):
            return [False, "coroutine expected"]
        if th.status == "dead":
            return [False, "cannot resume dead coroutine"]

        th.resume_history.append(list(args))
        th.yield_count = 0
        caller_coro = self.current_coro
        if caller_coro is not None:
            caller_coro.status = "normal"
        self.current_coro = th
        th.status = "running"

        try:
            res = self.invoke_callable(th.func, th.resume_history[0])
            th.status = "dead"
            vals = res if isinstance(res, list) else ([] if res is None else [res])
            return [True] + vals
        except _CoroYield as y:
            th.status = "suspended"
            return [True] + y.values
        except LuaRuntimeError as e:
            th.status = "dead"
            return [False, e.message]
        except Exception as e:
            th.status = "dead"
            return [False, str(e)]
        finally:
            self.current_coro = caller_coro
            if caller_coro is not None:
                caller_coro.status = "running"

    def _lua_coro_yield(self, *args):
        th = self.current_coro
        if th is None:
            raise LuaRuntimeError("attempt to yield from outside a coroutine")
        th.yield_count += 1
        if th.yield_count < len(th.resume_history):
            res = th.resume_history[th.yield_count]
            return res[0] if len(res) == 1 else (res if res else None)
        raise _CoroYield(list(args))

    def _lua_assert(self, cond, msg="assertion failed!", *extra):
        if not self.is_truthy(cond):
            raise LuaRuntimeError(msg)
        return [cond, msg] + list(extra) if extra else ([cond, msg] if msg != "assertion failed!" else cond)

    def _lua_next(self, tbl, k=None):
        if not isinstance(tbl, LuaTable):
            return None
        keys = list(tbl.data.keys())
        if not keys:
            return None
        if k is None:
            nxt_k = keys[0]
        else:
            try:
                idx = keys.index(k)
                if idx + 1 >= len(keys):
                    return None
                nxt_k = keys[idx + 1]
            except ValueError:
                return None
        return [nxt_k, tbl.get(nxt_k)]

    def _lua_select(self, index, *args):
        if index == "#":
            return len(args)
        idx = int(index)
        if idx < 0:
            idx = len(args) + idx + 1
        return list(args[idx - 1:])

    def _lua_load(self, chunk_str, chunkname="=(load)", mode="bt", env=None):
        from antlr4 import InputStream, CommonTokenStream
        from antlr4.atn.PredictionMode import PredictionMode
        from LuaLexer import LuaLexer
        try:
            if callable(chunk_str) or isinstance(chunk_str, LuaFunction):
                parts = []
                while True:
                    piece = self.invoke_callable(chunk_str, [])
                    if piece is None or piece == "":
                        break
                    parts.append(str(piece))
                code = "".join(parts)
            else:
                code = str(chunk_str)

            lexer = LuaLexer(InputStream(code))
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
            block_ctx = tree.chunk().block()
            target_env = env if isinstance(env, LuaTable) else self.scopes[0]
            return LuaFunction([], block_ctx, [target_env])
        except Exception as e:
            return [None, str(e)]

    def _lua_table_pack(self, *args):
        tbl = LuaTable()
        for i, v in enumerate(args, start=1):
            tbl.set(i, v)
        tbl.set("n", len(args))
        return tbl

    def _lua_table_unpack(self, tbl, i=1, j=None):
        if not isinstance(tbl, LuaTable):
            return []
        i = int(i)
        j = tbl.length() if j is None else int(j)
        return [tbl.get(k) for k in range(i, j + 1)]

    # --- GESTIÓN DE MEMORIA (SCOPES) ---
    def get_var(self, name):
        for scope in reversed(self.scopes):
            if isinstance(scope, LuaTable):
                val = scope.get(name)
                if isinstance(val, tuple) and len(val) == 4 and val[0] == "__call_index__":
                    _, fn_mm, tbl_obj, k_obj = val
                    val = self.invoke_callable(fn_mm, [tbl_obj, k_obj])
                if val is not None:
                    return val
            elif name in scope and name != "__to_close__":
                return scope[name]
        return None

    def set_var(self, name, value, is_local=False):
        if is_local:
            self.scopes[-1][name] = value
            return
        for scope in reversed(self.scopes):
            if isinstance(scope, LuaTable):
                res = scope.set(name, value)
                if isinstance(res, tuple) and len(res) == 5 and res[0] == "__call_newindex__":
                    _, fn_mm, tbl_obj, k_obj, v_obj = res
                    self.invoke_callable(fn_mm, [tbl_obj, k_obj, v_obj])
                return
            if name in scope and name != "__to_close__":
                scope[name] = value
                return
        if isinstance(self.scopes[0], LuaTable):
            self.scopes[0].set(name, value)
        else:
            self.scopes[0][name] = value

    def is_truthy(self, val):
        return val is not False and val is not None

    def _to_num(self, val):
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return val
        if isinstance(val, str):
            num = self._lua_tonumber(val)
            if num is not None:
                return num
        return 0

    def _to_lua_int(self, val):
        if isinstance(val, int) and not isinstance(val, bool):
            return val
        if isinstance(val, float) and val.is_integer():
            return int(val)
        raise LuaRuntimeError("number has no integer representation")

    def _get_metamethod(self, a, b, mm_name):
        for obj in (a, b):
            if isinstance(obj, LuaTable) and isinstance(obj.metatable, LuaTable):
                mm = obj.metatable.data.get(mm_name)
                if mm is not None:
                    return mm
        return None

    # --- EJECUCIÓN DE FUNCIONES ---
    def invoke_callable(self, func, args):
        if isinstance(func, LuaFunction):
            func_scope = {"__to_close__": []}
            for i, param_name in enumerate(func.params):
                func_scope[param_name] = args[i] if i < len(args) else None

            if func.is_vararg:
                extra_args = list(args[len(func.params):])
                func_scope["..."] = extra_args
                func_scope["arg"] = self._lua_table_pack(*extra_args)
            else:
                func_scope["..."] = []

            old_scopes = self.scopes
            base_scopes = list(func.closure_scopes) if func.closure_scopes else [old_scopes[0]]
            self.scopes = base_scopes + [func_scope]
            close_err = None

            try:
                self.visitChildren(func.body_ctx)
                return None
            except ReturnException as ret:
                return ret.values[0] if len(ret.values) == 1 else (ret.values if ret.values else None)
            except LuaRuntimeError as e:
                close_err = e.message
                raise
            finally:
                for var_name in reversed(func_scope.get("__to_close__", [])):
                    val = func_scope.get(var_name)
                    if isinstance(val, LuaTable) and isinstance(val.metatable, LuaTable):
                        close_mm = val.metatable.data.get("__close")
                        if close_mm is not None:
                            self.invoke_callable(close_mm, [val, close_err])
                self.scopes = old_scopes

        if isinstance(func, LuaTable) and isinstance(func.metatable, LuaTable):
            call_mm = func.metatable.data.get("__call")
            if call_mm is not None:
                return self.invoke_callable(call_mm, [func] + list(args))

        if callable(func):
            return func(*args)

        func_scope = {"__to_close__": []}
        for i, param_name in enumerate(func.params):
            func_scope[param_name] = args[i] if i < len(args) else None
        func_scope["..."] = list(args[len(func.params):]) if func.is_vararg else []
        
        return None

        
    # --- BLOQUES Y SENTENCIAS ---
    def visitBlock(self, ctx: LuaParser.BlockContext):
        self.scopes.append({"__to_close__": []})
        close_err = None
        try:
            stats = ctx.stat()
            retstat = ctx.retstat()

            labels = {}
            for idx, st in enumerate(stats):
                if st.label():
                    lbl_name = st.label().NAME().getText()
                    labels[lbl_name] = idx

            idx = 0
            while idx < len(stats):
                try:
                    self.visit(stats[idx])
                    idx += 1
                except GotoException as gt:
                    if gt.label in labels:
                        idx = labels[gt.label]
                    else:
                        raise

            if retstat:
                return self.visit(retstat)
            return None
        except LuaRuntimeError as e:
            close_err = e.message
            raise
        finally:
            scope = self.scopes.pop()
            for var_name in reversed(scope.get("__to_close__", [])):
                val = scope.get(var_name)
                if isinstance(val, LuaTable) and isinstance(val.metatable, LuaTable):
                    close_mm = val.metatable.data.get("__close")
                    if close_mm is not None:
                        self.invoke_callable(close_mm, [val, close_err])

    def visitRetstat(self, ctx: LuaParser.RetstatContext):
        values = self._eval_explist_flat(ctx.explist()) if ctx.explist() else []
        raise ReturnException(values)

    def visitStat(self, ctx: LuaParser.StatContext):
        first = ctx.getChild(0).getText()

        if first == 'return':
            values = self._eval_explist_flat(ctx.explist()) if ctx.explist() else []
            raise ReturnException(values)

        if first == 'goto':
            raise GotoException(ctx.NAME().getText())

        if first == ';':
            return None

        if first == 'break':
            raise BreakException()

        # 'local' 'function' NAME funcbody
        if first == 'local' and ctx.getChild(1).getText() == 'function':
            func_name = ctx.NAME().getText()
            self.set_var(func_name, None, is_local=True)
            func_obj = self._build_function(ctx.funcbody())
            self.set_var(func_name, func_obj, is_local=True)
            return None

        # 'local' attnamelist ('=' explist)?
        if first == 'local':
            if ctx.attnamelist():
                att_children = list(ctx.attnamelist().getChildren())
                names = []
                to_close = []
                i = 0
                while i < len(att_children):
                    child_txt = att_children[i].getText()
                    if child_txt != ',':
                        var_n = child_txt
                        names.append(var_n)
                        if i + 1 < len(att_children) and isinstance(att_children[i + 1], LuaParser.AttribContext):
                            if 'close' in att_children[i + 1].getText():
                                to_close.append(var_n)
                            i += 1
                    i += 1

                values = self._eval_explist_flat(ctx.explist()) if ctx.explist() else []
                for idx_v, name in enumerate(names):
                    val = values[idx_v] if idx_v < len(values) else None
                    self.set_var(name, val, is_local=True)
                    if name in to_close:
                        self.scopes[-1].setdefault("__to_close__", []).append(name)
            return None

        # Extensión 'global' de LuaParser.g4
        if first == 'global':
            if ctx.getChildCount() > 1 and ctx.getChild(1).getText() == 'function':
                func_name = ctx.NAME().getText()
                func_obj = self._build_function(ctx.funcbody())
                self.scopes[0][func_name] = func_obj
            elif ctx.attnamelist():
                names = [name.getText() for name in ctx.attnamelist().NAME()]
                values = self._eval_explist_flat(ctx.explist()) if ctx.explist() else []
                for i, name in enumerate(names):
                    val = values[i] if i < len(values) else None
                    self.scopes[0][name] = val
            return None

        # 'function' funcname funcbody (soporta puntos '.' y métodos con ':')
        if first == 'function':
            funcname_ctx = ctx.funcname()
            names = [n.getText() for n in funcname_ctx.NAME()]
            has_colon = ':' in funcname_ctx.getText()
            func_obj = self._build_function(ctx.funcbody(), inject_self=has_colon)

            if len(names) == 1:
                self.set_var(names[0], func_obj, is_local=False)
            else:
                target = self.get_var(names[0])
                for part in names[1:-1]:
                    target = target.get(part)
                if isinstance(target, LuaTable):
                    target.set(names[-1], func_obj)
            return None

        if first == 'do':
            return self.visit(ctx.block(0))

        if first == 'if':
            exps = ctx.exp()
            blocks = ctx.block()
            for i, exp_ctx in enumerate(exps):
                if self.is_truthy(self.visit(exp_ctx)):
                    return self.visit(blocks[i])
            if len(blocks) > len(exps):
                return self.visit(blocks[-1])
            return None

        if first == 'while':
            try:
                while self.is_truthy(self.visit(ctx.exp(0))):
                    self.visit(ctx.block(0))
            except BreakException:
                pass
            return None

        if first == 'repeat':
            try:
                while True:
                    self.scopes.append({"__to_close__": []})
                    try:
                        self.visitChildren(ctx.block(0))
                        cond = self.is_truthy(self.visit(ctx.exp(0)))
                    finally:
                        self.scopes.pop()
                    if cond:
                        break
            except BreakException:
                pass
            return None

        # For numérico
        if first == 'for' and ctx.getChild(2).getText() == '=':
            var_name = ctx.NAME().getText()
            start = self.visit(ctx.exp(0))
            limit = self.visit(ctx.exp(1))
            step = self.visit(ctx.exp(2)) if len(ctx.exp()) == 3 else 1

            curr = start
            try:
                while (step > 0 and curr <= limit) or (step < 0 and curr >= limit):
                    self.scopes.append({var_name: curr, "__to_close__": []})
                    try:
                        self.visitChildren(ctx.block(0))
                    finally:
                        self.scopes.pop()
                    curr += step
            except BreakException:
                pass
            return None

        # For genérico: 'for' namelist 'in' explist 'do' block 'end'
        if first == 'for' and ctx.namelist():
            var_names = [n.getText() for n in ctx.namelist().NAME()]
            exp_vals = self._eval_explist_flat(ctx.explist())
            iter_fn = exp_vals[0] if len(exp_vals) > 0 else None
            state = exp_vals[1] if len(exp_vals) > 1 else None
            ctrl = exp_vals[2] if len(exp_vals) > 2 else None

            try:
                while True:
                    res = self.invoke_callable(iter_fn, [state, ctrl])
                    vals = res if isinstance(res, list) else ([res] if res is not None else [])
                    if not vals or vals[0] is None:
                        break
                    ctrl = vals[0]
                    new_scope = {"__to_close__": []}
                    for idx, v_name in enumerate(var_names):
                        new_scope[v_name] = vals[idx] if idx < len(vals) else None
                    self.scopes.append(new_scope)
                    try:
                        self.visitChildren(ctx.block(0))
                    finally:
                        self.scopes.pop()
            except BreakException:
                pass
            return None

        # Asignación
        if ctx.varlist() and ctx.explist():
            vars_nodes = ctx.varlist().var()
            values = self._eval_explist_flat(ctx.explist())
            for i, var_ctx in enumerate(vars_nodes):
                val = values[i] if i < len(values) else None
                self._assign_var(var_ctx, val)
            return None

        if ctx.functioncall():
            return self.visit(ctx.functioncall())

        return self.visitChildren(ctx)

    def _build_function(self, funcbody_ctx: LuaParser.FuncbodyContext, inject_self=False):
        params = ["self"] if inject_self else []
        is_vararg = False
        if funcbody_ctx.parlist():
            if '...' in funcbody_ctx.parlist().getText():
                is_vararg = True
            if funcbody_ctx.parlist().namelist():
                params.extend([n.getText() for n in funcbody_ctx.parlist().namelist().NAME()])
        return LuaFunction(params, funcbody_ctx.block(), self.scopes, is_vararg=is_vararg)

    def _eval_explist_flat(self, explist_ctx):
        exps = explist_ctx.exp()
        result = []
        for i, e in enumerate(exps):
            val = self.visit(e)
            if i == len(exps) - 1 and isinstance(val, list):
                result.extend(val)
            else:
                result.append(val[0] if isinstance(val, list) and val else val)
        return result

    def _assign_var(self, var_ctx: LuaParser.VarContext, value):
        if var_ctx.getChildCount() == 1:
            self.set_var(var_ctx.getText(), value, is_local=False)
            return

        container = self.visit(var_ctx.prefixexp())
        sep = var_ctx.getChild(1).getText()
        if sep == '[':
            key = self.visit(var_ctx.exp())
        else:
            key = var_ctx.NAME().getText()

        if isinstance(container, LuaTable):
            res = container.set(key, value)
            if isinstance(res, tuple) and len(res) == 5 and res[0] == "__call_newindex__":
                _, fn_mm, tbl_obj, k_obj, v_obj = res
                self.invoke_callable(fn_mm, [tbl_obj, k_obj, v_obj])

    # --- EXPRESIONES ---
    def visitExplist(self, ctx: LuaParser.ExplistContext):
        return [self.visit(e) for e in ctx.exp()]

    def visitExp(self, ctx: LuaParser.ExpContext):
        n = ctx.getChildCount()

        if n == 1:
            child = ctx.getChild(0)
            text = child.getText()
            if text == 'nil':
                return None
            if text == 'true':
                return True
            if text == 'false':
                return False
            if text == '...':
                return self.get_var('...') or []
            return self.visit(child)

        if n == 2:
            op = ctx.getChild(0).getText()
            val = self.visit(ctx.exp(0))
            if op == '-':
                mm = self._get_metamethod(val, None, "__unm")
                if mm is not None:
                    return self.invoke_callable(mm, [val, val])
                return -self._to_num(val)
            if op == 'not':
                return not self.is_truthy(val)
            if op == '#':
                mm = self._get_metamethod(val, None, "__len")
                if mm is not None:
                    return self.invoke_callable(mm, [val])
                if isinstance(val, LuaTable):
                    return val.length()
                return len(val) if val is not None else 0
            if op == '~':
                return ~int(self._to_num(val))

        if n == 3:
            if ctx.getChild(0).getText() == '(':
                return self.visit(ctx.exp(0))

            op = ctx.getChild(1).getText()
            left = self.visit(ctx.exp(0))

            if op == 'and':
                return self.visit(ctx.exp(1)) if self.is_truthy(left) else left
            if op == 'or':
                return left if self.is_truthy(left) else self.visit(ctx.exp(1))

            right = self.visit(ctx.exp(1))

            ARITH_MM = {
                '+': '__add', '-': '__sub', '*': '__mul', '/': '__div',
                '//': '__idiv', '%': '__mod', '^': '__pow',
                '&': '__band', '|': '__bor', '~': '__bxor', '<<': '__shl', '>>': '__shr'
            }
            if op in ARITH_MM:
                mm = self._get_metamethod(left, right, ARITH_MM[op])
                if mm is not None:
                    return self.invoke_callable(mm, [left, right])

                # Operadores bitwise (solo enteros o floats exactos; rechaza strings)
                if op in ('&', '|', '~', '<<', '>>'):
                    li = self._to_lua_int(left)
                    ri = self._to_lua_int(right)
                    if op == '&':
                        return li & ri
                    if op == '|':
                        return li | ri
                    if op == '~':
                        return li ^ ri
                    if op == '<<':
                        if ri < 0:
                            ri = -ri
                            return 0 if ri >= 64 else (li >> ri)
                        return 0 if ri >= 64 else ((li << ri) & 0xFFFFFFFFFFFFFFFF)
                    if op == '>>':
                        if ri < 0:
                            ri = -ri
                            return 0 if ri >= 64 else ((li << ri) & 0xFFFFFFFFFFFFFFFF)
                        return 0 if ri >= 64 else (li >> ri)

                l_num = self._to_num(left)
                r_num = self._to_num(right)
                if op == '+':
                    return l_num + r_num
                if op == '-':
                    return l_num - r_num
                if op == '*':
                    return l_num * r_num
                if op == '/':
                    if r_num == 0:
                        if l_num == 0:
                            return float('nan')
                        return float('inf') if l_num > 0 else float('-inf')
                    return float(l_num) / float(r_num)
                if op == '//':
                    return l_num // r_num
                if op == '%':
                    return l_num % r_num
                if op == '^':
                    return float(l_num ** r_num)

            if op == '..':
                mm = self._get_metamethod(left, right, "__concat")
                if mm is not None:
                    return self.invoke_callable(mm, [left, right])
                return self._format_lua_val(left) + self._format_lua_val(right)

            if op == '==':
                if isinstance(left, LuaTable) and isinstance(right, LuaTable) and left is not right:
                    mm = self._get_metamethod(left, right, "__eq")
                    if mm is not None:
                        return self.is_truthy(self.invoke_callable(mm, [left, right]))
                return left == right
            if op == '~=':
                if isinstance(left, LuaTable) and isinstance(right, LuaTable) and left is not right:
                    mm = self._get_metamethod(left, right, "__eq")
                    if mm is not None:
                        return not self.is_truthy(self.invoke_callable(mm, [left, right]))
                return left != right
            if op == '<':
                mm = self._get_metamethod(left, right, "__lt")
                if mm is not None:
                    return self.is_truthy(self.invoke_callable(mm, [left, right]))
                return left < right
            if op == '<=':
                mm = self._get_metamethod(left, right, "__le")
                if mm is not None:
                    return self.is_truthy(self.invoke_callable(mm, [left, right]))
                mm_lt = self._get_metamethod(right, left, "__lt")
                if mm_lt is not None:
                    return not self.is_truthy(self.invoke_callable(mm_lt, [right, left]))
                return left <= right
            if op == '>':
                mm = self._get_metamethod(right, left, "__lt")
                if mm is not None:
                    return self.is_truthy(self.invoke_callable(mm, [right, left]))
                return left > right
            if op == '>=':
                mm = self._get_metamethod(right, left, "__le")
                if mm is not None:
                    return self.is_truthy(self.invoke_callable(mm, [right, left]))
                mm_lt = self._get_metamethod(left, right, "__lt")
                if mm_lt is not None:
                    return not self.is_truthy(self.invoke_callable(mm_lt, [left, right]))
                return left >= right

        return self.visitChildren(ctx)

    # --- EVALUADOR UNIFICADO DE PREFIXEXP, VAR Y FUNCTIONCALL ---
    def _eval_chain(self, ctx):
        children = list(ctx.getChildren())
        idx = 0
        current = None

        first = children[0]
        first_text = first.getText()
        if first_text == '(':
            current = self.visit(children[1])
            idx = 3
        elif isinstance(first, (LuaParser.VarContext, LuaParser.PrefixexpContext, LuaParser.FunctioncallContext)):
            current = self.visit(first)
            idx = 1
        else:
            current = self.get_var(first_text)
            idx = 1

        while idx < len(children):
            token_text = children[idx].getText()
            if token_text == '[':
                key = self.visit(children[idx + 1])
                current = current.get(key) if isinstance(current, LuaTable) else None
                if isinstance(current, tuple) and len(current) == 4 and current[0] == "__call_index__":
                    _, fn_mm, tbl_obj, k_obj = current
                    current = self.invoke_callable(fn_mm, [tbl_obj, k_obj])
                idx += 3
            elif token_text == '.':
                key = children[idx + 1].getText()
                current = current.get(key) if isinstance(current, LuaTable) else None
                if isinstance(current, tuple) and len(current) == 4 and current[0] == "__call_index__":
                    _, fn_mm, tbl_obj, k_obj = current
                    current = self.invoke_callable(fn_mm, [tbl_obj, k_obj])
                idx += 2
            elif token_text == ':':
                method_name = children[idx + 1].getText()
                if isinstance(current, LuaTable):
                    method = current.get(method_name)
                    if isinstance(method, tuple) and len(method) == 4 and method[0] == "__call_index__":
                        _, fn_mm, tbl_obj, k_obj = method
                        method = self.invoke_callable(fn_mm, [tbl_obj, k_obj])
                elif isinstance(current, str):
                    method = self.scopes[0]["string"].get(method_name)
                else:
                    method = None
                args = [current] + self.visit(children[idx + 2])
                current = self.invoke_callable(method, args)
                idx += 3
            elif isinstance(children[idx], LuaParser.ArgsContext):
                args = self.visit(children[idx])
                current = self.invoke_callable(current, args)
                idx += 1
            else:
                idx += 1

        return current

    def visitVar(self, ctx: LuaParser.VarContext):
        return self._eval_chain(ctx)

    def visitPrefixexp(self, ctx: LuaParser.PrefixexpContext):
        return self._eval_chain(ctx)

    def visitFunctioncall(self, ctx: LuaParser.FunctioncallContext):
        return self._eval_chain(ctx)

    def visitFunctiondef(self, ctx: LuaParser.FunctiondefContext):
        return self._build_function(ctx.funcbody())

    def visitArgs(self, ctx: LuaParser.ArgsContext):
        if ctx.explist():
            return self._eval_explist_flat(ctx.explist())
        if ctx.tableconstructor():
            return [self.visit(ctx.tableconstructor())]
        if ctx.string():
            return [self.visit(ctx.string())]
        return []

    # --- TABLAS ({}) ---
    def visitTableconstructor(self, ctx: LuaParser.TableconstructorContext):
        tbl = LuaTable()
        if not ctx.fieldlist():
            return tbl

        fields = ctx.fieldlist().field()
        array_index = 1
        for idx_f, field_ctx in enumerate(fields):
            is_last = (idx_f == len(fields) - 1)
            first_text = field_ctx.getChild(0).getText()
            if first_text == '[':
                key = self.visit(field_ctx.exp(0))
                val = self.visit(field_ctx.exp(1))
                if isinstance(val, list):
                    val = val[0] if val else None
                tbl.set(key, val)
            elif field_ctx.getChildCount() == 3 and field_ctx.getChild(1).getText() == '=':
                key = field_ctx.NAME().getText()
                val = self.visit(field_ctx.exp(0))
                if isinstance(val, list):
                    val = val[0] if val else None
                tbl.set(key, val)
            else:
                val = self.visit(field_ctx.exp(0))
                if isinstance(val, list):
                    if is_last:
                        for item in val:
                            tbl.set(array_index, item)
                            array_index += 1
                    else:
                        tbl.set(array_index, val[0] if val else None)
                        array_index += 1
                else:
                    tbl.set(array_index, val)
                    array_index += 1

        return tbl

    # --- LITERALES ---
    def visitNumber(self, ctx: LuaParser.NumberContext):
        text = ctx.getText()
        if text.startswith(('0x', '0X')):
            if '.' in text or 'p' in text or 'P' in text:
                return float.fromhex(text)
            return int(text, 16)
        if '.' in text or 'e' in text or 'E' in text:
            return float(text)
        return int(text)

    def visitString(self, ctx: LuaParser.StringContext):
        text = ctx.getText()
        if text.startswith(('"', "'")):
            raw = text[1:-1]
            raw = re.sub(r'\\z\s*', '', raw)
            raw = re.sub(r'\\u\{([0-9a-fA-F]+)\}', lambda m: chr(int(m.group(1), 16)), raw)
            raw = re.sub(r'\\(\d{1,3})', lambda m: chr(int(m.group(1), 10)), raw)
            simple_escapes = {
                'a': '\a', 'b': '\b', 'f': '\f', 'n': '\n',
                'r': '\r', 't': '\t', 'v': '\v', '\\': '\\',
                '"': '"', "'": "'", '\n': '\n'
            }

            def _replace_esc(m):
                seq = m.group(1)
                if seq.startswith('x') and len(seq) == 3:
                    return chr(int(seq[1:], 16))
                return simple_escapes.get(seq, seq)

            return re.sub(r'\\(x[0-9a-fA-F]{2}|.)', _replace_esc, raw)

        if text.startswith('['):
            m = re.match(r'^\[(=*)\[\r?\n?(.*)\]\1\]$', text, re.DOTALL)
            if m:
                return m.group(2)
            return text[2:-2]
        return text