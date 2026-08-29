# ==========================================
# Production Python Runtime
# ==========================================
FROM python:3.12-slim-bookworm AS runner

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DOCKER=true \
    YTM_SYNC_DATA_DIR=/config \
    HOST=0.0.0.0 \
    PORT=8080

# Install runtime dependencies: curl, ffmpeg, unzip, and Deno for yt-dlp challenge solver
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ffmpeg \
    unzip \
    && curl -fsSL https://deno.land/install.sh | sh \
    && mv /root/.deno/bin/deno /usr/local/bin/deno \
    && rm -rf /root/.deno \
    && rm -rf /var/lib/apt/lists/*

# Create application user and group
RUN groupadd -g 1000 ytmsync && \
    useradd -u 1000 -g ytmsync -d /app -s /bin/bash ytmsync

WORKDIR /app

# Install Python backend dependencies
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend application package
COPY backend/ytm_service/ ./ytm_service/

# Copy compiled Flutter Web UI static distribution
COPY backend/web_dist/ ./web_dist/

# Provision persistent directories and set ownership
RUN mkdir -p /config/database /config/auth /config/logs /config/backups /music /downloads && \
    chown -R ytmsync:ytmsync /app /config

USER ytmsync

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:8080/health || exit 1

ENTRYPOINT ["python", "-m", "ytm_service.main"]
