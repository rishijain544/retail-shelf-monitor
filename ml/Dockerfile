FROM python:3.10-slim

WORKDIR /app

# Install system packages needed by OpenCV and PyTorch (for YOLOv8)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgl1-mesa-glx \
    libglib2.0-0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy and install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy ML scripts and files
COPY . .

# Default command to run
CMD ["python", "scripts/detect.py"]
