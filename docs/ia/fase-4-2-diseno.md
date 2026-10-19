# Fase 4, etapa 4.2 — Diseño: eventos, scoring y trazas

Parte de la [etapa 4.1](fase-4-1-diseno.md), cerrada: las nueve herramientas de solo lectura, el registro y la política ya existen. Esta etapa les agrega **memoria**: qué se pidió, cómo se resolvió y qué tan útil resultó. Es lo único del roadmap que no se puede recuperar después, porque la independencia progresiva (4.6) se alimenta de lo registrado desde el principio.

**Estado: borrador a confirmar. No hay código escrito.**

## 1. Alcance

| Dentro | Fuera (etapa) |
|---|---|
| Tabla de **trazas**: una fila por ejecución de una herramienta | Router de intenciones y pantalla del asistente (4.3) |
| Tabla de **eventos**: lo que la persona hace con el resultado | Puntaje de selección con sus tres comportamientos (4.3) |
| **Score de utilidad** por herramienta, calculado desde los eventos | Promoción de patrones a L1 (4.6) |
| Registro de cada ejecución desde `run_tool`, incluidas las denegaciones | Señales del CRM (4.4 y 4.7) |
| Primera señal real: la narrativa semanal (`weekly_narrative`) | Botones de utilidad en la pantalla (4.3) |
| Retención: las trazas vencen a los 180 días (decisión ya tomada) | |

## 2. Decisiones a confirmar

| # | Tema | Propuesta | Por qué |
|---|---|---|---|
| 1 | Dónde se registra | Dentro de `run_tool`, en un solo lugar. Ninguna herramienta registra por su cuenta | Una herramienta nueva queda trazada sin acordarse de hacerlo |
| 2 | Qué guarda una traza | `account_id`, `store_id`, herramienta, escalón (`L0`), proveedor (`template`), resultado (`ok` o la razón de denegación), latencia en ms, rol, fecha. **Los parámetros se guardan por nombre y tipo, nunca el valor** salvo los acotados (por ejemplo `days`) | Cumple lo decidido: sin valores ni texto libre en las trazas |
| 3 | Pregunta del usuario | **No se guarda en esta etapa.** No hay pregunta libre hasta la 4.3. Cuando exista, se guarda normalizada y sin valores | Evita diseñar la normalización sin ver preguntas reales |
| 4 | Cifras | Nunca en una traza ni en un evento. Solo la estructura de lo que se mostró | Las cifras cambian y son datos de la cuenta |
| 5 | Eventos | Tabla aparte, enlazada a la traza: `shown`, `useful`, `not_useful`, `dismissed`, `acted`. Un evento por persona y traza | Separa "se ejecutó" de "sirvió". Es el puntaje de utilidad, distinto del de selección |
| 6 | Score de utilidad | Por herramienta y escalón: `útiles / (útiles + no útiles)` sobre una ventana móvil de 30 días, con un **mínimo de 20 muestras**. Por debajo del mínimo, "sin datos" y no un número | Un porcentaje sobre 3 muestras engaña |
| 7 | Tasa de rechazo del validador | Se guarda por herramienta junto al score: es el segundo insumo de "confianza de la herramienta" de la 4.3 | Lo pide el roadmap |
| 8 | Cuenta demo | No escribe trazas ni eventos ni puntúa (ya decidido) | Evita contaminar el score con visitas |
| 9 | Aislamiento | Toda fila lleva `account_id`. Las lecturas del score pasan por la cuenta. Se suma al test de aislamiento existente | Mismo estándar que la 4.1 |
| 10 | Borrado de cuenta | Las dos tablas se borran con la cuenta, y entran en la exportación de "Privacidad y mis datos" | Compatible con `account_deletion.py` y el cumplimiento básico |
| 11 | Retención | Tarea periódica que borra trazas y eventos de más de 180 días | Decidido en la consulta 9 |
| 12 | Si falla el registro | Se ignora y se deja un log. **Registrar nunca rompe ni demora una respuesta** | La traza es observación, no parte del resultado |

## 3. Esquema propuesto

Migración `036` (la `035` la usó el consentimiento). Se aplica en cada base antes de desplegar.

```sql
CREATE TABLE assistant_traces (
    id UUID PRIMARY KEY,
    account_id UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    store_id UUID REFERENCES stores(id) ON DELETE CASCADE,
    tool VARCHAR(60) NOT NULL,
    tier VARCHAR(2) NOT NULL DEFAULT 'L0',
    provider VARCHAR(40) NOT NULL DEFAULT 'template',
    outcome VARCHAR(40) NOT NULL,          -- 'ok' o un DenialReason
    validator_rejected BOOLEAN NOT NULL DEFAULT FALSE,
    param_shape JSONB NOT NULL DEFAULT '{}',   -- {"days": 14}: solo parámetros acotados
    latency_ms INTEGER NOT NULL,
    role VARCHAR(20) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON assistant_traces (account_id, tool, created_at DESC);

CREATE TABLE assistant_events (
    id UUID PRIMARY KEY,
    trace_id UUID NOT NULL REFERENCES assistant_traces(id) ON DELETE CASCADE,
    account_id UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind VARCHAR(20) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (trace_id, user_id, kind)
);
```

El score no tiene tabla: se calcula con una consulta sobre estas dos. Si más adelante pesa, se agrega una vista materializada.

## 4. Orden de trabajo

| Paso | Qué | Verificación |
|---|---|---|
| 1 | Migración `036`, modelos y registro en `account_deletion` y en la exportación | Test de borrado de cuenta y de exportación |
| 2 | Servicio `app/ai/traces.py`: guardar traza, guardar evento, calcular score | Tests unitarios, incluido el mínimo de muestras |
| 3 | `run_tool` registra cada ejecución, con éxito o denegación, sin romper si falla | Test: una traza por llamada, cero valores guardados, el demo no registra |
| 4 | Guardián: ninguna traza contiene un valor de parámetro fuera de la lista acotada | Test sobre todo el registro, como el guardián de la 4.1 |
| 5 | Endpoint para registrar un evento sobre una traza propia, y otro para leer el score de una herramienta | Aislamiento entre cuentas y por rol |
| 6 | La narrativa semanal pasa por `run_tool` y suma una traza real. Se registra si el validador rechazó | Test con el escritor de plantilla |
| 7 | Tarea de retención de 180 días | Test con filas viejas |
| 8 | Aislamiento: las tablas nuevas entran al test que recorre el registro | Suite completa y `ruff` limpios |

## 5. Criterio de salida (del roadmap)

- El score se calcula con datos reales.
- La compuerta rechaza cifras inválidas y queda registrado.
- Las trazas no guardan valores ni texto libre.

## 6. Riesgos

| Riesgo | Mitigación |
|---|---|
| Una traza filtra un dato de la cuenta | Solo `param_shape` acotado, guardián que lo comprueba y sin pregunta libre |
| Registrar enlentece las respuestas | Escritura mínima y fallo silencioso. Se mide la latencia con y sin registro |
| Score engañoso con pocas muestras | Mínimo de 20 y "sin datos" explícito |
| Las trazas crecen sin límite | Retención de 180 días e índice por cuenta y fecha |
| Un usuario puntúa la traza de otra cuenta | La traza se busca siempre por `account_id` de la sesión |

## 7. Preguntas abiertas

1. ¿El mínimo de 20 muestras y la ventana de 30 días son razonables como punto de partida? Son hipótesis, igual que los pesos de la 4.3.
2. ¿`acted` (la persona hizo algo después) se puede medir ya? Sin pantalla del asistente no hay acción que observar, así que propongo dejar el valor reservado y usarlo desde la 4.3.
