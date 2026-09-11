FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server/ server/
COPY client/ client/
COPY alembic/ alembic/
COPY alembic.ini .

EXPOSE 8000

# Render (and most PaaS hosts) inject $PORT at runtime; uvicorn.run() in
# server/main.py's __main__ block is for local dev only and isn't used here.
CMD ["sh", "-c", "uvicorn server.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
