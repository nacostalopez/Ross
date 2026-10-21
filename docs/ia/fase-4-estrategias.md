# Fase 4 — Asistente, CRM y aprendizaje por utilidad: estrategias

Sigue a la [Fase 3](fase-3-decision.md). Este documento **no implementa nada**: propone las decisiones de diseño, con sus opciones, pros y contras,
para elegir el camino antes de escribir código. Corresponde a la fase 6 del roadmap v2.0 ("Preparación del asistente y CRM").

> **Actualización:** las decisiones D2 y D3 y el camino recomendado (sección 3) fueron reemplazados por [fase-4-roadmap.md](fase-4-roadmap.md):
> el CRM es un módulo opcional para cada cuenta, el asistente cubre las dos funciones y el modelo de IA se espera pronto, pero el diseño busca depender cada vez menos de él.

## 0. Qué se quiere lograr

1. Un **asistente** que responda preguntas sobre las métricas de una tienda.
2. Un **CRM interno** que lleve la relación con cuentas y prospectos.
3. Que **ambos se retroalimenten**: lo que el asistente responde y lo que el usuario hace después queda registrado, y eso afina las respuestas siguientes.
4. Un **scoring de respuestas** basado en su utilidad, medida por cuánto se involucró el usuario con ROSS después de decidir, para depender cada vez más de lo que demostró servir.

## 1. Lo que el código ya da y lo que falta

| Pieza | Estado hoy | Consecuencia para el diseño |
|---|---|---|
| Aislamiento por cuenta | `get_owned_store` compara `store.account_id` con la cuenta del usuario. `require_store_role` resuelve el rol efectivo por tienda | Las herramientas del asistente deben resolver la tienda **en el servidor** con estas mismas dependencias, nunca con un id que ponga el modelo |
| Permisos por plan | `require_plan_feature("...")` y la tabla `plans.features` | El asistente puede ser una función de plan sin inventar nada |
| Cuenta demo | `is_demo_user` bloquea toda escritura | El asistente y el CRM heredan la regla: la demo no escribe ni puntúa |
| Cálculo de métricas | Las consultas (`PNL_SQL`, `SUMMARY_SQL`, `CAC_BY_CHANNEL_SQL`, etc.) viven **dentro de `routes/metrics.py`**. `weekly_narrative.py` ya las importa desde la ruta | Hay que extraerlas a un servicio antes de que el asistente las use. Es un refactor sin cambio de cálculo y es el primer paso obligatorio |
| Narrativa | Contrato `NarrativeWriter`, fábrica, validador de cifras, caché diaria, columnas `provider` y `model` | Es el molde del gateway de IA: se generaliza, no se rehace |
| Validador de cifras | `narrative_validator.check_figures`, 13 de 13 casos | Sirve de compuerta también para las respuestas del asistente |
| Clientes de tiendas | `Customer` guarda solo `email_hash` y `phone_hash`. No hay nombre ni email en claro | Un CRM de **compradores** no tiene sobre qué trabajar sin cambiar esa política (decisión D2) |
| Prospectos | `DemoRequest` (nombre, email, plataforma) es lo único comercial que existe | Es el semillero natural del CRM de ROSS |
| Auditoría | `CustomerDataAccessLog` y `AccountActivityLog`, ambos deliberadamente angostos | No sirven como registro de interacciones con IA: hace falta una tabla propia |
| Migraciones | Archivos SQL numerados en `db/init/`, la última es `034` | Las tablas nuevas empiezan en `035` |
| Proveedor de IA | Ninguno. La Fase 3 retiró Anthropic y dejó el punto de extensión | Un asistente conversacional reabre esa decisión (D3) |

## 2. Decisiones

Cada una tiene opciones, pros y contras, y una recomendación. Las decisiones D1 a D4 definen el producto, las D5 a D8 definen el aprendizaje y la D9 define los límites.

### D1. Alcance y orden

| Opción | Pros | Contras |
|---|---|---|
| A. Asistente primero, CRM después | Entrega valor visible al cliente antes. Reutiliza lo que ya hay (métricas y narrativa) | El bucle de retroalimentación con el CRM queda sin probar hasta tarde |
| B. CRM primero, asistente después | El CRM es determinista y de bajo riesgo | No toca el problema difícil (la IA) ni el scoring. El valor para el cliente llega último |
| C. Los dos en paralelo | Cierra el bucle completo más rápido | Es el riesgo que el roadmap ya señaló: ampliar el alcance antes de validar. Duplica el trabajo de revisión |
| D. Núcleo común primero (registro de interacciones + scoring), después el asistente y el CRM como dos clientes de ese núcleo | El bucle se construye una sola vez. Se puede medir antes de tener IA conversacional | El primer entregable no se ve desde afuera |

**Recomendación: D, y dentro de ella A.** El registro de eventos y el scoring son lo único que los dos productos comparten, así que van primero. Se prueban con algo que ya existe: la narrativa semanal. Cada narrativa puede registrarse como una "interacción" y puntuarse con las mismas señales. Eso valida el scoring sin escribir una línea del asistente.

### D2. Qué es el CRM

| Opción | Pros | Contras |
|---|---|---|
| A. CRM comercial de ROSS: prospectos, cuentas, contactos, oportunidades, tareas | Hay semillero real (`demo_requests`). Sin datos personales de terceros. Cumple el principio "no mezclar dominios" | Es un CRM de ventas clásico: el valor es para el equipo de ROSS, no para el cliente |
| B. CRM de compradores de las tiendas conectadas | Es lo que más valor daría al comerciante (segmentos, recompra, LTV) | Hoy solo hay hashes: no se puede escribir a nadie. Pasar a datos en claro es una decisión legal y de seguridad grande (consentimiento, `SECURITY.md`, acceso por tienda) y los canales (WhatsApp) tienen reglas de plantilla y baja |
| C. Ambos, en dominios separados | Cubre las dos necesidades | El doble de superficie y de riesgo antes de validar nada |

**Recomendación: A ahora, B como proyecto aparte.** Con A, la "salud de cada cuenta" (uso, conexiones activas, alertas, interacciones con el asistente) es una vista del CRM. B puede resolverse más adelante a nivel de **segmentos agregados** (LTV por cohorte ya existe) sin guardar datos personales.

### D3. Motor del asistente

La Fase 3 decidió no usar IA porque no había evidencia de que mejorara el texto. Para la narrativa, esa decisión se mantiene. Para preguntas en texto libre, la situación es distinta: una plantilla no interpreta "¿por qué bajó mi ROAS en Meta?".

| Opción | Pros | Contras |
|---|---|---|
| A. Router de intenciones determinista (palabras clave, menú de preguntas) y respuestas con plantilla | Costo cero, cifras exactas, sin proveedor, coherente con la Fase 3 | Rígido. Falla ante preguntas que no prevé. Se siente como un menú |
| B. Modelo con llamada a herramientas (tool calling), alojado | Entiende texto libre. Es la forma más rápida de saber si el asistente sirve | Costo recurrente, latencia, un proveedor y una clave. Reabre la decisión de la Fase 3 |
| C. Modelo local (Ollama) | Sin costo por llamada. Los datos no salen | Requiere infraestructura; sin GPU la latencia puede ser inviable. El roadmap ya lo marcó como no asumible en Railway |
| D. Híbrido: herramientas y respuestas verificables siempre; el modelo solo **interpreta la pregunta** y **redacta**, y puede apagarse | La exactitud no depende del modelo. Con el modelo apagado cae a A. Reutiliza `NarrativeWriter`, fábrica y validador | Es lo más trabajo de diseño. Hay que mantener dos caminos |

**Recomendación: D, empezando por A.** Primero el router determinista con las herramientas (D4) y el scoring funcionando. El modelo entra después, por la misma fábrica de proveedores, y se activa solo si un experimento corto muestra mejora medida contra A. Es el mismo criterio de la Fase 2 y 3: la IA se gana su lugar con datos. El proveedor (alojado o local) se elige en ese momento, no ahora.

### D4. Cómo accede el asistente a los datos

| Opción | Pros | Contras |
|---|---|---|
| A. El modelo escribe SQL | Responde cualquier cosa | Inaceptable aquí: riesgo de fuga entre cuentas, cifras sin validar, costo impredecible |
| B. Herramientas fijas de solo lectura sobre los servicios de métricas | El aislamiento y los permisos quedan en el servidor. Cada herramienta es testeable. Las cifras salen del P&L existente | Solo responde lo que tiene herramienta. Cada pregunta nueva exige una herramienta nueva |
| C. B más un catálogo de consultas parametrizadas que el modelo compone | Más cobertura | Es un SQL disfrazado: más superficie que revisar |

**Recomendación: B.** Primera ronda de herramientas, todas con la tienda resuelta por el servidor: P&L del período, resumen diario, CAC por canal, atribución por canal, cohortes de LTV, pronóstico, creatividades. Todas corresponden a consultas que ya existen. Requisito previo: extraerlas de `routes/metrics.py` a `services/` sin cambiar el resultado, con los tests de las rutas como red de seguridad.

### D5. Validación de salida

| Opción | Pros | Contras |
|---|---|---|
| A. Reutilizar `check_figures` sobre el texto final | Ya probado en 13 de 13 casos | Compara contra un conjunto de cifras conocidas: hay que armar ese conjunto con lo que devolvió cada herramienta |
| B. A más respuesta estructurada: cada cifra citada lleva la herramienta y el campo de origen | Se puede mostrar "de dónde sale" y auditar | Más trabajo de interfaz |

**Recomendación: A desde el inicio y B como mejora visible.** Una respuesta rechazada por el validador no se muestra: se cae a la respuesta de plantilla de esa herramienta. Esa regla no la aprende ningún scoring (ver D7).

### D6. Qué cuenta como "útil"

Es la decisión más delicada. Medir "cuánto se involucró el usuario después de decidir" mide **uso**, y el uso no es lo mismo que **acierto**. Una respuesta alarmista genera más clics que una calmada y correcta. Por eso conviene separar señales por confiabilidad.

| Señal | Ejemplo | Pros | Contras |
|---|---|---|---|
| Explícita | "Me sirvió" / "No me sirvió" con motivo opcional | La más clara. Fácil de implementar | Pocos usuarios responden. Sesgo hacia los extremos |
| Implícita de conducta | Repite la misma pregunta (negativa). Abre la métrica citada. Vuelve en 7 días. Cambia una configuración relacionada (gasto, alertas, reportes) | Abundante y sin fricción | Ambigua: una repregunta puede ser desconfianza o interés. Premia lo que atrae, no lo que acierta |
| De resultado | La métrica se movió en el sentido sugerido en los 7 a 14 días siguientes | Es la utilidad real | Está confundida con todo lo demás (estacionalidad, campañas, mercado). Sin grupo de control no prueba causalidad |
| Compuesta | Combina las anteriores con pesos | Más robusta | Los pesos son una decisión y hay que justificarlos |

**Recomendación: registrar las tres desde el primer día y puntuar con una compuesta, con jerarquía.**

1. **Compuerta, no se aprende:** una respuesta con cifras no válidas, de otra tienda o sin autorización puntúa cero y no se muestra. Nunca entra al ranking.
2. **Utilidad, se aprende:** explícita (peso mayor) más conducta (peso medio) más resultado (peso menor y solo si hay grupo de control o una comparación con el período equivalente).
3. **Penalización por alarmismo:** si una respuesta genera más actividad pero no mejora la métrica, no sube. Se mide el resultado, no solo el clic.

### D7. Qué se ajusta con el score

| Opción | Pros | Contras |
|---|---|---|
| A. Ranking y priorización de qué insight o sugerencia se muestra primero | Cambio acotado, reversible, fácil de explicar | No mejora la redacción |
| B. Selección entre variantes de plantilla o de tono (bandido multibrazo) | Aprende con pocos datos. Cada variante pasa por el validador | Necesita varias variantes escritas a mano |
| C. Ajuste del prompt o del modelo según el score | Puede mejorar la redacción | Solo existe si hay modelo (D3). Difícil de aislar qué cambió |
| D. Fine-tuning con las respuestas mejor puntuadas | Máxima adaptación | Volumen insuficiente casi seguro. Riesgo de aprender sesgos. Exige consentimiento de uso de datos. No recomendable en esta etapa |

**Recomendación: A y B ahora; C cuando haya modelo; D descartada por ahora.** La unidad que se puntúa es el **tipo de respuesta** (herramienta, intención, plantilla, variante), no cada texto individual. Con volumen bajo (no medí cuántas cuentas hay hoy), conviene estadística simple y transparente, con intervalos de confianza y un mínimo de muestras antes de actuar, antes que aprendizaje automático. Lo que se ajusta nunca incluye las cifras ni el validador.

**Niveles de autonomía** para cumplir con "depender cada vez más de lo que sirve":

| Nivel | Qué hace el sistema | Condición para subir |
|---|---|---|
| 0 | Responde cuando se le pregunta, sin ranking | Estado inicial |
| 1 | Ordena sus sugerencias por score | Muestras mínimas y score estable |
| 2 | Propone proactivamente (en el dashboard o en el reporte semanal) lo mejor puntuado | Score alto sostenido y sin quejas explícitas |
| 3 | Sugiere acciones de escritura (con confirmación) | Revisión humana y tasa de utilidad comprobada |

Hay una revisión humana semanal de lo que subió y bajó. Cualquier tipo de respuesta se puede congelar con una bandera.

### D8. Cómo se retroalimentan el asistente y el CRM

| Opción | Pros | Contras |
|---|---|---|
| A. Una tabla de eventos append-only (`ai_events`) que ambos escriben y leen | Un solo lugar para el scoring. Simple. Auditable | Hay que cuidar el esquema para que no se vuelva un cajón de sastre |
| B. Tablas separadas del asistente y del CRM, unidas por claves | Dominios más limpios | El scoring tiene que juntar tablas distintas. Más migraciones |
| C. Bus de eventos (cola) | Escala | Es infraestructura nueva sin necesidad: el volumen cabe en PostgreSQL |

**Recomendación: A para eventos, con las entidades del CRM en tablas propias que referencian los eventos.** El flujo sería:

```text
Pregunta o narrativa ──▶ interacción (id, tipo, herramienta, cifras citadas, proveedor, modelo)
                                  │
              ┌───────────────────┴───────────────────┐
              ▼                                       ▼
   señales: explícita, conducta,            línea de tiempo en el CRM de esa cuenta
   resultado (diferido 7 a 14 días)         (qué se le dijo, qué hizo después)
              │                                       │
              └──────────────▶ score por tipo ◀───────┘
                                  │
        CRM ─▶ salud de la cuenta ─▶ qué sugerir y a quién (nivel 2)
```

El CRM aporta contexto al asistente (cuentas con riesgo de abandono, conexiones caídas, etapa del cliente) y el asistente aporta al CRM (qué le interesa a cada cuenta, qué le sirvió). Los eventos de resultado se calculan por un trabajo programado, no en la petición del usuario.

### D9. Privacidad, aislamiento y límites

| Tema | Opciones | Recomendación |
|---|---|---|
| Qué ve el modelo | Datos crudos, o solo cifras agregadas del período | Solo agregados, como hoy en la narrativa. Nunca hashes de clientes ni credenciales |
| Aprender entre cuentas | Cada cuenta aislada (poco dato), o score global por tipo de respuesta (más dato) | **Global por tipo de respuesta**, sin contenido de ninguna cuenta. La personalización por cuenta queda para después y con consentimiento |
| Retención de interacciones | Indefinida, o con vencimiento | Con vencimiento configurable. Guardar cifras y referencias, no el texto libre del usuario más tiempo del necesario |
| Qué se registra en logs | Todo, o nada sensible | Tipo, estado, latencia, proveedor. Sin cifras ni texto del usuario en logs de aplicación (ya hay precedente en el commit `722e6f9`) |
| Escrituras del asistente | Libres, o con permiso y confirmación | Solo con permiso explícito (`require_store_role`), confirmación y registro. La cuenta demo nunca escribe |
| Canales | Web, WhatsApp, email | **Solo web al inicio.** WhatsApp y email exigen identidad por canal, plantillas y bajas. No se heredan permisos entre canales |
| Consentimiento | Implícito en los términos, o aviso explícito | Aviso explícito de que las interacciones se usan para mejorar las respuestas, con opción de salir. Es una decisión legal que el equipo debe confirmar |

## 3. Camino recomendado

| Etapa | Contenido | Criterio de salida |
|---|---|---|
| **4.1 Base** | Extraer las consultas de métricas de `routes/metrics.py` a servicios, sin cambiar resultados. Definir las herramientas de solo lectura (D4) | Los tests de las rutas pasan igual. Cada herramienta tiene un test de aislamiento entre cuentas |
| **4.2 Núcleo de eventos y scoring** | Tabla de interacciones, señales y score por tipo (D6, D8). Se prueba con la narrativa semanal: botón "me sirvió" y señales de conducta | El score se calcula con datos reales de narrativas. La compuerta rechaza cifras inválidas. Los eventos no se escriben desde la cuenta demo |
| **4.3 Asistente determinista** | Router de intenciones, respuestas con plantilla, validador de cifras. En el dashboard, detrás de `require_plan_feature` | Responde las preguntas de las herramientas. Una pregunta sin herramienta se contesta con "no puedo todavía" y se registra como hueco de cobertura |
| **4.4 CRM de ROSS** | Prospectos (desde `demo_requests`), cuentas, contactos, oportunidades, tareas. Salud de cuenta alimentada por los eventos | Un prospecto pasa a cuenta sin duplicar datos. Toda escritura queda auditada |
| **4.5 Experimento con modelo** | Un proveedor detrás de la fábrica, solo para interpretar y redactar. Comparación contra 4.3 con el score de 4.2 | El modelo se activa solo si supera a la versión determinista en utilidad, sin subir el descarte del validador. Se vuelve atrás con una variable |

Cada etapa se entrega y se mide antes de empezar la siguiente, como en las fases anteriores. La 4.5 es opcional: si 4.3 alcanza, el asistente no necesita modelo.

## 4. Riesgos propios de este diseño

| Riesgo | Mitigación |
|---|---|
| El score premia lo llamativo y no lo correcto (ley de Goodhart) | Compuerta de validez fuera del aprendizaje. Penalización por alarmismo. Peso mayor a la señal explícita. Revisión humana semanal |
| Poco volumen: el score no converge | Puntuar por tipo y no por texto. Mínimo de muestras antes de actuar. Mostrar el intervalo de confianza |
| El resultado de negocio se confunde con otras causas | Peso menor y solo con comparación contra el período equivalente o un grupo de control |
| Bucle de realimentación: lo que sube se muestra más y por eso sube más | Reservar una fracción de exploración (bandido) y congelar tipos por bandera |
| Fuga entre cuentas por una herramienta mal escrita | La tienda siempre se resuelve en el servidor. Un test de aislamiento obligatorio por herramienta |
| El refactor de las consultas cambia un número | Congelar con tests los resultados actuales antes de moverlas |
| El CRM crece hasta incluir compradores sin haberlo decidido | D2 lo deja fuera de alcance. Entrar requiere una decisión propia |
| Reabrir la IA sin evidencia | La etapa 4.5 es un experimento con criterio de salida, no una decisión |

## 5. Lo que necesito que decidas

1. **D1:** ¿Aceptás empezar por el núcleo de eventos y scoring (4.1 y 4.2) antes que por el asistente?
2. **D2:** ¿El CRM es el comercial de ROSS (opción A), o lo que querés es un CRM de compradores de las tiendas (B)? Es la decisión que más cambia el proyecto.
3. **D3:** ¿Ves bien arrancar sin modelo y reabrir la IA solo con evidencia (4.5)?
4. **D6:** ¿Qué acción de un usuario cuenta, para vos, como "se involucró con ROSS después de decidir"? Por ejemplo: cambió el gasto publicitario, activó una alerta, volvió al dashboard, aplicó una sugerencia.
5. **D9:** ¿Quién confirma la parte legal del aviso de consentimiento sobre el uso de las interacciones?

Con esas respuestas, el siguiente documento es el diseño detallado de la etapa 4.1, con el modelo de datos de `035` y la lista de herramientas.
