FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

CMD gunicorn decisionsupport.web:app --bind 0.0.0.0:$PORT --workers 2 --timeout 30
