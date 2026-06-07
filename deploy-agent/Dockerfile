FROM python:3.12-alpine

WORKDIR /app

RUN apk add --no-cache \
    rsync \
    git \
    curl \
    docker-cli \
    docker-cli-compose

COPY app.py /app/app.py

EXPOSE 8090

CMD ["python3", "/app/app.py"]

