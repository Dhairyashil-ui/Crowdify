FROM python:3.11-slim

WORKDIR /app

# System deps for OpenCV + YOLO
RUN apt-get update && apt-get install -y \
    build-essential \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all backend code including yolov8m.pt
COPY . .

EXPOSE 8000

# Render injects $PORT — fall back to 8000 locally
CMD uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000}
