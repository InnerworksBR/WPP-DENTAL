FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOST=0.0.0.0 \
    PORT=3000 \
    WORKERS=1 \
    ENABLE_APPOINTMENT_CONFIRMATION_SCHEDULER=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chmod +x deploy/start.sh \
    && mkdir -p data credentials

EXPOSE 3000

CMD ["./deploy/start.sh"]
