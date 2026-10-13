# Fase 1 — Base sólida y plantilla mejorada

Sigue a la [Fase 0](fase-0-auditoria.md). Sin clave de IA en producción, la narrativa semanal siempre sale de la plantilla,
así que esta fase mejora esa base y deja el proveedor como una pieza opcional. No cambia lo que ven los usuarios,
salvo una corrección de gramática.

## Qué se hizo

| Actividad del roadmap | Resultado |
|---|---|
| 1. Corregir la gramática de la plantilla | "Lo que más subió **fueron** las comisiones". El verbo concuerda con el costo (plural con "los" y "las"). Hay una prueba por cada costo. |
| 2. `NarrativeWriter`, resultado con estado y `TemplateNarrativeWriter` | Paquete nuevo [`narrative_writers/`](../../backend/app/services/narrative_writers/): contrato, resultado (`written`, `disabled`, `failed`), servicio, fábrica y escritor de plantilla. |
| 3. Anthropic en su propio escritor, importado solo con clave | [`anthropic_writer.py`](../../backend/app/services/narrative_writers/anthropic_writer.py). `weekly_narrative.py` ya no importa `anthropic`. |
| 4. Capturar todo error del proveedor | El escritor devuelve un fallo para cualquier error de la librería, y el servicio atrapa además cualquier excepción inesperada. La ruta no responde 500 por eso. |
| 5. Registrar proveedor y modelo | Columnas `provider` y `model` en `weekly_narratives` (migración [034](../../db/init/034_narrative_provider.sql)). |
| 6. Reenviar las variables en Docker Compose | `NARRATIVE_PROVIDER`, `ANTHROPIC_API_KEY` y `NARRATIVE_MODEL`, más los `.env.example` y el de producción. |
| 7. Reemplazar el mock de Claude por un escritor falso | Los tests usan `FakeWriter`. Solo los del escritor de Anthropic simulan su cliente, sin red. |
| 8. Enriquecer la plantilla (opcional) | No se hizo. Queda para cuando se decida el rumbo de la IA. |

## Configuración

`NARRATIVE_PROVIDER` vale `template` o `anthropic`. Vacío significa `anthropic` si hay `ANTHROPIC_API_KEY`, y `template` si no,
así que ninguna instalación existente cambia de comportamiento. Un valor desconocido, o `anthropic` sin clave, **detiene la aplicación al arrancar**
con un mensaje claro, en lugar de caer en silencio a la plantilla.

## Un error que encontraron los tests nuevos

Un texto de solo espacios pasaba como válido: no tiene números, así que el chequeo de cifras no lo rechazaba, y se guardaba como "ia".
El servicio ahora recorta el texto y lo trata como vacío.

## Fallos de una sesión anterior que se corrigieron acá

Al agregar el envío de emails en segundo plano (timeout de SMTP y `BackgroundTasks`) se rompieron 7 tests que parchean `send_email` en las rutas
de contraseña, invitaciones y pedidos de demo. No se vio antes porque esos tests necesitan la base de datos. Ahora parchean `app.email.send_email`,
que es donde se llama, y `send_email_quietly` lo invoca con argumentos con nombre. La suite completa pasa.

## Cómo se verificó

- Suite completa, con base de datos: **447 tests pasan** (Docker con TimescaleDB local). Antes de esta fase eran 419.
- `ruff check .` sin errores.
- `docker compose config` válido.
- Una prueba arranca un intérprete con `anthropic` bloqueado y comprueba que la narrativa se genera con la plantilla.
- El script de línea base sigue corriendo. Su salida guardada en [fase-0-linea-base.md](fase-0-linea-base.md) es la de **antes** de esta fase
  y se deja así a propósito: documenta el punto de partida, incluido el error de gramática.

## Criterios de salida del roadmap

| Criterio | Estado |
|---|---|
| Los tests de la narrativa pasan con un escritor falso y sin instalar `anthropic` | Cumplido |
| La plantilla pasa su chequeo en 11 de 11 casos y ya no dice "fue las comisiones" | Cumplido |
| Cada narrativa guarda proveedor y modelo | Cumplido, **una vez aplicada la migración 034** |
| Las variables llegan por Docker Compose | Cumplido |

## Orden de despliegue

La migración tiene que aplicarse **antes** de que se despliegue este código. El código nuevo escribe las columnas `provider` y `model`, y si no
existen, guardar una narrativa falla. Ver `DEPLOY.md`:

1. Aplicar en la base de producción:
   ```bash
   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/init/034_narrative_provider.sql
   ```
   Es idempotente (`ADD COLUMN IF NOT EXISTS`) y las filas existentes quedan como `template`.
2. Recién después, hacer el push del código.

Si no se aplica, la consecuencia es que `GET /stores/{id}/narrative` y el resumen semanal fallan hasta que se aplique.
