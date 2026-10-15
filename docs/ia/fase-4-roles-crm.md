# Fase 4 — Roles y funciones del CRM, a partir de los asistentes del dashboard

Propuesta para decidir. Parte de lo que ya existe en el producto y suma lo que pidieron las decisiones legales: exportar los datos de un contacto
lo ven quienes tienen acceso a auditorías y los comerciales que tratan con clientes.

## 1. Punto de partida

| Hecho | Detalle |
|---|---|
| Roles de cuenta | `owner`, `admin` y `viewer`. Un rol por tienda (`store_memberships`) puede reemplazar al de la cuenta, con la misma lista de tres |
| Auditoría hoy | La ve `owner` o `admin`, y solo si el plan tiene la función `audit_log`, que hoy es solo Scale |
| Asistentes del dashboard | Seis, en la "Oficina de agentes": Pulso (conectores), Saldo (finanzas), Foco (anuncios), Retorno (clientes), Vigía (alertas) y Bitácora (reportes) |
| Cómo se controlan sus tareas | Cada tarea declara si escribe. Las que escriben solo se habilitan a `owner` y `admin`, y nunca en la cuenta demo |
| Herramientas del asistente conversacional | Registro con módulo, rol mínimo, función de plan y riesgo. El CRM entra como un módulo más |

## 2. Funciones del CRM

Se separan en permisos concretos para poder repartirlos entre roles.

| Permiso | Qué permite |
|---|---|
| Ver | Ver contactos, empresas, oportunidades, tareas y notas |
| Editar | Crear y modificar contactos, empresas y notas |
| Gestionar trato | Crear y mover oportunidades, y crear y cerrar tareas |
| Importar | Cargar contactos desde un CSV |
| Exportar un contacto | Exportar los datos de un contacto (el pedido de un titular) |
| Borrar un contacto | Borrado definitivo, que deja constancia durante 7 días |
| Ver auditoría | Ver el registro de accesos, las constancias de borrado y las fichas de pedidos |
| Administrar el módulo | Activar y desactivar el CRM, y asignar roles del CRM |
| Borrar todo | Eliminar todo el CRM de la cuenta |

## 3. Roles

Los tres roles actuales más dos nuevos, propuestos a partir de lo que pediste: un **Comercial** y un **Auditor**.

| Permiso | Owner | Admin | Comercial | Auditor | Viewer |
|---|---|---|---|---|---|
| Ver | Sí | Sí | Sí | Sí | Sí |
| Editar | Sí | Sí | Sí | No | No |
| Gestionar trato | Sí | Sí | Sí | No | No |
| Importar | Sí | Sí | Sí | No | No |
| Exportar un contacto | Sí | Sí | Sí | Sí | No |
| Borrar un contacto | Sí | Sí | No | No | No |
| Ver auditoría | Sí | Sí | No | Sí | No |
| Administrar el módulo | Sí | Sí | No | No | No |
| Borrar todo | Sí | No | No | No | No |

Criterios detrás de la tabla:
- **Borrar todo** y **administrar el módulo** por ser irreversible y ligado al plan: lo más conservador es que lo haga solo `owner`. Se puede abrir a `admin`.
- **Comercial** no borra: puede pedir el borrado a un `admin`. Se puede abrir si trabajan solos.
- **Auditor** ve y exporta, pero no modifica nada, para que pueda revisar sin alterar lo que revisa.
- **Viewer** lee, pero no exporta ni ve la auditoría, igual que hoy no ve la auditoría de pedidos.

### Cómo se guardan los roles

| Opción | Pros | Contras |
|---|---|---|
| A. Agregar `comercial` y `auditor` a los roles de cuenta | Un solo sistema de roles | Hay que cambiar la restricción de roles en las tablas de usuarios y de roles por tienda, y los dos roles nuevos aparecerían en todo el producto aunque no use el CRM |
| **B. Un rol del CRM aparte, solo para cuentas con el módulo** *(recomendada)* | No toca los roles actuales ni sus restricciones. Owner y admin son administradores del CRM sin hacer nada | Dos sistemas de roles que hay que explicar |
| C. Permisos sueltos por usuario | Máxima flexibilidad | Difícil de auditar y de explicar a quien administra |

Con B, una tabla `crm_members` guarda, por cuenta y usuario, uno de: `comercial`, `auditor` o `lectura`. Quien es `owner` o `admin` de la cuenta tiene los permisos de administrador del CRM sin fila.

## 4. Los asistentes del dashboard y el CRM

Cada asistente toma tareas del CRM según lo que ya mide. Una tarea solo aparece para quien tiene el permiso que pide.

| Asistente | Lo que ya mide | Qué suma en el CRM | Permiso |
|---|---|---|---|
| **Retorno** (clientes) | Valor de vida por cohorte y recompra | Contactos y oportunidades: a quién seguir esta semana, abrir la ficha de un contacto | Ver. Crear tareas pide Gestionar trato |
| **Vigía** (alertas) | CAC por canal y ROAS mínimo | Tareas vencidas, oportunidades sin movimiento y plazos del CRM (pedidos de exportación a vencer, cuentas cerca de los 180 días de retención) | Ver |
| **Bitácora** (reportes) | El resumen de la semana de cada tienda | Resumen semanal del CRM, registro de accesos, constancias de borrado y fichas de pedidos | Ver, y Ver auditoría para el registro |
| **Pulso** (conectores) | Frescura de los datos | Estado de las importaciones: última carga y filas con error | Importar |
| **Saldo** (finanzas) | True ROAS y ganancia real | Valor del embudo de oportunidades, si las oportunidades llevan un monto | Ver |
| **Foco** (anuncios) | Gasto, CTR y CPC por creativo | Contactos por origen: qué canales traen contactos. Pide que el origen tenga valores controlados | Ver |

**Reutilizar o sumar un asistente.** Se pueden repartir las tareas entre los seis (recomendado, sin cambiar la oficina) o sumar un séptimo asistente solo para el CRM. Con seis, el CRM queda repartido; con siete, queda concentrado pero hay que dibujar un asiento nuevo.

**Asistente conversacional.** Usa las mismas reglas: las herramientas del CRM son un módulo, y se les agrega el permiso que exigen además del rol. La política sigue siendo un único punto de decisión.

## 5. Decisiones abiertas

1. **El comercial ¿puede borrar un contacto?** La propuesta es que no.
2. **El auditor ¿ve el detalle de los contactos?** La propuesta es que sí, para poder exportar. Si no, solo vería registros y constancias.
3. **El registro de accesos del CRM ¿se limita a Scale, como la auditoría de pedidos?** El nivel de registro que se eligió es obligatorio, así que no puede depender del plan. La propuesta es incluirlo en todos los planes con CRM (Growth y Scale).
4. **Roles guardados:** opción A, B o C. Se recomienda B.
5. **Asistentes:** repartir el CRM entre los seis o sumar un séptimo. Se recomienda repartir.
