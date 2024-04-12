# Benchmark Service

## Overview

The Benchmark Service allows storing and fetching benchmark
results. Benchmark results are then presented in a web page that
allows comparing two runs and viewing graphs of runs over time.

This is WIP and subject to change.

## Running the Benchmark Service

### Locally

#### Deps
```bash
pip install -r requirements.txt
sudo apt-get -y install npm
```

#### Build

```bash
cd ../web/ && npm install && npm run build
```

#### Run

First, you need to, for oauth:
- Generate a secret key for signing JWT tokens, e.g. `openssl rand -hex 32`
- Setup or get a Google oauth client ID and secret, they come from the Google Cloud console section "APIs and Services / Credentials / OAuth 2.0 Client IDs" and should be of type "Client ID for Web application".
- Make sure `http://localhost:9000/auth/google` is listed in "Authorized redirect URIs".

```bash
JWT_SECRET=<FILLME> GOOGLE_CLIENT_ID=<FILLME> GOOGLE_CLIENT_SECRET=<FILLME> DATABASE_URL=sqlite:///./benchmark_service.db uvicorn service.main:app --reload --log-config=service/log_conf.yaml --port=9000
```

The port `9000` must match what is present in "Authorized redirect URIs" in Google's "OAuth 2.0 Client IDs".

Navigating to [http://localhost:9000] should show a webpage showing
runs exported to the service and stored in the SQLITE DB file
`benchmark_service.db`.

Making the service use https can be done by passing certs with `--ssl-keyfile`, `--ssl-certfile`. For local testing, self-signed certificates can be generated with:
```bash
openssl req -x509 -newkey rsa:4096 -nodes -out cert.pem -keyout key.pem -days 365
```
and used with:
```
--ssl-keyfile key.pem --ssl-certfile cert.pem
```
and env variable `DOMAIN=https://localhost:9000`.

For Google oauth to work, you need to make sure
`https://localhost:9000/auth/google` redirection is authorized in
"Authorized redirect URIs"

The service can connect to a Google Cloud SQL instance (typically Postegres) with env variables:
```
DB_PASSWORD=<FILLME>
DATABASE_URL="postgresql+pg8000://"
INSTANCE_CONNECTION_NAME=<FILLME>
DB_USER=<FILLME>
DB_NAME=<FILLME>
```

### Docker

#### Build
```bash
cd .. && docker build -t quickwit/benchmark_service_and_web -f Dockerfile.service_and_web .
```
This packages both the REST API service in this directory, and the web interface from ../web.

#### Run

```bash
docker run -d --name quickwit_service_and_web -p 443:443 -v certs:/certs \
-e DB_PASSWORD=$(cat ~/secrets/postgres_password.txt) \
-e DATABASE_URL="postgresql+pg8000://" \
-e INSTANCE_CONNECTION_NAME="<FILLME>" \
-e DB_USER="<FILLME>" \
-e DB_NAME="<FILLME>"
-e JWT_SECRET=$(cat ~/secrets/jwt_secret.txt) \
-e GOOGLE_CLIENT_ID=$(cat ~/secrets/google_client_id.txt) \
-e GOOGLE_CLIENT_SECRET=$(cat ~/secrets/google_client_secret.txt) \
-e DOMAIN="<FILLME>" \
quickwit/benchmark_service_and_web --port 443 --ssl-keyfile /certs/key.pem --ssl-certfile /certs/cert.pem
```

Consider Secret Manager instead of passing secrets through env
variables for better security:
https://cloud.google.com/secret-manager/docs/overview.

