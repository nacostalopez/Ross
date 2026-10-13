# Fase 0 — Auditoría y línea base

Parte del [roadmap de IA de ROSS](../../README.md): desacoplar la narrativa semanal de Anthropic. Esta fase no cambia
el comportamiento de la aplicación. Deja medido el punto de partida y los riesgos.

- **Alcance auditado:** "Ross explica tu semana" (`backend/app/services/weekly_narrative.py`).
- **Fecha del relevamiento:** 13 de octubre de 2026, sobre `main`.
- **Línea base ejecutable:** [`scripts/narrative_baseline.py`](../../scripts/narrative_baseline.py).
  Su salida de este relevamiento está en [fase-0-linea-base.md](fase-0-linea-base.md).

## Hallazgo central

**No hay clave de Anthropic en producción** (confirmado el 13 de octubre de 2026). Entonces `ai_text()` devuelve `None`
y la narrativa semanal siempre sale de la plantilla: en el dashboard, en el resumen por email y en todos los entornos.
La IA para esta función nunca corrió en producción.

Lo que implica para el plan:

- **La dependencia de Anthropic es solo de código, no de operación.** Si Anthropic dejara de existir mañana, ningún usuario lo notaría.
  Sí queda una dependencia de arranque: `import anthropic` está al inicio de `weekly_narrative.py`, así que la aplicación
  no inicia si el paquete falta.
- **No hay línea base de Claude que medir.** No hay historial de textos escritos por IA (la tabla `weekly_narratives` solo tiene
  `source = 'plantilla'`) ni latencia real que comparar.
- **La pregunta pasa de "cómo reemplazar a Anthropic" a "¿queremos IA en la narrativa, y con qué proveedor?".** Esa
  decisión nunca se tomó con datos. Ver la sección 11.

## 1. Inventario de dependencias

Anthropic aparece en un solo punto de ejecución. Todo lo demás es configuración, pruebas o documentación.

| Dónde | Qué es | En qué fase se toca |
|---|---|---|
| `backend/app/services/weekly_narrative.py` | `import anthropic` y la llamada en `ai_text()` (líneas 214-248) | 1 |
| `backend/requirements.txt:17` | `anthropic==1.12.0` | 3 (se retira al final) |
| `backend/app/config.py:41-44` | `anthropic_api_key`, `narrative_model` | 1 |
| `backend/tests/test_weekly_narrative.py` | `_fake_claude` reemplaza `weekly_narrative.anthropic.Anthropic` | 1 |
| `.env.example`, `backend/.env.example`, `.env.production.example` | `ANTHROPIC_API_KEY`, `NARRATIVE_MODEL` (esta última no figura en el de producción) | 1 y 3 |
| `DEPLOY.md:28` y `README.md` | Documentan la clave y el comportamiento | 3 |
| `db/init/032_weekly_narratives.sql` | Un comentario que dice "Claude" y la columna `source` (`ia` o `plantilla`) | 1 |
| `backend/app/schemas/narrative.py` | `source: Literal["ia", "plantilla"]` | 1 |
| `scripts/commit.ps1` | Usa **Claude Code** para commitear. No es la API y no se toca | no aplica |

No hay uso de Anthropic en el frontend, en el cron ni en otros servicios. La narrativa se consume desde dos lugares:
`GET /stores/{id}/narrative` y el resumen semanal por email (`send_weekly_report`).

## 2. Cómo llega la configuración a cada entorno

| Entorno | `ANTHROPIC_API_KEY` | `NARRATIVE_MODEL` | Resultado |
|---|---|---|---|
| Desarrollo local (`.env` de esta máquina) | No está cargada | No está | Siempre plantilla |
| Docker Compose | **No se reenvía** al contenedor | **No se reenvía** | Siempre plantilla, aunque la tengas en `.env` |
| CI | No está | No está | Plantilla y cliente falso en los tests |
| Producción, servicio `backend` | **No está cargada** (confirmado) | No figura | Siempre plantilla |
| Producción, servicio `cron` | Hereda las del backend por referencia: tampoco | Idem | El resumen semanal también usa la plantilla |

Los usuarios ven hoy la plantilla en todos lados.

## 3. Pruebas existentes

| Conjunto | Cantidad | Necesita base de datos | Estado en esta máquina |
|---|---|---|---|
| Total | 419 | — | — |
| Sin base de datos (`-m "not db"`) | 92 | No | **92 pasan** |
| Con base de datos (`-m db`) | 327 | Sí, Postgres con TimescaleDB en el puerto 5433 | **No se pudieron correr**: Docker Desktop está apagado |
| De la narrativa | 18 | 11 no, 7 sí | Las 11 sin base pasan; las 7 con base quedan para CI |

CI corre las dos pasadas, con un servicio de Postgres, y no usa red ni la API de Anthropic.
Las pruebas de la narrativa que usan base de datos cubren: plantilla sin clave, texto de Claude con cifras conocidas,
caída a plantilla si inventa una cifra, reutilización del día, no llamar a Claude sin datos, y la ruta con aislamiento entre cuentas.

**Hallazgo corregido durante la auditoría.** El timeout de SMTP que se agregó al principio de esta sesión rompió
`tests/test_email.py::test_configured_smtp_sends_message`, y quedó así en `main`. Se corrigió el test para esperar el
timeout y se agregó una prueba de `send_email_quietly`. Con eso los 92 tests sin base pasan.

## 4. Latencia

Se midió lo que se puede sin clave y se dedujo del código lo demás. **La latencia de Claude no se puede medir** porque no hay clave,
y hoy no afecta a nadie: la llamada no se hace. Lo que sigue describe el comportamiento si algún día se carga una.

**Medido (offline):** armar la plantilla, el ánimo, los datos mostrados y las líneas del prompt toma menos de 0,1 ms
por caso. La plantilla no es un problema de rendimiento.

**Deducido del código:**
- `ai_text()` usa `timeout=30.0` y `max_retries=1`. El peor caso de una llamada es de **unos 60 segundos**, más la espera entre intentos.
- La llamada es **síncrona y está dentro del request** de `GET /stores/{id}/narrative`. Se hace una sola vez por tienda y por día. Si Claude está
  caído y hay clave, no se guarda nada y **cada apertura del dashboard vuelve a intentar**.
- En el frontend, la narrativa se pide **al final** de una cadena secuencial (métricas, conectores, creativos, narrativa). Si tarda, no retrasa
  las métricas, pero el widget queda cargando.
- El resumen semanal recorre las tiendas **una por una**. Con N tiendas y Claude lento, el peor caso se acerca a N minutos. No hay un usuario esperando.

## 5. Escenarios de falla

| Escenario | Dónde ocurre | Qué pasa hoy | ¿Se guarda en caché? |
|---|---|---|---|
| Sin clave | `ai_text()` devuelve `None` | Plantilla | Sí, con `source="plantilla"` |
| Límite de uso (`RateLimitError`) | `ai_text()` | Plantilla y aviso en el log | **No**: reintenta en la próxima visita |
| Error de la API (`APIStatusError`, incluye clave inválida) | `ai_text()` | Plantilla y aviso en el log | No |
| Error de conexión o timeout | `ai_text()` | Plantilla y aviso en el log | No |
| Respuesta que no terminó (`stop_reason` distinto de `end_turn`) | `ai_text()` | Plantilla | No |
| Texto con una cifra que no está en los datos | `_uses_only_known_figures()` | Plantilla | No |
| Respuesta vacía | `ai_text()` | Plantilla | No |
| Otro error de la librería (por ejemplo `APIResponseValidationError`) | **No se captura** | La excepción sube y `GET /narrative` responde **500** | No |
| Sin datos de la tienda | `get_or_build()` no llama a la IA | Plantilla de "sin datos" | Sí |
| Falla al guardar en la base | `get_or_build()` | Error 500 | No |

Hay que tener en cuenta que el job semanal atrapa los errores por tienda y sigue con las demás, pero la ruta del dashboard no.
**Como hoy no hay clave, solo se recorren las dos primeras filas y la de "sin datos".** El resto describe lo que pasaría al activar un proveedor.

## 6. Línea base de calidad

No hay datos históricos de producción a mano, y esta auditoría no accede a la base real. Se armó un **conjunto de 11 casos sintéticos**
(crece, cae fuerte, pierde plata, estable, sin semana anterior, sin publicidad, dólares, cifras grandes y chicas, sin datos), ejecutables con el script.

**Resultados offline** (detalle en [fase-0-linea-base.md](fase-0-linea-base.md)):
- La plantilla pasa su propio chequeo de cifras en **11 de 11** casos.
- El chequeo de cifras coincide con lo esperado en **6 de 13** situaciones de prueba. Lo que hay que resolver en la fase de validación:

| Falla | Ejemplo | Tipo |
|---|---|---|
| Rechaza un formato válido | `300000`, `300 mil`, `1,5 millones`, `2.50` | Falso rechazo |
| No mira el signo | "Perdiste $300.000" cuando hubo una ganancia de $300.000 | Falso aceptado |
| No mira el sentido | "La ganancia bajó un 20%" cuando subió | Falso aceptado |
| No ata la cifra a su magnitud | "La ganancia subió un 25%" cuando 25% es el cambio de ventas | Falso aceptado |

**Hallazgo de la plantilla.** Con costos en plural dice "Lo que más subió **fue las comisiones**". Conviene corregirlo en la Fase 1,
porque la plantilla es el respaldo oficial.

**Para completar con datos reales** (con acceso a producción y un criterio de anonimización, sin `store_id`):

```sql
SELECT period_end, source, text, facts
FROM weekly_narratives
WHERE source = 'ia'
ORDER BY period_end DESC
LIMIT 100;
```

Los importes siguen siendo datos del negocio. Conviene guardarlos con acceso restringido.

## 7. Matriz de riesgos

| # | Riesgo | Probabilidad | Impacto | Acción |
|---|---|---|---|---|
| 1 | Planificar 16 semanas de infraestructura para una función que nunca usó IA | **Alta** | Alto | Decidir primero si la IA aporta valor sobre la plantilla (sección 11) |
| 2 | Al activar un proveedor, si falla, cada visita reintenta y espera hasta 60 s | Latente | Medio | Memoria negativa corta y timeout menor (Fase 1) |
| 3 | Al activar un proveedor, una excepción no prevista tumba `GET /narrative` | Latente | Medio | Capturar todo error del proveedor (Fase 1) |
| 4 | El chequeo de cifras deja pasar textos falsos con cifras reales | Media | **Alto** | Validación por magnitud, signo y sentido (Fase 2) |
| 5 | El chequeo rechaza textos correctos y se usa la plantilla de más | Alta con un modelo chico | Bajo | Normalizar formatos (Fase 2) |
| 6 | No se registra qué modelo escribió cada texto, y no se puede comparar el piloto | Alta | Medio | Registrar proveedor y modelo (Fase 1) |
| 7 | Docker Compose nunca usa IA aunque haya clave | Cierta | Bajo | Reenviar las variables (Fase 1) |
| 8 | La app no arranca si falta el paquete `anthropic` aunque no se use | Cierta | Bajo | Importarlo solo cuando haya clave (Fase 1) |
| 9 | 327 tests con base no se corren localmente | Cierta | Bajo | Encender Docker Desktop para la Fase 1 |
| 10 | Un modelo local en hardware modesto es lento o de mala calidad | Alta en esta máquina | Medio | Medir con el script antes de decidir (Fase 2) |

## 8. Plan de pruebas y criterios propuestos para las fases siguientes

Los números de latencia y de tasa de descarte se fijan **después** de correr la línea base con Claude. Lo demás se puede definir ya.

| Fase | Criterio de aceptación |
|---|---|
| 1 | Los 18 tests de la narrativa pasan con un escritor falso y **sin** instalar `anthropic`. La plantilla pasa su chequeo en 11 de 11 casos y ya no dice "fue las comisiones". Las variables nuevas llegan por Docker Compose. Cada narrativa registra proveedor y modelo. |
| 2 | El chequeo de cifras acierta en **13 de 13** situaciones de prueba. Hay pruebas para respuestas vacías, incompletas, inventadas y mal formateadas. El peor caso de una llamada queda acotado por configuración. |
| 3 | Si se activa un proveedor: el piloto compara su texto contra la plantilla sobre los casos sintéticos (tasa de descarte, palabras hasta 60, voseo y tono revisados a mano) y se vuelve a la plantilla con una sola variable. |
| 4 | Un diseño de asistente y CRM que reutilice la interfaz de generación y no cambie el cálculo de métricas. |

## 9. Cómo repetir la medición

```bash
cd backend
../.venv/Scripts/python ../scripts/narrative_baseline.py                 # sin clave, sin base de datos
../.venv/Scripts/python ../scripts/narrative_baseline.py --live --repeat 3 --out ../docs/ia/fase-0-linea-base.md
```

La segunda corrida solo sirve si se carga una clave. Hoy no hay, así que la línea base es la offline.

## 10. Estado de la fase

| Actividad del roadmap | Estado |
|---|---|
| 1. Revisar archivos de configuración y dependencias | Hecho |
| 2. Cómo se configuran las variables en Compose y producción | Hecho: no hay clave en ningún entorno |
| 3. Todos los usos de Anthropic | Hecho: un solo punto de ejecución, sin uso real en producción |
| 4. Tests existentes y cuáles usan base de datos | Hecho: 92 sin base corren; 327 con base requieren Docker |
| 5. Latencia y escenarios de falla | Hecho: escenarios completos; no hay latencia de IA que medir |
| 6. Ejemplos para evaluar calidad | Hecho con 11 casos sintéticos: no existe histórico de IA, y no habría con qué compararlo |

**Criterio de salida del roadmap:** *se conoce el alcance real de la dependencia y existe una línea base reproducible.* Cumplido: la dependencia
es de código y no de operación, y la línea base offline es reproducible. Lo único que podría sumarse es el resultado de
`--live`, y solo si se decide activar un proveedor.

## 11. Consecuencia para el roadmap y decisión pendiente

El roadmap original supone una dependencia operativa que no existe. Propuesta de ajuste:

| Antes | Ahora |
|---|---|
| Fase 1: desacoplar para poder cambiar de proveedor | Se mantiene, **más liviana**: corregir la plantilla, registrar proveedor y modelo, no importar `anthropic` sin clave, reenviar las variables en Compose, capturar errores del proveedor |
| Fase 2: modelo local en Ollama con 5 semanas | **No se justifica todavía.** Primero hay que saber si la IA mejora el texto |
| Fase 3: eliminar Anthropic | Se reduce a **limpieza**: sacar el SDK y la configuración si se decide no usarlo |
| Fase 4: asistente y CRM | Sin cambios |

**Decisión que hay que tomar antes de seguir:** ¿la narrativa tiene que escribirla un modelo, o alcanza con una plantilla mejorada?

1. **Plantilla mejorada, sin IA.** Costo cero, resultado exacto y sin riesgo de cifras falsas. Se puede enriquecer con más casos (por ejemplo, qué canal cambió más).
2. **IA con un proveedor alojado.** La forma más barata de probar si el texto mejora. No hace falta infraestructura propia.
3. **Modelo local.** Solo se justificaría por privacidad o por costo a gran escala. Con la narrativa actual ninguna de las dos aplica: al modelo solo llegan cifras agregadas, y el volumen es una llamada por tienda por día.

Recomendación: la opción 1 ahora, y probar la 2 con un experimento corto si el equipo siente que el texto de la plantilla queda corto.
