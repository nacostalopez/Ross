# Informe de propuestas visuales — ROSS

**Preparado para:** reunión de presentación con cliente  
**Fecha:** 1 de octubre de 2026  
**Objetivo:** identificar mejoras visuales y de comunicación que hagan más clara, convincente y presentable la experiencia de ROSS.

## Resumen ejecutivo

ROSS ya cuenta con una identidad reconocible —azul marino, azul de acción y recursos gráficos de flujo— y una base visual consistente entre la página pública y el acceso a la plataforma. La principal oportunidad no es cambiar la marca, sino hacer que la propuesta de valor se entienda más rápido y que la interfaz demuestre el producto con ejemplos concretos.

Para la reunión de mañana, recomendamos priorizar tres puntos: **alinear lo que se promete con lo que el producto hace**, **mostrar una vista realista del tablero** y **resolver los precios de ejemplo antes de mostrar esa sección**. Después, se puede simplificar el contenido repetido y pulir navegación, contraste y versión móvil.

## Alcance y método

Se revisaron la página de presentación, la pantalla de acceso, la página de precios y la estructura/código visual del dashboard. La revisión visual directa se hizo sobre páginas estáticas. **No fue posible abrir la aplicación servida en `localhost:3100`**, por lo que no se pudo validar el dashboard con datos reales, sus estados tras iniciar sesión ni todos los comportamientos adaptables. Las observaciones de dashboard se basan en su estructura y estilos existentes, no en una prueba funcional completa.

Páginas revisadas: [frontend/landing.html](frontend/landing.html), [frontend/pricing.html](frontend/pricing.html), [frontend/index.html](frontend/index.html) y [frontend/style.css](frontend/style.css).

## Diagnóstico

### 1. La promesa necesita alinearse con el producto

La página pública habla de gestión, ventas, caja, stock y de sectores muy diversos; el producto y las pantallas funcionales se orientan principalmente a analítica de e-commerce, rentabilidad, publicidad y conectores. Esta diferencia puede generar preguntas o expectativas que la demo no responda.

**Propuesta:** ajustar el titular, el subtítulo y los sectores atendidos para describir con precisión qué integra ROSS hoy, para quién y qué decisión ayuda a tomar. Si la visión incluye otros rubros, presentarlos como expansión o roadmap, no como capacidades disponibles.

**Prioridad: alta — resolver antes de presentar.**

### 2. La página de inicio necesita enseñar el producto

El hero tiene un titular visible y una paleta sólida, pero se apoya casi exclusivamente en texto y decoración abstracta. No aparece una captura del dashboard ni una demostración breve que permita entender de inmediato qué verá el cliente al entrar.

**Propuesta:** añadir junto al hero una captura cuidada del tablero o una composición visual con 3–4 métricas reales de demostración (por ejemplo: ingresos, gasto publicitario, beneficio y True ROAS), con período y moneda visibles. Usar información ficticia claramente identificada como demostración o datos anonimizados autorizados.

**Prioridad: alta — aporta comprensión y credibilidad durante la reunión.**

### 3. La sección de precios no está lista para mostrarse como definitiva

Las tarjetas incluyen el marcador literal **“[Precio]”** y un aviso de que los valores aún se están definiendo. También conviven llamadas a la acción diferentes, incluida una prueba gratuita. En una presentación comercial, esto puede distraer de la conversación sobre valor y hacer parecer incompleta la propuesta.

**Propuesta:** antes de compartir la página, acordar precios y condiciones; si todavía no están aprobados, sustituir temporalmente la tabla por “Planes en preparación — solicitar propuesta” y un único canal de contacto. Revisar que prueba gratuita, límites y prestaciones sean condiciones ya confirmadas.

**Prioridad: crítica si se va a abrir la página de precios.**

### 4. El recorrido de contenido es largo y repite beneficios

Después del hero aparecen “El desafío / La solución / El resultado”, cuatro pilares y varias frases de marca que reiteran ideas parecidas. El visitante recorre bastante contenido antes de llegar a una acción o a una evidencia del producto.

**Propuesta:** condensar el relato en un flujo simple: problema concreto → cómo lo resuelve ROSS → captura del producto → beneficios verificables → llamada a la demo. Mantener uno de los bloques de mensajes de marca, no ambos si repiten lo mismo.

**Prioridad: media.**

### 5. Las llamadas a la acción pueden ser más directas

“Solicitar demo” conduce a un enlace de correo (`mailto:`), que obliga a cambiar de aplicación y no ofrece una experiencia de reserva ni recoge contexto del prospecto.

**Propuesta:** enlazar a un formulario breve o agenda de reunión. Como solución de transición para mañana, usar una dirección de contacto visible y consistente, con asunto precompletado y texto que explique qué ocurrirá al hacer clic.

**Prioridad: media-alta.**

### 6. Hay oportunidad de ordenar el dashboard para la lectura ejecutiva

La cabecera de tienda concentra selector de período y varias acciones —datos de demo, alertas, reportes, miembros, auditoría y personalización— con un peso visual parecido. Para un cliente nuevo puede ser difícil identificar primero los resultados principales.

**Propuesta:** ordenar la pantalla según la tarea principal: contexto de tienda y período; indicadores clave; gráfico de evolución; detalle y conectores. Dejar como acción primaria solo la tarea más importante para el usuario y agrupar las acciones administrativas/secundarias en un menú. Mostrar estado de sincronización y fecha de última actualización donde resulte pertinente.

**Prioridad: media; validar con la demo autenticada.**

### 7. Unificar idioma y nomenclatura de métricas

La interfaz combina español con términos como “Revenue”, “Profit”, “Forecast”, “Performance” y “True ROAS”. Algunos pueden ser vocabulario habitual del sector, pero la mezcla puede parecer inconsistente y exige explicaciones.

**Propuesta:** definir un glosario breve y mantenerlo en botones, tarjetas y reportes. Se pueden conservar nombres estándar como “True ROAS”, acompañados de una aclaración inicial, por ejemplo: “retorno sobre gasto publicitario, ajustado a ventas reales”.

**Prioridad: media.**

### 8. Aprovechar mejor la identidad gráfica

La estética de azul marino, azul y celeste distingue a ROSS y conviene conservarla. Sin embargo, las curvas y nodos decorativos ocupan espacio visual en zonas de decisión y no sustituyen a imágenes del producto, datos o señales de confianza. Las tarjetas repetidas también pueden hacer que toda la página tenga el mismo énfasis.

**Propuesta:** reservar el motivo de flujo para el hero y elementos puntuales; dar más contraste a la acción principal y crear jerarquía entre secciones. Incorporar iconografía funcional o visuales de producto solo cuando ayuden a explicar una capacidad.

**Prioridad: media-baja.**

### 9. Verificar legibilidad, accesibilidad y móvil

En la composición observada, algunos textos secundarios son pequeños y de bajo peso visual; los planes atenuados en la página de precios reducen todavía más su legibilidad. No se verificó toda la experiencia móvil ni el contraste computado.

**Propuesta:** revisar legibilidad de textos secundarios y contraste de botones, etiquetas y estados; no indicar resultados solo mediante color; comprobar foco de teclado y tamaños táctiles. Probar las páginas en anchos de móvil habituales y asegurar que navegación, controles, tarjetas y llamadas a la acción no queden apretados.

**Prioridad: media, antes del lanzamiento público.**

## Plan recomendado para mañana

### Antes de la reunión — cambios de mayor impacto

1. Acordar una frase de posicionamiento que describa las capacidades disponibles hoy y usarla en hero y discurso comercial.
2. Decidir si se va a mostrar la página de precios. Si no hay precios aprobados, no presentarla como una tarifa cerrada: ocultar el enlace durante la demo o usar una versión temporal de consulta comercial.
3. Preparar una captura o cuenta demo con datos coherentes, período, moneda y etiquetas legibles; confirmar que no expone información de terceros.
4. Sustituir afirmaciones generales por dos o tres beneficios demostrables en el producto.
5. Tener lista una llamada a la acción clara: agenda, formulario o contacto responsable.

### Siguiente iteración

- Reordenar el contenido público para reducir repetición y mostrar el producto antes.
- Simplificar y priorizar acciones del dashboard.
- Normalizar el vocabulario español/inglés.
- Revisar responsive, contraste, foco y estados vacíos con la aplicación en funcionamiento.

## Propuesta de mensaje para presentar al cliente

> “La identidad visual de ROSS ya tiene una base clara y reconocible. Nuestra propuesta es hacer que esa identidad explique mejor el valor del producto: concretar el mensaje, mostrar el tablero desde el primer recorrido y dejar la información comercial —especialmente los precios— sin elementos provisionales. Así la presentación conduce al cliente desde su problema hasta una evidencia visible de cómo ROSS le ayuda a decidir.”

## Resultado esperado

Con estos ajustes, la primera impresión debería comunicar con mayor precisión qué hace ROSS, para quién es y qué obtiene el cliente. La demo ganaría claridad y foco comercial, reduciría dudas por promesas o precios ambiguos y conservaría los elementos visuales de marca que ya funcionan.
