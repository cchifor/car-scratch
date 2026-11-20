# Car server image for tests and the workstation simulator. Same Debian release and apt packages as the Pi.
FROM debian:trixie-slim AS base
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-aiohttp python3-smbus2 ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app/server
WORKDIR /app

FROM base AS test
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3-pytest python3-pytest-asyncio python3-pytest-aiohttp \
 && rm -rf /var/lib/apt/lists/*

FROM base AS run
COPY server/ /app/server/
EXPOSE 8080
CMD ["python3", "-m", "picar_server", "--port", "8080", "--hardware", "sim"]
