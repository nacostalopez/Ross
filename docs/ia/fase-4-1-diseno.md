# Fase 4, etapa 4.1 — Base: métricas como servicio y herramientas con permisos

Primera etapa del [roadmap revisado](fase-4-roadmap.md). No agrega ninguna función visible. Deja listo el terreno para todo lo que sigue:
las cifras que el asistente use salen de funciones reutilizables, y cada herramienta del asistente hereda los mismos permisos que ya protegen la API.

## 1. Decisiones tomadas

| Tema | Decisión |
|---|---|
| Modelo de IA | **Híbrido**: primero local (Ollama), APIs comerciales desactivadas por defecto, proveedor elegible por capacidad. No afecta a la etapa 4.1 |
| Aprobación de promociones | Humana en cada una al principio |
| Contactos del CRM | **Confirmado:** solo los que carga la cuenta. Compradores de tiendas: opcionales, nunca automáticos, con finalidad, acceso y base legal definidos antes |
| Planes que incluyen el CRM | **Growth y Scale.** Pendiente: qué pasa con los datos al bajar de plan |
| Datos personales | Administrador y contaduría en primera instancia, más revisión del responsable legal antes de la etapa 4.4 (consulta en `fase-4-crm-consulta-legal.docx`) |
| **Ubicación del paquete** | `backend/app/ai/tools/` |
| **Período de una herramienta** | Ventana móvil de N días que termina ahora. Los períodos de calendario se diseñan en el contrato pero se implementan después |
| **Qué devuelve el servicio** | Diccionarios simples. **Las herramientas** los validan con los modelos de `schemas/metrics.py` |
| **Ventana máxima por consulta** | 90 días, configurable (es el máximo del selector del dashboard) |
| **Niveles de riesgo** | Cada herramienta declara si es de lectura, de escritura de bajo riesgo o de alto riesgo. Las 9 de esta etapa son de lectura |
| **Capa de política** | Un único punto decide si una herramienta se ejecuta: rol, plan, módulo y riesgo |

## 2. Qué incluye y qué no

| Incluye | No incluye |
|---|---|
| Mover el cálculo de métricas de `routes/metrics.py` a un servicio, sin cambiar ningún resultado | Tablas nuevas. **No hay migración** en esta etapa. La `035` llega con la 4.2 |
| Un registro de herramientas con módulo, rol, función de plan, nivel de riesgo y parámetros validados, y una capa de política que las autoriza | Eventos, trazas y scoring (4.2) |
| Un fixture de dos cuentas y pruebas de aislamiento que recorren el registro | Router de intenciones y pantalla del asistente (4.3) |
| Una regla de pruebas: ningún servicio importa desde las rutas | Cualquier llamada a un modelo |

## 3. Lo que se encontró en el código

| Hallazgo | Consecuencia |
|---|---|
| Las 8 consultas de métricas son constantes dentro de `routes/metrics.py` | Se mueven a un servicio. Las rutas quedan finas |
| Tres servicios ya importan desde la ruta: `alerts.py` (`CAC_BY_CHANNEL_SQL`, `DAILY_SQL`), `reports.py` (`CAC_BY_CHANNEL_SQL`, `SUMMARY_SQL`) y `weekly_narrative.py` (`PNL_SQL`) | La dependencia está invertida. Se corrige moviendo las consultas y se agrega un test que lo impide |
| Dos rutas tienen lógica propia más allá de ejecutar SQL: `ltv-cohorts` (armado de cohortes y mes de recupero) y `forecast` (umbral de 7 días, proyección y totales) | Esa lógica se mueve al servicio. Son las dos piezas con más riesgo de cambiar un número |
| Varias métricas están detrás de una función de plan: `creative_performance`, `ltv_cohorts`, `cac_by_channel`, `attribution_by_channel`, `forecast` | Las herramientas deben aplicar **la misma regla**. Hoy está dentro de `require_plan_feature` y hay que extraerla como función reutilizable |
| `tests/test_route_guards.py` recorre todas las rutas y falla si una no tiene autenticación, `get_owned_store` o chequeo de rol | Es el molde exacto para el guardián de herramientas |
| El dashboard pide `start` y `end` como ventanas móviles de N días terminadas **ahora** (`dateRange()` en `app.js`). La narrativa usa días UTC completos | Las herramientas usan **ventanas móviles como el dashboard**, así el asistente y la pantalla muestran la misma cifra |
| Cobertura de `/summary` y `/creatives`: `summary` aparece en tests de P&L, atribución, demo y propiedad, pero ninguno parece fijar su contenido | Se agregan tests de caracterización antes de mover nada |
| La suite con base de datos necesita Docker (`test-db`) | La verificación de esta etapa lo requiere |

## 4. Parte A — Extraer las métricas a un servicio

### 4.1 Estructura

```text
backend/app/services/metrics/
├── __init__.py       # exporta las funciones públicas
├── queries.py        # las 8 constantes SQL, con sus comentarios, sin tocar una coma
└── service.py        # una función por métrica
```

Cada función recibe `(db, store_id, ...)` y devuelve diccionarios simples, como hoy devuelven las rutas.

| Función | Reemplaza | Notas |
|---|---|---|
| `summary` | `SUMMARY_SQL` | |
| `pnl` | `PNL_SQL` | La usa también la narrativa |
| `daily` | `DAILY_SQL` | La usan también las alertas |
| `creatives` | `CREATIVES_SQL` | |
| `ltv_cohorts` | `LTV_COHORTS_SQL` más el armado de cohortes de la ruta | Mueve el cálculo del mes de recupero |
| `cac_by_channel` | `CAC_BY_CHANNEL_SQL` | La usan también alertas y reportes |
| `attribution_by_channel` | `ATTRIBUTION_BY_CHANNEL_SQL` | |
| `forecast` | `FORECAST_HISTORY_SQL` más la proyección de la ruta | Se lleva `MIN_FORECAST_HISTORY_DAYS` |

### 4.2 Qué devuelve el servicio

| Opción | Pros | Contras |
|---|---|---|
| A. Diccionarios y listas simples | Igual a lo que devuelven las rutas hoy. La narrativa, las alertas y los reportes siguen leyendo los mismos valores sin tocar su código | La forma se garantiza solo con tests |
| B. Los modelos de `app/schemas/metrics.py` | Estructuras tipadas y validadas | Ata el servicio a los esquemas de la API y cambia los tipos que reciben tres consumidores existentes (por ejemplo, `Decimal` por `float`), con riesgo de mover un número |
| **C. Diccionarios en el servicio y esquemas en la herramienta** *(elegida)* | El servicio no cambia ningún valor. El asistente recibe datos **tipados y validados** igual que con B | Un paso de validación más en cada herramienta |

La revisión externa propuso B para que el asistente reciba datos consistentes. C da esa garantía donde importa, en la herramienta, sin tocar los consumidores de hoy en una etapa cuyo criterio de salida es que ningún número cambie.

### 4.3 Orden de trabajo

Cada paso es un commit y deja la suite en verde.

| Paso | Cambio | Qué lo protege |
|---|---|---|
| 1 | Tests de caracterización de `/summary` y `/creatives`, y un script que vuelca la respuesta de **todos** los endpoints de métricas para una tienda con datos sembrados | Los tests existentes (P&L, diario, CAC, atribución, cohortes, pronóstico) |
| 2 | Mover las consultas a `queries.py` y actualizar los tres importadores. Sin cambio de lógica | La suite completa y el volcado, que debe ser idéntico |
| 3 | Mover el armado de cohortes y el pronóstico a `service.py` | Tests de cohortes y de pronóstico, y el volcado |
| 4 | Rutas finas: dependencia, llamada al servicio, `response_model` | La suite completa y el volcado |
| 5 | Extraer la regla de plan de `require_plan_feature` a una función reutilizable | Tests de facturación y de rutas con puerta de plan |

Criterio del volcado: ejecutar el script antes del paso 2 y después del paso 4 sobre los mismos datos y comparar los JSON. Cualquier diferencia detiene el trabajo.

## 5. Parte B — Registro de herramientas

### 5.1 Contrato

```text
Tool
  name                 "pnl", "cac_by_channel", ...
  module               "analytics" (más adelante "crm")
  description          texto para el router y, si hay modelo, para el modelo
  params               modelo de parámetros, sin store_id, account_id ni user_id
  result               esquema de salida: el de schemas/metrics.py que corresponda
  plan_feature         función de plan que exige, o ninguna
  min_role             rol mínimo, "viewer" para todas las de lectura
  risk                 "read" | "write_low" | "high"
  run(ctx, params)     devuelve datos y procedencia

ToolContext            lo arma el servidor, nunca el modelo ni el usuario
  db, user, store, role efectivo en esa tienda, plan

policy.authorize(ctx, tool)   Permitir | Denegar(motivo)
  motivos: plan_requerido, sin_permiso, modulo_inactivo, requiere_confirmacion
```

Reglas:

1. **El contexto sale de la solicitud autenticada**: `get_current_user` y `get_owned_store`. Una herramienta nunca recibe un identificador de tienda como parámetro.
2. **Los parámetros se validan con un modelo que rechaza campos extra.** Si alguien (o un modelo) agrega `store_id`, la llamada falla.
3. **Un único punto decide la autorización.** `policy.authorize` revisa rol, plan, módulo y nivel de riesgo. No lee ningún puntaje de confianza: una confianza alta nunca habilita una operación prohibida.
4. **Niveles de riesgo.** `read` se ejecuta. `write_low` exige confirmación visible y registro. `high` (enviar mensajes, borrar registros, acciones financieras) queda **deshabilitado** hasta definir controles adicionales. En esta etapa todas las herramientas son `read`.
5. **El resultado se valida con su esquema e incluye su procedencia**: nombre de la herramienta, parámetros usados y la ventana de tiempo. Sirve para el validador de cifras (D5) y para citar de dónde sale cada número.
6. **Una herramienta que la cuenta no puede usar devuelve un resultado tipado** ("requiere un plan superior", "sin permiso"), no una excepción. El asistente puede explicarlo en lugar de fallar.
7. **Los límites son los de las rutas** (`months` de 1 a 12, `history_days` de 7 a 365, `forecast_days` de 1 a 90), más un **tope de ventana de 90 días**, configurable, para las métricas por período.

### 5.2 Herramientas iniciales

| Herramienta | Función | Plan | Parámetros |
|---|---|---|---|
| `summary` | `summary` | ninguno | período |
| `pnl` | `pnl` | ninguno | período |
| `daily` | `daily` | ninguno | período |
| `creatives` | `creatives` | `creative_performance` | período |
| `ltv_cohorts` | `ltv_cohorts` | `ltv_cohorts` | período y meses |
| `cac_by_channel` | `cac_by_channel` | `cac_by_channel` | período |
| `attribution_by_channel` | `attribution_by_channel` | `attribution_by_channel` | período |
| `forecast` | `forecast` | `forecast` | días de historia y de proyección |
| `weekly_narrative` | `get_or_build` | ninguno | ninguno |

**Período.** El contrato define dos tipos y esta etapa implementa uno:

| Tipo | Qué es | Estado |
|---|---|---|
| Ventana móvil | N días terminados ahora, igual que `dateRange()` del dashboard. No depende del huso horario | **Se implementa en 4.1** |
| Calendario | Fechas de inicio y fin explícitas, en el huso horario de la tienda. Pensado para reportes semanales y comparaciones contables | Se diseña en el contrato, **se implementa después** |

Por qué el calendario queda para después: los límites de un día dependen de la columna `timezone` de la tienda (por defecto `UTC`), y la narrativa semanal hoy usa días UTC completos. Agregarlo sin decidir antes si la narrativa pasa al huso de la tienda dejaría dos criterios para el mismo número. Se hace como un cambio aparte, con su propia verificación contra la pantalla.

### 5.3 Dónde vive

| Opción | Pros | Contras |
|---|---|---|
| **A. `backend/app/ai/tools/`** *(recomendada)* | Separa lo del asistente de los servicios de negocio. Es el lugar natural de la 4.3, la 4.5 y la 4.7 | Un paquete nuevo |
| B. `backend/app/services/assistant_tools.py` | Un archivo | Se queda chico en cuanto entra el CRM |

## 6. Parte C — Pruebas de aislamiento

### 6.1 Fixture de dos cuentas

Hoy existen `test_account`, `other_account`, `test_user`, `other_user` y `test_store`. Falta una tienda de la otra cuenta y datos con **cifras que se distingan a simple vista**.

| Elemento | Detalle |
|---|---|
| Cuenta A y cuenta B | Cada una con una tienda y un usuario `owner` y otro `viewer` |
| Datos sembrados | Pedidos, gasto publicitario, creatividades y clientes con **magnitudes distintas y reconocibles** (por ejemplo, ingresos cuyo dígito inicial siempre sea 7 en A y 3 en B) |
| Planes | Un caso en `scale` y uno en `starter` para probar las puertas de plan |

### 6.2 Qué se comprueba, herramienta por herramienta

El mismo conjunto de pruebas corre sobre **todo lo que esté registrado**.

| Prueba | Resultado esperado |
|---|---|
| Un usuario de A usa una herramienta sobre la tienda de A | Devuelve los datos de A |
| Un usuario de A intenta usarla sobre la tienda de B | No resuelve la tienda: mismo "no encontrada" que la API |
| Ningún valor de la cuenta B aparece en lo que recibe A | El volcado completo del resultado no contiene las marcas de B |
| Plan `starter` en una herramienta con función de plan | Resultado tipado "requiere un plan superior" |
| Rol `viewer` en una herramienta de lectura | Permitido |
| Parámetro `store_id`, `account_id` o `user_id` en la llamada | Rechazado por la validación |
| Una herramienta de riesgo `high` | Denegada siempre por la política, sin importar el rol |
| Resultado de cada herramienta | Cumple su esquema de salida |

### 6.3 Guardián del registro

Un test al estilo de `test_route_guards.py`, que recorre el registro y falla ante cualquiera de estas situaciones:

- Una herramienta sin `module` ni `min_role`.
- Una herramienta con un parámetro llamado `store_id`, `account_id` o `user_id`.
- Una herramienta sin `risk` declarado, o con `risk` distinto de `read` sin confirmación declarada (anticipa la 4.7).
- Una herramienta que se pueda ejecutar sin pasar por `policy.authorize`.
- Una herramienta sin esquema de salida.
- Una herramienta registrada que **no figure en la lista de pruebas de aislamiento**. Así, una herramienta nueva no pasa sin su prueba.
- Un módulo bajo `app/services/` que importe algo de `app.routes` (el mismo recurso que ya usa la Fase 3 con `anthropic`).

Como en `test_route_guards`, hay una prueba que comprueba que el recorrido encuentra la cantidad esperada de herramientas, para que el guardián no pase revisando nada.

## 7. Criterios de salida

| Criterio | Cómo se comprueba |
|---|---|
| Ningún número cambió | El volcado de respuestas es idéntico antes y después. Los tests existentes pasan sin modificarse |
| `/summary` y `/creatives` están fijados por tests | Los tests de caracterización del paso 1 |
| Ningún servicio depende de las rutas | El test del guardián |
| Cada herramienta pasa el aislamiento, la puerta de plan y el rol | La batería de la sección 6.2, sobre las 9 herramientas |
| Una herramienta nueva no se puede registrar sin su prueba | El test del guardián |
| `ruff` limpio y suite completa con base de datos | Cierre de la etapa |

## 8. Riesgos de esta etapa

| Riesgo | Mitigación |
|---|---|
| Mover `ltv_cohorts` o `forecast` cambia un número sin que se note | Volcado antes y después, más los tests de cada uno, y moverlos en un commit propio |
| Un mock de test depende de la ruta y se rompe al moverla | Los tests solo importan la app y no `app.routes.metrics` (verificado: no hay importaciones desde `tests/`) |
| La función de plan extraída cambia el comportamiento de `require_plan_feature` | Un test específico de facturación antes y después. La dependencia sigue llamando a la misma función |
| Se cuelan campos de identidad entre los parámetros de una herramienta | Validación que rechaza extras y guardián que lo comprueba |
| La ventana móvil del asistente difiere de la de la pantalla por redondeos | Se usa el mismo cálculo de milisegundos que `dateRange()` y se compara con una consulta de la API en un test |

## 9. Qué sigue

Con las decisiones de la sección 1 cerradas, esta etapa se puede implementar en el orden de la sección 4.3. Lo único previo a escribir código es confirmar este diseño.

La etapa 4.2 agrega las tablas de eventos, señales, trazas y score (migración `035`), probadas con la narrativa semanal, que se registra aquí como la herramienta `weekly_narrative`. La etapa 4.3 suma el puntaje de selección del [roadmap](fase-4-roadmap.md).

## 10. Estado: etapa cerrada

| Criterio de salida | Resultado |
|---|---|
| Ningún número cambió | La instantánea de los ocho endpoints es idéntica antes y después. Los tests existentes pasan sin modificarse |
| `/summary` y `/creatives` fijados por tests | Cuatro tests nuevos de `/summary`. `/creatives` ya tenía cinco |
| Ningún servicio depende de las rutas | `tests/test_service_layering.py`, que falla sobre el código anterior y nombra a los tres infractores |
| Cada herramienta pasa aislamiento, puerta de plan y rol | `tests/test_ai_tools_isolation.py`: 81 casos sobre las 9 herramientas, parametrizados sobre el registro |
| Una herramienta nueva no se registra sin su prueba | `tests/test_ai_tools_guards.py`. La batería se genera desde el registro, así que cubre las nuevas sola |
| `ruff` limpio y suite completa | 684 tests pasan, `ruff` limpio |

Se comprobó que las pruebas detectan fallas rompiendo el código a propósito: sin el filtro de tienda en una consulta falla el aislamiento, y sin el chequeo de rol, sin el de plan o sin la defensa de contexto armado a mano fallan los tests de la política.

Cambios respecto del diseño original:

- `policy.authorize` recibe `(ctx, tool)`, sin `params`.
- El servicio devuelve diccionarios y las herramientas los validan con los esquemas de `schemas/metrics.py`.
- La política deniega también un contexto armado a mano con la tienda de otra cuenta, además de que `resolve_context` no lo construya.
- `find_owned_store` es la única regla de "esta tienda es mía", compartida por la API y el contexto.

Pendiente, fuera de esta etapa: el período de calendario con el huso horario de la tienda, que queda para un cambio aparte.
