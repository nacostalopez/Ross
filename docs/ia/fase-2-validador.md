# Fase 2 — Validador de cifras

Sigue a la [Fase 1](fase-1-base-solida.md). El chequeo viejo solo preguntaba si cada número del texto aparecía en los datos. Dejaba pasar textos
falsos con cifras reales y rechazaba textos correctos escritos con otro formato. Esta parte de la fase lo reemplaza. El experimento con un
proveedor alojado se descartó por ahora (ver la decisión al final).

## Qué cambió

Módulo nuevo [`narrative_validator.py`](../../backend/app/services/narrative_validator.py). `check_figures(texto, hechos)` devuelve `None` si el texto
se puede mostrar, o un código con el primer problema:

| Código | Significa | Ejemplo |
|---|---|---|
| `unknown_figure` | La cifra no está en los datos | "Te quedaron $310.000" |
| `wrong_metric` | Es una cifra real dicha sobre otra cosa | "La ganancia subió un 25%" (25 es el cambio de ventas) |
| `wrong_sign` | Una pérdida contada como ganancia, o al revés | "Perdiste $300.000" cuando hubo ganancia |
| `wrong_direction` | Dice que subió lo que bajó | "La ganancia real bajó un 20%" |

Formatos que ahora acepta: `300000`, `300 mil`, `1,5 millones`, `2.50` y `2,50`. Las cifras abreviadas pueden estar redondeadas a lo que
muestran, con un margen máximo del 1%. Una cifra pegada a otra magnitud (20 contra 20.000) se distingue por el signo `$`, el `%` o la palabra "pesos".

El servicio ya no recibe un booleano: recibe el código y lo guarda como motivo (`rejected_unknown_figure`, etc.), y lo registra en el log.
Eso permite medir después por qué se descartan los textos de un modelo.

## Resultado

| Medición | Antes | Ahora |
|---|---|---|
| Situaciones de prueba resueltas bien ([línea base](fase-2-linea-base.md)) | 6 de 13 | **13 de 13** |
| Plantilla pasa su propio chequeo | 11 de 11 | 11 de 11 |
| Suite completa con base de datos | 447 | **483 pasan**, `ruff` limpio |

Los tests nuevos cubren 23 textos de prueba, pérdidas, porcentajes que no existen en los datos, costos sin porcentaje y que la plantilla pase su propio
chequeo con 11 combinaciones de datos.

## Límites conocidos

- Mira cifras, no hechos. "Tu ganancia es buena" sin números pasa, y una frase como "vendiste más que nunca" no se puede comprobar.
- La atribución usa la palabra más cercana ("ventas", "ganancia", "publicidad"). Una frase muy enredada puede rechazarse de más, lo cual cae a la plantilla
  y no muestra nada falso. El error contrario (aceptar algo mal atribuido) es el que se busca evitar.
- Los números escritos en palabras ("trescientos mil") no se leen. El prompt pide copiar las cifras tal como están.

## Decisión: la plantilla es la definitiva por ahora

Se decidió no hacer el experimento con un proveedor alojado y dejar la plantilla como la narrativa del producto. No hay clave de IA en producción, la
plantilla pasa su chequeo en todos los casos y no cambia nada para los usuarios.

La IA sigue siendo una pieza opcional: el código, el validador y las pruebas quedan listos. Si más adelante se quiere medirla, los pasos son:

```bash
cd backend
ANTHROPIC_API_KEY=... ../.venv/Scripts/python ../scripts/narrative_baseline.py --live --repeat 3 --out ../docs/ia/fase-2-experimento.md
```

Mide latencia (mediana y p95), qué fracción de textos acepta el validador y por qué descarta el resto. Para activarla en producción:
`NARRATIVE_PROVIDER=anthropic` y `ANTHROPIC_API_KEY` en el servicio Ross de Railway.
