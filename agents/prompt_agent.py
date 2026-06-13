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

        system_prompt = agent_config.get("system_prompt", "Eres Genie, un asistente de operaciones inteligente.")
        model_id = agent_config.get("model_id") or os.environ.get("GENIE_DEFAULT_MODEL", "claude-sonnet-4-5")
        temperature = float(agent_config.get("temperature", 0.3))
        max_tokens = int(agent_config.get("max_tokens", 2048))
        autonomy = agent_config.get("autonomy_level", "supervised")
        tools_config = agent_config.get("tools", [])

        # Enriquecer system prompt con contexto RAG del stream
        user_message = input_data.get("message", "")
        rag_context = get_full_context(org_id, user_message, stream_id)
        if rag_context:
            system_prompt = f"{system_prompt}\n\n{rag_context}"

        # Recuperar historial reciente del stream
        messages = self._get_recent_messages(stream_id, org_id, user_message)

        tools = self._build_tools(org_id, tools_config)

        response = self._call_claude(
            model_id=model_id,
            system_prompt=system_prompt,
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
        )

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

        reply_text = response.get("content", "")

        # Escribir respuesta de vuelta al stream
        if reply_text and stream_id:
            self._write_to_stream(org_id, stream_id, reply_text, job)

        if autonomy == "supervised" and response.get("requires_approval"):
            raise HumanReviewRequired(response.get("approval_reason", "Requiere revisión humana"))

        return {
            "response": reply_text,
            "model_used": model_id,
            "tool_calls_made": response.get("tool_calls_made", []),
        }

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
                text = content.get("text", "") if isinstance(content, dict) else str(content)
                role = m["role"]
                # Solo incluir roles válidos para el modelo
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

    def _build_tools(self, org_id: str, tools_config: list) -> list:
        # TODO: cargar tools dinámicamente desde conectores registrados
        return []

    def _execute_tool_calls(self, org_id, job, tool_calls, messages, model_id,
                             system_prompt, temperature, max_tokens, autonomy) -> dict:
        tool_results = []
        tools_made = []

        for tc in tool_calls:
            tool_name = tc["function"]["name"]
            tool_args = json.loads(tc["function"].get("arguments", "{}"))
            log.info(f"[prompt_agent] Tool call: {tool_name}({tool_args})")
            result = self._execute_tool(org_id, tool_name, tool_args)
            tool_results.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": json.dumps(result),
            })
            tools_made.append({"tool": tool_name, "args": tool_args, "result": result})

        messages_with_results = messages + [
            {"role": "assistant", "tool_calls": tool_calls},
        ] + tool_results

        final = self._call_claude(
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
