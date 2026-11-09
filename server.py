"""
Schlanker MCP-Server (Model Context Protocol) für OpenAI-kompatible lokale LLMs.

Dieser Server stellt lokale oder entfernte Sprachmodelle (z. B. Ollama, vLLM,
LM Studio, llama.cpp) über das offizielle Model Context Protocol (Streamable HTTP)
für KI-Agenten wie Antigravity bereit.

Externe Referenzen & Spezifikationen:
- Model Context Protocol (MCP) Spezifikation (Streamable HTTP Transport):
  https://modelcontextprotocol.io/specification/2025-03-26/basic/transports#streamable-http
- Offizielles MCP Python SDK (FastMCP):
  https://github.com/modelcontextprotocol/python-sdk
- OpenAI Chat Completions & Models API Spezifikation:
  https://platform.openai.com/docs/api-reference/chat
- Docker Host-Netzwerkauflösung (host.docker.internal):
  https://docs.docker.com/desktop/features/networking/#i-want-to-connect-from-a-container-to-a-service-on-the-host
"""

from __future__ import annotations

import inspect
import json
import os
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx
from mcp.server.fastmcp import FastMCP


# ==============================================================================
# 1. KONFIGURATION AUS UMGEBUNGSVARIABLEN (.env) LADEN
# ==============================================================================
# Warum: Alle Parameter werden über Umgebungsvariablen gesteuert (12-Factor-App-
# Prinzip: https://12factor.net/config), damit exakt dasselbe schlanke Docker-
# Image für beliebig viele LLM-Endpunkte/Container-Instanzen genutzt werden kann.
INSTANCE_NAME: str = os.getenv("INSTANCE_NAME", "local-llm").strip() or "local-llm"
RAW_ENDPOINT: str = os.getenv("LLM_ENDPOINT", "http://127.0.0.1:11434/v1").strip().rstrip("/")
DEFAULT_MODEL: str = os.getenv("LLM_MODEL", "auto").strip() or "auto"
API_KEY: str = os.getenv("LLM_API_KEY", "").strip()
TIMEOUT_SECONDS: float = float(os.getenv("LLM_TIMEOUT_SECONDS", "180"))
REWRITE_LOCALHOST: bool = os.getenv("DOCKER_REWRITE_LOCALHOST", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
MCP_INTERNAL_PORT: int = int(os.getenv("MCP_INTERNAL_PORT", "8000"))


def _resolve_docker_endpoint(endpoint: str) -> str:
    """
    Übersetzt `127.0.0.1` bzw. `localhost` innerhalb des Docker-Containers automatisch
    nach `host.docker.internal`, sofern `DOCKER_REWRITE_LOCALHOST=true` aktiv ist.

    Warum: In einem isolierten Docker-Container zeigt `127.0.0.1` auf den Container
    selbst und nicht auf den Host-Rechner, auf dem der LLM-Server (z. B. Port 11434)
    läuft. Durch diese automatische Umschreibung kann in der `.env` weiterhin
    intuitiv `http://127.0.0.1:11434/v1` verwendet werden.
    Quelle: https://docs.docker.com/desktop/features/networking/
    """
    if not REWRITE_LOCALHOST:
        return endpoint

    parsed = urlparse(endpoint)
    if parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
        # Ersetze nur den Hostnamen und behalte Schema, Port und Pfad (/v1) exakt bei.
        netloc = "host.docker.internal"
        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"
        if parsed.username:
            auth = parsed.username
            if parsed.password:
                auth = f"{auth}:{parsed.password}"
            netloc = f"{auth}@{netloc}"
        return urlunparse(parsed._replace(netloc=netloc))
    return endpoint


EFFECTIVE_ENDPOINT: str = _resolve_docker_endpoint(RAW_ENDPOINT)


# ==============================================================================
# 2. FASTMCP SERVER-INITIALISIERUNG (STREAMABLE HTTP TRANSPORT)
# ==============================================================================
# Warum: Antigravity nutzt für Remote-/Container-Server den modernen
# Streamable-HTTP-Transport unter dem Pfad `/mcp` (Spezifikation 2025-03-26).
# Wir prüfen die unterstützten Konstruktor-Parameter dynamisch per `inspect`,
# um mit allen 1.x-Versionen des offiziellen `mcp`-Pakets kompatibel zu bleiben
# und DNS-Rebinding-Blockaden bei Port-Mappings (z. B. 8090 -> 8000) zu vermeiden.
# Quelle: https://modelcontextprotocol.io/specification/2025-03-26/basic/transports#streamable-http
def _create_mcp_server() -> FastMCP:
    init_params = inspect.signature(FastMCP.__init__).parameters
    kwargs: dict[str, Any] = {
        "host": "0.0.0.0",
        "port": MCP_INTERNAL_PORT,
    }
    if "stateless_http" in init_params:
        # Stateless HTTP vermeidet Session-State-Verluste bei Container-Neustarts.
        kwargs["stateless_http"] = True
    if "transport_security" in init_params:
        try:
            from mcp.server.transport_security import TransportSecuritySettings

            kwargs["transport_security"] = TransportSecuritySettings(
                enable_dns_rebinding_protection=False
            )
        except ImportError:
            pass
    return FastMCP(INSTANCE_NAME, **kwargs)


mcp = _create_mcp_server()


# ==============================================================================
# 3. HILFSFUNKTIONEN FÜR HTTP-KOMMUNIKATION MIT DEM LLM-ENDPUNKT
# ==============================================================================
def _build_headers() -> dict[str, str]:
    """
    Erzeugt die HTTP-Header für den OpenAI-kompatiblen Endpunkt.
    Warum: Der API-Key ist laut Anforderung optional. Nur wenn `LLM_API_KEY`
    gesetzt ist, wird der `Authorization: Bearer <key>` Header nach RFC 6750
    mitgesendet.
    Quelle: https://datatracker.ietf.org/doc/html/rfc6750
    """
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"
    return headers


async def _fetch_available_models(client: httpx.AsyncClient) -> list[str]:
    """
    Ruft die Liste der verfügbaren Modelle über `GET {EFFECTIVE_ENDPOINT}/models` ab.
    Quelle: https://platform.openai.com/docs/api-reference/models/list
    """
    response = await client.get(f"{EFFECTIVE_ENDPOINT}/models", headers=_build_headers())
    response.raise_for_status()
    payload = response.json()
    models_data = payload.get("data", []) if isinstance(payload, dict) else []
    return [
        str(item.get("id"))
        for item in models_data
        if isinstance(item, dict) and item.get("id")
    ]


async def _resolve_model(client: httpx.AsyncClient, requested_model: str | None) -> str:
    """
    Bestimmt das zu nutzende Modell nach folgender Priorität:
    1. Expliziter `model`-Override im aktuellen Tool-Aufruf
    2. Konfiguriertes `LLM_MODEL` aus der `.env` (falls nicht `auto` oder leer)
    3. Automatische Erkennung des ersten verfügbaren Modells über `/v1/models`
    """
    if requested_model and requested_model.strip() and requested_model.strip().lower() != "auto":
        return requested_model.strip()

    if DEFAULT_MODEL and DEFAULT_MODEL.lower() != "auto":
        return DEFAULT_MODEL

    models = await _fetch_available_models(client)
    if not models:
        raise ValueError(
            f"Kein Modell auf '{EFFECTIVE_ENDPOINT}/models' gefunden. "
            "Bitte 'LLM_MODEL' explizit in der .env setzen oder ein Modell im LLM-Server laden."
        )
    return models[0]


# ==============================================================================
# 4. MCP TOOLS (FÜR ANTIGRAVITY BEREITGESTELLTE FUNKTIONEN)
# ==============================================================================
@mcp.tool()
async def ask_llm(
    prompt: str,
    system_prompt: str = "",
    model: str = "",
    temperature: float = 0.2,
    max_tokens: int = 0,
    json_mode: bool = False,
    json_schema: dict[str, Any] | None = None,
) -> str:
    """
    Sendet einen Prompt an den konfigurierten lokalen LLM-Endpunkt (/v1/chat/completions)
    und gibt die generierte Antwort zurück.

    Args:
        prompt: Die Benutzeranfrage oder Aufgabe an das Sprachmodell.
        system_prompt: Optionaler System-Prompt zur Vorgabe von Rolle, Kontext oder Regeln.
        model: Optionaler Modellname zum Überschreiben des Standardmodells aus der .env.
        temperature: Sampling-Temperatur (Standard: 0.2 für präzise/deterministische Antworten).
        max_tokens: Optionale Begrenzung der generierten Tokens (0 = kein Limit).
        json_mode: Wenn True, wird `response_format={"type": "json_object"}` erzwungen.
        json_schema: Optionales JSON-Schema-Objekt für Structured Outputs
                     (`response_format={"type": "json_schema", ...}`).
    """
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        target_model = await _resolve_model(client, model)

        messages: list[dict[str, str]] = []
        if system_prompt and system_prompt.strip():
            messages.append({"role": "system", "content": system_prompt.strip()})
        messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {
            "model": target_model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }

        if max_tokens and max_tokens > 0:
            payload["max_tokens"] = max_tokens

        # Unterstützung für JSON-Mode und Structured Outputs gemäß OpenAI-Spezifikation:
        # Quelle: https://platform.openai.com/docs/guides/structured-outputs
        if json_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "structured_response",
                    "strict": True,
                    "schema": json_schema,
                },
            }
        elif json_mode:
            payload["response_format"] = {"type": "json_object"}

        response = await client.post(
            f"{EFFECTIVE_ENDPOINT}/chat/completions",
            headers=_build_headers(),
            json=payload,
        )
        response.raise_for_status()
        data = response.json()

        choices = data.get("choices", [])
        if not choices:
            return json.dumps(
                {
                    "error": "Keine Antwort (choices leer) vom LLM-Endpunkt erhalten.",
                    "raw_response": data,
                },
                ensure_ascii=False,
            )

        message = choices[0].get("message", {})
        content = message.get("content", "")
        return str(content)


@mcp.tool()
async def list_models() -> str:
    """
    Fragt alle auf dem konfigurierten LLM-Endpunkt (/v1/models) verfügbaren Modelle ab
    und gibt diese zusammen mit den Instanz-Informationen als JSON-String zurück.
    """
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        models = await _fetch_available_models(client)
        resolved_default = (
            DEFAULT_MODEL
            if DEFAULT_MODEL.lower() != "auto"
            else (models[0] if models else None)
        )
        return json.dumps(
            {
                "instance_name": INSTANCE_NAME,
                "configured_endpoint": RAW_ENDPOINT,
                "effective_endpoint": EFFECTIVE_ENDPOINT,
                "configured_default_model": DEFAULT_MODEL,
                "active_default_model": resolved_default,
                "available_models": models,
            },
            indent=2,
            ensure_ascii=False,
        )


@mcp.tool()
async def get_instance_info() -> str:
    """
    Gibt die aktuellen Konfigurations- und Verbindungsmetadaten dieser MCP-Container-Instanz
    zurück. Hilfreich, wenn mehrere LLM-Endpunkte als separate Container eingebunden sind.
    """
    return json.dumps(
        {
            "instance_name": INSTANCE_NAME,
            "configured_endpoint": RAW_ENDPOINT,
            "effective_endpoint": EFFECTIVE_ENDPOINT,
            "configured_model": DEFAULT_MODEL,
            "api_key_configured": bool(API_KEY),
            "timeout_seconds": TIMEOUT_SECONDS,
        },
        indent=2,
        ensure_ascii=False,
    )


if __name__ == "__main__":
    # Startet den MCP-Server über den Streamable-HTTP-Transport (Standardpfad: /mcp).
    # Quelle: https://modelcontextprotocol.io/specification/2025-03-26/basic/transports#streamable-http
    mcp.run(transport="streamable-http")
