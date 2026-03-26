FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && pip install --no-cache-dir rawpy || echo "[INFO] rawpy niet beschikbaar op dit platform — RAW/ARW bestanden niet ondersteund"

COPY declutter-app.py .
COPY translations/ translations/

RUN mkdir -p /data

EXPOSE 8765

CMD ["python", "declutter-app.py"]
