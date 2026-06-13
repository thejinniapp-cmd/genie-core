"""
agents/workers/rfq_worker.py
============================
Worker de RFQ industrial — migrado de brain-agentes a Genie multi-tenant.

Diferencia con la versión original:
- Las credenciales vienen de tenant.get_credentials(org_id, ...) en lugar de os.environ
- El agente hereda de BaseAgent (polling, logs, audit automáticos)
- Compatible con cualquier cliente que tenga 1CRM + SerpAPI configurados

Este es el ejemplo de worker "pesado" del marketplace.
"""

import os
import re
import json
import logging
from datetime import date
import httpx
import anthropic

from agents.base_agent import BaseAgent, HumanReviewRequired
from core import tenant, audit

log = logging.getLogger("genie.workers.rfq")


class RFQBuscadorWorker(BaseAgent):
    """
    Worker que busca productos en 1CRM + Google y rankea Top 5.
    Migrado de agente_buscador.py con soporte multi-tenant.
    """

    agent_id = "rfq_buscador"
    job_type = "rfq_search"

    def run(self, job: dict, org_id: str) -> dict:
        input_data = job.get("input_data", {})
        marca = input_data.get("marca", "").strip()
        modelo = input_data.get("modelo", "").strip()
        urgente = input_data.get("urgente", False)
        stream_id = job.get("stream_id")

        if not modelo:
            raise ValueError("El campo 'modelo' es requerido")

        self._add_log(job["id"], "init", f"Buscando: '{marca}' '{modelo}' | urgente={urgente}")

        # Obtener credenciales del tenant (no de os.environ)
        onecrm_creds = tenant.get_credentials(org_id, "1crm")
        serpapi_creds = tenant.get_credentials(org_id, "serpapi")
        claude_api_key = tenant.get_credentials(org_id, "anthropic").get("api_key") or os.environ["ANTHROPIC_API_KEY"]

        # Tipo de cambio
        fx = self._get_fx_usd_mxn()
        self._add_log(job["id"], "fx", f"FX USD/MXN: {fx}")

        # Búsquedas en paralelo
        resultados = []

        try:
            res_crm = self._buscar_crm_productos(marca, modelo, onecrm_creds)
            resultados.extend(res_crm)
            self._add_log(job["id"], "crm_productos", f"{len(res_crm)} resultados")
        except Exception as e:
            self._add_log(job["id"], "crm_productos_error", str(e))

        try:
            res_proveedores = self._buscar_crm_proveedores(marca, onecrm_creds)
            resultados.extend(res_proveedores)
            self._add_log(job["id"], "crm_proveedores", f"{len(res_proveedores)} resultados")
        except Exception as e:
            self._add_log(job["id"], "crm_proveedores_error", str(e))

        try:
            if serpapi_creds.get("api_key"):
                res_google = self._buscar_google(marca, modelo, serpapi_creds["api_key"])
                resultados.extend(res_google)
                self._add_log(job["id"], "google", f"{len(res_google)} resultados")
        except Exception as e:
            self._add_log(job["id"], "google_error", str(e))

        if not resultados:
            self._add_log(job["id"], "sin_resultado", "Ninguna fuente devolvió resultados")
            return {"top5": [], "fx": fx, "message": "No se encontraron resultados"}

        # Filtrar basura
        resultados_limpios = self._filtrar(resultados, modelo)

        # Claude rankea
        self._add_log(job["id"], "ranking", f"Rankeando {len(resultados_limpios)} resultados")
        top5 = self._rankear_con_claude(marca, modelo, urgente, resultados_limpios, fx, claude_api_key)

        # Si hay resultados que requieren aprobación según autonomía
        agent_config = job.get("agent_config", {})
        autonomy = agent_config.get("autonomy_level", "supervised")

        if autonomy == "supervised" and top5:
            raise HumanReviewRequired(
                f"Búsqueda completa para {marca} {modelo}. "
                f"Se encontraron {len(top5)} opciones. "
                f"¿Aprobas publicar con la opción #1 ({top5[0].get('proveedor', '?')})?",
            )

        return {"top5": top5, "fx": fx, "total_found": len(resultados)}

    def _get_fx_usd_mxn(self) -> float:
        try:
            resp = httpx.get(
                "https://api.frankfurter.dev/v1/latest",
                params={"from": "USD", "to": "MXN"},
                timeout=10,
                follow_redirects=True,
            )
            return float(resp.json()["rates"]["MXN"])
        except Exception:
            return 17.50

    def _buscar_crm_productos(self, marca: str, modelo: str, creds: dict) -> list:
        base_url = creds.get("url", "").rstrip("/")
        user = creds.get("username", "")
        pwd = creds.get("password", "")

        resp = httpx.get(
            f"{base_url}/api.php/data/Product",
            auth=(user, pwd),
            params={"filter_text": modelo, "limit": 20},
            timeout=20,
        )
        resp.raise_for_status()
        records = resp.json().get("records", [])

        resultados = []
        for r in records:
            nombre = r.get("name", "")
            if not self._coincide_modelo(modelo, nombre):
                continue
            resultados.append({
                "proveedor": "1CRM Catálogo",
                "nombre_producto": nombre,
                "precio_orig": float(r.get("price") or 0) or None,
                "moneda": "USD",
                "disponibilidad": "en_stock",
                "fuente": "1crm_productos",
                "url": f"{base_url}/index.php?module=Products&record={r.get('id')}",
                "dist_autorizado": True,
            })
        return resultados

    def _buscar_crm_proveedores(self, marca: str, creds: dict) -> list:
        base_url = creds.get("url", "").rstrip("/")
        user = creds.get("username", "")
        pwd = creds.get("password", "")

        resp = httpx.get(
            f"{base_url}/api.php/data/Account",
            auth=(user, pwd),
            params={"filters[account_type]": "Supplier", "filter_text": marca, "limit": 10},
            timeout=20,
        )
        resp.raise_for_status()
        records = resp.json().get("records", [])
        return [
            {
                "proveedor": r.get("name"),
                "nombre_producto": f"{marca} (proveedor)",
                "precio_orig": None,
                "moneda": "USD",
                "disponibilidad": "bajo_pedido",
                "fuente": "1crm_proveedores",
                "url": r.get("website", ""),
                "dist_autorizado": False,
            }
            for r in records
        ]

    def _buscar_google(self, marca: str, modelo: str, api_key: str) -> list:
        resp = httpx.get(
            "https://serpapi.com/search.json",
            params={"q": f"{marca} {modelo}".strip(), "api_key": api_key, "engine": "google", "num": 10},
            timeout=20,
        )
        resp.raise_for_status()
        items = resp.json().get("organic_results", [])
        return [
            {
                "proveedor": item.get("link", "").split("/")[2] if item.get("link") else "web",
                "nombre_producto": item.get("title", ""),
                "precio_orig": None,
                "moneda": "USD",
                "disponibilidad": "consultar",
                "fuente": "web",
                "url": item.get("link", ""),
                "notas": item.get("snippet", ""),
                "dist_autorizado": False,
            }
            for item in items
        ]

    def _coincide_modelo(self, modelo: str, texto: str) -> bool:
        norm = lambda s: re.sub(r"[\s\-\./]", "", s).lower()
        m = norm(modelo)
        return len(m) >= 5 and m in norm(texto)

    def _filtrar(self, resultados: list, modelo: str) -> list:
        norm_modelo = re.sub(r"[\s\-\./]", "", modelo).lower()
        limpios = []
        dominios_basura = ["facebook.com", "instagram.com", "youtube.com", "wikipedia.org"]
        for r in resultados:
            fuente = r.get("fuente", "")
            if fuente in ("1crm_productos", "1crm_proveedores"):
                limpios.append(r)
                continue
            url = (r.get("url") or "").lower()
            if any(d in url for d in dominios_basura):
                continue
            nombre = re.sub(r"[\s\-\./]", "", r.get("nombre_producto", "")).lower()
            if len(norm_modelo) >= 5 and norm_modelo not in nombre:
                notas = re.sub(r"[\s\-\./]", "", r.get("notas", "")).lower()
                if norm_modelo not in notas:
                    continue
            limpios.append(r)
        return limpios

    def _rankear_con_claude(
        self, marca: str, modelo: str, urgente: bool,
        resultados: list, fx: float, api_key: str,
    ) -> list:
        ponderacion = "30% precio / 70% disponibilidad" if urgente else "60% precio / 40% disponibilidad"
        client = anthropic.Anthropic(api_key=api_key)

        prompt = f"""Rankea el Top 5 de resultados para: {marca} {modelo}
Modo: {"URGENTE" if urgente else "Normal"} | Ponderación: {ponderacion} | FX: {fx}

Resultados:
{json.dumps(resultados[:20], ensure_ascii=False, indent=2)}

REGLAS:
- Solo usar resultados de la lista
- No inventar precios ni disponibilidad
- Si precio_orig es null, devolver null
- Máximo 5 resultados, mínimo los que haya con calidad

Responde SOLO con JSON array:
[{{"rank":1,"proveedor":"...","precio_orig":null,"moneda":"USD","precio_mxn":null,
"disponibilidad":"en_stock|consultar|bajo_pedido","fuente":"...","url":"...","score_ranking":0.0}}]"""

        resp = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip().replace("```json", "").replace("```", "").strip()
        try:
            return json.loads(text)
        except Exception:
            log.error(f"[rfq_worker] Could not parse Claude response: {text[:200]}")
            return []
