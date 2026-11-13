FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY asystent ./asystent
COPY konfiguracja.yaml reguly.yaml ./

ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["python", "-m", "asystent.serwer_http"]
