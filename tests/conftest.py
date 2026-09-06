"""Configuration minimale des tests qui chargent le serveur MCP."""

import os


# La production doit fournir sa propre politique Host/Origin. Ces valeurs ne
# concernent que l'ASGI local des tests unitaires.
os.environ.setdefault("MCP_ALLOWED_HOSTS", '["localhost:*", "127.0.0.1:*"]')
os.environ.setdefault("MCP_ALLOWED_ORIGINS", '["http://localhost:*", "http://127.0.0.1:*"]')
