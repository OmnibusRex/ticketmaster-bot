# Activation checklist

Use this list before the first real run.

## 1. Local environment

```bash
bash scripts/setup_playwright.sh
source .venv/bin/activate
```

## 2. Secrets

```bash
cp .env.example .env
# Edit .env and fill:
#   CAPSOLVER_API_KEY     (optional until you want auto-solve)
#   PROXY_URL             (residential, optional)
#   OPENAI_API_KEY        (optional)
#   TELEGRAM_* / DISCORD  (optional notifications)
```

## 3. Config

Edit `config/config.yaml`:

- `event.url` → real Ticketmaster event URL
- `proxy.enabled: true` and `proxy.urls: ["${PROXY_URL}"]` when you have a proxy
- `captcha.auto_solve: true` when you have CapSolver
- `timing.humanize.enabled: true` (already on by default)

Copy and fill accounts:

```bash
cp config/accounts.yaml.example config/accounts.yaml
```

## 4. Warm up the Chromium profile (recommended)

```bash
python scripts/warmup_profile.py --url https://www.ticketmaster.com.br/
# Log in manually if needed. Session is saved in sessions/default.
```

## 5. Validate

```bash
python run.py --dry-run
python run.py --explain
```

## 6. First real run (no purchase)

```bash
python run.py --no-headless --no-auto-purchase
```

## 7. Vendors

| Host | Adapter |
|------|---------|
| ticketmaster.com | ticketmaster |
| ticketmaster.sg | ticketmaster_sg |
| ticketmaster.com.br | ticketmaster_br |

The BR adapter reuses the US flow with BR-oriented selectors in
`config/selectors/ticketmaster_br.yaml`. Expect to refine selectors after
the first real runs.

## 8. Only after success

Consider `checkout.auto_purchase: true` with extreme caution.
