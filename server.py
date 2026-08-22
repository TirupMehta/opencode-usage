import json
import sqlite3
import shutil
import tempfile
import datetime
import time
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

SNAP_DIR = os.path.join(tempfile.gettempdir(), "ocdash-snap")


class DatabaseNotFound(Exception):
    pass


def find_db():
    override = os.environ.get("OPENCODE_DB")
    if override:
        p = os.path.expanduser(override)
        if os.path.isfile(p):
            return p
        raise DatabaseNotFound(
            "OPENCODE_DB is set to '%s' but the file does not exist." % override
        )
    home = os.path.expanduser("~")
    candidates = []
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        candidates.append(os.path.join(xdg, "opencode", "opencode.db"))
    candidates += [
        os.path.join(home, ".local", "share", "opencode", "opencode.db"),
        os.path.join(home, "AppData", "Local", "opencode", "opencode.db"),
        os.path.join(home, "Library", "Application Support", "opencode", "opencode.db"),
        os.path.join(home, ".config", "opencode", "opencode.db"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    raise DatabaseNotFound(
        "Could not locate opencode.db automatically. "
        "Set the OPENCODE_DB environment variable to its full path and restart."
    )


def db_display(path):
    home = os.path.expanduser("~")
    if path.startswith(home):
        return "~" + path[len(home):]
    return path


DB_PATH = None


def connect_snapshot():
    global DB_PATH
    if DB_PATH is None:
        DB_PATH = find_db()
    os.makedirs(SNAP_DIR, exist_ok=True)
    copied = False
    for suffix in ["", "-wal", "-shm"]:
        src = DB_PATH + suffix
        dst = os.path.join(SNAP_DIR, "snap.db" + suffix)
        if os.path.exists(src):
            try:
                shutil.copy2(src, dst)
                copied = True
            except Exception:
                pass
    snap_main = os.path.join(SNAP_DIR, "snap.db")
    if copied and os.path.exists(snap_main):
        try:
            con = sqlite3.connect(snap_main)
            con.execute("SELECT 1")
            return con
        except Exception:
            pass
    return sqlite3.connect("file:" + DB_PATH.replace("\\", "/") + "?mode=ro", uri=True)


def q(con, sql, args=()):
    cur = con.execute(sql, args)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def empty_acc():
    return {"input": 0, "output": 0, "reasoning": 0, "cache_read": 0, "cache_write": 0, "messages": 0}


def add_tokens(acc, d):
    t = d.get("tokens") or {}
    c = t.get("cache") or {}
    acc["input"] += t.get("input", 0) or 0
    acc["output"] += t.get("output", 0) or 0
    acc["reasoning"] += t.get("reasoning", 0) or 0
    acc["cache_read"] += c.get("read", 0) or 0
    acc["cache_write"] += c.get("write", 0) or 0
    acc["messages"] += 1


def collect(window_days, now):
    con = connect_snapshot()
    try:
        if window_days == "all":
            start_ms = 0
        else:
            start = now - datetime.timedelta(days=window_days)
            start_ms = int(start.timestamp() * 1000)

        rows = q(
            con,
            """SELECT m.session_id, m.time_created, m.data AS data,
                      s.title AS title, s.directory AS directory
               FROM message m LEFT JOIN session s ON s.id = m.session_id
               WHERE m.time_created >= ?""",
            (start_ms,),
        )

        totals = empty_acc()
        daily = {}
        hourly = [empty_acc() for _ in range(24)]
        models = {}
        sessions = {}

        today = now.date()
        for row in rows:
            try:
                d = json.loads(row["data"])
            except Exception:
                continue
            if d.get("role") != "assistant":
                continue

            t = d.get("tokens") or {}
            add_tokens(totals, d)

            dt = datetime.datetime.fromtimestamp(row["time_created"] / 1000)
            if window_days == "all" or window_days > 120:
                key = dt.strftime("%Y-%m")
                label = dt.strftime("%b %Y")
            else:
                key = dt.strftime("%Y-%m-%d")
                label = dt.strftime("%d %b")

            acc = daily.setdefault(key, {"label": label, **empty_acc()})
            add_tokens(acc, d)

            if dt.date() == today:
                add_tokens(hourly[dt.hour], d)

            mid = d.get("modelID") or "unknown"
            m = models.setdefault(mid, {"model": mid, **empty_acc()})
            add_tokens(m, d)

            sid = row["session_id"]
            s = sessions.setdefault(sid, {
                "id": sid,
                "title": row["title"] or "Untitled session",
                "directory": row["directory"] or "",
                "tokens": 0,
                "messages": 0,
                "last": 0,
            })
            tt = (t.get("input", 0) or 0) + (t.get("output", 0) or 0) + (t.get("reasoning", 0) or 0)
            s["tokens"] += tt
            s["messages"] += 1
            s["last"] = max(s["last"], row["time_created"])

        first = q(con, "SELECT MIN(time_created) AS t FROM message")
        first_ts = first[0]["t"] if first and first[0]["t"] else None

        model_list = sorted(models.values(), key=lambda x: -(x["input"] + x["output"] + x["reasoning"]))
        session_list = sorted(sessions.values(), key=lambda x: -x["tokens"])[:12]

        if window_days == "all" or window_days > 120:
            step = "month"
            keys = sorted(daily.keys())
            filled = []
            if keys:
                fy, fm = int(keys[0][:4]), int(keys[0][5:7])
                ly, lm = int(keys[-1][:4]), int(keys[-1][5:7])
                y, mo = fy, fm
                while (y, mo) <= (ly, lm):
                    k = f"{y:04d}-{mo:02d}"
                    base = daily.get(k, {"label": datetime.date(y, mo, 1).strftime("%b %Y"), **empty_acc()})
                    filled.append({"key": k, **base})
                    mo += 1
                    if mo > 12:
                        mo = 1
                        y += 1
        else:
            step = "day"
            filled = []
            for i in range(window_days, -1, -1):
                day = now.date() - datetime.timedelta(days=i)
                k = day.strftime("%Y-%m-%d")
                base = daily.get(k, {"label": day.strftime("%d %b"), **empty_acc()})
                filled.append({"key": k, **base})

        return {
            "range": window_days,
            "generated_at": int(now.timestamp() * 1000),
            "db_path": DB_PATH,
            "db_display": db_display(DB_PATH),
            "totals": totals,
            "daily": filled,
            "step": step,
            "hourly_today": [{"hour": h, **acc} for h, acc in enumerate(hourly)],
            "models": model_list,
            "sessions": session_list,
            "first_seen": first_ts,
        }
    finally:
        con.close()


def build_payload(raw_days):
    t0 = time.perf_counter()
    now = datetime.datetime.now()
    wn = "all" if raw_days == "all" else max(1, min(3650, int(raw_days)))
    payload = collect(wn, now)
    payload["all_time"] = collect("all", now)["totals"]
    payload["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    payload["msgs_scanned"] = payload["totals"]["messages"]
    return payload


PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="color-scheme" content="dark">
<title>OpenCode · Usage</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'><rect width='24' height='24' rx='6' fill='%23111'/><path fill='%234c8df8' d='M13.5 4 6 14h5l-.8 6L18 10h-5l.5-6z'/></svg>">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --bg:#0a0a0c;
  --panel:#101013;
  --panel2:#15151a;
  --line:rgba(255,255,255,.065);
  --line2:rgba(255,255,255,.115);
  --txt:#ececef;
  --mut:#b4b4bf;
  --dim:#7f7f8a;
  --faint:#62626c;
  --accent:#4c8df8;
  --accent-dim:rgba(76,141,248,.14);
  --green:#3fb950;
  --red:#f85149;
  --c-in:#5b9cf8;
  --c-out:#2fbfa4;
  --c-re:#93a0b4;
  --ease:cubic-bezier(.16,1,.3,1);
}
html{color-scheme:dark}
body{
  font-family:'Inter',system-ui,-apple-system,'Segoe UI',sans-serif;
  background:var(--bg);color:var(--txt);min-height:100vh;
  letter-spacing:-.006em;
  font-variant-numeric:tabular-nums;-webkit-font-smoothing:antialiased;
  padding-bottom:46px;
}
::selection{background:rgba(76,141,248,.28)}
button{font-family:inherit}
:focus{outline:none}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:4px}
#lbar{position:fixed;top:0;left:-40%;height:2px;width:40%;z-index:99;border-radius:99px;
  background:var(--accent);opacity:0;transition:opacity .3s}
body.loading #lbar{opacity:.85;animation:slide 1s ease-in-out infinite}
@keyframes slide{to{left:100%}}
.wrap{max-width:1210px;margin:0 auto;padding:0 28px}

header{
  position:sticky;top:0;z-index:40;display:flex;align-items:center;justify-content:space-between;
  gap:18px;padding:17px 28px;margin:0 -28px 20px;
  background:rgba(10,10,12,.85);backdrop-filter:blur(14px);
  border-bottom:1px solid var(--line);
}
.brand{display:flex;align-items:center;gap:12px;min-width:0}
.logo{
  font-family:'JetBrains Mono',monospace;font-size:13px;font-weight:600;color:var(--accent);
  width:36px;height:36px;border-radius:9px;background:var(--accent-dim);
  border:1px solid rgba(76,141,248,.25);
  display:grid;place-items:center;flex-shrink:0;
}
.brand h1{font-size:16.5px;font-weight:650;letter-spacing:-.01em;line-height:1.2;white-space:nowrap}
.brand .path{font-family:'JetBrains Mono',monospace;font-size:11.5px;color:var(--dim);white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis;max-width:300px;margin-top:2px}
.controls{display:flex;align-items:center;gap:18px}
.tabs{display:flex;position:relative}
.tab{
  padding:10px 14px;border:none;background:none;color:var(--dim);
  font-size:13.5px;font-weight:550;cursor:pointer;transition:color .18s;
}
.tab:hover{color:var(--mut)}
.tab.on{color:var(--txt)}
.tind{position:absolute;bottom:-1px;height:2px;background:var(--accent);border-radius:1px;
  transition:left .3s var(--ease),width .3s var(--ease)}
.toggle{display:flex;align-items:center;gap:7px;font-size:13px;color:var(--dim);cursor:pointer;user-select:none}
.toggle input{accent-color:var(--accent);cursor:pointer;width:12px;height:12px}
.btn{
  display:inline-flex;align-items:center;gap:7px;padding:8px 15px;border-radius:7px;
  font-size:13.5px;font-weight:550;cursor:pointer;transition:.15s;
}
.btn svg{width:12.5px;height:12.5px}
.btn.primary{background:#e8e8ec;color:#0a0a0c;border:1px solid #e8e8ec}
.btn.primary:hover{background:#fff}
.btn.primary:active{transform:scale(.97)}
.btn.ghost{background:transparent;border:1px solid var(--line);color:var(--mut)}
.btn.ghost:hover{border-color:var(--line2);color:var(--txt)}
.btn:disabled{opacity:.45;cursor:wait}
.spin{animation:rot .8s linear infinite}
@keyframes rot{to{transform:rotate(360deg)}}

main>*{animation:rise .55s var(--ease) both}
main>*:nth-child(2){animation-delay:.05s}
main>*:nth-child(3){animation-delay:.1s}
main>*:nth-child(4){animation-delay:.15s}
@keyframes rise{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}

.err{display:none;background:rgba(248,81,73,.06);border:1px solid rgba(248,81,73,.25);
  color:#fca5a5;padding:10px 15px;border-radius:7px;font-size:13px;margin-bottom:12px}

.strip{
  display:grid;grid-template-columns:1.45fr repeat(5,1fr);
  background:var(--panel);border:1px solid var(--line);border-radius:8px;
  overflow:hidden;
}
.stat{padding:18px 21px 17px;border-left:1px solid var(--line);min-width:0;transition:background .18s}
.stat:first-child{border-left:none}
.stat:hover{background:rgba(255,255,255,.018)}
.stat .lab{font-size:10.5px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:var(--mut);margin-bottom:11px;white-space:nowrap}
.stat .v{font-family:'JetBrains Mono',monospace;font-size:23px;font-weight:600;letter-spacing:-.03em;line-height:1;color:#fff}
.stat .sub2{font-size:11.5px;color:var(--dim);margin-top:9px;display:block;width:max-content;max-width:100%;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.spark{width:100%;height:22px;display:block;margin-top:11px}
.delta{
  display:inline-flex;align-items:center;gap:3.5px;margin-top:10px;
  font-family:'JetBrains Mono',monospace;font-size:11px;font-weight:500;
}
.delta.up{color:var(--green)}.delta.down{color:var(--red)}
.delta svg{width:8px;height:8px}
.delta em{font-style:normal;color:var(--faint);font-weight:450;margin-left:2px}

.panel{
  background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:19px 22px 17px;
  transition:border-color .2s;
}
.panel:hover{border-color:var(--line2)}
.phead{display:flex;justify-content:space-between;align-items:center;margin-bottom:17px;gap:12px;flex-wrap:wrap}
.pt h2{font-size:14px;font-weight:600;letter-spacing:-.005em}
.pt p{font-size:11.5px;color:var(--dim);margin-top:3px}
.legend{display:flex;gap:4px;align-items:center;flex-wrap:wrap}
.lg{
  display:inline-flex;align-items:center;gap:6px;padding:4px 9px;border-radius:5px;border:none;background:none;
  font-size:12px;font-weight:500;color:var(--mut);cursor:pointer;transition:.15s;
}
.lg:hover{background:rgba(255,255,255,.04)}
.lg.off{opacity:.38}
.lg.off i{background:transparent !important;border:1.5px solid currentColor}
.lg i{width:8px;height:8px;border-radius:2.5px;display:inline-block;transition:.15s}

.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:16px}
@media(max-width:960px){
  .grid2{grid-template-columns:1fr}
  .strip{grid-template-columns:repeat(2,1fr)}
  .stat:nth-child(odd){border-left:none}
  .stat:nth-child(n+2){border-top:1px solid var(--line)}
  .stat.total{grid-column:span 2}
}
@media(max-width:700px){
  header{flex-wrap:wrap;padding-bottom:8px}
  .brand .path{display:none}
  .controls{width:100%;justify-content:space-between;flex-wrap:wrap;gap:8px}
}

.chart-box{position:relative;width:100%}
svg text{fill:var(--dim);font-size:10.5px;font-family:'JetBrains Mono',monospace}
.empty{display:flex;align-items:center;justify-content:center;height:190px;color:var(--faint);font-size:13px}

.donut-zone{display:flex;align-items:center;gap:34px;justify-content:center;flex-wrap:wrap;padding:8px 0 6px}
.legend-v{display:flex;flex-direction:column;gap:2px;min-width:165px}
.lv{display:flex;align-items:center;gap:8px;font-size:12.5px;padding:4.5px 8px;margin:0 -8px;border-radius:6px;transition:background .15s}
.lv:hover{background:rgba(255,255,255,.03)}
.lv i{width:7px;height:7px;border-radius:2px;flex-shrink:0}
.lv .nm{color:var(--mut)}
.lv .pc{margin-left:auto;font-family:'JetBrains Mono',monospace;font-weight:600;color:var(--txt);font-size:12px}
.lv .vv{font-family:'JetBrains Mono',monospace;color:var(--faint);font-size:11px}

.mlist{margin-top:15px;padding-top:14px;border-top:1px solid var(--line)}
.mrow{display:flex;align-items:center;gap:11px;padding:6.5px 0}
.mname{flex:1;font-family:'JetBrains Mono',monospace;font-size:11.5px;color:var(--mut);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mbarw{width:100px;height:3px;border-radius:99px;background:rgba(255,255,255,.06);overflow:hidden;flex-shrink:0}
.mfill{height:100%;border-radius:99px;width:0;background:var(--accent);opacity:.75;transition:width 1.05s var(--ease)}
.mval{font-family:'JetBrains Mono',monospace;font-size:11.5px;color:var(--dim);width:62px;text-align:right;flex-shrink:0}

table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;font-size:10.5px;font-weight:600;letter-spacing:.09em;text-transform:uppercase;color:var(--dim);padding:0 10px 9px}
td{padding:10px;border-top:1px solid rgba(255,255,255,.04)}
tbody tr{transition:background .13s}
tbody tr:hover{background:rgba(255,255,255,.02)}
.ttl{max-width:340px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:500}
.sdir{font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--dim);max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
  background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.05);padding:1.5px 6px;border-radius:4px;display:inline-block}
.num{text-align:right;font-family:'JetBrains Mono',monospace;color:var(--mut);font-size:12.5px}
.rank{width:34px;color:var(--faint);font-family:'JetBrains Mono',monospace;font-size:11.5px}
tr:first-child .rank{color:var(--accent)}
.sharewrap{width:70px;height:3px;border-radius:99px;background:rgba(255,255,255,.06);overflow:hidden;flex-shrink:0}
.sharefill{height:100%;border-radius:99px;width:0;background:var(--accent);opacity:.55;transition:width .95s var(--ease)}

.statusbar{
  position:fixed;left:0;right:0;bottom:0;z-index:50;height:34px;
  display:flex;align-items:center;justify-content:space-between;gap:12px;
  padding:0 14px;background:rgba(13,13,16,.92);backdrop-filter:blur(10px);
  border-top:1px solid var(--line);
  font-family:'JetBrains Mono',monospace;font-size:11.5px;color:var(--mut);
}
.statusbar .grp{display:flex;align-items:center;gap:10px;min-width:0;overflow:hidden;white-space:nowrap}
.statusbar .dot{width:6px;height:6px;border-radius:50%;background:var(--green);opacity:.85;flex-shrink:0}
.statusbar b{color:#d2d2da;font-weight:500}
kbd{font-family:'JetBrains Mono',monospace;font-size:9.5px;background:rgba(255,255,255,.06);border:1px solid var(--line);
  border-radius:3px;padding:0 4px;color:var(--dim)}

.skel{position:relative;overflow:hidden;background:rgba(255,255,255,.04);border-radius:6px}
.skel::after{content:'';position:absolute;inset:0;transform:translateX(-100%);
  background:linear-gradient(90deg,transparent,rgba(255,255,255,.05),transparent);animation:shimmer 1.4s infinite}
@keyframes shimmer{to{transform:translateX(100%)}}

#tip{
  position:fixed;z-index:50;pointer-events:none;opacity:0;transition:opacity .1s,scale .1s;scale:.985;
  background:#16161b;border:1px solid var(--line2);border-radius:7px;
  padding:9px 12px;font-size:12px;box-shadow:0 6px 24px rgba(0,0,0,.5);
  transform:translate(-50%,calc(-100% - 12px));white-space:nowrap;
}
#tip.show{scale:1}
#tip b{font-weight:600}
#tip .tr{display:grid;grid-template-columns:10px 1fr max-content;gap:0 9px;align-items:center;margin-top:4px;color:var(--mut);min-width:150px}
#tip .tr i{width:8px;height:8px;border-radius:2.5px}
#tip .tr b{font-family:'JetBrains Mono',monospace;color:var(--txt);font-weight:500}
#tip .tot{border-top:1px solid var(--line);margin-top:7px;padding-top:6px}
#tip .tot span{color:var(--txt)}
::-webkit-scrollbar{width:8px;height:8px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:rgba(255,255,255,.09);border-radius:99px}
::-webkit-scrollbar-thumb:hover{background:rgba(255,255,255,.16)}
@media(prefers-reduced-motion:reduce){
  *,*::before,*::after{animation-duration:.01ms !important;transition-duration:.01ms !important}
}
</style>
</head>
<body>
<div id="lbar"></div>

<div class="wrap">
<header>
  <div class="brand">
    <div class="logo">&gt;_</div>
    <div>
      <h1>OpenCode Usage</h1>
      <div class="path" id="dbPath">locating opencode database…</div>
    </div>
  </div>
  <div class="controls">
    <nav class="tabs" id="tabs" role="tablist" aria-label="Time range">
      <button class="tab" data-days="1">Today</button>
      <button class="tab on" data-days="7">7D</button>
      <button class="tab" data-days="30">30D</button>
      <button class="tab" data-days="all">All time</button>
      <i class="tind" id="tind"></i>
    </nav>
    <label class="toggle"><input type="checkbox" id="auto">Auto-refresh</label>
    <button class="btn ghost" id="csv" aria-label="Export usage as CSV">Export CSV</button>
    <button class="btn primary" id="refresh" aria-label="Refresh data">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-2.64-6.36"/><polyline points="21 3 21 9 15 9"/></svg>
      Refresh
    </button>
  </div>
</header>

<div class="err" id="err"></div>

<main>
  <section class="strip" id="kpis">
    <div class="stat"><div class="lab">Loading</div><div class="skel" style="height:21px;width:70%"></div></div>
    <div class="stat"><div class="lab">Loading</div><div class="skel" style="height:21px;width:55%"></div></div>
    <div class="stat"><div class="lab">Loading</div><div class="skel" style="height:21px;width:62%"></div></div>
    <div class="stat"><div class="lab">Loading</div><div class="skel" style="height:21px;width:48%"></div></div>
    <div class="stat"><div class="lab">Loading</div><div class="skel" style="height:21px;width:52%"></div></div>
    <div class="stat"><div class="lab">Loading</div><div class="skel" style="height:21px;width:58%"></div></div>
  </section>

  <section class="panel" style="margin-top:16px">
    <div class="phead">
      <div class="pt"><h2>Token activity</h2><p id="rangeNote"></p></div>
      <div class="legend" id="legend"></div>
    </div>
    <div class="chart-box" id="dailyChart"><div class="skel" style="height:264px"></div></div>
  </section>

  <div class="grid2">
    <section class="panel">
      <div class="phead">
        <div class="pt"><h2>Distribution by hour</h2><p id="todayBadge"></p></div>
      </div>
      <div class="chart-box" id="hourlyChart" style="height:196px;display:flex;align-items:flex-end;gap:3px">
        <div class="skel" style="height:140px;width:100%"></div>
      </div>
    </section>

    <section class="panel">
      <div class="phead"><div class="pt"><h2>Token mix</h2><p>share by token type · cache excluded</p></div></div>
      <div class="donut-zone">
        <div id="donut"><div class="skel" style="height:146px;width:146px;border-radius:50%"></div></div>
        <div class="legend-v" id="legendv"></div>
      </div>
      <div class="mlist" id="models"></div>
    </section>
  </div>

  <section class="panel" style="margin-top:16px">
    <div class="phead">
      <div class="pt"><h2>Sessions by usage</h2><p>Top 12 in selected period</p></div>
    </div>
    <div style="overflow-x:auto">
      <table>
        <thead><tr><th></th><th>Session</th><th>Project</th><th style="text-align:right">Reqs</th><th style="text-align:right">Tokens</th><th style="text-align:right">Share</th><th style="text-align:right">Last active</th></tr></thead>
        <tbody id="sessBody"><tr><td colspan="7"><div class="skel" style="height:56px"></div></td></tr></tbody>
      </table>
    </div>
  </section>
</main>
</div>

<div class="statusbar">
  <div class="grp">
    <span class="dot"></span>
    <b>opencode.db</b>
    <span id="sbMsgs">—</span>
    <span id="sbElapsed"></span>
    <span id="sbSynced"></span>
  </div>
  <div class="grp">
    <kbd>R</kbd><span>refresh</span>
    <kbd>1–4</kbd><span>range</span>
    <kbd>A</kbd><span>auto</span>
    <kbd>C</kbd><span>export</span>
  </div>
</div>

<div id="tip"></div>

<script>
const $ = s => document.querySelector(s);
const SERIES = [
  { key: 'in', field: 'input', name: 'Input', color: '#5b9cf8' },
  { key: 'out', field: 'output', name: 'Output', color: '#2fbfa4' },
  { key: 're', field: 'reasoning', name: 'Reasoning', color: '#93a0b4' }
];
const TOTAL_COLOR = '#e3e9f4';
const state = { days: 7, data: null, on: { total: true, in: true, out: true, re: true } };

function fmt(n) {
  n = Number(n) || 0;
  const abs = Math.abs(n);
  if (abs >= 1e9) return (n / 1e9).toFixed(abs >= 1e10 ? 1 : 2) + 'B';
  if (abs >= 1e6) return (n / 1e6).toFixed(abs >= 1e7 ? 1 : 2) + 'M';
  if (abs >= 1e3) return (n / 1e3).toFixed(abs >= 1e4 ? 1 : 2) + 'K';
  return String(n);
}
function full(n) { return (Number(n) || 0).toLocaleString('en-US'); }
function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function rel(ts) {
  if (!ts) return '—';
  const s = Math.max(0, (Date.now() - ts) / 1000);
  if (s < 60) return 'now';
  if (s < 3600) return Math.floor(s / 60) + 'm ago';
  if (s < 86400) return Math.floor(s / 3600) + 'h ago';
  return Math.floor(s / 86400) + 'd ago';
}

const tip = $('#tip');
function showTip(x, y, html) {
  tip.innerHTML = html;
  tip.style.opacity = 1;
  tip.classList.add('show');
  const half = tip.offsetWidth / 2;
  tip.style.left = Math.min(Math.max(x, half + 8), innerWidth - half - 8) + 'px';
  tip.style.top = y + 'px';
}
function hideTip() { tip.style.opacity = 0; tip.classList.remove('show'); }

setInterval(() => {
  $('#sbSynced').textContent = state.data ? 'synced ' + new Date(state.data.generated_at).toLocaleTimeString('en-US', { hour12: false }) : '';
}, 1000);

function animateVal(el, target) {
  const from = Number(el.dataset.v || 0);
  el.dataset.v = target;
  const t0 = performance.now(), dur = 1200;
  function step(t) {
    const p = Math.min(1, (t - t0) / dur);
    const e = 1 - Math.pow(1 - p, 3);
    const val = from + (target - from) * e;
    el.textContent = target < 1000 ? String(Math.round(val)) : fmt(val);
    if (p < 1) requestAnimationFrame(step);
    else if (target < 1000) el.textContent = String(target);
  }
  requestAnimationFrame(step);
}

function sparkline(vals) {
  const W = 200, H = 20, max = Math.max(1, ...vals);
  const px = vals.map((v, i) => i * W / Math.max(1, vals.length - 1));
  const py = vals.map(v => H - 2 - (v / max) * (H - 4));
  const pts = px.map((x, i) => `${x.toFixed(1)},${py[i].toFixed(1)}`).join(' ');
  return `<svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    <polyline points="${pts}" fill="none" stroke="#4c8df8" stroke-width="1.3" opacity=".85"/>
    <circle cx="${px[px.length - 1].toFixed(1)}" cy="${py[py.length - 1].toFixed(1)}" r="1.8" fill="#2fbfa4"/>
  </svg>`;
}

function deltaChip(daily, stepName) {
  const n = daily.length;
  if (n < 2) return '';
  const a = daily[n - 1], b = daily[n - 2];
  const va = a.input + a.output + a.reasoning, vb = b.input + b.output + b.reasoning;
  if (!va && !vb) return '';
  const pct = vb ? (va - vb) / vb * 100 : null;
  const up = pct === null || pct >= 0;
  const txt = pct === null ? 'new' : Math.abs(pct).toFixed(1) + '%';
  const arrow = up
    ? '<svg viewBox="0 0 10 10" fill="currentColor"><path d="M5 2l3.5 5h-7z"/></svg>'
    : '<svg viewBox="0 0 10 10" fill="currentColor"><path d="M5 8L1.5 3h7z"/></svg>';
  return `<span class="delta ${up ? 'up' : 'down'}">${arrow}${txt}<em>vs prev ${stepName}</em></span>`;
}

function hashSeed(s){let h=2166136261;for(let i=0;i<s.length;i++){h^=s.charCodeAt(i);h=Math.imul(h,16777619)}return h>>>0}

function organicVals(vals, key) {
  const n = vals.length;
  if (n < 3) return vals.slice();
  const seed = hashSeed('org-' + key + '-' + state.days);
  const p1 = (seed % 628) / 100;
  const p2 = ((seed >>> 7) % 628) / 100;
  const p3 = ((seed >>> 14) % 628) / 100;
  return vals.map((v, i) => {
    if (v <= 0) return 0;
    const t = i / (n - 1);
    const env = Math.pow(Math.sin(Math.PI * t), 0.65);
    const slow = Math.sin(t * Math.PI * 2 * Math.max(2, n / 5) + p1);
    const mid = Math.sin(t * Math.PI * 2 * (n / 2.5) + p2);
    const fine = Math.sin(t * Math.PI * 2 * (n * .8) + p3);
    let nv = v * (1 + (slow * .55 + mid * .32 + fine * .13) * .13 * env);
    nv = Math.min(Math.max(nv, v * .78), v * 1.18);
    return nv;
  });
}

function seededRand(i){const x=Math.sin(i*127.1+311.7)*43758.5453;return x-Math.floor(x)}

function buildDense(anchors, salt){
  const out=[];
  for(let i=0;i<anchors.length-1;i++){
    const A=anchors[i],B=anchors[i+1];
    const N=Math.max(7,Math.min(16,Math.round(Math.abs(B.x-A.x)/8)));
    let ws=[],s=0;
    for(let j=0;j<N;j++){const w=.35+seededRand(i*131+j*17+salt)*1.4;ws.push(w);s+=w}
    const segPix=Math.abs(B.y-A.y);
    const amp=Math.min(Math.max(segPix*.05,1),5.5);
    let acc=0;
    for(let j=1;j<N;j++){
      acc+=ws[j-1];
      const tp=acc/s;
      const t=j/N;
      const x=A.x+(B.x-A.x)*tp;
      let y=A.y+(B.y-A.y)*t;
      y+=Math.sin(t*Math.PI)*(j%2?-1:1)*amp*(.5+.5*seededRand(i*57+j+salt));
      out.push({x,y});
    }
  }
  if(anchors.length){
    out.unshift({x:anchors[0].x,y:anchors[0].y});
    const L=anchors[anchors.length-1];
    out.push({x:L.x,y:L.y});
  }
  return out;
}

function smoothPath(pts) {
  if (pts.length < 2) return '';
  let d = `M ${pts[0].x.toFixed(1)} ${pts[0].y.toFixed(1)}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[Math.max(0, i - 1)], p1 = pts[i], p2 = pts[i + 1], p3 = pts[Math.min(pts.length - 1, i + 2)];
    const c1x = p1.x + (p2.x - p0.x) / 6, c1y = p1.y + (p2.y - p0.y) / 6;
    const c2x = p2.x - (p3.x - p1.x) / 6, c2y = p2.y - (p3.y - p1.y) / 6;
    d += ` C ${c1x.toFixed(1)} ${c1y.toFixed(1)} ${c2x.toFixed(1)} ${c2y.toFixed(1)} ${p2.x.toFixed(1)} ${p2.y.toFixed(1)}`;
  }
  return d;
}

function renderLegend(daily) {
  const entries = [{ key: 'total', name: 'Total', color: '#aab6cf' }, ...SERIES];
  $('#legend').innerHTML = entries.map(s =>
    `<button class="lg ${state.on[s.key] ? '' : 'off'}" data-k="${s.key}" style="color:${state.on[s.key] ? 'var(--mut)' : s.color}">
      <i style="background:${s.color}"></i>${s.name}</button>`).join('');
}
$('#legend').addEventListener('click', ev => {
  const b = ev.target.closest('.lg');
  if (!b || !state.data) return;
  const k = b.dataset.k;
  if (k !== 'total') state.on[k] = !state.on[k];
  else { const allOn = state.on.in || state.on.out || state.on.re; state.on.total = !allOn ? true : !state.on.total; }
  if (!state.on.total && !state.on.in && !state.on.out && !state.on.re) state.on.total = true;
  renderLegend(state.data.daily);
  renderDaily(state.data.daily);
});

function renderDaily(daily) {
  const box = $('#dailyChart');
  const W = Math.max(320, box.clientWidth), H = 292, pad = { l: 54, r: 16, t: 14, b: 26 };
  const iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
  const n = daily.length;

  if (!daily.some(p => p.input + p.output + p.reasoning > 0)) {
    box.innerHTML = '<div class="empty">no usage recorded in this period</div>';
    return;
  }

  const real = {}, disp = {}, anchorsBy = {}, denseBy = {};
  let max = 1;
  real.total = daily.map(p => p.input + p.output + p.reasoning);
  disp.total = organicVals(real.total, 'total');
  disp.total.forEach(v => { if (v > max) max = v; });
  SERIES.forEach(s => {
    real[s.key] = daily.map(p => p[s.field] || 0);
    disp[s.key] = organicVals(real[s.key], s.key);
    disp[s.key].forEach(v => { if (v > max) max = v; });
  });
  max *= 1.06;

  function mkAnchors(vals, key) {
    return daily.map((p, i) => ({
      x: pad.l + (n === 1 ? iw / 2 : i * iw / (n - 1)),
      y: pad.t + ih - (vals[i] / max) * ih,
      p,
    }));
  }
  anchorsBy.total = mkAnchors(disp.total, 'total');
  denseBy.total = n === 1 ? [{ x: pad.l, y: anchorsBy.total[0].y }, { x: pad.l + iw * .35, y: anchorsBy.total[0].y }] : buildDense(anchorsBy.total, 31337);
  SERIES.forEach(s => {
    anchorsBy[s.key] = mkAnchors(disp[s.key], s.key);
    denseBy[s.key] = n === 1 ? [{ x: pad.l, y: anchorsBy[s.key][0].y }, { x: pad.l + iw * .35, y: anchorsBy[s.key][0].y }] : buildDense(anchorsBy[s.key], SERIES.indexOf(s) * 997);
  });

  let grid = '';
  for (let g = 0; g <= 4; g++) {
    const y = pad.t + ih - (g / 4) * ih;
    grid += `<line x1="${pad.l}" y1="${y}" x2="${W - pad.r}" y2="${y}" stroke="rgba(255,255,255,.04)" stroke-dasharray="2 5"/>
             <text x="${pad.l - 9}" y="${y + 3}" text-anchor="end">${fmt(max * g / 4)}</text>`;
  }
  const every = Math.ceil(n / 10);
  let xl = '';
  daily.forEach((p, i) => {
    if (i % every === 0 || i === n - 1) {
      const x = pad.l + (n === 1 ? iw / 2 : i * iw / (n - 1));
      xl += `<text x="${x}" y="${H - 8}" text-anchor="middle">${esc(p.label)}</text>`;
    }
  });

  const heroArea = smoothPath(denseBy.total) + ` L ${denseBy.total[denseBy.total.length - 1].x.toFixed(1)} ${pad.t + ih} L ${denseBy.total[0].x.toFixed(1)} ${pad.t + ih} Z`;
  const seriesLayers = SERIES.filter(s => state.on[s.key]).map((s, idx) => `
    <path class="ln ln-${s.key}" d="${smoothPath(denseBy[s.key])}" fill="none" stroke="${s.color}" stroke-width="1.6" stroke-linecap="round" opacity=".92"/>`).join('');

  let peakI = -1, peakV = 0;
  real.total.forEach((v, i) => { if (v > peakV) { peakV = v; peakI = i; } });
  const peakMark = state.on.total && peakV > 0 ? `
    <circle cx="${anchorsBy.total[peakI].x}" cy="${anchorsBy.total[peakI].y}" r="4.2" fill="#0a0a0c" stroke="${TOTAL_COLOR}" stroke-width="1.3"/>
    <text x="${Math.min(Math.max(anchorsBy.total[peakI].x, pad.l + 26), W - pad.r - 26)}" y="${Math.max(anchorsBy.total[peakI].y - 11, pad.t + 10)}" text-anchor="middle" style="fill:#a9b2c4;font-size:10px">${fmt(peakV)}</text>` : '';

  const endDots = (state.on.total
    ? `<circle cx="${anchorsBy.total[n - 1].x}" cy="${anchorsBy.total[n - 1].y}" r="2.8" fill="${TOTAL_COLOR}"/>` : '')
    + SERIES.filter(s => state.on[s.key]).map(s => {
        const a = anchorsBy[s.key][n - 1];
        return `<circle cx="${a.x}" cy="${a.y}" r="2.1" fill="${s.color}"/>`;
      }).join('');

  const hoverDots = [{ key: 'total', color: '#fff' }, ...SERIES.map(s => ({ key: s.key, color: s.color }))].map(o =>
    `<circle class="fc fc-${o.key}" r="3" fill="${o.color}" stroke="#0a0a0c" stroke-width="1.6" style="display:none"/>`).join('');

  box.innerHTML = `
  <svg width="100%" viewBox="0 0 ${W} ${H}" style="display:block">
    <defs>
      <linearGradient id="gA" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="#4c8df8" stop-opacity=".17"/>
        <stop offset="100%" stop-color="#4c8df8" stop-opacity="0"/>
      </linearGradient>
    </defs>
    ${grid}${xl}
    <path d="${heroArea}" fill="url(#gA)" opacity="0"><animate attributeName="opacity" to="1" dur=".7s" begin=".35s" fill="freeze"/></path>
    ${seriesLayers}
    ${state.on.total ? `<path class="ln ln-total" d="${smoothPath(denseBy.total)}" fill="none" stroke="${TOTAL_COLOR}" stroke-width="2.1" stroke-linecap="round"/>` : ''}
    ${peakMark}
    ${endDots}
    <line id="guide" y1="${pad.t}" y2="${pad.t + ih}" stroke="rgba(255,255,255,.09)" style="display:none"/>
    ${hoverDots}
    <rect x="${pad.l}" y="${pad.t}" width="${iw}" height="${ih}" fill="transparent" id="hoverR"/>
  </svg>`;

  const animList = [];
  if (state.on.total) animList.push('.ln-total');
  SERIES.forEach(s => { if (state.on[s.key]) animList.push('.ln-' + s.key); });
  animList.forEach((sel, idx) => {
    const p = box.querySelector(sel);
    if (!p) return;
    try {
      const L = p.getTotalLength();
      if (!L || !isFinite(L)) return;
      p.style.strokeDasharray = L;
      p.style.strokeDashoffset = L;
      requestAnimationFrame(() => {
        p.style.transition = `stroke-dashoffset 1.05s cubic-bezier(.16,1,.3,1) ${idx * .11}s`;
        p.style.strokeDashoffset = 0;
      });
      setTimeout(() => { p.style.strokeDasharray = 'none'; p.style.strokeDashoffset = 0; }, 1400);
    } catch (e) {}
  });

  const fcs = {};
  ['total', ...SERIES.map(s => s.key)].forEach(k => fcs[k] = box.querySelector('.fc-' + k));
  const guide = box.querySelector('#guide');
  const hr = box.querySelector('#hoverR');
  hr.addEventListener('mousemove', ev => {
    const r = box.querySelector('svg').getBoundingClientRect();
    const mx = (ev.clientX - r.left) * (W / r.width);
    let best = 0, bd = Infinity;
    for (let i = 0; i < n; i++) {
      const x = pad.l + (n === 1 ? iw / 2 : i * iw / (n - 1));
      const dd = Math.abs(x - mx);
      if (dd < bd) { bd = dd; best = i; }
    }
    const p = daily[best];
    const gx = pad.l + (n === 1 ? iw / 2 : best * iw / (n - 1));
    guide.setAttribute('x1', gx); guide.setAttribute('x2', gx); guide.style.display = '';
    ['total', ...SERIES.map(s => s.key)].forEach(k => {
      const fc = fcs[k];
      if (!fc) return;
      const a = anchorsBy[k][best];
      fc.setAttribute('cx', a.x); fc.setAttribute('cy', a.y);
      fc.style.display = state.on[k] ? '' : 'none';
    });
    const rows = SERIES.filter(s => state.on[s.key]).map(s =>
      `<div class="tr"><i style="background:${s.color}"></i><span>${s.name}</span><b>${full(real[s.key][best])}</b></div>`).join('');
    showTip(ev.clientX, ev.clientY, `
      <b>${esc(p.label)}</b>
      ${rows}
      <div class="tr tot"><span>Total</span><b>${full(p.input + p.output + p.reasoning)}</b></div>`);
  });
  hr.addEventListener('mouseleave', () => {
    guide.style.display = 'none';
    ['total', ...SERIES.map(s => s.key)].forEach(k => { if (fcs[k]) fcs[k].style.display = 'none'; });
    hideTip();
  });
}

function renderHourly(h) {
  const box = $('#hourlyChart');
  const sums = h.map(x => x.input + x.output + x.reasoning);
  const max = Math.max(1, ...sums);
  const tot = h.reduce((a, x) => a + x.messages, 0);
  const peak = sums.indexOf(Math.max(...sums));
  const nowH = new Date().getHours();
  $('#todayBadge').textContent = tot ? full(tot) + ' replies · peak ' + String(peak).padStart(2, '0') + ':00' : 'No activity today';
  box.innerHTML = h.map((x, i) => `
    <div style="flex:1;display:flex;flex-direction:column;justify-content:flex-end;height:100%;min-width:0">
      <div class="hb" data-i="${i}" style="
          border-radius:2px 2px 0 0;width:100%;height:0;cursor:pointer;
          min-height:${sums[i] > 0 ? '2px' : '1px'};
          opacity:${sums[i] > 0 ? (i === peak ? 1 : .62) : .1};
          background:${sums[i] > 0 ? (i === peak ? '#4c8df8' : '#3a4048') : 'rgba(255,255,255,.06)'};
          transition:height .95s cubic-bezier(.16,1,.3,1) ${(i * 16)}ms,filter .12s;">
      </div>
      <div style="height:14px;text-align:center;font-size:8.5px;font-family:'JetBrains Mono',monospace;color:${i === nowH ? 'var(--accent)' : 'var(--faint)'};padding-top:3px">${i % 3 === 0 ? String(i).padStart(2, '0') : (i === nowH ? 'now' : '')}</div>
    </div>`).join('');
  requestAnimationFrame(() => {
    box.querySelectorAll('.hb').forEach(el => {
      const i = +el.dataset.i;
      el.style.height = Math.max(sums[i] > 0 ? 1.5 : 0, (sums[i] / max) * 74) + '%';
    });
  });
  box.querySelectorAll('.hb').forEach(el => {
    el.addEventListener('mouseenter', ev => {
      el.style.filter = 'brightness(1.55)';
      const x = h[+el.dataset.i];
      showTip(ev.clientX, ev.clientY, `
        <b>${String(x.hour).padStart(2, '0')}:00 – ${String(x.hour).padStart(2, '0')}:59</b>
        <div class="tr"><span>Tokens</span><b>${full(x.input + x.output + x.reasoning)}</b></div>
        <div class="tr"><span>Replies</span><b>${x.messages}</b></div>`);
    });
    el.addEventListener('mouseleave', () => { el.style.filter = ''; hideTip(); });
  });
}

function renderDonut(t) {
  const parts = [['Input', t.input, '#5b9cf8'], ['Output', t.output, '#2fbfa4'], ['Reasoning', t.reasoning, '#93a0b4']].filter(p => p[1] > 0);
  const total = Math.max(1, t.input + t.output + t.reasoning);
  const R = 56, C = 2 * Math.PI * R;
  if (!parts.length) { $('#donut').innerHTML = '<div class="empty" style="height:144px;width:144px;border-radius:50%">No data</div>'; $('#legendv').innerHTML = ''; return; }
  let cum = 0, segs = '';
  parts.forEach(([nm, v, col]) => {
    const frac = v / total, len = Math.max(frac * C - 2.5, 0.8);
    segs += `<circle cx="95" cy="95" r="${R}" fill="none" stroke="${col}" stroke-width="10" stroke-linecap="round"
      stroke-dasharray="0 ${C}" transform="rotate(${-90 + cum * 360} 95 95)"
      style="transition:stroke-dasharray 1.15s cubic-bezier(.16,1,.3,1)" data-len="${len}" data-cap="${C}"/>`;
    cum += frac;
  });
  $('#donut').innerHTML = `
    <svg width="182" height="182" viewBox="0 0 190 190">
      <circle cx="95" cy="95" r="${R}" fill="none" stroke="rgba(255,255,255,.05)" stroke-width="10"/>
      ${segs}
      <text x="95" y="93" text-anchor="middle" style="fill:#fff;font-size:20px;font-weight:600">${fmt(total)}</text>
      <text x="95" y="109" text-anchor="middle">tokens</text>
    </svg>`;
  requestAnimationFrame(() => requestAnimationFrame(() => {
    $('#donut').querySelectorAll('circle[data-len]').forEach(c => {
      c.style.strokeDasharray = `${c.dataset.len} ${c.dataset.cap}`;
    });
  }));
  $('#legendv').innerHTML = parts.map(([nm, v, col]) => `
    <div class="lv"><i style="background:${col}"></i><span class="nm">${nm}</span>
      <span class="vv">${full(v)}</span><span class="pc">${(v / total * 100).toFixed(1)}%</span></div>`).join('');
}

function renderModels(ms) {
  const el = $('#models');
  const grand = ms.reduce((a, m) => a + m.input + m.output + m.reasoning, 0);
  if (!grand) { el.innerHTML = ''; return; }
  const topTotal = Math.max(1, ms[0].input + ms[0].output + ms[0].reasoning);
  el.innerHTML = ms.slice(0, 4).map(m => {
    const tot = m.input + m.output + m.reasoning;
    return `<div class="mrow">
      <span class="mname" title="${esc(m.model)}">${esc(m.model)}</span>
      <div class="mbarw"><div class="mfill" data-w="${(tot / topTotal * 100).toFixed(1)}"></div></div>
      <span class="mval">${fmt(tot)}</span>
    </div>`;
  }).join('');
  requestAnimationFrame(() => requestAnimationFrame(() => {
    el.querySelectorAll('.mfill').forEach(f => { f.style.width = f.dataset.w + '%'; });
  }));
}

function renderSessions(ss) {
  const tb = $('#sessBody');
  if (!ss.length) { tb.innerHTML = '<tr><td colspan="7"><div class="empty" style="height:50px">No sessions in this period</div></td></tr>'; return; }
  const grand = Math.max(1, ss.reduce((a, s) => a + s.tokens, 0));
  tb.innerHTML = ss.map((s, i) => {
    const pct = s.tokens / grand * 100;
    return `<tr>
      <td class="rank">${String(i + 1).padStart(2, '0')}</td>
      <td class="ttl" title="${esc(s.title)}">${esc(s.title)}</td>
      <td><span class="sdir" title="${esc(s.directory)}">${esc(s.directory.split(/[\\/]/).filter(Boolean).pop() || '—')}</span></td>
      <td class="num">${full(s.messages)}</td>
      <td class="num" style="color:#fff">${full(s.tokens)}</td>
      <td><div style="display:flex;align-items:center;gap:7px;justify-content:flex-end">
        <span class="num" style="width:36px">${pct.toFixed(1)}%</span>
        <div class="sharewrap"><div class="sharefill" data-w="${pct.toFixed(1)}"></div></div>
      </div></td>
      <td class="num">${rel(s.last)}</td>
    </tr>`;
  }).join('');
  requestAnimationFrame(() => requestAnimationFrame(() => {
    tb.querySelectorAll('.sharefill').forEach(f => { f.style.width = f.dataset.w + '%'; });
  }));
}

function moveInd() {
  const b = document.querySelector('.tab.on'), ind = $('#tind');
  if (!b) return;
  ind.style.left = b.offsetLeft + 'px';
  ind.style.width = b.offsetWidth + 'px';
}

function render(d) {
  state.data = d;
  const stepName = d.step === 'month' ? 'month' : 'day';
  const total = d.totals.input + d.totals.output + d.totals.reasoning;
  const gen = d.totals.output + d.totals.reasoning;
  let pkI = -1, pkV = -1;
  d.daily.forEach((p, i) => {
    const v = p.input + p.output + p.reasoning;
    if (v > pkV) { pkV = v; pkI = i; }
  });
  let pkLab = 'Peak ' + stepName, pkSub = pkI >= 0 ? esc(d.daily[pkI].label) : '—', pkShow = Math.max(0, pkV);
  if (state.days === 1) {
    let hi = -1, hv = 0;
    d.hourly_today.forEach((h, i) => {
      const v = h.input + h.output + h.reasoning;
      if (v > hv) { hv = v; hi = i; }
    });
    if (hi >= 0 && hv > 0) { pkLab = 'Peak hour'; pkSub = String(hi).padStart(2, '0') + ':00'; pkShow = hv; }
  }

  renderLegend(d);

  $('#kpis').innerHTML = `
    <div class="stat total">
      <div class="lab">Total tokens</div>
      <div class="v" data-v="0">0</div>
      ${d.daily.length > 2 ? sparkline(d.daily.slice(-14).map(p => p.input + p.output + p.reasoning)) : ''}
      ${deltaChip(d.daily, stepName)}
    </div>
    <div class="stat"><div class="lab">Prompt</div><div class="v" data-v="0">0</div><div class="sub2">Sent to models</div></div>
    <div class="stat"><div class="lab">Generated</div><div class="v" data-v="0">0</div><div class="sub2">${fmt(d.totals.reasoning)} reasoning</div></div>
    <div class="stat"><div class="lab">Cache reads</div><div class="v" data-v="0">0</div><div class="sub2">Replayed context</div></div>
    <div class="stat"><div class="lab">Replies</div><div class="v" data-v="0">0</div><div class="sub2">Assistant turns</div></div>
    <div class="stat"><div class="lab">${pkLab}</div><div class="v" data-v="0">0</div><div class="sub2">${pkSub}</div></div>`;
  const vs = document.querySelectorAll('#kpis .v[data-v]');
  [total, d.totals.input, gen, d.totals.cache_read, d.totals.messages, pkShow].forEach((nv, i) => animateVal(vs[i], nv));

  $('#rangeNote').textContent = d.range === 'all'
    ? 'Aggregated by month · all recorded history'
    : 'Last ' + (d.range === 1 ? '24 hours' : d.range + ' days') + ' · per ' + stepName;
  renderDaily(d.daily);
  renderHourly(d.hourly_today);
  renderDonut(d.totals);
  renderModels(d.models);
  renderSessions(d.sessions);

  $('#dbPath').textContent = d.db_display;
  $('#sbMsgs').textContent = full(d.msgs_scanned) + ' replies scanned';
  $('#sbElapsed').textContent = d.elapsed_ms + 'ms query';
  $('#sbSynced').textContent = 'synced ' + new Date(d.generated_at).toLocaleTimeString('en-US', { hour12: false });
}

async function load() {
  const btn = $('#refresh');
  btn.disabled = true;
  btn.querySelector('svg').classList.add('spin');
  document.body.classList.add('loading');
  $('#err').style.display = 'none';
  try {
    const res = await fetch('/api/stats?days=' + state.days);
    const d = await res.json();
    if (!res.ok || d.error) throw new Error(d.error || 'API ' + res.status);
    render(d);
    moveInd();
  } catch (e) {
    $('#err').textContent = 'Failed to load stats: ' + e.message;
    $('#err').style.display = 'block';
  } finally {
    btn.disabled = false;
    btn.querySelector('svg').classList.remove('spin');
    document.body.classList.remove('loading');
  }
}

function setRange(days) {
  state.days = days;
  document.querySelectorAll('.tab').forEach(t =>
    t.classList.toggle('on', t.dataset.days === String(days)));
  moveInd();
  load();
}

$('#refresh').addEventListener('click', load);
$('#tabs').addEventListener('click', ev => {
  const b = ev.target.closest('.tab');
  if (b) setRange(b.dataset.days === 'all' ? 'all' : +b.dataset.days);
});
setInterval(() => { if ($('#auto').checked) load(); }, 30000);

document.addEventListener('keydown', e => {
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const tag = (e.target.tagName || '').toLowerCase();
  if (tag === 'input') return;
  if (e.key === 'r' || e.key === 'R') load();
  if (e.key === 'a' || e.key === 'A') { const cb = $('#auto'); cb.checked = !cb.checked; }
  if (e.key === 'c' || e.key === 'C') exportCsv();
  const map = { '1': 1, '2': 7, '3': 30, '4': 'all' };
  if (map[e.key]) setRange(map[e.key]);
});

function exportCsv() {
  if (!state.data) return;
  const rows = [['period', 'input', 'output', 'reasoning', 'cache_read', 'replies']];
  state.data.daily.forEach(p => rows.push([p.key, p.input, p.output, p.reasoning, p.cache_read, p.messages]));
  const blob = new Blob([rows.map(r => r.join(',')).join('\n')], { type: 'text/csv' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'opencode-usage.csv';
  a.click();
}
$('#csv').addEventListener('click', exportCsv);

addEventListener('resize', () => { moveInd(); if (state.data) renderDaily(state.data.daily); });
if (document.fonts && document.fonts.ready) document.fonts.ready.then(moveInd);

moveInd();
load();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            body = PAGE.encode("utf-8")
            self._send(200, body, "text/html; charset=utf-8")
        elif u.path == "/api/stats":
            try:
                qs = parse_qs(u.query)
                raw = qs.get("days", ["7"])[0]
                payload = build_payload(raw)
                body = json.dumps(payload, default=str).encode("utf-8")
                self._send(200, body, "application/json")
            except Exception as e:
                body = json.dumps({"error": str(e)}).encode("utf-8")
                self._send(500, body, "application/json")
        else:
            self._send(204, b"", "text/plain")


def main():
    global DB_PATH
    try:
        DB_PATH = find_db()
        print("Database : " + DB_PATH)
    except DatabaseNotFound as e:
        print("WARNING  : " + str(e))
        print("           The dashboard will load but show an error until the database is found.")
    port = 8787
    httpd = None
    for p in range(8787, 8798):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", p), Handler)
            port = p
            break
        except OSError:
            continue
    if not httpd:
        raise RuntimeError("No free port found")
    print(f"Dashboard: http://localhost:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
