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
#   CAPSOLVER_API_KEY
#   PROXY_URL          (residential)
#   OPENAI_API_KEY     (optional, for yii_image)
```

## 3. Config

Edit `config/config.yaml`:

- `event.url` → real Ticketmaster event URL
- `proxy.enabled: true` and `proxy.urls: ["${PROXY_URL}"]`
- `captcha.auto_solve: true`
- `timing.humanize.enabled: true` (already on by default after this update)

Copy and fill accounts:

```bash
cp config/accounts.yaml.example config/accounts.yaml
# or rely on TM_EMAIL / TM_PASSWORD in .env
```

## 4. Real Chromium profile (recommended)

```bash
mkdir -p sessions/default
# Open Chromium once manually, log into Ticketmaster, accept cookies,
# then point browser.user_data_dir at that folder.
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

Watch the log for:

- `Proxy OK — public IP: …`
- Vendor detection (US / SG / BR)
- Captcha solver attempts

## 7. Brazil note

`ticketmaster.com.br` is detected and mapped to the default US adapter.
Expect selector and flow differences; treat the first runs as exploratory.

## 8. Only after success

Consider `checkout.auto_purchase: true` with extreme caution.
