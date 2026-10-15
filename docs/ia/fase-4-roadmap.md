# Fase 4 — Roadmap revisado: un asistente que cada vez necesita menos al modelo

Reemplaza las decisiones **D2**, **D3** y el **camino recomendado** de [fase-4-estrategias.md](fase-4-estrategias.md). Las demás decisiones se mantienen,
con los ajustes de la sección 6. Esta es la segunda revisión: la primera fijó el CRM como módulo opcional, esta suma que el modelo de IA llega pronto
pero el asistente debe tender a resolver sin él.

## 1. Qué cambió

| Antes | Ahora |
|---|---|
| CRM interno de ROSS | **Módulo del producto**, opcional: cada cuenta lo activa si lo necesita |
| El asistente respondía sobre métricas | **Un solo asistente**, en la app, que cubre métricas y, con el CRM activo, también lo opera |
| Sin modelo hasta que un experimento lo justifique | El modelo **llega en un futuro cercano**. El diseño lo espera, pero lo trata como el último escalón y no como el motor |
| Éxito del asistente: respuestas útiles | Éxito: respuestas útiles **y una dependencia del modelo que baja con el tiempo** |
| Proveedor de IA sin definir | **Local primero** (Ollama, desarrollado en una máquina disponible) y APIs comerciales como opción futura, **desactivadas por defecto** |
| El score medía solo la utilidad posterior | Dos puntajes distintos: uno de **selección** (cuánto confiar antes de actuar) y uno de **utilidad** (qué tan bien resultó después) |

Lo que no cambia: las cifras salen del backend, el modelo nunca toca la base, la validez no se aprende y el score mide la utilidad posterior a la decisión.

## 2. La escalera de resolución

Cada consulta se resuelve en el escalón más bajo que alcance. Subir un escalón cuesta más (tiempo, dinero, riesgo).

| Escalón | Qué hace | Usa modelo | Costo y latencia |
|---|---|---|---|
| **L0 Directo** | La pregunta coincide con una intención conocida. Se ejecuta la herramienta y se arma la respuesta con una plantilla | No | Mínimos |
| **L1 Patrón aprendido** | La pregunta no coincide exacto, pero se parece a una intención ya resuelta antes y bien puntuada. Se reutiliza su plan de herramientas y su plantilla | No | Mínimos |
| **L2 Interpretar** | El modelo solo **entiende** la pregunta: elige la intención y extrae los parámetros. Las herramientas y la plantilla hacen el resto | Sí, una llamada corta | Bajos |
| **L3 Componer** | El modelo razona sobre los resultados de varias herramientas y redacta. Todo pasa por el validador de cifras | Sí, la más cara | Altos |

Cuando falla un escalón (el validador rechaza, el modelo no responde, se acaba el presupuesto), la consulta cae a la plantilla de la herramienta o a un "todavía no sé responder eso". Nunca queda sin respuesta ni muestra una cifra no validada.

### Puntaje de selección: cuánto confiar antes de actuar

Antes de ejecutar o responder, el asistente puntúa cada candidato (intención, herramienta o patrón). Es **otro puntaje** que el de utilidad: el de selección se calcula *antes* de responder, el de utilidad se mide *después* de que el usuario decide.

| Factor | Peso inicial | Cómo se obtiene en ROSS |
|---|---|---|
| Coincidencia con la intención | 40 % | Coincidencia del router o del clasificador con una intención conocida |
| Compatibilidad con el contexto | 25 % | Módulo activo, plan, tienda elegida y datos en el período pedido |
| Confianza de la herramienta | 20 % | Score de utilidad histórico y tasa de rechazos del validador de esa herramienta (sale de la etapa 4.2) |
| Calidad de los datos disponibles | 15 % | Días con datos en el período (el pronóstico ya exige 7) y estado del conector (`connector_status`: último sincronizado y último error) |

**Los pesos son hipótesis de partida, no valores calibrados.** Se ajustan con las trazas y el modo sombra.

| Confianza | Qué hace |
|---|---|
| Alta | Responde o ejecuta una consulta de **solo lectura** |
| Intermedia | Pide una aclaración o una confirmación. Si hay modelo disponible, sube a L2 para interpretar |
| Baja | No ejecuta. Pide más información o cae a la plantilla |

**El puntaje nunca autoriza.** Los permisos los decide una capa de política independiente (rol, plan, módulo y nivel de riesgo) que no lee el puntaje. Una confianza alta no habilita una operación prohibida.

## 3. Cómo se vuelve independiente

Lo que el modelo resuelve bien hoy es material para que mañana no haga falta. Cada consulta que llega a L2 o L3 deja una traza: la pregunta normalizada, la intención, el plan de herramientas usado y el score que obtuvo. Cuando un patrón se repite y puntúa bien, se **promueve** a L1.

| Mecanismo | Pros | Contras |
|---|---|---|
| A. Caché de respuestas completas | Fácil de entender | Las respuestas llevan cifras que cambian. Servir una vieja es un error financiero. Descartada |
| **B. Promoción de intención, plan y plantilla** *(recomendada)* | Se promueve la **estructura**, nunca las cifras: se recalculan siempre. Auditable, reversible y barata | Necesita volumen para detectar patrones y una revisión humana al principio |
| C. Clasificador de intenciones propio (embeddings o modelo chico) | Entiende variaciones sin llamar al modelo grande | Necesita un corpus etiquetado. Se arma a partir de las trazas de B |
| D. Destilar o ajustar un modelo con las trazas | Máxima independencia | Volumen probablemente insuficiente, riesgo de aprender sesgos, exige consentimiento. Descartada ahora, a reevaluar con datos |

**Recomendación: B desde ya, C cuando haya volumen, D solo si los datos lo justifican.**

### Garantías para que menos modelo no signifique peor respuesta

| Garantía | Qué hace |
|---|---|
| Modo sombra | Antes de promover, el escalón bajo corre en paralelo con el modelo sobre una muestra y se comparan resultados y score |
| Umbral de promoción | Se promueve solo con un mínimo de muestras, un score igual o mayor al del modelo dentro de un margen y sin rechazos del validador |
| Revisión humana | Al principio toda promoción la aprueba una persona. La aprobación automática se evalúa con datos, no se asume |
| Exploración | Una fracción de consultas ya promovidas se sigue contrastando con el modelo, para detectar deriva y que el router no se estanque |
| Versionado y reversión | Cada patrón promovido tiene versión y bandera para volver al escalón anterior |
| Cambios en las herramientas | Si cambia una herramienta o su esquema, los patrones que la usan se invalidan y vuelven a ganar su lugar |

### Cómo se mide

| Indicador | Qué dice |
|---|---|
| Tasa de resolución sin modelo (L0 + L1 sobre el total) | El indicador principal. Debe subir |
| Llamadas al modelo por cuenta y por día | Costo y presupuesto |
| Score de utilidad por escalón | Que subir la independencia no baje la calidad |
| Huecos de cobertura | Preguntas sin respuesta. Alimentan las nuevas intenciones |
| Rechazos del validador por escalón | Seguridad de las cifras |
| Latencia por escalón | Experiencia |

No se fija una meta numérica ahora. Se mide primero la línea base con las trazas de la etapa 4.3 y recién entonces se define qué porcentaje es razonable.

## 4. Lo que se prepara ya, aunque el modelo aún no exista

| Pieza | Por qué conviene hacerla antes |
|---|---|
| Trazas desde la primera consulta | Cuando llegue el modelo, ya habrá un corpus de preguntas, huecos y planes. Los huecos de L0 son la lista de lo que el modelo tendrá que resolver |
| Contrato de IA con capacidades separadas (`interpretar`, `componer`, `clasificar`) | Se puede conectar un modelo distinto por capacidad. Ya existe el molde en `NarrativeWriter` y la fábrica de proveedores |
| Escritor falso | Permite probar la escalera completa sin el modelo real, como se hizo en la Fase 1 |
| Presupuesto y límites | Tope de llamadas por cuenta y por día, timeout y caída a escalones bajos |

Qué es "el modelo de ROSS" (propio, alojado por ROSS o de un tercero) cambia costos, latencia y privacidad, pero no la arquitectura. Por eso se define en la sección 9.

## 5. CRM como módulo opcional

| Tema | Propuesta |
|---|---|
| Activación | La cuenta lo activa desde su configuración. No se crea ningún dato hasta entonces. Se controla con una bandera por cuenta y con `require_plan_feature("crm")` |
| Alcance mínimo | Contactos, empresas, embudo simple de oportunidades, tareas y notas. Alta manual e importación CSV. **Sin envío de email ni WhatsApp en esta fase** |
| Origen y finalidad | Cada contacto lleva su **origen** y la **finalidad** para la que se guarda. Los contactos comerciales de la cuenta se mantienen separados de los compradores de sus tiendas |
| Plan | Hay que agregar la función `crm` a `plans.features`. La migración de planes pide que toda función con puerta figure al menos en `scale`. **Decidido: Growth y Scale** (Starter no). **Decidido:** una cuenta sin CRM (lo desactiva o baja a un plan que no lo incluye) funciona sin la lógica del CRM, y los datos que tenía quedan resguardados: conservados, aislados y sin acceso desde el producto. Propuesta a confirmar: exportar y borrar siguen disponibles aun con el módulo inactivo, para poder atender los derechos de las personas |
| Aislamiento | Toda tabla lleva `account_id`. Todo acceso pasa por las dependencias actuales. **Test de aislamiento entre cuentas obligatorio por ruta y por herramienta** |
| Roles | `viewer` lee. `admin` y `owner` escriben. El asistente hereda el rol de quien le habla |
| Datos personales | ROSS pasa a ser encargado del tratamiento de los contactos de la cuenta. En primera instancia lo aprueban el administrador y quien lleve la contaduría. **La revisión legal o de privacidad es un requisito formal antes de empezar la etapa 4.4**, a cargo del responsable legal. La consulta está redactada en `fase-4-crm-consulta-legal.docx` |
| Derechos | Exportar y borrar todo el CRM de una cuenta, y borrar un contacto, desde el primer día |
| Cuenta demo | No escribe en el CRM ni puntúa |

### De dónde salen los contactos

| Opción | Pros | Contras |
|---|---|---|
| A. Los carga o importa la cuenta | Sin flujo nuevo de datos personales desde las plataformas. Sirve para leads, mayoristas, proveedores y B2B | Más trabajo manual |
| B. Se sincronizan desde las órdenes de las tiendas | Es lo que más valor daría: compradores, recompra, segmentos | Hoy `Customer` guarda solo hashes. Habría que capturar nombre y email en claro desde Shopify y Tiendanube. Decisión legal y de seguridad grande |
| C. A primero, B después y opcional | Entrega el CRM sin esperar la decisión legal | Quien tiene tienda pide B desde el inicio |

**Decidido: C.** Lo posterior es el cruce por hash (etapa 4.8), no la sincronización de compradores en claro.

### Privacidad de las trazas

| Tema | Propuesta |
|---|---|
| Qué se guarda | Intención, plan de herramientas, escalón, proveedor, latencia y score. La pregunta se guarda **normalizada y sin valores** (sin nombres de contactos ni montos) |
| Aprender entre cuentas | Solo estructura: intenciones y plantillas. **Nunca notas, contactos ni cifras de una cuenta para otra** |
| Retención | Las trazas vencen |
| Aviso | Aviso explícito de que las interacciones mejoran las respuestas, con opción de salir. Lo confirma el equipo legal |

## 6. Consecuencias para las decisiones

| Decisión | Efecto |
|---|---|
| **D1** Orden | Se mantiene: eventos, scoring y trazas primero |
| **D2** CRM | Módulo opcional por cuenta (sección 5) |
| **D3** Motor | De "híbrido, arrancando sin modelo" a **escalera L0 a L3**. El modelo se espera, pero ocupa L2 y L3 y se mide por cuánto se lo evita |
| **D4** Datos | Las herramientas se registran por módulo y tienen un **nivel de riesgo** (lectura, escritura de bajo riesgo, alto riesgo). Una capa de política única decide si se ejecutan: una herramienta de CRM solo existe si la cuenta activó el módulo y el rol tiene permiso |
| **D5** Validación | El validador es la compuerta de L3 y de toda respuesta con cifras. Los patrones promovidos no la saltean |
| **D6** Utilidad | Se agregan señales del CRM (sugerencia aceptada, tarea completada, oportunidad que avanzó) y el score por escalón |
| **D7** Qué ajusta el score | Se suma la **promoción de patrones** a L1. El ajuste fino de un modelo sigue descartado y se reevalúa con datos |
| **D8** Retroalimentación | La tabla de eventos guarda también el escalón y la traza. El CRM aporta contexto al asistente y el asistente al CRM |
| **D9** Límites | Se amplía con la sección 5 |

## 7. Roadmap

| Etapa | Contenido | Criterio de salida |
|---|---|---|
| **4.1 Base** | Extraer las consultas de `routes/metrics.py` a servicios, sin cambiar un resultado. Registro de herramientas con módulo y permiso. Fixture de aislamiento entre cuentas, reutilizable | Los tests de las rutas pasan igual. Cada herramienta pasa el test de aislamiento |
| **4.2 Eventos, scoring y trazas** | Tabla de interacciones, señales, score por tipo y esquema de trazas (intención, escalón, plan, proveedor, latencia, puntaje de selección). Se prueba con la narrativa semanal | El score se calcula con datos reales. La compuerta rechaza cifras inválidas. Las trazas no guardan valores ni texto libre |
| **4.3 Asistente en la app (L0 y L1)** | Router de intenciones, **puntaje de selección con sus tres comportamientos**, plantillas, validador y botones de utilidad. Solo analítica. Registra huecos de cobertura. Una persona revisa los huecos y promueve intenciones a mano | Responde las preguntas de cada herramienta. Existe la **línea base de resolución sin modelo** |
| **4.4 Módulo CRM** | **Requisito previo: revisión legal o de privacidad.** Activación por cuenta, contactos con origen y finalidad, empresas, oportunidades, tareas y notas, con pantalla propia. Alta manual, CSV, exportar, borrar y auditoría | El CRM se usa sin el asistente. Aislamiento probado en cada ruta. Un contacto se borra de punta a punta |
| **4.5 Gateway de IA y modelo** | Contrato con capacidades separadas, escritor falso, presupuestos y caída a escalones bajos. **Primer proveedor: modelo local con Ollama**, desarrollado en una máquina disponible. Las APIs comerciales quedan como opción, desactivadas por defecto. Se elige proveedor por capacidad y por sensibilidad del dato (propuesta: los datos del CRM solo van a un proveedor local). Se conecta en L2 y L3 y se compara contra el router | La escalera completa funciona con el escritor falso. Con el modelo real, el validador no rechaza más que antes y todo se apaga con una variable. **Antes de activarlo en producción se define dónde corre el modelo local** (el entorno actual no tiene GPU) |
| **4.6 Promoción y autonomía creciente** | Modo sombra, umbral de promoción, revisión humana, versionado, reversión, exploración y tablero de indicadores | Los patrones promovidos igualan o superan el score del modelo. La tasa de resolución sin modelo sube sin caer la utilidad |
| **4.7 El asistente opera el CRM** | Primero lectura ("¿a quién tengo que seguir esta semana?"). Después escrituras de bajo riesgo con confirmación visible y registro, y "preparar un mensaje para revisión" (sin enviarlo). **Enviar mensajes, borrar registros y acciones financieras son de alto riesgo y exigen controles adicionales**, que se definen antes de habilitarlas. Respuestas que cruzan métricas y CRM | Ninguna escritura sin confirmación. Un `viewer` no escribe ni por el asistente. Las acciones se pueden auditar. Las de alto riesgo están deshabilitadas |
| **4.8 Historial de compras de los contactos, por cruce de hash** *(opcional)* | Un contacto que la cuenta cargó muestra su historial de compras y su LTV comparando el hash de su email con el de los compradores de las tiendas de esa misma cuenta. No se capturan datos nuevos. **Sincronizar compradores en claro queda fuera y exige una revisión propia: para qué se usarían, quién accede y qué base legal corresponde** | El cruce solo opera dentro de una misma cuenta. Revisión de privacidad hecha antes y no al final. Aislamiento por tienda probado |

Notas de orden:
- **Las trazas empiezan en la 4.2.** Es lo único que no se puede recuperar después: la independencia progresiva se alimenta de lo registrado desde el principio.
- **4.3 y 4.4 son independientes.** Con el equipo actual conviene en serie, la 4.3 primero.
- **4.5 no espera al modelo.** Se construye y se prueba con el escritor falso. Cuando el modelo esté listo, solo se conecta.
- **4.6 es continua.** Después de la primera entrega sigue como un ciclo de mejora, no como un hito que termina.
- **4.7 usa la escalera.** Un pedido como "anotá que hablé con Marta y volvé a llamarla el jueves" necesita L2 para extraer parámetros. Si el modelo no mejora al router, se entrega con formularios guiados.

## 8. Qué se evita

| Dentro | Fuera de esta fase |
|---|---|
| Contactos, empresas, embudo simple, tareas, notas | Envío de email y WhatsApp desde el CRM |
| Importar CSV, exportar, borrar | Automatizaciones y secuencias |
| Promover estructura (intención, plan, plantilla) | Servir respuestas con cifras guardadas |
| Sugerencias con confirmación | Escrituras automáticas sin confirmar |
| Vista de salud de cuenta para ROSS | Reportes personalizables |

## 9. Riesgos

| Riesgo | Mitigación |
|---|---|
| Se promueve una intención equivocada y el asistente responde otra cosa | Modo sombra, umbral, revisión humana al principio y reversión por bandera |
| El router se estanca y deja de mejorar | Exploración: parte de las consultas promovidas se contrasta con el modelo |
| Bajar el costo sin notar que bajó la calidad | El score se mide por escalón y la promoción exige igualar al modelo |
| Una herramienta cambia y el patrón promovido queda viejo | Los patrones se invalidan al cambiar el esquema de su herramienta |
| Las trazas guardan datos personales | Pregunta normalizada y sin valores, retención corta y solo estructura entre cuentas |
| Un contacto o nota de una cuenta aparece en otra | `account_id` en todo y test de aislamiento obligatorio |
| El asistente escribe algo que el usuario no quiso | Confirmación visible, auditoría y herencia del rol |
| El corpus es chico y los patrones no se estabilizan | Mínimo de muestras, y más tiempo en L2 hasta tener volumen |
| Se espera al modelo para empezar | La escalera se construye y se prueba con el escritor falso desde la 4.5 |
| El CRM crece hasta ser un producto aparte | Lista "Fuera de esta fase" revisada en cada etapa |
| Un puntaje de selección alto autoriza algo prohibido | La política es una capa independiente que no lee el puntaje |
| Los pesos del puntaje de selección no están calibrados | Se tratan como hipótesis y se ajustan con trazas y modo sombra |
| El modelo local no rinde en producción por falta de hardware | Se desarrolla en una máquina disponible y el alojamiento se decide antes de activarlo. Con el modelo apagado, la escalera sigue funcionando |

## 10. Decisiones tomadas

| Tema | Decisión |
|---|---|
| Modelo de IA | **Híbrido**. Primero un modelo local (Ollama). Las APIs comerciales son opcionales y están desactivadas por defecto. Se elige proveedor por capacidad |
| Promoción de patrones | Aprobación humana en cada una al principio |
| Contactos del CRM | **Confirmado:** solo los que carga la cuenta (formulario o CSV), sin vínculo con `customers` ni con los pedidos. Los compradores de las tiendas son opcionales, nunca automáticos, y exigen definir finalidad, acceso y base legal antes. **Decidido:** el cruce por hash es el contenido de la etapa 4.8 |
| Planes que incluyen el CRM | **Growth y Scale.** **Decidido:** una cuenta sin CRM (lo desactiva o baja a un plan que no lo incluye) funciona sin la lógica del CRM, y los datos que tenía quedan resguardados: conservados, aislados y sin acceso desde el producto. Propuesta a confirmar: exportar y borrar siguen disponibles aun con el módulo inactivo, para poder atender los derechos de las personas |
| Datos personales | Administrador y contaduría en primera instancia, **más** revisión del responsable legal como requisito formal antes de la 4.4. Documento de consulta: `fase-4-crm-consulta-legal.docx` |
| Selección de respuestas | Puntaje híbrido con pesos iniciales como hipótesis y tres comportamientos según la confianza. Nunca autoriza |
| Ubicación de las herramientas | `backend/app/ai/tools/` |
| Período de una herramienta | Ventana móvil de N días que termina ahora. Los períodos de calendario se agregan después, como un cambio aparte |
| Qué devuelve el servicio de métricas | Diccionarios simples. Las herramientas los validan con los modelos de `schemas/metrics.py` |
| Ventana máxima por consulta | 90 días, configurable |

Siguiente documento: [fase-4-1-diseno.md](fase-4-1-diseno.md), el diseño detallado de la etapa 4.1.
