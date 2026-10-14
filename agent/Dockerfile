FROM python:3.12-slim
WORKDIR /srv/agent
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# Third PDF renderer in the Week 2 fallback chain (pdftoppm). Inert unless W2_ENABLED.
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd -r -u 10001 agent && mkdir -p /srv/agent/data && chown -R agent /srv/agent/data
USER agent
EXPOSE 8400
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8400"]
