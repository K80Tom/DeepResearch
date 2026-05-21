# Production Deployment

This project can be deployed as a single-machine Docker Compose stack:

- `frontend`: Nginx serving the Vue build and reverse-proxying `/api` and `/health`.
- `backend`: FastAPI + LangGraph.
- `postgres`: checkpoint, short-term memory, and long-term memory storage.
- `milvus-standalone`: vector store, backed by `etcd` and `minio`.
- `attu`: optional Milvus UI, enabled only with the `tools` profile.

## 1. Prepare the server

Recommended minimum for a demo is 4C/8G. If Milvus and the app share the same server in production, 8C/16G is safer.

Install Docker and Docker Compose, then clone or upload this repository to the server.

Only expose ports `80` and `443` to the public internet. Do not expose PostgreSQL, Milvus, MinIO, or Redis directly.

## 2. Configure secrets

Create the production env file:

```bash
cp .env.production.example .env.production
```

Edit `.env.production` and set at least:

```bash
DASHSCOPE_API_KEY=...
BOCHA_API_KEY=...
POSTGRES_PASSWORD=...
POSTGRES_DSN=postgresql://root:<same-password>@postgres:5432/postgres
AUTH_TOKEN_TTL_HOURS=168
MINIO_ROOT_PASSWORD=...
CORS_ALLOW_ORIGINS=https://your-domain.com
```

Keep `.env.production` out of Git.

## 3. Start the stack

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml up -d --build
```

Check status:

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml ps
docker compose --env-file .env.production -f docker-compose.prod.yml logs -f backend
```

Health check:

```bash
curl http://127.0.0.1/health
```

## 4. Test the API

```bash
TOKEN=$(curl -s http://127.0.0.1/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"demo_user","password":"demo123456","display_name":"Demo"}' \
  | python -c "import json,sys; print(json.load(sys.stdin)['token'])")

curl -N http://127.0.0.1/api/v1/research/stream \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"query":"你好","thread_id":"thread-demo"}'
```

The response should stream Server-Sent Events:

```text
data: {"type":"status",...}
data: {"type":"phase",...}
data: {"type":"final",...}
```

## 5. Optional Milvus UI

Start Attu only when needed:

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml --profile tools up -d attu
```

Then open `http://server-ip:8001`. Do not leave this exposed publicly in production.

## 6. HTTPS

For a public domain, put a host-level reverse proxy or cloud load balancer in front of this stack and terminate TLS there.

If using host Nginx + Certbot, proxy to the Compose frontend:

```nginx
location / {
    proxy_pass http://127.0.0.1:80;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_buffering off;
}
```

`proxy_buffering off` matters because `/api/v1/research/stream` uses SSE streaming.

## 7. Updating

```bash
git pull
docker compose --env-file .env.production -f docker-compose.prod.yml up -d --build
```

## Notes

- The current code uses one Milvus collection configured by `MILVUS_COLLECTION`.
- For long-term production, consider splitting RAG knowledge and user memory into separate collections.
- Keep API keys and database passwords only in `.env.production` or your cloud secret manager.
