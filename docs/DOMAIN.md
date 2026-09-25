# Putting the site on www.jengasec.com

Render serves the whole platform — marketing pages, dashboards, API — so
there is one domain to point, not two. Roughly 20 minutes of work plus DNS
propagation.

## 1. Tell Render about the domain

Dashboard → the `jengasec` service → **Settings → Custom Domains → Add**:

* `www.jengasec.com`  ← the one people use
* `jengasec.com`      ← Render will offer to redirect it to www; accept

Render then shows the DNS records it wants. Leave the page open.

## 2. Point DNS at Render

At the registrar where jengasec.com is held (Truehost, Safaricom, GoDaddy,
Namecheap — wherever it was bought), open DNS management and add exactly
what Render showed:

| Type | Name | Value |
|---|---|---|
| CNAME | `www` | `jengasec.onrender.com` (Render shows the exact target) |
| A | `@` (apex) | the IP Render lists for the apex |

Notes that catch people out:

* The apex (`jengasec.com`) cannot be a CNAME at most registrars — that is
  why Render gives an A record for it. Use exactly the value Render shows.
* Delete any existing A/CNAME for `@` or `www` pointing somewhere else
  (a parking page, an old Vercel deployment), or the two fight.
* If the domain is behind Cloudflare, set both records to **DNS only**
  (grey cloud) until Render says "Certificate issued", then turn the orange
  cloud back on if you want Cloudflare in front.

Propagation is usually minutes, up to a few hours. Render verifies by
itself and issues a free Let's Encrypt certificate; the domain shows
**Certificate issued** when it is done. Do not skip ahead — the app will
400 until step 3 is deployed anyway.

## 3. Tell Django about the domain

Django refuses a `Host:` header it does not know, and refuses POSTs (login,
registration) from an origin it does not trust. In Render → **Environment**:

| Variable | Value |
|---|---|
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1,jengasec.com,www.jengasec.com` |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | `https://jengasec.com,https://www.jengasec.com` |
| `DJANGO_SITE_URL` | `https://www.jengasec.com` |

`render.yaml` already carries the first two for a fresh blueprint deploy;
an existing service needs them set by hand. `DJANGO_SITE_URL` is what goes
into verification and invitation emails — leave it wrong and every link in
every email points at the old host.

Save → Render redeploys. Then check:

```bash
python manage.py check_email          # confirms SITE_URL in the running app
```

## 4. Verify

* `https://www.jengasec.com/` — landing page, padlock, no certificate warning
* `https://jengasec.com/` — redirects to www
* `https://www.jengasec.com/prepare/`, `/partner/`, `/tracks/application-blue/`
* Log in, and register a throwaway account: the verification email's link
  must start `https://www.jengasec.com/accounts/verify/`

## 5. Afterwards

* The `*.onrender.com` URL keeps working. Leave it — it is useful when DNS
  is being changed.
* Social previews and `rel=canonical` already point at
  `https://www.jengasec.com`; nothing to change per page.
* Optional hardening once the certificate is live: set
  `SECURE_SSL_REDIRECT = True` and an HSTS header in `config/settings.py`
  (behind `if not DEBUG`). Safe here because `SECURE_PROXY_SSL_HEADER` is
  already configured for Render's proxy.
