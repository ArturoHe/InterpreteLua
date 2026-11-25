-- 1. Recursividad y return
function factorial(n)
    if n <= 1 then
        return 1
    end
    return n * factorial(n - 1)
end

print("Factorial de 6:", factorial(6))

-- 2. Retornos múltiples
function min_max(a, b)
    if a < b then
        return a, b
    else
        return b, a
    end
end

menor, mayor = min_max(42, 15)
print("Menor y mayor:", menor, mayor)

-- 3. Tablas (arreglos indexados en 1 + diccionarios + table.insert)
persona = {
    nombre = "Ana",
    notas = {4.5, 3.8, 5.0}
}
table.insert(persona.notas, 4.7)

suma = 0
for i, nota in ipairs(persona.notas) do
    suma = suma + nota
end

promedio = suma / #persona.notas
print(persona.nombre .. " - Promedio:", promedio, "Raiz(16):", math.sqrt(16))