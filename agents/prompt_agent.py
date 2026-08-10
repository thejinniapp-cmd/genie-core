"""
agents/prompt_agent.py
======================
Agente ligero que corre 100% dentro de Genie.
Recibe un mensaje del stream, lo procesa con Claude y escribe la
respuesta de vuelta al stream como mensaje de rol "assistant".
"""

import os
import json
import logging
import httpx
from typing import Any

from agents.base_agent import BaseAgent, HumanReviewRequired
from core.connectors.executor import sanitize_params
from core.rag.context import get_full_context

log = logging.getLogger("genie.prompt_agent")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_BASE = "https://api.anthropic.com/v1"

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_BASE = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")


class PromptAgent(BaseAgent):
    """
    Agente que responde mensajes del stream usando Claude.
    Se dispara automáticamente cuando el usuario escribe en el stream.
    """

    agent_id = "prompt_agent"
    job_type = "prompt"

    def run(self, job: dict, org_id: str) -> dict:
        agent_config = job.get("agent_config", {})
        input_data = job.get("input_data", {})
        stream_id = job.get("stream_id")

        # Guardar contexto del job para que _execute_tool pueda accederlo
        self._current_stream_id = stream_id
        self._current_job_id = job.get("id")

        system_prompt = agent_config.get("system_prompt", "Eres Genie, un asistente de operaciones inteligente.")
        model_id = agent_config.get("model_id") or os.environ.get("GENIE_DEFAULT_MODEL", "claude-sonnet-4-5")
        temperature = float(agent_config.get("temperature", 0.3))
        max_tokens = int(agent_config.get("max_tokens", 4096))
        autonomy = agent_config.get("autonomy_level", "supervised")
        tools_config = agent_config.get("tools", [])

        # Si el stream tiene ADNs invitados, su system prompt combinado toma prioridad
        if stream_id:
            adn_prompt = self._get_adn_system_prompt(stream_id, org_id)
            if adn_prompt:
                # ADN prompt primero; el system_prompt manual del agente se agrega como contexto adicional
                manual = agent_config.get("system_prompt", "").strip()
                if manual and manual != "Eres Genie, un asistente de operaciones inteligente.":
                    system_prompt = f"{adn_prompt}\n\n## Instrucciones adicionales del stream\n\n{manual}"
                else:
                    system_prompt = adn_prompt

        # Inyectar estado actual de conectores — evita que Claude asuma estado del historial
        connectors_context = self._get_connectors_context(org_id)
        system_prompt = f"{system_prompt}\n\n{connectors_context}"

        # Regla crítica: siempre usar tools para acceder a datos externos
        system_prompt += (
            "\n\n## REGLA CRÍTICA DE TOOLS\n"
            "SIEMPRE usa tus herramientas para acceder a datos externos. "
            "NUNCA asumas que un conector no funciona basándote en mensajes anteriores de esta conversación. "
            "Si el usuario pregunta sobre archivos, correos, calendarios o cualquier dato externo, "
            "llama el tool correspondiente EN ESTE TURNO para obtener datos frescos. "
            "Los errores anteriores en el historial NO significan que el conector sigue fallando.\n\n"
            "## REGLA NOTEBOOKLM — GENERACIÓN OBLIGATORIA\n"
            "Si el usuario pide crear un notebook Y generar contenido (infografía, audio, podcast, etc.), "
            "DEBES hacer las llamadas en este orden DENTRO DEL MISMO TURNO:\n"
            "1. notebooklm_crear (con fuentes si las dio)\n"
            "2. notebooklm_generar con el notebook_id retornado en el paso 1\n"
            "3. Responder con action_result widget confirmando que el notebook fue creado y que la generación está en progreso.\n"
            "NUNCA termines el turno después de crear el notebook sin haber llamado notebooklm_generar si el usuario pidió generación. "
            "La generación corre en background — no necesitas esperar.\n\n"
            "## FORMATO DE RESPUESTA TRAS EJECUTAR ACCIONES\n"
            "Cuando completes una o más acciones (copiar archivo, compartir, enviar email, crear evento, etc.), "
            "responde SIEMPRE con un bloque genie-widget de tipo action_result, seguido de un texto breve. "
            "Formato exacto:\n"
            "```genie-widget\n"
            "{\"type\":\"action_result\",\"title\":\"<título corto ej: NDA enviado>\","
            "\"subtitle\":\"<descripción breve opcional>\","
            "\"data\":{"
            "\"steps\":[\"<paso 1 completado>\",\"<paso 2 completado>\",\"...\"],"
            "\"link\":\"<webViewLink del archivo si aplica>\","
            "\"link_label\":\"<ej: Ver copia en Drive>\"}}\n"
            "```\n"
            "Usa solo los campos que apliquen. steps debe listar cada acción ejecutada (copia creada, compartido con X, email enviado a Y, etc.). "
            "NO respondas solo con texto plano después de ejecutar acciones."
        )

        # Enriquecer system prompt con contexto RAG del stream
        user_message = input_data.get("message", "")
        rag_context = get_full_context(org_id, user_message, stream_id)
        if rag_context:
            system_prompt = f"{system_prompt}\n\n{rag_context}"

        # Recuperar historial reciente del stream
        messages = self._get_recent_messages(stream_id, org_id, user_message, limit=10)

        tools = self._build_tools(org_id, tools_config)

        response = self._call_claude(
            model_id=model_id,
            system_prompt=system_prompt,
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        # Loop de tool calls — Claude tiene herramientas disponibles en cada ronda
        all_tool_calls_made = []
        current_messages = messages
        MAX_TOOL_ROUNDS = 8

        for _round in range(MAX_TOOL_ROUNDS):
            if not response.get("tool_calls"):
                break

            # Mostrar texto intermedio al usuario antes de ejecutar
            if response.get("content") and stream_id:
                self._write_to_stream(org_id, stream_id, response["content"], job)

            # 1. Ejecutar tools y construir bloques tool_use + tool_result
            tool_use_blocks = []
            tool_result_contents = []
            round_tools_made = []

            for tc in response["tool_calls"]:
                tool_name = tc["function"]["name"]
                tool_args = json.loads(tc["function"].get("arguments", "{}"))
                log.info(f"[prompt_agent] Tool call (round {_round+1}): {tool_name}({sanitize_params(tool_args)})")
                try:
                    result = self._execute_tool(org_id, tool_name, tool_args)
                except Exception as e:
                    result = {"error": str(e)}
                    log.error(f"[prompt_agent] {tool_name} failed: {e}")

                tool_use_blocks.append({"type": "tool_use", "id": tc["id"], "name": tool_name, "input": tool_args})
                tool_result_contents.append({"type": "tool_result", "tool_use_id": tc["id"], "content": json.dumps(result)})
                round_tools_made.append({"tool": tool_name, "args": tool_args, "result": result})

            all_tool_calls_made.extend(round_tools_made)

            # 2. Actualizar historial con esta ronda
            current_messages = current_messages + [
                {"role": "assistant", "content": tool_use_blocks},
                {"role": "user", "content": tool_result_contents},
            ]

            # 3. Llamar a Claude de nuevo CON herramientas para que pueda seguir encadenando
            try:
                response = self._call_claude(
                    model_id=model_id,
                    system_prompt=system_prompt,
                    messages=current_messages,
                    tools=tools,  # Claude puede seguir llamando tools
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            except Exception as e:
                log.error(f"[prompt_agent] Claude call failed in round {_round+1}: {e}", exc_info=True)
                if stream_id:
                    self._write_to_stream(org_id, stream_id, f"Error en ronda {_round+1}: {e}", job)
                return {"response": str(e), "model_used": model_id, "tool_calls_made": all_tool_calls_made}

        reply_text = response.get("content", "")

        # Escribir respuesta de vuelta al stream
        if reply_text and stream_id:
            self._write_to_stream(org_id, stream_id, reply_text, job)
        elif not reply_text and stream_id:
            # Salvaguarda: si Claude no devolvió texto, avisar en lugar de silencio
            log.warning(f"[prompt_agent] Empty reply from Claude for stream {stream_id}")
            self._write_to_stream(org_id, stream_id,
                "No obtuve respuesta. Por favor intenta de nuevo.", job)

        if autonomy == "supervised" and response.get("requires_approval"):
            raise HumanReviewRequired(response.get("approval_reason", "Requiere revisión humana"))

        return {
            "response": reply_text,
            "model_used": model_id,
            "tool_calls_made": all_tool_calls_made or response.get("tool_calls_made", []),
        }

    def _get_connectors_context(self, org_id: str) -> str:
        """Retorna el estado actual de conectores para inyectar en el system prompt."""
        try:
            res = self.supabase.table("connectors").select("connector_type,status") \
                .eq("org_id", org_id).execute()
            all_records = res.data or []
            active = sorted({r["connector_type"] for r in all_records if r.get("status") == "connected"})
            inactive = sorted({r["connector_type"] for r in all_records if r.get("status") != "connected"})

            lines = ["## Estado actual de conectores (verificado en tiempo real)"]
            if active:
                lines.append(f"ACTIVOS (puedes usar sus herramientas ahora mismo): {', '.join(active)}")
            else:
                lines.append("Ningún conector activo.")
            if inactive:
                lines.append(f"NO conectados: {', '.join(inactive)}")
            lines.append(
                "INSTRUCCIONES para conectar si el usuario lo pide:\n"
                "- Google (Gmail/Drive/Docs/Sheets/Calendar): usa get_google_auth_url\n"
                "- NotebookLM: dile al usuario que vaya a panel Conectores → NotebookLM → "
                "'Iniciar sesión en NotebookLM' (requiere login interactivo, NO tiene API key)\n"
                "- Otros (Telegram, Slack, etc.): usa save_connector con sus credenciales"
            )
            return "\n".join(lines)
        except Exception:
            return ""

    def _get_recent_messages(self, stream_id: str, org_id: str, current_message: str, limit: int = 20) -> list:
        """Recupera historial reciente del stream para dar contexto conversacional al modelo."""
        try:
            res = (
                self.supabase
                .table("messages")
                .select("role, content")
                .eq("stream_id", stream_id)
                .eq("org_id", org_id)
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
            )
            history = list(reversed(res.data or []))
            messages = []
            for m in history:
                content = m.get("content", {})
                # Normalizar content a texto plano para el historial
                if isinstance(content, dict):
                    text = content.get("text", "")
                    # Incluir adjuntos como contexto legible
                    attachments = content.get("attachments", [])
                    if attachments:
                        lines = [text] if text else []
                        for att in attachments:
                            name = att.get("name", "archivo")
                            url = att.get("url", "")
                            ftype = att.get("type", "")
                            if ftype.startswith("image/"):
                                lines.append(f"[Imagen adjunta: {name}] {url}")
                            else:
                                lines.append(f"[Documento adjunto: {name}] {url}")
                        text = "\n".join(lines)
                elif isinstance(content, str):
                    text = content
                else:
                    text = ""
                role = m["role"]
                if text and role in ("user", "assistant"):
                    messages.append({"role": role, "content": text})

            # Evitar duplicar el mensaje actual si ya está en el historial
            if messages and messages[-1]["role"] == "user" and messages[-1]["content"] == current_message:
                return messages

            messages.append({"role": "user", "content": current_message})
            return messages
        except Exception as e:
            log.warning(f"[prompt_agent] Could not load history: {e}")
            return [{"role": "user", "content": current_message}]

    def _call_claude(self, model_id, system_prompt, messages, tools, temperature, max_tokens) -> dict:
        """Llama a Claude via Anthropic API. Fallback a OpenRouter si no hay API key."""
        if ANTHROPIC_API_KEY:
            return self._call_anthropic(model_id, system_prompt, messages, tools, temperature, max_tokens)
        elif OPENROUTER_API_KEY:
            return self._call_openrouter(model_id, system_prompt, messages, tools, temperature, max_tokens)
        else:
            raise ValueError("No AI provider configured. Set ANTHROPIC_API_KEY or OPENROUTER_API_KEY.")

    def _call_anthropic(self, model_id, system_prompt, messages, tools, temperature, max_tokens) -> dict:
        # Normalizar model_id (quitar prefix vendor si viene de otro formato)
        if "/" in model_id:
            model_id = model_id.split("/")[-1]

        headers = {
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": model_id,
            "system": system_prompt,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools

        resp = httpx.post(f"{ANTHROPIC_BASE}/messages", headers=headers, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        log.info(f"[anthropic] stop_reason={data.get('stop_reason')} content_blocks={len(data.get('content',[]))} usage={data.get('usage')}")
        if not data.get("content"):
            log.warning(f"[anthropic] Empty content! Full response: {data}")

        text = ""
        tool_calls = []
        for block in data.get("content", []):
            if block.get("type") == "text":
                text += block.get("text", "")
            elif block.get("type") == "tool_use":
                tool_calls.append({
                    "id": block.get("id"),
                    "function": {
                        "name": block.get("name"),
                        "arguments": json.dumps(block.get("input", {})),
                    }
                })

        return {"content": text, "tool_calls": tool_calls}

    def _call_openrouter(self, model_id, system_prompt, messages, tools, temperature, max_tokens) -> dict:
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://genie.ai",
            "X-Title": "Genie",
        }
        payload = {
            "model": model_id,
            "messages": [{"role": "system", "content": system_prompt}] + messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools

        resp = httpx.post(f"{OPENROUTER_BASE}/chat/completions", headers=headers, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        choice = data["choices"][0]["message"]
        return {
            "content": choice.get("content", ""),
            "tool_calls": choice.get("tool_calls", []),
        }

    def _write_to_stream(self, org_id: str, stream_id: str, text: str, job: dict):
        """Escribe la respuesta del agente como mensaje en el stream (visible en Workstation)."""
        try:
            self.supabase.table("messages").insert({
                "org_id": org_id,
                "stream_id": stream_id,
                "role": "assistant",
                "content": {"type": "text", "text": text},
                "metadata": {
                    "agent_id": job.get("agent_id"),
                    "job_id": job.get("id"),
                    "model": job.get("agent_config", {}).get("model_id"),
                },
            }).execute()
            log.info(f"[prompt_agent] Response written to stream {stream_id}")
        except Exception as e:
            log.error(f"[prompt_agent] Failed to write response to stream: {e}")

    # ── Definición de herramientas por conector ───────────────────────────────
    CONNECTOR_TOOLS = {
        "gmail": [
            {"name": "gmail_list_emails", "description": "Lista emails recientes del inbox de Gmail.",
             "input_schema": {"type": "object", "properties": {
                 "max_results": {"type": "integer"}, "label": {"type": "string"}}, "required": []}},
            {"name": "gmail_search_emails", "description": "Busca emails en Gmail. Usa from:, to:, subject:, etc.",
             "input_schema": {"type": "object", "properties": {
                 "query": {"type": "string", "description": "Query de búsqueda Gmail"},
                 "max_results": {"type": "integer"}}, "required": ["query"]}},
            {"name": "gmail_get_email", "description": "Lee el contenido completo de un email por su ID.",
             "input_schema": {"type": "object", "properties": {
                 "message_id": {"type": "string"}}, "required": ["message_id"]}},
            {"name": "gmail_send_email", "description": "Envía un email desde Gmail.",
             "input_schema": {"type": "object", "properties": {
                 "to": {"type": "string"}, "subject": {"type": "string"},
                 "body": {"type": "string"}, "html": {"type": "boolean"}}, "required": ["to", "subject", "body"]}},
            {"name": "gmail_create_draft", "description": "Crea un borrador en Gmail sin enviarlo.",
             "input_schema": {"type": "object", "properties": {
                 "to": {"type": "string"}, "subject": {"type": "string"},
                 "body": {"type": "string"}}, "required": ["to", "subject", "body"]}},
        ],
        "sheets": [
            {"name": "sheets_get_spreadsheet_info", "description": "Obtiene info y lista de hojas de un Google Spreadsheet.",
             "input_schema": {"type": "object", "properties": {
                 "spreadsheet_id": {"type": "string"}}, "required": ["spreadsheet_id"]}},
            {"name": "sheets_read_range", "description": "Lee un rango de celdas de Google Sheets. range ej: 'Sheet1!A1:D10'",
             "input_schema": {"type": "object", "properties": {
                 "spreadsheet_id": {"type": "string"}, "range": {"type": "string"}}, "required": ["spreadsheet_id", "range"]}},
            {"name": "sheets_write_range", "description": "Escribe valores en un rango de Google Sheets.",
             "input_schema": {"type": "object", "properties": {
                 "spreadsheet_id": {"type": "string"}, "range": {"type": "string"},
                 "values": {"type": "array", "description": "Lista de listas con los valores"}}, "required": ["spreadsheet_id", "range", "values"]}},
            {"name": "sheets_append_rows", "description": "Agrega filas al final de un rango en Google Sheets.",
             "input_schema": {"type": "object", "properties": {
                 "spreadsheet_id": {"type": "string"}, "range": {"type": "string"},
                 "values": {"type": "array"}}, "required": ["spreadsheet_id", "range", "values"]}},
            {"name": "sheets_create_spreadsheet", "description": "Crea un nuevo Google Spreadsheet.",
             "input_schema": {"type": "object", "properties": {
                 "title": {"type": "string"}}, "required": ["title"]}},
        ],
        "docs": [
            {"name": "docs_get_document", "description": (
                "Lee el contenido de un Google Doc. "
                "Tras obtener el resultado, responde SIEMPRE con un bloque widget seguido de un resumen breve:\n"
                "```genie-widget\n"
                "{\"type\":\"document\",\"title\":\"<título del doc>\",\"data\":{\"url\":\"https://docs.google.com/document/d/<document_id>/edit\",\"type\":\"google_doc\",\"preview\":\"<primeras 300 chars del contenido>\"}}\n"
                "```\n"
                "Luego agrega el resumen o análisis del contenido."
             ),
             "input_schema": {"type": "object", "properties": {
                 "document_id": {"type": "string"}}, "required": ["document_id"]}},
            {"name": "docs_create_document", "description": (
                "Crea un nuevo Google Doc. "
                "Tras crearlo, muestra el widget:\n"
                "```genie-widget\n"
                "{\"type\":\"document\",\"title\":\"<título>\",\"data\":{\"url\":\"https://docs.google.com/document/d/<document_id>/edit\",\"type\":\"google_doc\"}}\n"
                "```"
             ),
             "input_schema": {"type": "object", "properties": {
                 "title": {"type": "string"}}, "required": ["title"]}},
            {"name": "docs_append_text", "description": "Agrega texto al final de un Google Doc.",
             "input_schema": {"type": "object", "properties": {
                 "document_id": {"type": "string"}, "text": {"type": "string"}}, "required": ["document_id", "text"]}},
            {"name": "docs_replace_text", "description": "Reemplaza texto en un Google Doc.",
             "input_schema": {"type": "object", "properties": {
                 "document_id": {"type": "string"}, "old_text": {"type": "string"},
                 "new_text": {"type": "string"}}, "required": ["document_id", "old_text", "new_text"]}},
        ],
        "drive": [
            {"name": "drive_list_files", "description": (
                "Lista archivos en Google Drive. "
                "Tras obtener el resultado, responde SIEMPRE con un bloque de widget en este formato exacto:\n"
                "```genie-widget\n"
                "{\"type\":\"drive_files\",\"title\":\"Google Drive\",\"data\":{\"files\":<array resultado>,\"query\":\"\"}}\n"
                "```\n"
                "Luego puedes agregar texto explicativo debajo del bloque."
             ),
             "input_schema": {"type": "object", "properties": {
                 "query": {"type": "string", "description": "Filtro ej: name contains 'reporte'"},
                 "max_results": {"type": "integer"}}, "required": []}},
            {"name": "drive_search_files", "description": (
                "Busca archivos en Drive por nombre. "
                "Tras obtener el resultado, responde SIEMPRE con un bloque de widget en este formato exacto:\n"
                "```genie-widget\n"
                "{\"type\":\"drive_files\",\"title\":\"Resultados en Drive\",\"data\":{\"files\":<array resultado>,\"query\":\"<nombre buscado>\"}}\n"
                "```\n"
                "Luego puedes agregar texto explicativo debajo del bloque."
             ),
             "input_schema": {"type": "object", "properties": {
                 "name": {"type": "string"}}, "required": ["name"]}},
            {"name": "drive_get_file_info", "description": "Obtiene metadata de un archivo en Drive.",
             "input_schema": {"type": "object", "properties": {
                 "file_id": {"type": "string"}}, "required": ["file_id"]}},
            {"name": "drive_copy_file", "description": "Crea una copia de un archivo o documento en Google Drive.",
             "input_schema": {"type": "object", "properties": {
                 "file_id": {"type": "string", "description": "ID del archivo a copiar"},
                 "new_name": {"type": "string", "description": "Nombre para la copia (opcional)"},
                 "parent_id": {"type": "string", "description": "ID de carpeta destino (opcional)"}}, "required": ["file_id"]}},
            {"name": "drive_create_folder", "description": "Crea una carpeta en Google Drive.",
             "input_schema": {"type": "object", "properties": {
                 "name": {"type": "string"}, "parent_id": {"type": "string"}}, "required": ["name"]}},
            {"name": "drive_share_file", "description": "Comparte un archivo de Drive con un email.",
             "input_schema": {"type": "object", "properties": {
                 "file_id": {"type": "string"}, "email": {"type": "string"},
                 "role": {"type": "string", "description": "reader, writer o commenter"}}, "required": ["file_id", "email"]}},
        ],
        "slides": [
            {"name": "slides_get_presentation", "description": "Lee el contenido de una presentación de Google Slides.",
             "input_schema": {"type": "object", "properties": {
                 "presentation_id": {"type": "string"}}, "required": ["presentation_id"]}},
            {"name": "slides_create_presentation", "description": "Crea una nueva presentación en Google Slides.",
             "input_schema": {"type": "object", "properties": {
                 "title": {"type": "string"}}, "required": ["title"]}},
            {"name": "slides_replace_text", "description": "Reemplaza texto en una presentación de Slides.",
             "input_schema": {"type": "object", "properties": {
                 "presentation_id": {"type": "string"}, "old_text": {"type": "string"},
                 "new_text": {"type": "string"}}, "required": ["presentation_id", "old_text", "new_text"]}},
        ],
        "calendar": [
            {"name": "calendar_list_events", "description": (
                "Lista eventos del Google Calendar del usuario. "
                "Usa time_min y time_max en formato RFC3339 (ej: '2026-06-19T00:00:00-06:00') para filtrar por rango. "
                "Incluye Google Meet links cuando el evento tiene videoconferencia."
             ),
             "input_schema": {"type": "object", "properties": {
                 "calendar_id": {"type": "string", "description": "ID del calendario, 'primary' para el principal"},
                 "max_results": {"type": "integer"},
                 "time_min": {"type": "string", "description": "Inicio del rango RFC3339"},
                 "time_max": {"type": "string", "description": "Fin del rango RFC3339"},
             }, "required": []}},
            {"name": "calendar_get_event", "description": "Obtiene detalle completo de un evento del calendario.",
             "input_schema": {"type": "object", "properties": {
                 "event_id": {"type": "string"},
                 "calendar_id": {"type": "string"},
             }, "required": ["event_id"]}},
            {"name": "calendar_create_event", "description": (
                "Crea un evento en Google Calendar. Si add_meet=true genera un link de Google Meet automáticamente. "
                "start_datetime y end_datetime en formato RFC3339 (ej: '2026-06-20T10:00:00-06:00')."
             ),
             "input_schema": {"type": "object", "properties": {
                 "summary": {"type": "string", "description": "Título del evento"},
                 "start_datetime": {"type": "string"},
                 "end_datetime": {"type": "string"},
                 "description": {"type": "string"},
                 "attendees": {"type": "array", "items": {"type": "string"}, "description": "Lista de emails"},
                 "location": {"type": "string"},
                 "add_meet": {"type": "boolean", "description": "true para agregar Google Meet"},
                 "calendar_id": {"type": "string"},
             }, "required": ["summary", "start_datetime", "end_datetime"]}},
            {"name": "calendar_delete_event", "description": "Elimina un evento del Google Calendar.",
             "input_schema": {"type": "object", "properties": {
                 "event_id": {"type": "string"},
                 "calendar_id": {"type": "string"},
             }, "required": ["event_id"]}},
            {"name": "calendar_list_calendars", "description": "Lista todos los calendarios disponibles en la cuenta.",
             "input_schema": {"type": "object", "properties": {}, "required": []}},
        ],
        "notebooklm": [
            {"name": "notebooklm_listar", "description": (
                "Lista todos los notebooks del usuario en NotebookLM. "
                "Tras obtener el resultado, responde SIEMPRE con un bloque widget:\n"
                "```genie-widget\n"
                "{\"type\":\"list\",\"title\":\"Notebooks en NotebookLM\","
                "\"data\":[{\"title\":\"<titulo del notebook>\",\"id\":\"<id>\"}]}\n"
                "```\n"
                "Usa el campo 'title' de cada notebook para el texto visible. "
                "NO agregues acciones al widget."
             ),
             "input_schema": {"type": "object", "properties": {}, "required": []}},
            {"name": "notebooklm_crear", "description": (
                "Crea un nuevo notebook en NotebookLM. Puedes agregar fuentes iniciales (URLs o texto). "
                "Retorna el ID y URL del notebook creado. "
                "OBLIGATORIO: Si el usuario pidió generar contenido (infografía, audio, podcast, presentación, quiz, resumen), "
                "DEBES llamar notebooklm_generar como siguiente paso inmediato usando el notebook_id retornado. "
                "NO termines el turno sin llamar notebooklm_generar si el usuario pidió generación."
             ),
             "input_schema": {"type": "object", "properties": {
                 "titulo": {"type": "string", "description": "Título del notebook"},
                 "fuentes": {"type": "array", "items": {"type": "string"},
                             "description": "URLs o textos a agregar como fuentes (opcional)"}
             }, "required": ["titulo"]}},
            {"name": "notebooklm_agregar_fuente", "description": (
                "Agrega una fuente (URL o texto) a un notebook existente. "
                "OBLIGATORIO: Si el usuario pidió generar contenido después de agregar la fuente, "
                "DEBES llamar notebooklm_generar como siguiente paso inmediato. "
                "NO termines el turno sin llamar notebooklm_generar si el usuario pidió generación."
             ),
             "input_schema": {"type": "object", "properties": {
                 "notebook_id": {"type": "string"},
                 "fuente": {"type": "string", "description": "URL o texto a agregar"}
             }, "required": ["notebook_id", "fuente"]}},
            {"name": "notebooklm_chatear", "description": "Hace una pregunta a un notebook de NotebookLM y retorna la respuesta con citas de las fuentes.",
             "input_schema": {"type": "object", "properties": {
                 "notebook_id": {"type": "string"},
                 "pregunta": {"type": "string"}
             }, "required": ["notebook_id", "pregunta"]}},
            {"name": "notebooklm_generar", "description": (
                "Genera contenido multimedia a partir de un notebook: podcast de audio, infografía, presentación (slide_deck), "
                "resumen (summary) o quiz. La generación corre en background y tarda 1-3 minutos. "
                "Después de llamar este tool, responde INMEDIATAMENTE al usuario con el action_result widget "
                "mostrando que el notebook está listo y que la generación está en progreso — "
                "NO esperes a que termine. El sistema enviará otro mensaje automáticamente cuando esté lista."
             ),
             "input_schema": {"type": "object", "properties": {
                 "notebook_id": {"type": "string"},
                 "tipo": {"type": "string",
                          "description": "Tipo de contenido: audio, podcast, infographic, infografia, slides, presentacion, summary, resumen, quiz"}
             }, "required": ["notebook_id", "tipo"]}},
        ],
        "telegram": [
            {"name": "telegram_send_message", "description": "Envía un mensaje por Telegram.",
             "input_schema": {"type": "object", "properties": {
                 "chat_id": {"type": "string"}, "text": {"type": "string"}}, "required": ["chat_id", "text"]}},
            {"name": "telegram_get_updates", "description": "Obtiene mensajes recientes recibidos por el bot de Telegram.",
             "input_schema": {"type": "object", "properties": {
                 "limit": {"type": "integer"}}, "required": []}},
            {"name": "telegram_get_bot_info", "description": "Obtiene información del bot de Telegram.",
             "input_schema": {"type": "object", "properties": {}, "required": []}},
        ],
        "slack": [
            {"name": "slack_send_message", "description": "Envía un mensaje a un canal o usuario de Slack.",
             "input_schema": {"type": "object", "properties": {
                 "channel": {"type": "string", "description": "ID o nombre del canal"},
                 "text": {"type": "string"}}, "required": ["channel", "text"]}},
            {"name": "slack_list_channels", "description": "Lista los canales del workspace de Slack.",
             "input_schema": {"type": "object", "properties": {}, "required": []}},
            {"name": "slack_get_channel_history", "description": "Obtiene mensajes recientes de un canal de Slack.",
             "input_schema": {"type": "object", "properties": {
                 "channel": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["channel"]}},
        ],
        "stripe": [
            {"name": "stripe_get_balance", "description": "Obtiene el balance actual de la cuenta Stripe.",
             "input_schema": {"type": "object", "properties": {}, "required": []}},
            {"name": "stripe_list_customers", "description": "Lista clientes de Stripe.",
             "input_schema": {"type": "object", "properties": {
                 "limit": {"type": "integer"}, "email": {"type": "string"}}, "required": []}},
            {"name": "stripe_list_payments", "description": "Lista pagos recientes de Stripe.",
             "input_schema": {"type": "object", "properties": {
                 "limit": {"type": "integer"}}, "required": []}},
        ],
        "hubspot": [
            {"name": "hubspot_list_contacts", "description": "Lista contactos de HubSpot CRM.",
             "input_schema": {"type": "object", "properties": {
                 "limit": {"type": "integer"}}, "required": []}},
            {"name": "hubspot_search_contacts", "description": "Busca contactos en HubSpot por email.",
             "input_schema": {"type": "object", "properties": {
                 "email": {"type": "string"}}, "required": ["email"]}},
            {"name": "hubspot_create_contact", "description": "Crea un nuevo contacto en HubSpot.",
             "input_schema": {"type": "object", "properties": {
                 "email": {"type": "string"}, "firstname": {"type": "string"},
                 "lastname": {"type": "string"}, "company": {"type": "string"},
                 "phone": {"type": "string"}}, "required": ["email"]}},
            {"name": "hubspot_list_deals", "description": "Lista deals/oportunidades en HubSpot.",
             "input_schema": {"type": "object", "properties": {
                 "limit": {"type": "integer"}}, "required": []}},
            {"name": "hubspot_sync", "description": "Sincroniza contactos, empresas y deals de HubSpot con el CRM de Genie.",
             "input_schema": {"type": "object", "properties": {}, "required": []}},
        ],
        "pipedrive": [
            {"name": "pipedrive_list_contacts", "description": "Lista personas/contactos de Pipedrive.",
             "input_schema": {"type": "object", "properties": {
                 "limit": {"type": "integer"}}, "required": []}},
            {"name": "pipedrive_list_deals", "description": "Lista deals/oportunidades de Pipedrive.",
             "input_schema": {"type": "object", "properties": {
                 "limit": {"type": "integer"}}, "required": []}},
            {"name": "pipedrive_list_products", "description": "Lista productos de Pipedrive.",
             "input_schema": {"type": "object", "properties": {
                 "limit": {"type": "integer"}}, "required": []}},
            {"name": "pipedrive_sync", "description": "Sincroniza personas, empresas, deals y productos de Pipedrive con Genie.",
             "input_schema": {"type": "object", "properties": {}, "required": []}},
        ],
        "odoo": [
            {"name": "odoo_list_partners", "description": "Lista clientes/proveedores de Odoo.",
             "input_schema": {"type": "object", "properties": {
                 "limit": {"type": "integer"}}, "required": []}},
            {"name": "odoo_list_products", "description": "Lista productos de Odoo.",
             "input_schema": {"type": "object", "properties": {
                 "limit": {"type": "integer"}}, "required": []}},
            {"name": "odoo_list_invoices", "description": "Lista facturas de cliente de Odoo.",
             "input_schema": {"type": "object", "properties": {
                 "limit": {"type": "integer"}}, "required": []}},
            {"name": "odoo_sync", "description": "Sincroniza partners, productos y facturas de Odoo con Genie.",
             "input_schema": {"type": "object", "properties": {}, "required": []}},
        ],
    }

    def _build_tools(self, org_id: str, tools_config: list) -> list:
        """Herramientas disponibles para Claude — dinámicas según conectores activos del org."""
        # Herramientas siempre disponibles
        tools = [
            {
                "name": "save_connector",
                "description": (
                    "Guarda las credenciales de un conector externo en la base de datos de la organización. "
                    "Úsalo cuando el usuario te proporcione las API keys o credenciales de un servicio "
                    "(Telegram, WhatsApp, Slack, Stripe, HubSpot, OpenAI, etc.) y quiera integrarlo. "
                    "IMPORTANTE: Para Gmail, Drive, Sheets, Docs, Slides, Calendar de Google, NO uses save_connector — "
                    "usa get_google_auth_url en su lugar, ya que requieren autenticación OAuth. "
                    "IMPORTANTE: Para NotebookLM, NO uses save_connector — NotebookLM requiere login "
                    "interactivo con Google. Dile al usuario que vaya a Conectores → NotebookLM → "
                    "'Iniciar sesión en NotebookLM' y que siga el flujo del navegador."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "connector_type": {
                            "type": "string",
                            "description": (
                                "Identificador del conector. Uno de: "
                                "telegram, whatsapp, slack, openai, stripe, hubspot, pipedrive, odoo"
                            ),
                        },
                        "credentials": {
                            "type": "object",
                            "description": "Objeto con las credenciales del conector. Ej: {\"bot_token\": \"...\"}",
                        },
                    },
                    "required": ["connector_type", "credentials"],
                },
            },
            {
                "name": "get_google_auth_url",
                "description": (
                    "Genera un link de autorización OAuth de Google. Úsalo cuando el usuario quiera conectar "
                    "Gmail, Google Drive, Google Sheets, Google Docs o Google Slides. "
                    "Devuelve una URL que el usuario debe abrir en su navegador para autorizar el acceso. "
                    "Con un solo clic se conectan TODOS los servicios de Google Workspace a la vez."
                ),
                "input_schema": {"type": "object", "properties": {}, "required": []},
            },
        ]

        # Agregar herramientas de conectores activos dinámicamente
        try:
            db_res = self.supabase.table("connectors").select("connector_type").eq("org_id", org_id).eq("status", "connected").execute()
            active = {r["connector_type"] for r in (db_res.data or [])}
            for connector_type, connector_tools in self.CONNECTOR_TOOLS.items():
                if connector_type in active:
                    tools.extend(connector_tools)
            log.info(f"[prompt_agent] Active connectors for org {org_id}: {active}")
        except Exception as e:
            log.warning(f"[prompt_agent] Could not load active connectors: {e}")

        return tools

    def _execute_tool_calls(self, org_id, job, tool_calls, messages, model_id,
                             system_prompt, temperature, max_tokens, autonomy) -> dict:
        tools_made = []

        # Construir bloques tool_use (formato Anthropic)
        tool_use_blocks = []
        tool_result_contents = []

        for tc in tool_calls:
            tool_name = tc["function"]["name"]
            tool_args = json.loads(tc["function"].get("arguments", "{}"))
            log.info(f"[prompt_agent] Tool call: {tool_name}({tool_args})")
            result = self._execute_tool(org_id, tool_name, tool_args)

            tool_use_blocks.append({
                "type": "tool_use",
                "id": tc["id"],
                "name": tool_name,
                "input": tool_args,
            })
            tool_result_contents.append({
                "type": "tool_result",
                "tool_use_id": tc["id"],
                "content": json.dumps(result),
            })
            tools_made.append({"tool": tool_name, "args": tool_args, "result": result})

        # Formato Anthropic: assistant con tool_use, luego user con tool_result
        messages_with_results = messages + [
            {"role": "assistant", "content": tool_use_blocks},
            {"role": "user", "content": tool_result_contents},
        ]

        final = self._call_claude(
            model_id=model_id,
            system_prompt=system_prompt,
            messages=messages_with_results,
            tools=[],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        log.info(f"[prompt_agent] Second Claude response content length: {len(final.get('content',''))}, preview: {final.get('content','')[:200]!r}")
        final["tool_calls_made"] = tools_made
        final["_messages_with_results"] = messages_with_results
        return final

    def _execute_tool(self, org_id: str, tool_name: str, args: dict) -> Any:
        # ── Herramienta: guardar conector desde el chat ──────────────────────
        if tool_name == "save_connector":
            return self._save_connector(org_id, args)

        # ── Herramienta: OAuth de Google ─────────────────────────────────────
        if tool_name == "get_google_auth_url":
            return self._get_google_auth_url(org_id)

        # ── Herramientas de conectores: formato {connector}_{action} ─────────
        CONNECTOR_PREFIXES = ["gmail", "sheets", "docs", "drive", "slides", "calendar",
                               "notebooklm", "telegram", "slack", "stripe", "hubspot", "whatsapp",
                               "pipedrive", "odoo"]
        for prefix in CONNECTOR_PREFIXES:
            if tool_name.startswith(f"{prefix}_"):
                action = tool_name[len(prefix) + 1:]
                try:
                    from core.connectors.executor import execute_connector_action
                    return execute_connector_action(
                        org_id, prefix, action, args,
                        stream_id=self._current_stream_id,
                        job_id=self._current_job_id,
                    )
                except Exception as e:
                    log.error(f"[prompt_agent] {tool_name} failed: {e}")
                    return {"error": str(e)}

        # ── Herramientas de conectores: formato connector_action ─────────────
        parts = tool_name.split("_", 1)
        if len(parts) < 2:
            return {"error": f"Invalid tool name: {tool_name}"}
        connector_type, action = parts[0], parts[1]
        try:
            from core.connectors.executor import execute_connector_action
            return execute_connector_action(org_id, connector_type, action, args)
        except Exception as e:
            log.error(f"[prompt_agent] Tool {tool_name} failed: {e}")
            return {"error": str(e)}

    def _get_google_auth_url(self, org_id: str) -> dict:
        """Genera la URL de OAuth de Google para conectar todos los servicios de Workspace."""
        import urllib.parse
        client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
        redirect_uri = os.environ.get("GOOGLE_REDIRECT_URI", "http://localhost:8000/api/connectors/google/callback")
        scopes = [
            "https://www.googleapis.com/auth/gmail.modify",
            "https://www.googleapis.com/auth/drive",
            "https://www.googleapis.com/auth/documents",
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/presentations",
            "openid", "email", "profile",
        ]
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(scopes),
            "access_type": "offline",
            "prompt": "consent",
            "state": org_id,
        }
        url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)
        return {
            "auth_url": url,
            "message": "Abre este link para autorizar el acceso a Google Workspace (Gmail, Drive, Sheets, Docs y Slides se conectan de una vez).",
        }

    def _save_connector(self, org_id: str, args: dict) -> dict:
        """Guarda credenciales de un conector en Supabase."""
        connector_type = args.get("connector_type", "")
        credentials = args.get("credentials", {})
        if not connector_type:
            return {"error": "connector_type es requerido"}
        try:
            res = self.supabase.table("connectors").upsert({
                "org_id": org_id,
                "connector_type": connector_type,
                "credentials": credentials,
                "config": {},
                "status": "connected",
            }, on_conflict="org_id,connector_type").execute()
            log.info(f"[prompt_agent] Connector '{connector_type}' saved for org {org_id}")
            return {"status": "saved", "connector_type": connector_type}
        except Exception as e:
            log.error(f"[prompt_agent] save_connector failed: {e}")
            return {"error": str(e)}

    def _get_adn_system_prompt(self, stream_id: str, org_id: str) -> str:
        """Fetch combined system prompt for all ADNs invited to a stream (lead first)."""
        try:
            rows = (
                self.supabase
                .table("stream_adn")
                .select("is_lead, sort_order, adn_catalog(name, role, system_prompt)")
                .eq("stream_id", stream_id)
                .eq("org_id", org_id)
                .execute()
            )
            if not rows.data:
                return ""

            # Sort: lead first, then by sort_order
            entries = sorted(rows.data, key=lambda r: (0 if r["is_lead"] else 1, r.get("sort_order", 99)))

            parts = []
            for row in entries:
                adn = row.get("adn_catalog") or {}
                if not adn.get("system_prompt"):
                    continue
                label = "## Tu rol principal" if row["is_lead"] else f"## Perspectiva adicional: {adn['name']}"
                parts.append(f"{label} — {adn['name']} ({adn['role']})\n\n{adn['system_prompt']}")

            return "\n\n---\n\n".join(parts)
        except Exception as e:
            log.warning(f"[prompt_agent] Could not load ADN system prompt: {e}")
            return ""
