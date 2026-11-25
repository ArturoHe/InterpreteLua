-- 1. Closure con estado mutable
function contador(inicio)
    local c = inicio
    return function(paso)
        c = c + (paso or 1)
        return c
    end
end

local inc = contador(10)
print("Closure:", inc(), inc(5), inc()) -- 11, 16, 17

-- 2. Métodos de string con ':' y con '.'
local texto = "Universidad Nacional"
print("Sub:", texto:sub(1, 11), string.sub(texto, -8)) -- Universidad, Nacional
print("Upper/Len:", texto:upper():sub(1, 4), texto:len()) -- UNIV, 20

-- 3. Matrices (tablas anidadas)
local mat = {}
for i = 1, 2 do
    mat[i] = {}
    for j = 1, 3 do
        mat[i][j] = i * 10 + j
    end
end
print("Matriz[2][3]:", mat[2][3]) -- 23