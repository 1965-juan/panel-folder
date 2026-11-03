FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY arranque.py .
ENV PANEL_DATA=/data
EXPOSE 8090
CMD ["python", "/srv/arranque.py"]
