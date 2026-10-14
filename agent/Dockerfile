FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=America/Sao_Paulo

WORKDIR /app/agent
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY prompts ./prompts

RUN useradd --system --uid 1000 agent && mkdir -p /data && chown agent /data
USER agent

# 8080 = API/painel, 9092 = AudioSocket (o compose usa network_mode: host)
EXPOSE 8080 9092
CMD ["python", "-m", "app.main"]
