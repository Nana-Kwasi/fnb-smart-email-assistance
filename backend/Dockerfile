FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY requirements.txt requirements-drivers.txt ./
RUN pip install -r requirements.txt -r requirements-drivers.txt

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app

# Never run as root.
RUN useradd --system --uid 10001 fnbcare
USER fnbcare

EXPOSE 8000
# The API is only reachable through nginx on the internal Docker network.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'"]
