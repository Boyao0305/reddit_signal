# 0005 — Serve Seedcast on seedcast.co from the Contabo server

Date: 2026-10-10 · Status: accepted

## Context

`seedcast.co` is a Route 53 zone (AWS account 200889996426) shared with live email (Google Workspace)
and other projects' subdomains. Apex and `www` currently alias CloudFront distribution `E7QB936EYVRKS`,
which redirects to benchology.co; `event.seedcast.co` uses the same distribution. nginx on the Contabo
server belongs to the GTM project until it finishes moving (`crmflowhub.seedcast.co` is still served
there).

## Decision

- Change only `seedcast.co` and `www.seedcast.co` A/AAAA records to `94.72.121.103` /
  `2605:a141:2343:6993::1`, then remove those two aliases from the CloudFront distribution. All other
  records stay untouched.
- Add a separate nginx server block for `seedcast.co` (validated with `nginx -t`, applied with a graceful
  reload) and obtain a certificate with certbot. nginx proxies to the `api` container on localhost.
- Switch DNS only when Seedcast serves real data, not fixtures.

## Consequences

- The benchology.co redirect from seedcast.co ends (authorized by the owner).
- Until GTM leaves, nginx is shared; our change is limited to one new file in `sites-available`.

## Alternatives rejected

- **Caddy in Docker on 80/443** — conflicts with GTM's nginx while it is still here.
- **Keep CloudFront in front** — adds a second hosting layer for a low-traffic site.
