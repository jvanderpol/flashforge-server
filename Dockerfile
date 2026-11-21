FROM python:3.12-slim

RUN apt-get update && apt-get install -y git vim

WORKDIR /app

RUN git clone https://github.com/jvanderpol/flashforge-server.git .

ENTRYPOINT ["python", "server.py"]
