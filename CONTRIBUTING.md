# Contributing to OpenCode Usage

Thanks for considering a contribution! This project is intentionally small and strict — a few rules keep it that way.

## The three hard rules

1. **Zero dependencies.** Python standard library only on the server, vanilla JS/SVG/CSS on the frontend. No pip packages, no npm, no CDN scripts, no build step. If a feature truly needs a library, propose it in an issue first.
2. **Local only.** No outbound network requests of any kind — no telemetry, no update checks, no external fonts fetched at runtime beyond what already exists. Everything must work offline.
3. **Single file server.** All backend logic lives in `server.py`. Frontend HTML/CSS/JS lives in the embedded `PAGE` string. If the file grows unwieldy, discuss splitting strategy in an issue before doing it.

## Ways to contribute

- **Bug reports** — wrong numbers, crashes, OS-specific path issues
- **Schema support** — OpenCode's database format may evolve; keeping compatibility alive is high value
- **Portability** — Windows/macOS/Linux edge cases (paths, encodings, ports)
- **Frontend polish** — accessibility, responsive behavior, chart refinements
- **Docs** — clearer setup steps, screenshots, translations

## Development setup

```bash
git clone https://github.com/TirupMehta/opencode-usage.git
cd opencode-usage
python server.py        # or python3
# open http://localhost:8787
```

There is no test suite yet. Before opening a PR, verify manually:

- [ ] Dashboard loads with a real `opencode.db`
- [ ] Dashboard loads gracefully with **no** database found (friendly error, no traceback)
- [ ] All four ranges render (Today / 7D / 30D / All)
- [ ] CSV export downloads and re-imports cleanly through the import panel
- [ ] Keyboard shortcuts still work (`R`, `1–4`, `A`, `C`)

## Code style

**Python**

- Standard library only, target 3.8+
- No comments unless explaining *why*; prefer self-explanatory names
- Keep SQL read-only (`mode=ro` fallback); never write to the user's database

**JavaScript / CSS**

- No frameworks, no transpilation, ES2017+ is fine
- Keep animations subtle; respect `prefers-reduced-motion`
- Colors come from CSS variables in `:root` — reuse them instead of hardcoding

## Commits & PRs

- Short imperative subject: `Fix tooltip grid alignment`, `Add peak-hour detection`
- One logical change per PR; include before/after behavior in the description
- Screenshots or short recordings for visual changes are appreciated

## Reporting bugs

Open an issue and include:

1. OS + Python version
2. Whether the DB was auto-detected or via `OPENCODE_DB` (paste the startup log lines)
3. What you expected vs what happened
4. For wrong-number bugs: the relevant CSV export attached (it contains no message content)

## Feature ideas worth exploring

- Dark/light theme toggle
- Optional date-range picker beyond the four presets
- Session detail drill-down from the leaderboard
- Schema adapters if OpenCode changes its storage format

If you want to tackle one of these, comment on or open an issue first so we don't duplicate work.
