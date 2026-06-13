"""
agents/prompt_agent.py
======================
Agente ligero que corre 100% dentro de Genie.
No requiere Railway ni código Python externo.

El usuario define:
- system_prompt: instrucciones del agente
- model_id: qué modelo usar
- tools: qué conectores puede usar
- temperature, max_tokens

Genie lo ejecuta directamente vía API.
Cubre el 80% de los casos de uso.

Ejemplos de agentes ligeros:
- Atención a clientes
- Resumen ejecutivo diario
- Clasificación y ruteo de emails
- Seguimiento de cobranza
- Generación de contenido
- Monitoreo de KPIs con alerta
"""

import os
import json
import logging
import httpx
from typing import Any

from agents.base_agent import BaseAgent, HumanReviewRequired
from core import tenant
from core.rag.context import get_full_context

log = logging.getLogger("genie.prompt_agent")

OPENROUTER_BASE = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")


class PromptAgent(BaseAgent):
    """
    Agente que corre basado en un system prompt + tools de conectores.

    La configuración del agente viene de la tabla `agents` en Supabase:
    - system_prompt
    - model_id
    - temperature
    - max_tokens
    - tools (lista de conectores disponibles)
    - autonomy_level: "manual" | "supervised" | "autonomous"
    """

    agent_id = "prompt_agent"
    job_type = "prompt"

    def run(self, job: dict, org_id: str) -> dict:
        agent_config = job.get("agent_config", {})
        input_data = job.get("input_data", {})
        stream_id = job.get("stream_id")

        system_prompt = agent_config.get("system_prompt", "Eres un asistente útil.")
        model_id = agent_config.get("model_id") or os.environ.get("GENIE_DEFAULT_MODEL")
        temperature = agent_config.get("temperature", 0.3)
        max_tokens = agent_config.get("max_tokens", 2048)
        autonomy = agent_config.get("autonomy_level", "supervised")
        tools_config = agent_config.get("tools", [])

        # Enriquecer el system prompt con contexto RAG
        user_message = input_data.get("message", "")
        rag_context = get_full_context(org_id, user_message, stream_id)
        if rag_context:
            system_prompt = f"{system_prompt}\n\n{rag_context}"

        # Construir tools disponibles desde los conectores configurados
        tools = self._build_tools(org_id, tools_config)

        messages = input_data.get("messages") or [
            {"role": "user", "content": user_message}
        ]

        # Llamada al modelo vía OpenRouter
        response = self._call_model(
            model_id=model_id,
            system_prompt=system_prompt,
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        # Manejar tool calls si el modelo las retornó
        if response.get("tool_calls"):
            response = self._execute_tool_calls(
                org_id=org_id,
                job=job,
                tool_calls=response["tool_calls"],
                messages=messages,
                model_id=model_id,
                system_prompt=system_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                autonomy=autonomy,
            )

        # Si es supervised, verificar si necesita aprobación humana
        if autonomy == "supervised" and response.get("requires_approval"):
            raise HumanReviewRequired(response.get("approval_reason", "Requiere revisión humana"))

        return {
            "response": response.get("content", ""),
            "model_used": model_id,
            "tool_calls_made": response.get("tool_calls_made", []),
        }

    def _call_model(
        self,
        model_id: str,
        system_prompt: str,
        messages: list,
        tools: list,
        temperature: float,
        max_tokens: int,
    ) -> dict:
        """Llama al modelo vía OpenRouter."""
        headers = {
            "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
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

        resp = httpx.post(
            f"{OPENROUTER_BASE}/chat/completions",
            headers=headers,
            json=payload,
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()

        choice = data["choices"][0]
        message = choice["message"]

        return {
            "content": message.get("content", ""),
            "tool_calls": message.get("tool_calls", []),
        }

    def _build_tools(self, org_id: str, tools_config: list) -> list:
        """
        Construye la lista de tools disponibles para el agente.
        Cada tool corresponde a una acción de un conector conectado.
        """
        # TODO: cargar tools dinámicamente desde los conectores MCP configurados
        # Por ahora retorna lista vacía — se expande en siguiente iteración
        return []

    def _execute_tool_calls(
        self,
        org_id: str,
        job: dict,
        tool_calls: list,
        messages: list,
        model_id: str,
        system_prompt: str,
        temperature: float,
        max_tokens: int,
        autonomy: str,
    ) -> dict:
        """
        Ejecuta las tool calls que el modelo solicitó y hace una segunda llamada
        con los resultados para obtener la respuesta final.
        """
        tool_results = []
        tools_made = []

        for tc in tool_calls:
            tool_name = tc["function"]["name"]
            tool_args = json.loads(tc["function"].get("arguments", "{}"))

            log.info(f"[prompt_agent] Tool call: {tool_name}({tool_args})")

            # Ejecutar la tool via connector executor
            result = self._execute_tool(org_id, tool_name, tool_args)

            tool_results.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": json.dumps(result),
            })
            tools_made.append({"tool": tool_name, "args": tool_args, "result": result})

        # Segunda llamada con los resultados
        messages_with_results = messages + [
            {"role": "assistant", "tool_calls": tool_calls},
        ] + tool_results

        final = self._call_model(
            model_id=model_id,
            system_prompt=system_prompt,
            messages=messages_with_results,
            tools=[],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        final["tool_calls_made"] = tools_made
        return final

    def _execute_tool(self, org_id: str, tool_name: str, args: dict) -> Any:
        """
        Ejecuta una tool (acción de conector) para el org_id dado.
        Despacha al conector correspondiente.
        """
        # Formato del tool_name: "connector_action" ej: "gmail_send_email"
        parts = tool_name.split("_", 1)
        if len(parts) < 2:
            return {"error": f"Invalid tool name: {tool_name}"}

        connector_type = parts[0]
        action = parts[1]

        try:
            from core.connectors.executor import execute_connector_action
            return execute_connector_action(org_id, connector_type, action, args)
        except Exception as e:
            log.error(f"[prompt_agent] Tool {tool_name} failed: {e}")
            return {"error": str(e)}
