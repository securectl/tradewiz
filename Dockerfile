FROM python:3.11-slim

WORKDIR /app

# System deps for psycopg2, Pillow (QR codes)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libpq-dev libjpeg62-turbo-dev zlib1g-dev \
    libffi-dev libssl-dev python3-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5000

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--threads", "4", "--timeout", "300", "app:app"]
