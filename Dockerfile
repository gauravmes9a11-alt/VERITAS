FROM python:3.12-slim

# Install system dependencies, redis-server (for built-in fallback), and curl
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    redis-server \
    curl \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PORT=8000

WORKDIR /app

# Copy dependency definitions and install
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# Build vector index during image build so container boots instantly
RUN python -m app.corpus.indexer build

EXPOSE 8000

CMD ["python", "scripts/run_services.py"]
