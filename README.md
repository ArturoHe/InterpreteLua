
---

## Integrantes

| Integrantes |
|---|
| David Santiago Velasquez Gómez |
| Ana Maria González Hernández |
| Jose Arturo Herrera Rivera |


---

## Alcance del trabajo

El entregable es un **intérprete**: el árbol sintáctico se evalúa directamente y los `print` del programa Lua salen por la salida estándar.


---

## Cómo funciona

```
programa .lua (stdin)
        │
        ▼
   LuaLexer (ANTLR)  ──►  tokens
        │
        ▼
   LuaParser (ANTLR) ──►  árbol de parseo  (start_ → chunk → block)
        │
        ▼
   EvalVisitor (Python) ──►  ejecución
        │
        ▼
     stdout
```

1. `main.py` lee todo el programa desde `stdin` y arma el lexer/parser.
2. El parser se ejecuta primero en modo **SLL** (más rápido) y, si falla, se reintenta en modo **LL**.
3. `EvalVisitor` recorre el árbol y ejecuta sentencias y expresiones sobre valores propios: `LuaTable`, `LuaFunction`, `LuaThread`, `LuaUserdata`, más `None`/`bool`/`int`/`float`/`str` de Python.

---

## Funcionalidades implementadas

**Valores y tablas**
- `nil`, booleanos, enteros, flotantes, cadenas (cortas, largas y con escapes), funciones, hilos, userdata y tablas.
- Tablas con claves dinámicas, separadores `,`/`;`, expansión del último campo y longitud con `#`.
- Tablas anidadas y matrices.

**Sentencias y control de flujo**
- Asignación simple y múltiple (`a, b = b, a`), `local`, `local function`, atributos `<const>` / `<close>`.
- `do`, `if` / `elseif` / `else`, `while`, `repeat` / `until`, `for` numérico y `for` genérico.
- `break`, `goto` con etiquetas `::nombre::` y `return` con varios valores.
- Definición de funciones, métodos con `:` y funciones anidadas con `self` inyectado.

**Funciones y cierres**
- Parámetros, argumentos variables (`...`), retorno múltiple y ajuste de valores en llamadas y asignaciones.
- Cierres con estado compartido (contadores, closures).

**Operadores**
- Aritméticos (`+ - * / // % ^`), concatenación (`..`), comparación, lógicos con cortocircuito (`and` / `or`), longitud (`#`) y operadores a nivel de bits (`& | ~ << >>`).
- Precedencia y asociatividad de Lua; la división por cero da `inf`/`nan` en lugar de error.

**Metatablas y metamétodos**
- `__index` (tabla o función), `__newindex`, `__call`, `__tostring`, `__len`, `__unm`, `__eq`, `__lt`, `__le`, `__concat`, `__close`.
- Aritméticos y de bits: `__add`, `__sub`, `__mul`, `__div`, `__idiv`, `__mod`, `__pow`, `__band`, `__bor`, `__bxor`, `__shl`, `__shr`.
- Tablas débiles (`__mode`) y `rawget` / `rawset`.

**Errores**
- `error` con cualquier valor (no solo cadenas), `assert`, `pcall` y `xpcall` con el mensaje procesado por el handler.

**Corrutinas**
- `coroutine.create`, `resume`, `yield`, `status` y `running`, incluyendo el paso de valores entre `resume` y `yield`.

**Biblioteca estándar**
- Base: `print`, `type`, `tostring`, `tonumber` (con base, hexadecimal y hex-float), `next`, `select`, `load` / `loadstring`, `setmetatable`, `getmetatable`, `collectgarbage`, `_G`.
- `math`: `abs`, `ceil`, `floor`, `sqrt`, `sin`, `cos`, `tan`, `log`, `exp`, `max`, `min`, `pi`, `huge`.
- `table`: `insert`, `remove`, `pack`, `unpack`.
- `string`: `len`, `lower`, `upper`, `rep`, `reverse`, `sub`, `find`, `gsub`, `byte`, `char` (con azúcar `s:upper()`).
- `io`: `write`, `stdin`, `stdout`, `stderr`.
- `coroutine`: ver arriba.

---

## Requisitos

- **Python 3.8 o superior** (en este repositorio se ejecutó con Python 3.14).
- **Runtime de ANTLR para Python:**

```bash
python -m pip install antlr4-python3-runtime==4.13.2
```

No se necesitan librerías adicionales.

---

## Uso

```bash
python main.py < test1.lua
```

En PowerShell:

```powershell
Get-Content test1.lua -Raw | python main.py
```

Ejemplo (`test1.lua`, columnas separadas por tabulaciones):

```
Intercambio:	20	10
Dentro de bloque:	99	10
Fuera de bloque:	20	56	Lua 5.4
```

---

## Estructura del proyecto

| Archivo | Descripción |
|---|---|
| `main.py` | Punto de entrada: lee `stdin`, parsea (SLL → LL) y ejecuta |
| `EvalVisitor.py` | El intérprete: valores, entornos, operadores, sentencias, metatablas, corrutinas y biblioteca estándar |
| `LuaLexer.g4` / `LuaParser.g4` | Gramáticas ANTLR del lenguaje |
| `LuaLexer.py`, `LuaParser.py`, `LuaParserVisitor.py`, `*.interp`, `*.tokens` | Código generado por ANTLR. No se edita a mano |
| `LuaLexerBase.py` | Lógica del lexer que no se puede expresar con reglas: comentarios largos y shebang |
| `LuaParserBase.py` | Predicado que distingue `a.b` de `a.b(x)` |
| `transformGrammar.py` | Utilidad para adaptar las gramáticas antes de regenerar el parser |
| `test1.lua` … `test4.lua` | Pruebas de ejemplo |
| `01.in` … `06.in` | Batería de pruebas organizada por secciones del manual de referencia de Lua |

---
