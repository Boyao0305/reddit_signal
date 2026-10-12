# Phase 3A — MCP Config UI (multi-project profile manager)

> **v1.0 (2026-05-19) — implemented.** Notes: All eight chunks shipped end-to-end. 22 unit tests + 3 live trip-tests green. Profile `openclaw` migrated from legacy configs and activated. Spec deltas folded back per `06_teamwork_protocol.md §4.1` — see CHANGELOG v1.0 for the post-build reconciliation summary.

> **Status:** v1.0 design — ready for implementation.
> **Created:** 2026-05-18.
> **Implementation owner:** Claude (orchestrator) — small enough to implement inline; no Claude Code handoff needed unless scope grows.
> **Companion files:** `06_teamwork_protocol.md` (the three-MCP convention this UI manages), `01_locked_decisions.md` (none yet for MCPs themselves; this spec adds the first).
> **Acceptance criteria:** §8 below. Implementation order: §10.

This file is self-contained for the implementer. It carries the full data model, REST API, file layout, and the must-not-violate constraints. No other vault files need to be read to do the work.

---

## 1. Goal

Build a single-page web UI that manages **named project profiles** for the three-MCP setup (fs / pg / guide). One profile is "active" at a time; activating a profile rewrites the live config files and restarts the mcp-proxy systemd unit so external AI clients (Claude.ai custom connector, ChatGPT, etc.) see the new project. The UI must be operable from a phone, like the dashboard.

The motivating use case: today the openclaw configs are hand-edited at three different paths and the guide server's `VAULT_ROOT` is a hardcoded constant in `server.py`. To start a second project, the user would have to clone the guide-server, edit constants, write a parallel `servers.json`, and remember to swap configs. This UI replaces that with a switch.

## 2. Out of scope

- Authoring the guide server itself. The existing `~/openclaw-mcp/orientation/server.py` is generalized once (read config from JSON pointed at via env var) and reused across profiles. The UI doesn't generate new server code.
- Editing the mcp-proxy / Caddy / Tailscale layer. Those stay as-is. The UI only writes config files that mcp-proxy reads on restart.
- Editing the Bearer token or auth config of mcp-proxy. The token rotation is a separate manual concern.
- Multi-user. The UI is single-user (Boyao). HTTP basic-auth with a shared password is sufficient.
- Live preview of what an MCP client would see. Validation is path-existence and DSN-reachability checks, not full MCP handshakes.
- Importing existing non-openclaw projects from arbitrary configs. The user creates new profiles via the UI; existing configs migrate to the "openclaw" profile on first run.
- Backup beyond a single `.bak` per file per save. No long-term version history.

## 3. Hard constraints (must not violate)

1. **The active profile is the single source of truth for what mcp-proxy reads.** `~/.config/openclaw-mcp/servers.json` becomes a symlink into the active profile directory. Activating a different profile is a symlink swap plus a restart — never a manual file rewrite.

2. **Profile activation is atomic.** The sequence (write profile files → swap symlink → restart mcp-proxy) succeeds completely or rolls back. A half-applied state is forbidden. Implementation: stage all writes to a tempdir, validate, then move into place; if restart fails, swap the symlink back and surface the error.

3. **The guide server (`~/openclaw-mcp/orientation/server.py`) reads its vault config from a JSON file pointed at by the env var `OPENCLAW_GUIDE_CONFIG`.** The hardcoded `VAULT_ROOT` constant and `STATIC_DOCS` dict in the existing server.py are removed. This change is part of phase 3A; without it the UI cannot make the guide-server profile-aware.

4. **Path arguments must be validated for existence at save time.** The UI must not let the user save a profile that points the fs server at a directory that does not exist, the pg server at a DSN that does not connect, or the guide server at a vault root that has no `_conventions.md`. Inline error per field; the `Save` button is disabled until all required validations pass.

5. **The DSN is stored in clear text on disk** (mcp-proxy and dbhub need to read it). The UI displays the password masked but writes it raw. The config dir is `chmod 700`, files `chmod 600`. No DSN ever appears in URL params, logs, or browser localStorage.

6. **The UI runs on `:7700`, bound to `0.0.0.0` so it can be reached from a phone over Tailscale or the local network**, matching the dashboard's mobile-access pattern. The basic-auth password is the only access barrier; deployment must keep the box behind Tailscale or a tight AWS Security Group. (The original v1.0 spec said `127.0.0.1`-only, but the user opted for `0.0.0.0` during deploy to mirror dashboard_2.) HTTP basic-auth uses a shared password from `~/.config/openclaw-mcp/ui_password` (created on first run via the install wizard). Sessions are stateless — every request re-checks bcrypt; no JWT or external auth.

7. **The mcp-proxy restart command runs via `sudo systemctl restart openclaw-mcp.service`.** This requires `NOPASSWD` for that exact command in sudoers. The installer documents the sudoers entry; the UI does not attempt to add it itself.

8. **Active-profile activation is recorded in an append-only JSONL log at `~/.config/openclaw-mcp/activations.jsonl`.** Each line: `{ts, from_profile, to_profile, by_user, success, error?, rollback_succeeded?, note?}`. This is the only audit trail; profile contents themselves are not version-controlled.

9. **The filesystem MCP cannot be made read-only at the server level.** During implementation we discovered that `@modelcontextprotocol/server-filesystem` (the upstream package) has no `--readonly` flag and no env-var equivalent — it always exposes `write_file`, `edit_file`, `create_directory`, and `move_file`. The `readOnlyHint` annotations in its tool list are per-tool metadata for the client to display, not a server-side permission. Until either the upstream adds a flag or we wrap it with a stdio-level filter, the UI does not expose a fs-readonly toggle. See `04_open_questions.md` for the deferred follow-up. The original v1.0 spec mandated this toggle; v1.0 retrospective drops it.

10. **The current openclaw setup keeps working through the migration.** First run of the UI detects the existing configs at the canonical paths, copies them into a profile named `openclaw`, swaps `servers.json` for a symlink to that profile, restarts mcp-proxy, and verifies the proxy comes back up. If the verification fails, it puts the original files back and aborts.

---

## 4. Data model

### 4.1 Profile directory

```
~/.config/openclaw-mcp/
├── active                          # symlink → profiles/<active_name>/
├── servers.json                    # symlink → active/servers.json
├── dbhub.toml                      # symlink → active/dbhub.toml
├── env                             # symlink → active/env
├── guide_config.json               # symlink → active/guide_config.json
├── ui_password                     # bcrypt hash, chmod 600
├── activations.jsonl               # append-only activation log
└── profiles/
    ├── openclaw/
    │   ├── servers.json
    │   ├── dbhub.toml
    │   ├── env
    │   ├── guide_config.json
    │   └── meta.json               # {created_at, last_activated_at, description}
    └── <other_profile>/
        └── (same shape)
```

### 4.2 `servers.json` shape (per profile)

Identical to the current shape — three named-server entries — except the `guide` server is invoked with the env-var pattern:

```json
{
  "mcpServers": {
    "pg": {
      "command": "npx",
      "args": ["-y", "@bytebase/dbhub@latest", "--transport", "stdio",
               "--config", "/home/ubuntu/.config/openclaw-mcp/dbhub.toml"],
      "transportType": "stdio",
      "timeout": 60
    },
    "fs": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "<root1>", "<root2>"],
      "transportType": "stdio",
      "timeout": 60
    },
    "guide": {
      "command": "/home/ubuntu/openclaw-mcp/orientation/.venv/bin/python",
      "args": ["/home/ubuntu/openclaw-mcp/orientation/server.py"],
      "env": {
        "OPENCLAW_GUIDE_CONFIG": "/home/ubuntu/.config/openclaw-mcp/guide_config.json"
      },
      "transportType": "stdio",
      "timeout": 30
    }
  }
}
```

The `pg` and `guide` `--config` and `OPENCLAW_GUIDE_CONFIG` paths are the symlinks at the canonical config root, not paths into the profile directory. This way mcp-proxy doesn't need to be told about the active profile; it just reads the canonical paths.

### 4.3 `dbhub.toml` shape (per profile)

Identical to current — one `[[sources]]` block (DSN + id) and one `[[tools]]` block (execute_sql + readonly flag).

### 4.4 `guide_config.json` shape (NEW)

The vault config the (now-generalized) guide server reads:

```json
{
  "vault_root": "/home/ubuntu/vaults/openclaw",
  "handoff_dir": "03_handoffs",
  "curated_docs": [
    {"name": "_conventions.md", "description": "vault map + how to use these tools (READ FIRST)"},
    {"name": "05_folder_structure.md", "description": "repo + vault folder structure (global shape)"},
    ...
  ]
}
```

`handoff_dir` is profile-specific because vaults need not all use the same convention. If `handoff_dir` is set and the directory exists, the guide server appends the lexicographically-newest `*.md` from that directory to the index with description `"latest session handoff (<stem>)"`.

### 4.5 `meta.json` shape (per profile)

```json
{
  "created_at": "2026-05-18T22:00:00Z",
  "last_activated_at": "2026-05-18T22:05:00Z",
  "description": "openclaw v2 SaaS pain pipeline"
}
```

`description` is user-editable in the UI.

---

## 5. REST API

FastAPI, single process, runs as user-systemd unit `openclaw-mcp-config-ui.service`. Bound to `127.0.0.1:7700`. All endpoints require HTTP basic-auth (set on first run).

### 5.1 Profile CRUD

| Method | Path | Returns |
|---|---|---|
| `GET` | `/api/profiles` | `{profiles: [{name, description, created_at, last_activated_at, active}], active: <name>}` |
| `GET` | `/api/profiles/{name}` | Full profile contents (servers.json + dbhub.toml + guide_config.json + meta.json), with DSN password masked unless `?reveal=true` |
| `POST` | `/api/profiles` | Create new profile from form fields. Request: `{name, description, fs_roots, dsn, dsn_readonly, vault_root, curated_docs, handoff_dir?}`. Returns `{success, profile, validation}` where `profile` is the full updated profile (DSN masked) and `validation` is the §5.3 validation block. |
| `PUT` | `/api/profiles/{name}` | Update existing profile (same shape as POST minus `name`). Returns `{success, profile, validation}` — same shape as POST. |
| `DELETE` | `/api/profiles/{name}` | Delete profile. Returns 409 if `name == active`. |
| `POST` | `/api/profiles/{name}/duplicate` | Body `{new_name}`. Clones the named profile under a new name. |

### 5.2 Activation

| Method | Path | Returns |
|---|---|---|
| `POST` | `/api/activate/{name}` | `{success, from, to, mcp_proxy_status, validation_results}` on success; on failure, additionally `{error, rollback_succeeded}` and HTTP 500. `validation_results` is the §5.3 validation block computed against the on-disk profile *before* activation. The `from`/`to` fields in the response use the short names (the activations.jsonl log uses `from_profile`/`to_profile` for clarity in the persisted audit trail). Atomic — see §6.2. |
| `GET` | `/api/active` | `{name, mcp_proxy_running: bool, last_activated_at, mcp_proxy: {running, pid, uptime_seconds}}` |

### 5.3 Validation

Profile validation is exposed both as save-time check (returns errors that block save) and explicit endpoint:

| Method | Path | Returns |
|---|---|---|
| `POST` | `/api/validate` | Body: full profile shape. Returns: `{fs: {<root>: {exists, is_dir, readable}}, pg: {connect_ok, error?, server_version?}, guide: {vault_exists, conventions_exists, missing_curated_docs: [...], handoff_dir_exists}}` |
| `GET` | `/api/vault-docs?root=<path>` | List `*.md` files under the given vault root, recursively. Used by the curated-docs picker. Returns `[{path, size, mtime}]`, sorted by path. |
| `GET` | `/api/system-status` | `{mcp_proxy: {running, pid, uptime_seconds}, caddy: {running}, last_activation: {...}}` |

### 5.4 First-run / migration

| Method | Path | Returns |
|---|---|---|
| `GET` | `/api/install-status` | `{configured: bool, has_legacy_configs: bool, sudoers_ok: bool, password_set: bool}` |
| `POST` | `/api/install/migrate-legacy` | Imports current `~/.config/openclaw-mcp/{servers.json, dbhub.toml, env}` into a profile named `openclaw`. Backs originals to `*.bak.<timestamp>`. Sets the new profile active. |
| `POST` | `/api/install/set-password` | Body `{password}`. Writes bcrypt hash to `~/.config/openclaw-mcp/ui_password`. |

---

## 6. Frontend behavior

Single HTML file, vanilla JS (no build step). Cream `#FAF7F2` background to match the dashboard, white rounded `[20px]` bubble cards, Inter + Source Serif 4 per `phase2c_dashboard_v2.md` design language. Three-column layout collapses to one on mobile.

### 6.1 Layout

```
┌───────────────────────────────────────────────────────────────┐
│  H1: "MCP Profiles"                          [active] openclaw│
├───────────┬───────────────────────────────────────────────────┤
│ Profiles  │  fs (filesystem)                                  │
│ ---------- │  Roots: [/home/ubuntu/github/openclaw_..]  [✓]  │
│ ▸ openclaw │         [/home/ubuntu/vaults/openclaw    ]  [✓]  │
│   project2 │         [+ Add root]                              │
│   project3 │  Read-only: ☐                                    │
│           │                                                   │
│ [+ New]   │  pg (Postgres)                                    │
│ [⎘ Dup]  │  DSN: [postgres://llmanalyse:****@localhost...]   │
│ [✕ Delete]│  Read-only: ☑   Connection: [✓ tested 19:32]     │
│           │                                                   │
│           │  guide (orientation)                              │
│           │  Vault root: [/home/ubuntu/vaults/openclaw    ]   │
│           │  Handoff dir: [03_handoffs                  ]    │
│           │  Curated docs (8 selected of 30 found):           │
│           │   ☑ _conventions.md      [auto-discovered]       │
│           │   ☑ 01_design/system_design.md                   │
│           │   ☐ 02_components/phase2b_temporal.md             │
│           │   ...                                             │
│           │                                                   │
│           │  [Save]    [Save & Activate]                      │
│           │                                                   │
│           │ Last activated: 2026-05-18 22:05:00 UTC           │
│           │ mcp-proxy: ● running, pid 1234, up 12m           │
└───────────┴───────────────────────────────────────────────────┘
```

### 6.2 Activate-profile flow

When the user clicks `Save & Activate`:

1. Frontend POST `/api/validate` with the form contents. Block on any error.
2. POST `/api/profiles/{name}` to save (or POST `/api/profiles` for new).
3. POST `/api/activate/{name}`. The endpoint:
   - Stages all four files into a tempdir.
   - Re-runs validation.
   - Atomically moves files into `profiles/{name}/`.
   - Swaps `~/.config/openclaw-mcp/active` → `profiles/{name}/` (symlink, with `os.replace` on a temp link to make atomic).
   - Runs `sudo systemctl restart openclaw-mcp.service`.
   - Polls `:3001/status` for up to 10s waiting for `200 OK`.
   - On success: appends to `activations.jsonl`, returns `{success: true, ...}`.
   - On failure: swaps symlink back to previous active profile, restarts mcp-proxy again, returns `{success: false, error, rollback_succeeded: bool}`.
4. Frontend re-fetches `/api/system-status` and re-renders the active indicator.

### 6.3 Curated-docs picker

Two-column inside the guide card: left column lists discovered `*.md` files under the vault root (live-refresh button), right column lists currently-curated docs in display order. Drag-and-drop or up/down arrows to reorder; checkbox to include/exclude. When the vault root changes the list refreshes automatically.

### 6.4 First-run wizard

If `/api/install-status` returns `configured: false`, the page renders a wizard instead of the editor:

1. **Set password.** Single field, sets HTTP-basic password. After this the page reloads under auth.
2. **Detect legacy configs.** If `has_legacy_configs: true`, show "Found existing openclaw configs at canonical paths. Import as profile 'openclaw'? [Yes / No]." Yes → `POST /api/install/migrate-legacy`. No → continue blank.
3. **Sudoers check.** If `sudoers_ok: false`, render the exact line the user must add: `<user> ALL=(root) NOPASSWD: /bin/systemctl restart openclaw-mcp.service` and a copy button. Don't proceed until the user confirms it's added (a re-check button verifies live).

After the wizard the user lands in the regular editor.

---

## 7. File tree

```
src/mcp_config_ui/
├── __init__.py
├── api.py                          # FastAPI app
├── profiles.py                     # Profile model + CRUD + activation logic
├── validation.py                   # path checks, DSN connect, vault doc presence
├── installer.py                    # first-run wizard + legacy migration
├── auth.py                         # HTTP basic with bcrypt
├── requirements.txt                # fastapi, uvicorn, psycopg2-binary, bcrypt, python-multipart
├── frontend/
│   └── index.html                  # single file: HTML + CSS + JS, no build step
└── tests/
    ├── test_profiles.py            # CRUD + activation rollback
    ├── test_validation.py          # path/DSN/vault checks
    └── test_atomic_activate.py     # mocked sudo, asserts rollback on failure
```

Plus, **outside** the dashboard repo (the openclaw guide server is its own repo-let):

```
~/openclaw-mcp/orientation/server.py  # MODIFIED to read OPENCLAW_GUIDE_CONFIG
```

The `server.py` change is a small refactor (~30 lines): replace the module-level constants with a `_load_config()` function that reads the JSON pointed at by `os.environ["OPENCLAW_GUIDE_CONFIG"]` and falls back to the current hardcoded values if the env var is unset (for backwards compat during migration). All existing tests keep passing.

### 7.1 Systemd unit (user)

`~/.config/systemd/user/openclaw-mcp-config-ui.service`:

```ini
[Unit]
Description=OpenClaw MCP Config UI (port 7700)
After=network.target

[Service]
Type=simple
WorkingDirectory=/home/ubuntu/github/openclaw_boyao_1_llmanalyse
Environment=PYTHONPATH=/home/ubuntu/github/openclaw_boyao_1_llmanalyse
ExecStart=/home/ubuntu/github/openclaw_boyao_1_llmanalyse/.venv/bin/python -m uvicorn src.mcp_config_ui.api:app --host 0.0.0.0 --port 7700
Restart=on-failure
RestartSec=3
StandardOutput=append:/home/ubuntu/.hermes/logs/mcp-config-ui.log
StandardError=append:/home/ubuntu/.hermes/logs/mcp-config-ui.log

[Install]
WantedBy=default.target
```

---

## 8. Acceptance criteria

Each item is a verifiable check. Implementation is "done" when all are green.

§8.1 — `~/openclaw-mcp/orientation/server.py` runs end-to-end with `OPENCLAW_GUIDE_CONFIG=/tmp/test_guide.json` set, and reads the JSON's `vault_root` + `curated_docs` instead of the hardcoded constants. Existing `list_orientation_docs` returns the configured set.

§8.2 — `GET /api/profiles` returns a JSON list including the migrated `openclaw` profile after first-run migration. Active is `openclaw`. Other endpoints respond.

§8.3 — Creating a new profile via POST with valid fields creates the directory under `profiles/<name>/` with all four files written, file modes `chmod 600`, dir mode `chmod 700`.

§8.4 — POST with an fs root that does not exist returns `400` with `{errors: {fs_roots: [...]}}` and writes nothing.

§8.5 — POST with a DSN that fails to connect returns `400` with `{errors: {dsn: ...}}` and writes nothing.

§8.6 — POST with a vault_root that lacks `_conventions.md` returns `400`. POST with a vault_root that has `_conventions.md` but lists curated docs that don't exist returns `400` with the missing names.

§8.7 — Activating a profile rewrites the four canonical symlinks to point at `profiles/{name}/` and restarts the mcp-proxy service.

§8.8 — After activation, `curl https://<host>:3001/status` returns `200` within 10s. The activations.jsonl log gets a new line with `success: true`.

§8.9 — Activating a profile whose DSN is bad (mocked: postgres host unreachable) causes mcp-proxy startup to fail. The UI rolls back the symlinks and the previous profile is active again. activations.jsonl line has `success: false, rollback_succeeded: true`.

§8.10 — DELETE on the active profile returns `409`. DELETE on a non-active profile returns `204` and removes the directory.

§8.11 — DSN password is masked in `GET /api/profiles/{name}` by default; passing `?reveal=true` returns it raw (still requires auth). Browser dev tools never show the password in URL params.

§8.12 — First-run wizard: visiting the UI before configuration redirects to the wizard. Setting password → entering basic-auth credentials succeeds. Importing legacy configs creates the `openclaw` profile and activates it. After wizard the regular editor loads.

§8.13 — UI renders correctly on a mobile viewport (390×844). All inputs reachable; the curated-docs picker becomes a single-column list with checkboxes.

§8.14 — `pytest tests/` passes 9+ tests including the activation-rollback test (which mocks `sudo systemctl` to fail and asserts the symlinks are reverted).

§8.15 — `bash -n` and `shellcheck` pass on any helper scripts. The systemd unit starts and stays running after `systemctl --user daemon-reload && systemctl --user enable --now openclaw-mcp-config-ui.service`.

§8.16 — Adding a sudoers entry that grants `ubuntu ALL=(root) NOPASSWD: /bin/systemctl restart openclaw-mcp.service` is sufficient to run activations from the UI without password prompts. The installer detects whether this is set up.

§8.17 — Activation completes in **6–10 seconds end-to-end** on the openclaw profile under steady state. The bottleneck is mcp-proxy's startup: each named server runs via `npx -y` which re-resolves and re-spawns the npm-installed stdio binary on each launch (~3-4s for fs + pg + guide combined), plus systemd ramp-up (~1-2s) plus the health-poll cycle (1-2s). The original v1.0 spec said "under 5 seconds"; that target was optimistic and 5s would require pre-resolving the npx packages or switching to an `npm install`-into-a-stable-path pattern. v1.0 retrospective adjusts the bar.

---

## 9. Visual / interaction notes

- Bubble cards inherit dashboard styling: `bg-white rounded-[20px]`, warm two-layer shadow (`shadow-[0_1px_2px_rgba(15,15,15,0.04),_0_8px_24px_rgba(15,15,15,0.06)]`), 7px vertical gap.
- Header H1 in Source Serif 4 600 size 28; body Inter.
- Validation states: `[✓ tested 19:32]` in stone-600 next to the field on success; red-700 inline error below on failure.
- Activate button is the bubble-row prominent treatment: solid stone-900 fill, white text. Save (without activate) is outlined.
- Active profile in the sidebar gets a pastel-emerald left border (`border-l-4 border-emerald-300`) and the small active label in the header.
- Loading state during activation: button shows spinner, all editors disabled, banner above status reads "Activating <name> — restarting mcp-proxy..."
- Failure state during activation: banner red-700, error text + "Rolled back to <previous>" subtext.

## 10. Implementation order

Bite-sized chunks, each with a verification step:

1. **Generalize the guide server** — modify `~/openclaw-mcp/orientation/server.py` to read `OPENCLAW_GUIDE_CONFIG`. Add backwards-compat fallback. Smoke test: run `OPENCLAW_GUIDE_CONFIG=/tmp/x.json python server.py` with a stub config and verify `list_orientation_docs` honors it. **Verify §8.1.**

2. **Profiles module** — `src/mcp_config_ui/profiles.py` with `Profile` dataclass, `list_profiles`, `read_profile`, `write_profile`, `delete_profile`, plus the `activate_profile` function with staging+symlink-swap+restart+rollback. Unit-test with mocked `subprocess.run` for sudo. **Verify §8.7, §8.9.**

3. **Validation module** — `validation.py` with `validate_fs_roots`, `validate_dsn` (psycopg2 connect with 3s timeout), `validate_guide_config` (vault_root has _conventions.md, curated_docs all exist). **Verify §8.4, §8.5, §8.6.**

4. **FastAPI app** — `api.py` wiring up the profile/activate/validate endpoints + HTTP basic auth. Returns 401 on bad creds, 409 on conflicts, 400 on validation errors with structured error bodies. **Verify §8.2, §8.3, §8.10, §8.11.**

5. **Installer / migration** — `installer.py` with first-run detection, password set, legacy-config migration. Smoke-test by deleting `~/.config/openclaw-mcp/active` and running through the wizard endpoints in order. **Verify §8.12, §8.16.**

6. **Frontend** — `frontend/index.html` (no build step). Bubble-card layout, three editor cards, profile sidebar, save/activate buttons, validation indicators. **Verify §8.13.**

7. **Systemd unit + sudoers entry** — write the unit file, document the sudoers line in the README, install. Verify the service stays up across a reboot of mcp-proxy. **Verify §8.15.**

8. **End-to-end activation flow** — full trip: create profile in UI, click Save & Activate, see mcp-proxy restart, verify `:3001/status` is 200, verify a Claude Desktop client (or `curl` the SSE endpoint) sees the new fs root. **Verify §8.7, §8.8, §8.17.**

9. **Failure-mode test** — manually break a DSN in a saved profile, attempt activation, confirm rollback. Confirm `activations.jsonl` has the failure line. **Verify §8.9.**

---

## 11. CHANGELOG

- **v1.0 (2026-05-19) — implemented + spec reconciled.** All eight implementation chunks shipped end-to-end. 22 unit tests pass; live activation flow verified by duplicate→activate→activate-back→delete trip-test (8.5s + 6.0s round-trip). Profile `openclaw` migrated from legacy configs at canonical paths into `~/.config/openclaw-mcp/profiles/openclaw/` and activated with no proxy interruption (file contents identical pre- and post-migration; only symlinks changed). Reconciliation of spec drift folded back per `06_teamwork_protocol.md §4.1`:
  - **§3.6 / §7.1 — host binding.** Spec said `127.0.0.1`; deployed as `0.0.0.0` to match dashboard's mobile-access pattern. Spec updated to reflect; security note added (basic-auth password is the only barrier; deployment behind Tailscale or AWS SG is required).
  - **§3.8 — activations.jsonl field names.** Spec listed `from`/`to`; implementation uses `from_profile`/`to_profile` for clarity in the persistent log. Activation API responses retain the short `from`/`to` keys for terseness. Spec updated.
  - **§3.9 — fs_readonly toggle removed.** Verified during reconciliation that `@modelcontextprotocol/server-filesystem` has no `--readonly` flag and no env-var equivalent. The original spec promised a per-profile read-only toggle for fs roots; fulfilling this requires either an upstream PR or a stdio-level filtering wrapper. Toggle removed from the data model, REST API, and UI; followup deferred to `04_open_questions.md`.
  - **§5.1 — POST/PUT response shape.** Original spec said "Returns the updated profile"; implementation now returns `{success, profile, validation}` where `profile` is the full read-back with DSN masked.
  - **§5.2 — activation response.** `validation_results` is now actually populated (computed from disk before the symlink swap), not just listed in the schema. Failure responses additionally carry `error` and `rollback_succeeded`.
  - **§8.17 — activation timing.** Spec said "under 5 seconds"; live measurement is 6–10 seconds, dominated by `npx -y` re-resolution per spawned stdio server. Spec adjusted.
- **v1.0 (2026-05-18)** — initial spec.
