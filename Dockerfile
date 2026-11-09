# ==============================================================================
# DOCKERFILE: SCHLANKER LOCAL LLM MCP SERVER
# ==============================================================================
# Warum `python:3.12-slim`:
#   Bietet ein minimales Debian-basiertes Python-Image ohne unnötige Build-Tools
#   oder Desktop-Pakete, unterstützt aber vorkompilierte manylinux-Wheels
#   (wie `pydantic-core` im offiziellen `mcp`-SDK) ohne langsamen Compiler-Bau.
#   Quelle: https://hub.docker.com/_/python
# ==============================================================================
FROM python:3.12-slim

# Verhindert das Schreiben von .pyc-Dateien und aktiviert ungepufferte Logs
# für sofortige Sichtbarkeit in `docker compose logs`.
# Quelle: https://docs.python.org/3/using/cmdline.html#environment-variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Nur die beiden zwingend benötigten Pakete installieren:
# 1. `mcp`: Offizielles Model Context Protocol SDK (inkl. FastMCP & Uvicorn)
#    Quelle: https://pypi.org/project/mcp/
# 2. `httpx`: Asynchroner HTTP-Client für den OpenAI-kompatiblen Endpunkt
#    Quelle: https://www.python-httpx.org/
RUN pip install --no-cache-dir "mcp>=1.6.0,<2.0.0" "httpx>=0.27.0"

# Einzige Anwendungsdatei in das Image kopieren
COPY server.py /app/server.py

# Unprivilegierten Benutzer für erhöhte Container-Sicherheit anlegen
# Quelle: https://docs.docker.com/build/building/best-practices/#user
RUN useradd --create-home --shell /usr/sbin/nologin mcpuser
USER mcpuser

# Interner Standard-Port des Streamable-HTTP-MCP-Servers (/mcp)
EXPOSE 8000

# Kostenschutz: Der Startbefehl initialisiert lediglich den leichtgewichtigen
# HTTP-Listener; es erfolgen beim Containerstart keinerlei LLM-Abfragen.
CMD ["python", "/app/server.py"]
