# Local LLM MCP Server (Docker)

Ein extrem schlanker [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) Server als Docker-Container, der beliebige **OpenAI-kompatible lokale oder entfernte LLM-Endpunkte** (z. B. [Ollama](https://ollama.com/), [llama.cpp](https://github.com/ggml-org/llama.cpp), [vLLM](https://docs.vllm.ai/), [LM Studio](https://lmstudio.ai/)) über den modernen **Streamable HTTP Transport** (`/mcp`) für KI-Agenten (wie Antigravity, Claude Desktop, Cursor etc.) bereitstellt.

---

## ✨ Features

- **Minimaler Footprint**: Basiert auf `python:3.12-slim` und benötigt lediglich zwei Pakete (`mcp` und `httpx`) in einer einzigen Datei (`server.py`).
- **Streamable HTTP Transport (`2025-03-26`)**: Läuft dauerhaft im Hintergrund (`docker compose up -d`) und stellt den standardisierten `/mcp`-Endpunkt ohne Container-Kaltstart-Latenz bereit.
- **Multi-Instanz-fähig**: Über `INSTANCE_NAME` und `MCP_PORT` lassen sich mit demselben Image beliebig viele verschiedene LLM-Server parallel als eigene Container starten.
- **Automatische Docker-Host-Auflösung**: Lokale Endpunkte wie `http://127.0.0.1:11434/v1` werden innerhalb des Containers automatisch auf `host.docker.internal` umgeschrieben.
- **Automatische Modellerkennung (`LLM_MODEL=auto`)**: Erkennt auf Wunsch selbstständig das auf dem Endpunkt geladene Modell über `/v1/models` oder nutzt ein fest konfiguriertes Modell (inkl. optionalem Override pro Tool-Aufruf).
- **JSON-Mode & Structured Outputs**: Unterstützt sowohl `response_format: {"type": "json_object"}` als auch strikte JSON-Schemata (`json_schema`).
- **Kostenschutz (On-Demand)**: Beim Containerstart finden keinerlei automatische LLM-Abfragen statt; Requests erfolgen ausschließlich bei explizitem Tool-Aufruf.

---

## 📁 Projektstruktur

```text
.
├── .dockerignore        # Schließt .env und Git-Metadaten vom Docker-Build aus
├── .env.example         # Dokumentierte Vorlage für Umgebungsvariablen (ohne Secrets)
├── .gitignore           # Schützt .env und lokale Schlüssel vor Git-Commits
├── Dockerfile           # Schlankes Python 3.12-slim Image (non-root User)
├── README.md            # Projektdokumentation
├── docker-compose.yml   # Dynamische Compose-Konfiguration (Multi-Instanz-fähig)
└── server.py            # FastMCP Streamable-HTTP Server & OpenAI-Client
```

---

## 🚀 Schnellstart

### 1. Konfiguration anlegen

Kopiere die Vorlage `.env.example` nach `.env` und passe die Parameter bei Bedarf an:

```bash
cp .env.example .env
```

Wichtige Parameter in der `.env`:

| Variable | Standardwert | Beschreibung |
| :--- | :--- | :--- |
| `INSTANCE_NAME` | `local-llm` | Name des Compose-Projekts, des Containers und der MCP-Server-Instanz |
| `MCP_PORT` | `8090` | Lokaler Host-Port (`127.0.0.1:<MCP_PORT>`), unter dem `/mcp` erreichbar ist |
| `LLM_ENDPOINT` | `http://127.0.0.1:11434/v1` | OpenAI-kompatibler Endpunkt (`/v1/chat/completions` & `/v1/models`) |
| `LLM_MODEL` | `auto` | Standardmodell (`auto` wählt automatisch das erste verfügbare Modell) |
| `LLM_API_KEY` | *(leer)* | Optionaler Bearer API-Key (`Authorization: Bearer <LLM_API_KEY>`) |
| `LLM_TIMEOUT_SECONDS` | `180` | Timeout in Sekunden für lange Reasoning-/Code-Antworten oder Cold-Starts |
| `DOCKER_REWRITE_LOCALHOST` | `true` | Schreibt `127.0.0.1` / `localhost` im Container automatisch auf `host.docker.internal` um |

### 2. Container bauen und starten

```bash
docker compose up -d --build
```

Der MCP-Server ist anschließend unter folgender URL erreichbar:

```text
http://127.0.0.1:8090/mcp
```

### 3. Einbindung in Antigravity (`mcp_config.json`)

Trage den Server in deiner `~/.gemini/config/mcp_config.json` ein:

```json
{
  "mcpServers": {
    "local-llm": {
      "url": "http://127.0.0.1:8090/mcp"
    }
  }
}
```

---

## 🔄 Mehrere LLM-Endpunkte gleichzeitig betreiben (Multi-Instanz)

Da `docker-compose.yml` den Projektnamen (`name: ${INSTANCE_NAME}`) und Containernamen dynamisch aus der jeweiligen `.env`-Datei bezieht, kannst du beliebig viele LLM-Endpunkte parallel als eigene Container starten:

1. Zweite Konfigurationsdatei erstellen (z. B. `.env.workstation`):
   ```env
   INSTANCE_NAME=llm-workstation
   MCP_PORT=8091
   LLM_ENDPOINT=http://192.168.1.100:11434/v1
   LLM_MODEL=auto
   LLM_API_KEY=optional-api-key
   ```
2. Zweiten Container starten:
   ```bash
   docker compose --env-file .env.workstation up -d
   ```
3. In `mcp_config.json` registrieren:
   ```json
   "llm-workstation": {
     "url": "http://127.0.0.1:8091/mcp"
   }
   ```

---

## 🛠️ Bereitgestellte MCP-Tools

### 1. `ask_llm`
Sendet einen Prompt an `/v1/chat/completions` und gibt die Antwort des Modells zurück.
- `prompt` *(string, Pflicht)*: Die Anfrage oder Aufgabe an das Sprachmodell.
- `system_prompt` *(string, optional)*: System-Prompt zur Vorgabe von Rolle oder Kontext.
- `model` *(string, optional)*: Überschreibt das Standardmodell (`LLM_MODEL`) für diesen einzelnen Aufruf.
- `temperature` *(float, optional, Standard: `0.2`)*: Steuerung der Kreativität/Deterministik.
- `max_tokens` *(int, optional, Standard: `0` = unbegrenzt)*: Maximale Anzahl generierter Tokens.
- `json_mode` *(bool, optional, Standard: `false`)*: Erzwingt validen JSON-Output (`response_format={"type": "json_object"}`).
- `json_schema` *(object, optional)*: Übergibt ein striktes JSON-Schema für Structured Outputs (`response_format={"type": "json_schema", ...}`).

### 2. `list_models`
Fragt `GET /v1/models` ab und liefert alle auf dem Endpunkt verfügbaren Modelle sowie das aktuell aktive Standardmodell.

### 3. `get_instance_info`
Gibt die aktuelle Konfiguration der Container-Instanz (`instance_name`, `configured_endpoint`, `effective_endpoint`, `configured_model`, `api_key_configured`, `timeout_seconds`) zurück.

---

## 🔒 Sicherheitshinweise

- Die Datei `.env` ist sowohl in `.gitignore` als auch in `.dockerignore` ausgeschlossen, sodass **keine API-Keys oder internen Endpunkte** im Git-Repository oder im gebauten Docker-Image landen.
- Der Container läuft unter einem unprivilegierten Benutzer (`mcpuser`) und bindet den Host-Port standardmäßig ausschließlich an `127.0.0.1`.
