#!/usr/bin/env python3
"""Generate HTML mockups for the two new user-facing screens added in this
release (Activity day-timeline page, Proactive-settings contact-hours fields)
and render them to PNG via headless Chrome. Colors/spacing pulled from the
real app: frontend/src/app/globals.css (dark theme HSL vars) and the actual
component markup in frontend/src/app/activity/page.tsx and
frontend/src/components/agents/proactive-toggle.tsx.
"""
import subprocess
import sys
from pathlib import Path

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
OUT_DIR = Path(__file__).parent / "benutzerhandbuch" / "screenshots"

# Dark theme HSL vars from frontend/src/app/globals.css (.dark block)
BG = "hsl(222 47% 5%)"
CARD = "hsl(222 47% 7%)"
FG = "hsl(210 40% 98%)"
MUTED_FG = "hsl(215 20% 55%)"
PRIMARY = "hsl(217 91% 60%)"
BORDER = "hsla(210, 40%, 98%, 0.06)"

BASE_HEAD = f"""
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: {BG}; color: {FG};
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    font-size: 14px;
  }}
  .app {{ display: flex; min-height: 100vh; }}
  .sidebar {{
    width: 220px; flex-shrink: 0; background: {CARD};
    border-right: 1px solid {BORDER}; padding: 16px 12px; display: flex;
    flex-direction: column; gap: 20px;
  }}
  .brand {{ font-weight: 600; font-size: 15px; padding: 0 8px 12px; }}
  .navgroup-label {{
    font-size: 10px; text-transform: uppercase; letter-spacing: .05em;
    color: {MUTED_FG}; padding: 0 8px; margin-bottom: 6px;
  }}
  .navitem {{
    display: flex; align-items: center; gap: 10px; padding: 8px 10px;
    border-radius: 8px; color: {MUTED_FG}; font-size: 13px; margin-bottom: 2px;
  }}
  .navitem.active {{ background: hsla(217,91%,60%,0.12); color: {FG}; font-weight: 500; }}
  .navitem svg {{ width: 16px; height: 16px; flex-shrink: 0; }}
  .main {{ flex: 1; padding: 32px; }}
  h1 {{ font-size: 22px; font-weight: 600; letter-spacing: -0.01em; }}
  .sub {{ color: {MUTED_FG}; font-size: 13px; margin-top: 4px; }}
</style>
"""

ICON_ACTIVITY = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>'
ICON_DASH = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="9"/><rect x="14" y="3" width="7" height="5"/><rect x="14" y="12" width="7" height="9"/><rect x="3" y="16" width="7" height="5"/></svg>'
ICON_CPU = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/></svg>'
ICON_TASKS = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11"/></svg>'
ICON_BAR = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="20" x2="12" y2="10"/><line x1="18" y1="20" x2="18" y2="4"/><line x1="6" y1="20" x2="6" y2="16"/></svg>'
ICON_CAL = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>'
ICON_CHEVRON_L = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="15 18 9 12 15 6"/></svg>'
ICON_CHEVRON_R = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"/></svg>'


def sidebar(active: str) -> str:
    items = [
        ("dashboard", "Dashboard", ICON_DASH),
        ("agents", "Agents", ICON_CPU),
        ("tasks", "Tasks", ICON_TASKS),
        ("activity", "Activity", ICON_ACTIVITY),
        ("analytics", "Analytics", ICON_BAR),
    ]
    rows = "".join(
        f'<div class="navitem{" active" if key == active else ""}">{icon}<span>{label}</span></div>'
        for key, label, icon in items
    )
    return f"""
    <div class="sidebar">
      <div class="brand">AI Employee</div>
      <div>
        <div class="navgroup-label">Übersicht</div>
        {rows}
      </div>
    </div>
    """


def mockup_activity() -> str:
    def bar(left, width, color, pulse=False):
        anim = "animation: pulse 1.5s ease-in-out infinite;" if pulse else ""
        return (f'<div style="position:absolute; top:4px; bottom:4px; left:{left}%; width:{width}%; '
                f'min-width:6px; border-radius:6px; background:{color}; border:1px solid {color}; {anim}"></div>')

    def mark(left):
        return (f'<div style="position:absolute; top:0; left:{left}%; width:8px; height:8px; '
                f'transform:translateX(-50%) rotate(45deg); border:1px solid hsla(210,40%,98%,0.3); background:{BG};"></div>')

    def track(name, bars, marks):
        return f"""
        <div style="display:flex; align-items:center; gap:12px; margin-bottom:8px;">
          <div style="width:176px; flex-shrink:0; font-size:13px; font-weight:500;">{name}</div>
          <div style="position:relative; flex:1; height:40px; border-radius:8px; border:1px solid {BORDER};
                      background:hsla(210,40%,98%,0.02); overflow:hidden;">
            {"".join(f'<div style="position:absolute; top:0; bottom:0; left:{p}%; width:1px; background:hsla(210,40%,98%,0.04);"></div>' for p in (25,50,75))}
            {bars}{marks}
          </div>
        </div>"""

    rows = (
        track("DevAgent",
              bar(8, 10, "rgba(16,185,129,0.75)") + bar(25, 14, "rgba(239,68,68,0.75)") + bar(58, 8, "#3b82f6", pulse=True),
              mark(20) + mark(75))
        + track("Reise-Agent",
                bar(37, 6, "rgba(16,185,129,0.75)"),
                mark(37) + mark(80))
        + track("Marketing-Agent",
                "",
                mark(12) + mark(37) + mark(62) + mark(87))
    )

    hours = "".join(
        f'<div style="flex:1; text-align:{"right" if h == 24 else "left"};">{h:02d}:00</div>'
        for h in (0, 6, 12, 18, 24)
    )

    return f"""
    <div class="main">
      <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:24px;">
        <div>
          <h1>Activity</h1>
          <div class="sub">What every agent has planned and what it actually did, one day at a time</div>
        </div>
        <div style="display:flex; align-items:center; gap:8px;">
          <div style="width:36px; height:36px; border-radius:8px; border:1px solid {BORDER}; display:flex; align-items:center; justify-content:center; color:{MUTED_FG};">{ICON_CHEVRON_L}</div>
          <div style="min-width:220px; display:flex; align-items:center; justify-content:center; gap:8px; border:1px solid {BORDER}; border-radius:8px; padding:8px 12px; font-weight:500;">
            <span style="color:{MUTED_FG}; width:16px; height:16px;">{ICON_CAL}</span> Thursday, August 6, 2026
          </div>
          <div style="width:36px; height:36px; border-radius:8px; border:1px solid {BORDER}; display:flex; align-items:center; justify-content:center; color:{MUTED_FG};">{ICON_CHEVRON_R}</div>
        </div>
      </div>
      <div style="display:flex; padding-left:188px; padding-right:4px; font-size:10px; color:{MUTED_FG}; margin-bottom:8px;">{hours}</div>
      {rows}
    </div>
    """


def mockup_proactive_hours() -> str:
    return f"""
    <div class="main" style="padding:48px;">
      <div style="max-width:560px; border-radius:12px; border:1px solid {BORDER}; background:hsla(210,40%,98%,0.02); padding:16px;">
        <div style="display:flex; align-items:center; gap:12px; margin-bottom:16px;">
          <div style="width:32px; height:32px; border-radius:8px; background:hsla(16,185,129,0.1); display:flex; align-items:center; justify-content:center; color:#34d399;">⚡</div>
          <div>
            <div style="font-weight:500;">Proactive Mode</div>
            <div style="font-size:11px; color:{MUTED_FG};">Agent checks periodically for work to do on its own</div>
          </div>
        </div>
        <div style="border-top:1px solid {BORDER}; padding-top:12px;">
          <div style="font-size:10px; text-transform:uppercase; letter-spacing:.05em; color:{MUTED_FG}; margin-bottom:6px;">
            Erreichbarkeit des Ansprechpartners
          </div>
          <div style="display:flex; align-items:center; gap:8px;">
            <div style="border:1px solid {BORDER}; border-radius:8px; padding:6px 10px; font-size:12px;">09:00</div>
            <span style="font-size:11px; color:{MUTED_FG};">bis</span>
            <div style="border:1px solid {BORDER}; border-radius:8px; padding:6px 10px; font-size:12px;">18:00</div>
            <div style="border:1px solid {BORDER}; border-radius:8px; padding:6px 10px; font-size:12px; width:128px;">Europe/Berlin</div>
          </div>
          <div style="font-size:10px; color:{MUTED_FG}; margin-top:8px; line-height:1.5;">
            Außerhalb dieses Fensters meldet sich der Agent nur bei wirklich Dringendem
            (STEP 4 der Basis-Regeln). Leer lassen = jeder Lauf gilt als Off-Hours.
          </div>
          <div style="display:flex; justify-content:flex-end; margin-top:12px;">
            <div style="background:hsla(16,185,129,0.15); color:#6ee7b7; border-radius:6px; padding:6px 10px; font-size:11px; font-weight:500;">
              Speichern
            </div>
          </div>
        </div>
      </div>
    </div>
    """


def mockup_day_agenda() -> str:
    hour_px = 44

    def hour_row(h):
        return (f'<div style="position:relative; height:{hour_px}px; border-top:1px solid {BORDER};">'
                f'<span style="position:absolute; top:-7px; right:8px; font-size:10px; color:{MUTED_FG};">{h:02d}:00</span></div>')

    gutter = "".join(hour_row(h) for h in range(7, 17))

    def block(start_h, end_h, title, meta, color, lane=0, lanes=1, base_hour=7):
        top = (start_h - base_hour) * hour_px
        height = max((end_h - start_h) * hour_px, 18)
        lane_w = 100 / lanes
        show_meta = height >= 28
        return (f'<div style="position:absolute; top:{top}px; height:{height}px; '
                f'left:calc({lane*lane_w}% + 2px); width:calc({lane_w}% - 4px); '
                f'border-radius:6px; border-left:2px solid {color}; background:{color}26; '
                f'padding:3px 8px; overflow:hidden;">'
                f'<div style="font-size:11px; font-weight:500; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">{title}</div>'
                + (f'<div style="font-size:9px; color:{MUTED_FG}; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">{meta}</div>' if show_meta else "")
                + '</div>')

    blocks = (
        block(7.1, 7.7, "Check GitHub issues for AI-Employee", "07:06–07:42 · completed", "#34d399")
        + block(9.5, 10.6, "Fix flaky test in scheduler_service", "09:30–10:36 · failed", "#f87171", lane=0, lanes=2)
        + block(9.7, 10.2, "Parallel: review PR #530", "09:42–10:12 · running", "#60a5fa", lane=1, lanes=2)
        + block(13.0, 14.3, "Implement trigger_create MCP tool and tests", "13:00–14:18 · completed", "#34d399")
    )
    marks = "".join(
        f'<div style="position:absolute; left:2px; top:{(h-7)*hour_px}px; width:7px; height:7px; '
        f'transform:translateY(-50%) rotate(45deg); border:1px solid hsla(210,40%,98%,0.3); background:{BG};"></div>'
        for h in (8, 12, 16)
    )
    now_line = (
        f'<div style="position:absolute; left:0; right:0; top:{(11.5-7)*hour_px}px; display:flex; align-items:center; z-index:5;">'
        f'<div style="width:7px; height:7px; border-radius:50%; background:{PRIMARY};"></div>'
        f'<div style="flex:1; height:1px; background:{PRIMARY}; opacity:0.7;"></div></div>'
    )

    return f"""
    <div class="main" style="padding:32px;">
      <div style="max-width:520px;">
        <div style="font-size:13px; font-weight:500; color:{MUTED_FG}; margin-bottom:12px;">Tageskalender</div>
        <div style="max-height:{hour_px*10}px; overflow:hidden; border-radius:8px; border:1px solid {BORDER}; display:flex;">
          <div style="width:52px; flex-shrink:0; border-right:1px solid {BORDER};">{gutter}</div>
          <div style="position:relative; flex:1;">{blocks}{marks}{now_line}</div>
        </div>
      </div>
    </div>
    """


MOCKUPS = {
    "28-activity": (lambda: sidebar("activity") + mockup_activity(), 1280, 620),
    "29-proactive-contact-hours": (lambda: sidebar("agents") + mockup_proactive_hours(), 1000, 480),
    "30-day-agenda": (lambda: sidebar("agents") + mockup_day_agenda(), 900, 560),
}


def render(name: str, body_fn, width: int, height: int):
    html = f"<!doctype html><html><head>{BASE_HEAD}</head><body><div class='app'>{body_fn()}</div></body></html>"
    html_path = OUT_DIR / f"{name}.html"
    png_path = OUT_DIR / f"{name}.png"
    html_path.write_text(html)
    cmd = [
        CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
        "--hide-scrollbars", f"--window-size={width},{height}",
        f"--screenshot={png_path}", f"file://{html_path}",
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    html_path.unlink()
    print(f"  {name}.png ({width}x{height})")


if __name__ == "__main__":
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print("Rendering mockups...")
    for name, (fn, w, h) in MOCKUPS.items():
        render(name, fn, w, h)
    print("Done.")
