FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p static

ENV PORT=8000
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120
