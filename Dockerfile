# Use an official Python runtime as a parent image
FROM python:3.9-slim

# Set the working directory in the container
WORKDIR /app

# Copy the dependencies file to the working directory
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code to the working directory
# Note: Individual files are mounted as volumes in docker-compose.yml for live updates
COPY volume_farming_strategy.py .
COPY aster_api_manager.py .
COPY strategy_logic.py .
COPY utils.py .
COPY config_volume_farming_strategy.json .

# Create empty files for state and logs (will be overwritten by volumes)
RUN touch volume_farming_state.json volume_farming.log

# Set Python to run in unbuffered mode for real-time logging
ENV PYTHONUNBUFFERED=1

# Specify the command to run on container startup
CMD ["python", "volume_farming_strategy.py"]
