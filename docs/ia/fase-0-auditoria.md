# Fase 0 — Auditoría y línea base

Parte del [roadmap de IA de ROSS](../../README.md): desacoplar la narrativa semanal de Anthropic. Esta fase no cambia
el comportamiento de la aplicación. Deja medido el punto de partida y los riesgos.

- **Alcance auditado:** "Ross explica tu semana" (`backend/app/services/weekly_narrative.py`).
- **Fecha del relevamiento:** 13 de octubre de 2026, sobre `main`.
- **Línea base ejecutable:** [`scripts/narrative_baseline.py`](../../scripts/narrative_baseline.py).
  Su salida de este relevamiento está en [fase-0-linea-base.md](fase-0-linea-base.md).

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
| Producción, servicio `backend` | Figura en `.env.production.example`. **Si está cargada en Railway no lo puedo ver** | No figura: usa `claude-opus-5-5` | Pendiente de confirmar |
| Producción, servicio `cron` | Hereda las del backend por referencia | Idem | Si el backend la tiene, el resumen semanal usa Claude |

Para confirmar en Railway: servicio **Ross** → Variables → buscar `ANTHROPIC_API_KEY`. Sin eso, la narrativa que ven hoy
los usuarios es la plantilla.

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

Se midió lo que se puede sin clave y se dedujo del código lo demás. **La latencia real de Claude queda pendiente**:
requiere ejecutar `scripts/narrative_baseline.py --live` con la clave.

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
| 1 | La clave de Anthropic no está cargada en producción y nadie lo sabe | Media | Medio | Confirmar en Railway ya |
| 2 | Si Claude falla, cada visita reintenta y espera hasta 60 s | Media | Medio | Memoria negativa corta y timeout menor (Fase 1) |
| 3 | Una excepción no prevista de la librería tumba `GET /narrative` | Baja | Medio | Capturar todo error del proveedor (Fase 1) |
| 4 | El chequeo de cifras deja pasar textos falsos con cifras reales | Media | **Alto** | Validación por magnitud, signo y sentido (Fase 2) |
| 5 | El chequeo rechaza textos correctos y se usa la plantilla de más | Alta con un modelo chico | Bajo | Normalizar formatos (Fase 2) |
| 6 | No se registra qué modelo escribió cada texto, y no se puede comparar el piloto | Alta | Medio | Registrar proveedor y modelo (Fase 1) |
| 7 | Docker Compose nunca usa IA aunque haya clave | Cierta | Bajo | Reenviar las variables (Fase 1) |
| 8 | Quitar Anthropic sin una vía de vuelta deja sin respaldo probado | Media | Medio | Mantener Anthropic como proveedor opcional hasta el piloto |
| 9 | 327 tests con base no se corren localmente | Cierta | Bajo | Encender Docker Desktop para la Fase 1 |
| 10 | Un modelo local en hardware modesto es lento o de mala calidad | Alta en esta máquina | Medio | Medir con el script antes de decidir (Fase 2) |

## 8. Plan de pruebas y criterios propuestos para las fases siguientes

Los números de latencia y de tasa de descarte se fijan **después** de correr la línea base con Claude. Lo demás se puede definir ya.

| Fase | Criterio de aceptación |
|---|---|
| 1 | Los 18 tests de la narrativa pasan con un escritor falso y **sin** instalar `anthropic`. La plantilla pasa su chequeo en 11 de 11 casos y ya no dice "fue las comisiones". Las variables nuevas llegan por Docker Compose. Cada narrativa registra proveedor y modelo. |
| 2 | El chequeo de cifras acierta en **13 de 13** situaciones de prueba. Hay pruebas para respuestas vacías, incompletas, inventadas y mal formateadas. El peor caso de una llamada queda acotado por configuración. |
| 3 | El piloto compara el modelo nuevo contra la línea base: tasa de descarte, palabras (hasta 60), voseo y tono revisados a mano sobre los casos sintéticos. Se puede volver a la plantilla solo con una variable. |
| 4 | Un diseño de asistente y CRM que reutilice la interfaz de generación y no cambie el cálculo de métricas. |

## 9. Cómo repetir la medición

```bash
cd backend
../.venv/Scripts/python ../scripts/narrative_baseline.py                 # sin clave, sin base de datos
../.venv/Scripts/python ../scripts/narrative_baseline.py --live --repeat 3 --out ../docs/ia/fase-0-linea-base.md
```

La segunda corrida necesita `ANTHROPIC_API_KEY` en el entorno y completa la sección de latencia y calidad con Claude.

## 10. Estado de la fase

| Actividad del roadmap | Estado |
|---|---|
| 1. Revisar archivos de configuración y dependencias | Hecho |
| 2. Cómo se configuran las variables en Compose y producción | Hecho. Falta confirmar la clave en Railway |
| 3. Todos los usos de Anthropic | Hecho: un solo punto de ejecución |
| 4. Tests existentes y cuáles usan base de datos | Hecho: 92 sin base corren; 327 con base requieren Docker |
| 5. Latencia y escenarios de falla | Parcial: escenarios completos, latencia de Claude pendiente de la clave |
| 6. Ejemplos para evaluar calidad | Parcial: 11 casos sintéticos; falta el histórico real anonimizado |

**Criterio de salida del roadmap:** *se conoce el alcance real de la dependencia y existe una línea base reproducible.* El alcance está
conocido y la línea base offline es reproducible. Faltan tres entradas que dependen de vos: confirmar la clave en Railway,
correr el script con la clave, y exportar el histórico anonimizado.
