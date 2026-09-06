FROM python:3.11-slim

WORKDIR /app

# Dépendances Python
COPY requirements.txt .
COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

# Code source
COPY src/ src/
COPY scripts/ scripts/
COPY VERSION .

# Sécurité : utilisateur non-root
RUN useradd -r -s /bin/false mcp
USER mcp

EXPOSE 8002

# Point d'entrée : python -m mon_service
# Adapter "mon_service" au nom réel de votre package
CMD ["python", "-m", "src.mon_service"]
