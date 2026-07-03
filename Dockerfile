FROM python:3.14-slim

# Instalar FFmpeg (requerido para el conversor de audio al vuelo)
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copiamos solo los requirements primero para aprovechar la cache de capas Docker
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# Copiamos el resto del código
COPY src/ /app/src/

# Copiamos la config y las migraciones de Alembic (run_migrations() las necesita en runtime)
COPY alembic.ini .
COPY migrations/ /app/migrations/

# Exponemos el puerto por el que correrá Uvicorn dentro del contenedor
EXPOSE 8004

# Comando de inicio usando uvicorn. Levantamos en el host general para que Docker pueda mapearlo.
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8004"]
