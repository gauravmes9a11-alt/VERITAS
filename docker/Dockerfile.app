# One image, several roles (api / worker / researcher / advisor / indexer / eval): the roles are
# separate containers with separate resource profiles, but share code and dependency pins.
FROM python:3.12-slim
ARG EMBEDDINGS=st
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt requirements-embed.txt ./
RUN pip install -r requirements.txt \
 && if [ "$EMBEDDINGS" = "st" ]; then \
      pip install torch --index-url https://download.pytorch.org/whl/cpu && pip install -r requirements-embed.txt; fi
COPY app ./app
COPY eval ./eval
COPY corpus ./corpus
COPY web ./web
COPY contracts ./contracts
COPY scripts ./scripts
RUN useradd -m desk && mkdir -p /data/index /results && chown -R desk /data /results /app
USER desk
