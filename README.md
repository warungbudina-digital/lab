# Cloud Shell Debug Lab

Stack minimal untuk:

- `cloudflared` host mode
- `nginx` reverse proxy
- `debug-agent` read-only
- `deploy-agent` write/exec terkontrol

Tujuan:

- expose endpoint debug lewat tunnel
- baca info host secara read-only
- baca Docker API secara read-only
- memberi write/exec surface terkontrol untuk workspace lab
- jadi fondasi deploy-testing sebelum nanti ditambah exec/deploy runner

## File

- `docker-compose.yml`
- `nginx/nginx.conf`
- `debug-agent/app.py`
- `deploy-agent/app.py`
- `.env.example`

## Endpoint

- `/` → status teks sederhana
- `/health`
- `/host-info`
- `/docker-info`
- `/disk`
- `/env-safe`
- `/headers`
- `/deploy-health`
- `/deploy-exec`
- `/deploy-write-file`
- `/deploy-read-file`
- `/deploy-mkdir`

## Quick start

1. Copy env:

```bash
cp .env.example .env
```

2. Isi token Cloudflare tunnel:

```env
CLOUDFLARED_TUNNEL_TOKEN=isi_token
DEPLOY_AGENT_TOKEN=isi_token_panjang
HOST_HOME_DIR=/home/your-cloudshell-user
```

3. Jalankan:

```bash
docker compose up -d --build
```

4. Test lokal di host:

```bash
curl http://127.0.0.1:18080
curl http://127.0.0.1:18080/health
curl http://127.0.0.1:18080/host-info
curl http://127.0.0.1:18080/docker-info
curl http://127.0.0.1:18080/deploy-health
```

5. Di Cloudflare public hostname, arahkan origin ke:

```text
http://127.0.0.1:18080
```

## Catatan keamanan

- `debug-agent` **read-only**, tidak ada endpoint exec
- `deploy-agent` adalah **write/exec surface** dan harus dianggap sensitif
- mount host root adalah `/:/host:ro`
- docker socket tetap sensitif walaupun dipakai untuk baca; jangan expose setup ini ke publik tanpa proteksi tambahan
- `deploy-agent` memakai bearer token (`DEPLOY_AGENT_TOKEN`) dan dibatasi ke `${HOST_HOME_DIR}`
- idealnya tetap tambahkan Cloudflare Access atau auth layer tambahan sebelum dipakai rutin

## Contoh call deploy-agent

### Health

```bash
curl http://127.0.0.1:18080/deploy-health
```

### Exec

```bash
curl -X POST http://127.0.0.1:18080/deploy-exec \
  -H "Authorization: Bearer $DEPLOY_AGENT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"workdir":"n8n","command":"pwd && ls -la","timeout":20}'
```

### Write file

```bash
curl -X POST http://127.0.0.1:18080/deploy-write-file \
  -H "Authorization: Bearer $DEPLOY_AGENT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"path":"n8n/.gitignore","content":".env\n"}'
```
