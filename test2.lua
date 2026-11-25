-- Prueba de for, if/elseif/else y break
suma = 0
for i = 1, 10 do
    if i == 8 then
        break
    elseif i % 2 == 0 then
        suma = suma + i
    end
end
print("Suma pares < 8:", suma) -- 2 + 4 + 6 = 12

-- Prueba de repeat-until con variable local del bloque
n = 1
repeat
    local doble = n * 2
    n = n + 1
until doble >= 10
print("n final:", n) -- 6 (cuando n=5, doble=10, n pasa a 6 y termina)