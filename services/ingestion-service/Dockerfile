FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (Docker layer cache — only re-runs if requirements change)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Create upload directory
RUN mkdir -p /data/uploads

EXPOSE 8002

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8002", "--reload"]
