FROM python:3.11-slim

WORKDIR /app

# Install Java (required for PySpark)
RUN apt-get update && apt-get install -y \
    openjdk-21-jre-headless \
    procps \
    && rm -rf /var/lib/apt/lists/*

# Set Java home
ENV JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64
ENV PATH=$PATH:$JAVA_HOME/bin

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy processor scripts
COPY processor_lakehouse.py .
COPY processor_mnp.py .

# Run lakehouse processor by default (processes both UDC and MNP files)
CMD ["python", "processor_lakehouse.py"]
