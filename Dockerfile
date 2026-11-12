FROM python:3.12-slim

# Install system dependencies & redis-server
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    redis-server \
    curl \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    DISABLE_SQLALCHEMY_CEXT=1 \
    PORT=8000

WORKDIR /app

# Copy requirements and install
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# Setup entrypoint script
RUN chmod +x ./scripts/start_cloud.sh

EXPOSE 8000

CMD ["./scripts/start_cloud.sh"]
