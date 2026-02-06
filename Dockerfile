FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        libolm-dev \
        gcc \
        libc-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/mautrix-max

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN pip install --no-cache-dir .

ENV UID=1337 GID=1337
RUN groupadd -g $GID mautrix && useradd -u $UID -g $GID -d /opt/mautrix-max mautrix
USER mautrix

CMD ["python", "-m", "mautrix_max", "-c", "/data/config.yaml"]
