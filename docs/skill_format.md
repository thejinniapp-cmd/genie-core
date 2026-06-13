# Skill Format Specification

Un skill de Genie es un archivo `.md` que define un proceso de negocio preconfigurado.
Cualquier usuario puede crear uno y subirlo al marketplace.

## Estructura

```markdown
---
name: Nombre del skill
slug: nombre-del-skill
version: 1.0.0
description: Descripción corta (max 120 chars)
category: ventas | soporte | operaciones | finanzas | contenido | legal | custom
author: nombre o email
price: 0                    # 0 = gratis
connectors_required:        # conectores que necesita
  - gmail
  - google_drive
autonomy_default: supervised  # manual | supervised | autonomous
---

## System Prompt

Instrucciones del agente en lenguaje natural. Esto es lo que el modelo
de IA leerá como contexto de operación.

Ejemplo:
Eres un agente de seguimiento de cobranza para [NOMBRE_EMPRESA].
Tu objetivo es revisar facturas vencidas y enviar recordatorios profesionales.
Siempre usa un tono cordial y profesional. Nunca amenaces ni presiones.

## Trigger

Cuándo se activa este skill:
- `manual` — el usuario lo activa manualmente
- `scheduled: "0 9 * * 1-5"` — cron (lunes a viernes 9am)
- `event: invoice.overdue` — cuando ocurre un evento en el sistema

## Tools

Acciones que el agente puede ejecutar:

- `gmail.search_emails` — buscar emails de clientes con facturas pendientes
- `gmail.send_email` — enviar recordatorio de pago
- `google_drive.read_file` — leer el reporte de facturas

## Variables

Variables que el usuario configura al instalar:

- `DIAS_VENCIMIENTO` — número de días para considerar vencida (default: 30)
- `LIMITE_MONTO` — monto mínimo para enviar recordatorio (default: 1000)
- `NOMBRE_EMPRESA` — nombre de la empresa para el email

## Ejemplo de output

El agente devuelve:
- Lista de facturas contactadas
- Emails enviados
- Facturas que requieren atención manual
```

## Ejemplo completo: Seguimiento de cobranza

```markdown
---
name: Seguimiento de cobranza
slug: seguimiento-cobranza
version: 1.0.0
description: Revisa facturas vencidas y envía recordatorios automáticos por email
category: finanzas
author: genie-official
price: 0
connectors_required:
  - gmail
  - google_drive
autonomy_default: supervised
---

## System Prompt

Eres un agente de cobranza profesional para [NOMBRE_EMPRESA].

Tu tarea es:
1. Revisar el spreadsheet de facturas en Google Drive
2. Identificar facturas vencidas hace más de [DIAS_VENCIMIENTO] días
3. Para facturas mayores a [LIMITE_MONTO] MXN, redactar y enviar un email de recordatorio
4. Registrar qué facturas fueron contactadas

Tono: profesional, cordial, nunca agresivo.
Idioma: español mexicano.

## Trigger

scheduled: "0 9 * * 1"

## Tools

- google_drive.read_file
- gmail.send_email
- gmail.search_emails

## Variables

- DIAS_VENCIMIENTO: 30
- LIMITE_MONTO: 1000
- NOMBRE_EMPRESA: Mi Empresa
```

## Skills bundle (.plugin)

Un bundle agrupa múltiples skills en un solo archivo:

```markdown
---
name: Pack Atención a Clientes
slug: pack-atencion-clientes
type: bundle
skills:
  - seguimiento-cobranza
  - bot-soporte-whatsapp
  - reporte-semanal-ventas
---

Instala los 3 skills del pack de atención a clientes de una sola vez.
```
