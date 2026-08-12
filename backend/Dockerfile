FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for OpenCV and C++ compilation
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

# Pre-install numpy before 'lap' requires it at build time for its C++ extensions
# Note: Pinning numpy<2.0.0 because lap fails to compile against the new numpy 2.x C-API.
RUN pip install --no-cache-dir "numpy<2.0.0"

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Pre-download the YOLO model so it doesn't download at runtime
RUN python -c "from ultralytics import YOLO; YOLO('yolov8n.pt')"

EXPOSE 8000

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
