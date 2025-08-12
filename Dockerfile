
# syntax=docker/dockerfile:1
FROM python:3.11-slim

# System libs for matplotlib fonts/rasterization
RUN apt-get update && apt-get install -y --no-install-recommends         libgl1 libglib2.0-0 libsm6 libxrender1 libxext6 libfontconfig1         && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
ENV PYTHONUNBUFFERED=1
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
