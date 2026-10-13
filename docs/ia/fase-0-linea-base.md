# Línea base de la narrativa semanal

Modelo configurado: `claude-opus-5-5`. Clave de Anthropic: no cargada.

## 1. Plantilla (sin IA) sobre casos sintéticos

| Caso | Ánimo | Palabras | Pasa su propio chequeo | Datos mostrados | ms |
|---|---|---|---|---|---|
| crece | festeja | 27 | sí | 4 | 0.04 |
| crece-con-costo-que-sube | festeja | 40 | sí | 4 | 0.02 |
| cae-fuerte | preocupada | 27 | sí | 4 | 0.01 |
| pierde-plata | preocupada | 21 | sí | 4 | 0.01 |
| estable | neutral | 26 | sí | 4 | 0.01 |
| sin-semana-anterior | festeja | 20 | sí | 4 | 0.01 |
| sin-publicidad | festeja | 21 | sí | 3 | 0.01 |
| dolares | festeja | 27 | sí | 4 | 0.01 |
| cifras-grandes | festeja | 27 | sí | 4 | 0.01 |
| cifras-chicas | festeja | 27 | sí | 4 | 0.02 |
| sin-datos | neutral | 32 | sí | 3 | 0.01 |

La plantilla pasa su propio chequeo en 11 de 11 casos.

Textos de la plantilla:

- **crece**: En los últimos 7 días vendiste $1.500.000 y te quedaron $300.000 de ganancia real, un 20% más que la semana anterior. Tu True ROAS fue de 2,50.
- **crece-con-costo-que-sube**: En los últimos 7 días vendiste $1.500.000 y te quedaron $300.000 de ganancia real, un 20% más que la semana anterior. Lo que más subió fue las comisiones: $45.000 más que la semana anterior. Tu True ROAS fue de 2,50.
- **cae-fuerte**: En los últimos 7 días vendiste $1.500.000 y te quedaron $120.000 de ganancia real, un 52% menos que la semana anterior. Tu True ROAS fue de 0,60.
- **pierde-plata**: En los últimos 7 días vendiste $1.500.000, pero después de costos y publicidad perdiste $80.000. Tu True ROAS fue de -0,40.
- **estable**: En los últimos 7 días vendiste $1.500.000 y te quedaron $300.000 de ganancia real, lo mismo que la semana anterior. Tu True ROAS fue de 2,50.
- **sin-semana-anterior**: En los últimos 7 días vendiste $1.500.000 y te quedaron $300.000 de ganancia real. Tu True ROAS fue de 2,50.
- **sin-publicidad**: En los últimos 7 días vendiste $1.500.000 y te quedaron $300.000 de ganancia real, un 20% más que la semana anterior.
- **dolares**: En los últimos 7 días vendiste US$18.400 y te quedaron US$3.150 de ganancia real, un 20% más que la semana anterior. Tu True ROAS fue de 1,90.
- **cifras-grandes**: En los últimos 7 días vendiste $48.250.000 y te quedaron $9.800.000 de ganancia real, un 20% más que la semana anterior. Tu True ROAS fue de 2,50.
- **cifras-chicas**: En los últimos 7 días vendiste $4.300 y te quedaron $900 de ganancia real, un 20% más que la semana anterior. Tu True ROAS fue de 1,30.
- **sin-datos**: Todavía no tengo ventas ni gasto en publicidad de los últimos 14 días para comparar. Cuando conectes tu tienda y tus cuentas de publicidad, acá te cuento cómo te fue cada semana.

## 2. Cómo trata el chequeo de cifras distintos formatos

| Situación | Texto | Lo acepta | Debería aceptarlo | Resultado |
|---|---|---|---|---|
| Copia exacta de una cifra | Te quedaron $300.000 de ganancia real. | sí | sí | ok |
| Cifra inventada | Te quedaron $310.000 de ganancia real. | no | no | ok |
| Porcentaje que está en los datos | La ganancia real subió un 20%. | sí | sí | ok |
| Cifra real atribuida a otra magnitud (25 es el cambio de ventas) | La ganancia real subió un 25%. | sí | no | REVISAR |
| Porcentaje inventado | La ganancia real subió un 33%. | no | no | ok |
| Monto sin separador de miles | Ganaste 300000 pesos. | no | sí | REVISAR |
| Monto abreviado | Ganaste 300 mil pesos. | no | sí | REVISAR |
| Monto en millones | Vendiste 1,5 millones. | no | sí | REVISAR |
| Cifra negativa mostrada como positiva | Perdiste $300.000 esta semana. | sí | no | REVISAR |
| Sentido contrario al dato | La ganancia real bajó un 20%. | sí | no | REVISAR |
| Cifra de otra magnitud (20 contra 20.000) | Gastaste $20.000 en publicidad. | no | no | ok |
| True ROAS con coma | Tu True ROAS fue de 2,50. | sí | sí | ok |
| True ROAS con punto | Tu True ROAS fue de 2.50. | no | sí | REVISAR |

El chequeo coincide con lo esperado en 6 de 13 situaciones. Las marcadas REVISAR son lo que hay que resolver en la fase de validación.

## 3. Latencia y calidad con Claude

No se corrió (falta `--live`).
