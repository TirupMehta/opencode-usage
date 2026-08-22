<div align="center">

# OpenCode Usage

**A zero-dependency, 100% local dashboard for your [OpenCode](https://opencode.ai) token usage**

Input · Output · Reasoning · Cache · Sessions · Hourly activity — visualized straight from your local `opencode.db`

[![Python](https://img.shields.io/badge/python-3.8%2B-blue?logo=python&logoColor=white)](https://www.python.org)
[![Dependencies](https://img.shields.io/badge/dependencies-zero-success)](server.py)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-ff6d00.svg)](CONTRIBUTING.md)

</div>

---

No accounts. No telemetry. Nothing leaves your machine.
One Python file serves an animated analytics dashboard to your browser by reading the same SQLite database OpenCode already writes to.

## Highlights

| | |
| **Live KPI strip** | Total tokens, prompt, generated, cache reads, replies, peak day/hour, period-over-period delta |
| **Multi-series chart** | Input / output / reasoning as separate organic lines — clickable legend toggles, hover crosshair, full breakdown tooltips |
| **Hourly distribution** | Today's usage by hour with peak-hour highlight and live "now" marker |
| **Token mix donut** | Share by token type, cache excluded |
| **Session leaderboard** | Top 12 chats ranked by tokens, per-project chips, share bars |
| **Daily breakdown table** | Every period expandable — cache writes, share of period, sessions active, avg tokens/reply |
| **CSV export & import** | Detailed 11-column exports; drop any exported file back in to inspect it offline |
| **Keyboard-first** | `R` refresh · `1–4` ranges · `A` auto-refresh · `C` export |

## Quick start

Requires [Python 3.8+](https://www.python.org). Standard library only — nothing to install.

```bash
# Windows
python server.py

# macOS / Linux
python3 server.py
```

Open **http://localhost:8787**. Done.

The database is found automatically:

| Platform | Default path |
|---|---|
| Linux | `~/.local/share/opencode/opencode.db` |
| macOS | `~/.local/share/opencode/opencode.db` |
| Windows | `%USERPROFILE%\.local\share\opencode\opencode.db` |

<details>
<summary><b>Custom database location</b></summary>

```bash
# Windows (PowerShell)
$env:OPENCODE_DB = "D:\path\to\opencode.db"; python server.py

# macOS / Linux
OPENCODE_DB=/path/to/opencode.db python3 server.py
```
</details>

## Privacy

Everything binds to `127.0.0.1`. The server reads your local SQLite file through a temporary snapshot (so it never locks while OpenCode is running) and serves one page to your browser. There is no outbound network code at all — CSV imports are parsed in your browser via `FileReader`.

## How it works

```
OpenCode writes ──► opencode.db (SQLite, WAL mode)
                          │  snapshot copy on each request
                          ▼
              server.py (stdlib http.server)
                          │  aggregated JSON  /api/stats
                          ▼
              Single-page dashboard (vanilla JS + SVG)
```

Every assistant message OpenCode records carries token accounting (`input`, `output`, `reasoning`, cache). The dashboard snapshots the database, aggregates per bucket, and renders everything client-side — no chart libraries, no build step.

## Troubleshooting

<details>
<summary><b>"Could not locate opencode.db automatically"</b></summary>

Set the `OPENCODE_DB` environment variable to the full path of your database file, then restart. The startup log prints exactly which path is being used.
</details>

<details>
<summary><b>Port already in use</b></summary>

The server scans ports 8787–8797 automatically and uses the first free one — check the console output for the actual URL.
</details>

<details>
<summary><b>Numbers look different from my provider's dashboard</b></summary>

This dashboard reads what OpenCode recorded locally, including cached context replays that providers often bill differently. Cache reads are always shown separately from active tokens.
</details>

## Contributing

Contributions are welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) first — the short version: keep it standard-library-only, keep it local-only, and match the existing single-file style.

## License

[MIT](LICENSE) © Tirup Mehta
