a = 10
b = 20
a, b = b, a
print("Intercambio:", a, b)

do
    local a = 99
    print("Dentro de bloque:", a, b)
end

print("Fuera de bloque:", a, (3 + 4) * 2 ^ 3, "Lua" .. " 5.4")