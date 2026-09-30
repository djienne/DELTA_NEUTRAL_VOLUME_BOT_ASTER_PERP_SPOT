FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# docker-compose.yml also bind-mounts ./ over /app for live updates
COPY *.py config_volume_farming_strategy.json ./

ENV PYTHONUNBUFFERED=1

CMD ["python", "volume_farming_strategy.py"]
