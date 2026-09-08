FROM python:3.11.16-slim-trixie@sha256:9534e5a8e315485d4061ed659af0fd78a284c015f9b73661b41d6bab25604534

WORKDIR /app

COPY requirements.lock .
RUN python -m pip install --no-cache-dir --upgrade \
        pip==26.2.1 \
        setuptools==84.0.0 \
        wheel==0.48.0 \
        jaraco.context==6.1.2 \
        packaging==26.3 \
        backports.tarfile==1.2.0 \
    && python -m pip install --no-cache-dir --require-hashes -r requirements.lock

RUN useradd -r -s /bin/false moto
USER moto

EXPOSE 5000

CMD ["moto_server", "--host", "0.0.0.0", "--port", "5000"]
