# Reaching ComfyUI: local, SSH and a reverse proxy

The terminal works however you open ComfyUI, with one rule: **pairing needs HTTPS or a loopback address.** The pairing cookie is `Secure`, so a browser keeps it only on `https://` pages or on `http://127.0.0.1`, `http://localhost` and `http://[::1]`. Plain HTTP on a LAN address (`http://192.168.1.50:8188`) is refused with `insecure_origin`.

Pick the setup that matches where your browser is:

| Browser and ComfyUI | Use | Address in the browser |
|---|---|---|
| Same machine | [Local](#local-same-machine) | `http://127.0.0.1:8188` |
| Different machines, now and then | [SSH port forward](#ssh-port-forward) | `http://127.0.0.1:8188` (forwarded) |
| Different machines, every day, or several devices | [Reverse proxy with HTTPS](#reverse-proxy-with-https) | `https://comfy.example.com` |

Two things hold for all of them:

- **A pairing belongs to one address.** A browser paired at `https://comfy.example.com` is refused at `http://127.0.0.1:8188`, and the other way round. Open ComfyUI from the address you paired from, or pair that address too. The browser list in **Settings › Harness** shows the address each browser paired from.
- **Use Chrome or another Chromium browser.** Only Chromium is tested, and Safari does not keep `Secure` cookies set from `http://127.0.0.1`.

## Local (same machine)

Start ComfyUI as usual. It listens on `127.0.0.1:8188` unless you pass `--listen`:

```sh
python main.py
```

Open `http://127.0.0.1:8188`, press the backtick key on the canvas and pair. Run the `pair.py` command from the popup in a terminal on the same machine.

## SSH port forward

Use this when ComfyUI runs on another machine (a GPU box, a cloud VM) and you have SSH to it. Nothing is exposed to the network, and you need no certificate: the browser sees a loopback address, which pairing accepts.

1. On the ComfyUI machine, let ComfyUI listen on loopback only (the default):
   ```sh
   python main.py --listen 127.0.0.1 --port 8188
   ```
2. On your own machine, forward a local port to it:
   ```sh
   ssh -N -L 8188:127.0.0.1:8188 you@gpu-box
   ```
   `-N` opens the tunnel without a shell. Leave it running.
3. Open `http://127.0.0.1:8188` in Chrome and press the backtick key on the canvas.
4. Run the popup's `pair.py` command on the ComfyUI machine, in a second SSH session:
   ```sh
   ssh -t you@gpu-box python3 /path/to/ComfyUI/custom_nodes/comfyui-harness/pair.py K7QM-3XVD
   ```
   `-t` gives `pair.py` a terminal, so it can ask which agent and folder to use.

If port 8188 is taken on your machine, forward another one (`-L 9188:127.0.0.1:8188`) and always open `http://127.0.0.1:9188`. The pairing remembers the port.

The same SSH session is the place to sign the agent in the first time, since a sign-in that opens a callback on `localhost` cannot reach a browser on another machine. See [Sign in to the agent once](getting-started.md#sign-in-to-the-agent-once).

## Reverse proxy with HTTPS

A reverse proxy in front of ComfyUI gives you a real certificate and one stable address for every device. The examples below get the certificate from Let's Encrypt with the **DNS challenge**: the proxy proves it owns the name by writing a TXT record through your DNS provider's API, so the proxy needs no open port 80 or 443 on the internet. That suits a ComfyUI reached only over a LAN or a VPN (WireGuard, Tailscale): the name can point at a private address and still get a trusted certificate.

The examples use Cloudflare DNS, `comfy.example.com` as the name and `10.0.0.5:8188` as the ComfyUI machine. Every proxy here has plugins for other DNS providers; see [Other DNS providers](#other-dns-providers).

### What the proxy must do

Whatever proxy you use, the harness needs four things from it:

1. **Pass `Host` and `Origin` unchanged.** ComfyUI refuses a request whose `Host` and `Origin` differ (a bare 403, before the harness runs), and the harness checks `Origin` against the address you paired from. Caddy and Traefik pass both by default; nginx needs `proxy_set_header Host $host;`.
2. **Pass WebSocket upgrades.** The terminal and ComfyUI's own updates are WebSockets.
3. **Allow long-lived connections.** A terminal can sit quiet for a long time; raise idle timeouts where the proxy has them.
4. **Serve HTTPS** with a certificate the browser trusts.

### Where ComfyUI listens

- **Proxy on the same machine as ComfyUI:** start ComfyUI with `--listen 127.0.0.1` and point the proxy at `127.0.0.1:8188`.
- **Proxy on another machine:** start ComfyUI with `--listen 0.0.0.0` and point the proxy at the ComfyUI machine's address. Firewall port 8188 so that only the proxy can reach it.

Keep ComfyUI itself on plain HTTP behind the proxy. Started with `--tls-keyfile` and `--tls-certfile`, ComfyUI makes the [canvas bridge](canvas-bridge.md) unavailable, and the bridge also needs ComfyUI listening on loopback or on all interfaces, which both setups above do.

### The DNS API token

Create a token that can edit DNS records in one zone and nothing else. In Cloudflare: **My Profile › API Tokens › Create Token › Edit zone DNS**, with **Zone Resources** set to the one zone. Keep it out of every file you commit:

```sh
# .env, next to docker-compose.yml; never committed
CLOUDFLARE_API_TOKEN=paste-the-token-here
```

```sh
chmod 600 .env
echo .env >> .gitignore
```

The DNS record for `comfy.example.com` can be an `A` record pointing at the proxy's private address (for example `10.0.0.2`) in your DNS, or a name your VPN resolves. Clients reach it over the LAN or VPN; the TXT records for the challenge are added and removed by the proxy.

### Caddy

Caddy renews certificates on its own and passes `Host`, `Origin` and WebSockets by default. The DNS provider is a plugin, so build an image that includes it:

```dockerfile
# Dockerfile
FROM caddy:builder AS builder
RUN xcaddy build --with github.com/caddy-dns/cloudflare

FROM caddy:latest
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
```

```yaml
# docker-compose.yml
services:
  caddy:
    build: .
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    env_file: .env          # CLOUDFLARE_API_TOKEN
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data    # certificates; keep it, or Let's Encrypt rate limits bite
      - caddy_config:/config
volumes:
  caddy_data:
  caddy_config:
```

```caddyfile
# Caddyfile
comfy.example.com {
	encode zstd gzip
	reverse_proxy 10.0.0.5:8188

	tls {
		dns cloudflare {env.CLOUDFLARE_API_TOKEN}
	}
}
```

`docker compose up -d --build`, then open `https://comfy.example.com`. Caddy redirects HTTP to HTTPS by itself.

### Traefik

Traefik speaks to many DNS providers out of the box (through lego), passes `Host` by default and handles WebSockets without extra settings.

```yaml
# docker-compose.yml
services:
  traefik:
    image: traefik:v3.3
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    environment:
      CF_DNS_API_TOKEN: ${CLOUDFLARE_API_TOKEN}   # from .env
    command:
      - --entrypoints.web.address=:80
      - --entrypoints.web.http.redirections.entrypoint.to=websecure
      - --entrypoints.websecure.address=:443
      - --providers.file.filename=/etc/traefik/comfy.yml
      - --certificatesresolvers.le.acme.email=you@example.com
      - --certificatesresolvers.le.acme.storage=/letsencrypt/acme.json
      - --certificatesresolvers.le.acme.dnschallenge.provider=cloudflare
    volumes:
      - ./comfy.yml:/etc/traefik/comfy.yml:ro
      - ./letsencrypt:/letsencrypt   # acme.json holds the certificates; keep it private
```

```yaml
# comfy.yml
http:
  routers:
    comfy:
      rule: Host(`comfy.example.com`)
      entryPoints: [websecure]
      service: comfy
      tls:
        certResolver: le
  services:
    comfy:
      loadBalancer:
        servers:
          - url: http://10.0.0.5:8188
```

If ComfyUI runs in Docker on the same host, you can use container labels instead of the file provider; the router and service are the same.

### nginx with certbot

nginx does not fetch certificates itself. Get one with certbot's Cloudflare plugin, then point nginx at it.

```sh
sudo apt install certbot python3-certbot-dns-cloudflare   # or your distribution's packages
sudo install -m 600 /dev/null /etc/letsencrypt/cloudflare.ini
echo "dns_cloudflare_api_token = paste-the-token-here" | sudo tee /etc/letsencrypt/cloudflare.ini >/dev/null
sudo certbot certonly --dns-cloudflare \
  --dns-cloudflare-credentials /etc/letsencrypt/cloudflare.ini \
  -d comfy.example.com
```

certbot installs a timer that renews the certificate; add `--deploy-hook "systemctl reload nginx"` so nginx picks up each renewal.

```nginx
# /etc/nginx/conf.d/comfy.conf
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}

server {
    listen 80;
    server_name comfy.example.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    http2 on;                           # nginx 1.25.1+; older: listen 443 ssl http2;
    server_name comfy.example.com;

    ssl_certificate     /etc/letsencrypt/live/comfy.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/comfy.example.com/privkey.pem;

    client_max_body_size 200m;          # image and model uploads

    location / {
        proxy_pass http://10.0.0.5:8188;
        proxy_http_version 1.1;
        proxy_set_header Host $host;    # required: ComfyUI refuses a Host that differs from Origin
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 1d;          # a quiet terminal stays connected
        proxy_send_timeout 1d;
        proxy_buffering off;
    }
}
```

nginx passes the browser's `Origin` header unchanged; do not add a `proxy_set_header Origin` line.

### Other DNS providers

| Proxy | Where the providers are | Example |
|---|---|---|
| Caddy | [caddy-dns](https://github.com/caddy-dns) modules, added with `xcaddy build --with` | `github.com/caddy-dns/route53`, `.../digitalocean`, `.../porkbun` |
| Traefik | [lego's provider list](https://go-acme.github.io/lego/dns/), built in | `dnschallenge.provider=route53` with that provider's variables |
| certbot | `certbot-dns-*` plugins | `python3-certbot-dns-route53` |

### Checking the proxy

From any machine that resolves the name:

```sh
curl -sI https://comfy.example.com/ | head -1                                  # HTTP/2 200
curl -s -o /dev/null -w '%{http_code}\n' \
  -H 'Origin: https://comfy.example.com' https://comfy.example.com/harness/status   # 200
```

A `403` on the second command with an empty body means `Host` reached ComfyUI changed (nginx without `proxy_set_header Host $host;`, or a proxy rewriting it). Then open the page, press the backtick key and pair as usual; run `pair.py` on the ComfyUI machine, not on the proxy.

### Exposing it to the internet

The terminal runs a program as the account that runs ComfyUI, so treat the address like SSH. Pairing keeps unpaired browsers out of the terminal, but ComfyUI's own pages and API have no login. Prefer a VPN or LAN-only DNS name over a public one. If it must be public, put an authentication layer in front (your proxy's basic auth or forward auth, Cloudflare Access, a VPN-only listener) and read [SECURITY.md](../SECURITY.md) first.
