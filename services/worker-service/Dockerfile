FROM python:3.11-slim

# Install Tesseract OCR + Arabic language pack
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-ara \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download the embedding model into the image
# So the worker doesn't download it every time it starts
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('intfloat/multilingual-e5-base')"

COPY . .

# Start Celery worker — listens on the 'ingestion' queue
CMD ["celery", "-A", "worker", "worker", "--loglevel=info", "-Q", "ingestion", "--concurrency=1"]
