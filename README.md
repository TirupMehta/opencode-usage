# OpenCode Usage

A zero-dependency, 100% local dashboard that visualizes your [OpenCode](https://opencode.ai) token usage — input, output, reasoning, cache reads, sessions, hourly activity and more, straight from your local `opencode.db`.

No accounts. No API calls. Nothing leaves your machine.

## Quick start

Requires Python 3.8+ (standard library only — nothing to install).

```bash
# Windows
python server.py

# macOS / Linux
python3 server.py
```

Then open **http://localhost:8787**.

The database is detected automatically at:

| Platform | Path |
|---|---|
| Linux | `~/.local/share/opencode/opencode.db` |
| macOS | `~/.local/share/opencode/opencode.db` |
| Windows | `%USERPROFILE%\.local\share\opencode\opencode.db` |

### Custom database location

If your database lives somewhere else, point `OPENCODE_DB` at it:

```bash
# Windows (PowerShell)
$env:OPENCODE_DB = "D:\path\to\opencode.db"; python server.py

# macOS / Linux
OPENCODE_DB=/path/to/opencode.db python3 server.py
```

## Features

- **Live KPI strip** — total tokens, prompt, generated, cache reads, replies, peak day/hour, period-over-period delta
- **Multi-series activity chart** — input / output / reasoning as separate organic lines with clickable legend toggles, hover crosshair and full breakdown tooltips
- **Hourly distribution** — today's usage by hour with peak-hour highlighting
- **Token mix donut** — share by token type (cache excluded)
- **Session leaderboard** — top 12 chats ranked by tokens, with per-project chips and share bars
- **Model breakdown** — token split per model
- **CSV export**, auto-refresh (30 s), keyboard-driven UI
- Reads from a snapshot copy of the SQLite database, so it never locks the file while OpenCode is running

## Keyboard shortcuts

| Key | Action |
|---|---|
| `R` | Refresh |
| `1` `2` `3` `4` | Today / 7D / 30D / All time |
| `A` | Toggle auto-refresh |
| `C` | Export CSV |

## Privacy

Everything runs on `127.0.0.1`. The dashboard only reads your local SQLite database and serves one page to your browser. No telemetry, no network requests beyond your own machine.

## How it works

OpenCode records every assistant message (with token accounting) in a local SQLite database. The server copies that file (including WAL/SHM) to a temporary snapshot on each request, queries it read-only, and serves aggregated JSON to a dependency-free single-page dashboard.

## License

[MIT](LICENSE)
