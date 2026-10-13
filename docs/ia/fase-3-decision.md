# Fase 3 — Decisión y limpieza

Sigue a la [Fase 2](fase-2-validador.md). El roadmap tenía dos ramas para esta fase, según si se activaba la IA o no. Se eligió **no activarla**:
la narrativa seguirá escribiéndola la plantilla.

## La decisión y sus datos

| Dato | Valor |
|---|---|
| Clave de IA en producción | Ninguna. La narrativa nunca la escribió un modelo |
| Plantilla frente a su propio chequeo | 11 de 11 casos |
| Validador de cifras | 13 de 13 situaciones de prueba |
| Costo de la plantilla | Cero, sin latencia y sin proveedor |
| Experimento con un proveedor alojado | No se hizo. No hay datos de que un modelo mejore el texto |

Sin evidencia de que un modelo mejore lo que ya sale de la plantilla, mantener un proveedor, una clave, un costo mensual y una dependencia no se justifica.
La decisión se puede revisar: el camino para medirlo sigue descrito en la Fase 2.

## Qué se retiró

| Pieza | Estado |
|---|---|
| Escritor de Anthropic (`anthropic_writer.py`) | Eliminado. Queda en el historial de git |
| Paquete `anthropic` en `requirements.txt` | Eliminado |
| `ANTHROPIC_API_KEY` y `NARRATIVE_MODEL` | Eliminadas de la configuración, de Docker Compose y de los `.env.example` |
| `NARRATIVE_PROVIDER` | Queda en la configuración con un solo valor válido (`template`), pero ya no está en Docker Compose ni en los `.env.example` |
| Tests del escritor de Anthropic | Eliminados. Un test comprueba que ningún módulo de la app importa `anthropic` |

Si quedó una `ANTHROPIC_API_KEY` o `NARRATIVE_MODEL` cargada en algún entorno (por ejemplo en Railway), la aplicación la ignora. No hay que borrarla para que arranque.

## Qué se conserva

El punto de extensión: contrato `NarrativeWriter`, `NarrativeService`, el validador de cifras, la caché diaria y las columnas `provider` y `model`.
Agregar un proveedor en el futuro es escribir un escritor, listarlo en `PROVIDERS` y devolverlo desde `build_writer()` (ver `narrative_writers/factory.py`).
Todo lo demás ya funciona: fallo del proveedor, cifras inventadas y no cachear un resultado que cayó a la plantilla.

## Verificación

- Suite completa con base de datos y `ruff` limpio (resultado en el mensaje de cierre).
- Un test arranca un intérprete con `anthropic` bloqueado y comprueba que la narrativa se genera con la plantilla.
- Un test recorre `app/` y falla si algún módulo vuelve a importar `anthropic`.

## Criterio de salida del roadmap

| Criterio | Estado |
|---|---|
| Narrativa estable en producción con la plantilla | Cumplido: es lo que corre hoy |
| Se puede volver a la plantilla sin tocar código | Cumplido, es el único camino. No hay nada que apagar |
| Decisión documentada con sus números | Cumplido (este documento) |
| Dependencia de `anthropic` limpiada | Cumplido |

## Despliegue

Sin migración. El cambio solo quita código y configuración, así que se puede desplegar sin pasos previos.
