"""Generate the FibreOps technical architecture diagram (PNG).

Pure-Pillow renderer (no Graphviz / matplotlib dependency) so it runs in the
project's virtual environment with only ``Pillow`` installed::

    .\\.venv\\Scripts\\python.exe scripts\\gen_architecture_diagram.py

Output: docs/images/architecture-diagram.png
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Palette (Microsoft-ish)
# ---------------------------------------------------------------------------
BG = "#ffffff"
INK = "#1b1b1b"
MUTED = "#5c5c5c"
AZURE = "#0078d4"
AZURE_DK = "#005a9e"
TEAL = "#018574"
PURPLE = "#5c2d91"
AMBER = "#8a6d00"
GREEN = "#107c10"
GREY = "#7a7a7a"

W, H = 1760, 1180


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = (
        ("arialbd.ttf" if bold else "arial.ttf"),
        ("seguisb.ttf" if bold else "segoeui.ttf"),
        ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"),
    )
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


F_TITLE = _font(38, bold=True)
F_SUB = _font(20)
F_HEAD = _font(20, bold=True)
F_BODY = _font(16)
F_BODY_B = _font(16, bold=True)
F_SMALL = _font(13)
F_LABEL = _font(14)


def _text_size(draw: ImageDraw.ImageDraw, text: str, font) -> tuple[int, int]:
    l, t, r, b = draw.textbbox((0, 0), text, font=font)
    return r - l, b - t


def _wrap(draw, text, font, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if _text_size(draw, trial, font)[0] <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def rounded(draw, box, fill=None, outline=None, width=2, radius=14):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def panel(draw, box, title, accent, title_color="#ffffff"):
    """Card with a coloured title bar."""
    x0, y0, x1, y1 = box
    rounded(draw, box, fill="#ffffff", outline=accent, width=2, radius=14)
    bar_h = 36
    draw.rounded_rectangle((x0, y0, x1, y0 + bar_h), radius=14, fill=accent)
    draw.rectangle((x0, y0 + bar_h - 14, x1, y0 + bar_h), fill=accent)
    tw, th = _text_size(draw, title, F_HEAD)
    draw.text((x0 + (x1 - x0 - tw) / 2, y0 + (bar_h - th) / 2 - 2), title,
              font=F_HEAD, fill=title_color)
    return (x0, y0 + bar_h, x1, y1)


def chip(draw, box, lines, accent, fill="#f3f8fc"):
    rounded(draw, box, fill=fill, outline=accent, width=2, radius=10)
    x0, y0, x1, y1 = box
    total_h = 0
    rendered = []
    for text, font, color in lines:
        for ln in _wrap(draw, text, font, (x1 - x0) - 20):
            w, h = _text_size(draw, ln, font)
            rendered.append((ln, font, color, w, h))
            total_h += h + 3
    cy = y0 + ((y1 - y0) - total_h) / 2
    for ln, font, color, w, h in rendered:
        draw.text((x0 + ((x1 - x0) - w) / 2, cy), ln, font=font, fill=color)
        cy += h + 3


def arrow(draw, p0, p1, color=GREY, width=3, label=None, label_font=F_LABEL,
          label_off=(0, -18), dashed=False):
    x0, y0 = p0
    x1, y1 = p1
    if dashed:
        _dashed_line(draw, p0, p1, color, width)
    else:
        draw.line((x0, y0, x1, y1), fill=color, width=width)
    # arrowhead
    import math
    ang = math.atan2(y1 - y0, x1 - x0)
    size = 11
    for da in (math.radians(26), -math.radians(26)):
        hx = x1 - size * math.cos(ang + da)
        hy = y1 - size * math.sin(ang + da)
        draw.line((x1, y1, hx, hy), fill=color, width=width)
    if label:
        mx, my = (x0 + x1) / 2 + label_off[0], (y0 + y1) / 2 + label_off[1]
        tw, th = _text_size(draw, label, label_font)
        draw.rectangle((mx - tw / 2 - 4, my - 2, mx + tw / 2 + 4, my + th + 2),
                       fill="#ffffff")
        draw.text((mx - tw / 2, my), label, font=label_font, fill=MUTED)


def _arrowhead(draw, p0, p1, color, width, size=11):
    import math
    x0, y0 = p0
    x1, y1 = p1
    ang = math.atan2(y1 - y0, x1 - x0)
    for da in (math.radians(26), -math.radians(26)):
        hx = x1 - size * math.cos(ang + da)
        hy = y1 - size * math.sin(ang + da)
        draw.line((x1, y1, hx, hy), fill=color, width=width)


def poly_arrow(draw, pts, color=GREY, width=3, dashed=False, label=None,
               label_at=None, label_off=(0, -16)):
    """Orthogonal multi-point connector with a single arrowhead at the end."""
    for a, b in zip(pts, pts[1:]):
        if dashed:
            _dashed_line(draw, a, b, color, width)
        else:
            draw.line((a[0], a[1], b[0], b[1]), fill=color, width=width)
    _arrowhead(draw, pts[-2], pts[-1], color, width)
    if label and label_at:
        tw, th = _text_size(draw, label, F_LABEL)
        mx, my = label_at[0] + label_off[0], label_at[1] + label_off[1]
        draw.rectangle((mx - tw / 2 - 4, my - 2, mx + tw / 2 + 4, my + th + 2),
                       fill="#ffffff")
        draw.text((mx - tw / 2, my), label, font=F_LABEL, fill=MUTED)


def _dashed_line(draw, p0, p1, color, width, dash=10, gap=7):
    import math
    x0, y0 = p0
    x1, y1 = p1
    total = math.hypot(x1 - x0, y1 - y0)
    if total == 0:
        return
    dx, dy = (x1 - x0) / total, (y1 - y0) / total
    n = 0.0
    while n < total:
        sx, sy = x0 + dx * n, y0 + dy * n
        e = min(n + dash, total)
        ex, ey = x0 + dx * e, y0 + dy * e
        draw.line((sx, sy, ex, ey), fill=color, width=width)
        n += dash + gap


def main() -> Path:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    # Title
    d.text((40, 28), "FibreOps — Autonomous Fibre Outage Response", font=F_TITLE, fill=INK)
    d.text((42, 74),
           "Microsoft Agent Framework  •  Azure AI Foundry Agent Service  •  Event Hubs  •  Teams  •  D365 (mock)",
           font=F_SUB, fill=MUTED)

    # ---- INPUT: Telemetry -------------------------------------------------
    tele = (40, 150, 320, 470)
    inner = panel(d, tele, "Telemetry", AZURE)
    chip(d, (60, 205, 300, 285),
         [("Azure Event Hub", F_BODY_B, INK),
          ("DefaultAzureCredential producer / consumer", F_SMALL, MUTED)], AZURE)
    chip(d, (60, 300, 300, 370),
         [("Synthetic generator", F_BODY_B, INK),
          ("deterministic OLT signal burst", F_SMALL, MUTED)], TEAL)
    chip(d, (60, 385, 300, 450),
         [("TelemetrySignal", F_BODY_B, AZURE_DK),
          ("severity • node • loss_of_light", F_SMALL, MUTED)], GREY, fill="#f7f7f7")

    # ---- ORCHESTRATOR + AGENTS -------------------------------------------
    orch = (400, 150, 880, 540)
    inner = panel(d, orch, "Orchestrator  (handle_signal loop)", AZURE_DK)
    chip(d, (420, 200, 860, 250),
         [("1) analyse  2) coordinate + ticket + Teams  3) dispatch  4) persist RunRecord",
           F_SMALL, MUTED)], AZURE_DK, fill="#eef5fb")
    # agent pipeline
    chip(d, (420, 262, 860, 320),
         [("IncidentAnalysisAgent", F_BODY_B, INK),
          ("classify severity • root-cause • SOP lookup", F_SMALL, MUTED)], PURPLE)
    chip(d, (420, 332, 860, 390),
         [("NetOpsCoordinatorAgent", F_BODY_B, INK),
          ("file D365 ticket • post Teams notice", F_SMALL, MUTED)], PURPLE)
    chip(d, (420, 402, 860, 460),
         [("FieldDispatchAgent", F_BODY_B, INK),
          ("pick engineer • book resource • update Teams", F_SMALL, MUTED)], PURPLE)
    # backend selector
    chip(d, (420, 472, 860, 525),
         [("Agent backend:  hosted (Foundry Agent Service)  |  foundry (FoundryChatClient)  |  local",
           F_SMALL, AZURE_DK)], AMBER, fill="#fff8e8")

    # ---- INTEGRATIONS (right) --------------------------------------------
    integ = (960, 150, 1330, 540)
    panel(d, integ, "Integrations", TEAL)
    chip(d, (980, 200, 1310, 258),
         [("Microsoft Teams", F_BODY_B, INK),
          ("Adaptive Cards via Incoming Webhook", F_SMALL, MUTED)], AZURE)
    chip(d, (980, 270, 1310, 328),
         [("D365 Field Service (mock)", F_BODY_B, INK),
          ("FastAPI Dataverse-shaped REST", F_SMALL, MUTED)], TEAL)
    chip(d, (980, 340, 1310, 398),
         [("Azure AI Voice Live", F_BODY_B, INK),
          ("SSML status updates per severity", F_SMALL, MUTED)], PURPLE)
    chip(d, (980, 410, 1310, 468),
         [("GitHub Copilot SDK adapter", F_BODY_B, INK),
          ("chat over the same orchestrator", F_SMALL, MUTED)], INK)
    chip(d, (980, 480, 1310, 525),
         [("M365 Copilot declarative agent + action package", F_SMALL, MUTED)],
         GREY, fill="#f7f7f7")

    # ---- OBSERVABILITY + OPTIMISER (far right) ---------------------------
    obs = (1370, 150, 1720, 330)
    panel(d, obs, "Observability", AMBER)
    chip(d, (1390, 200, 1700, 312),
         [("OpenTelemetry spans", F_BODY_B, INK),
          ("structured JSON logs", F_BODY, MUTED),
          ("→ state/traces.jsonl", F_SMALL, MUTED),
          ("→ Application Insights", F_SMALL, MUTED)], AMBER, fill="#fff8e8")

    opt = (1370, 360, 1720, 540)
    panel(d, opt, "Optimiser", GREEN)
    chip(d, (1390, 410, 1700, 522),
         [("Rubric-based evaluation", F_BODY_B, INK),
          ("scores every run", F_BODY, MUTED),
          ("→ improvement suggestions", F_SMALL, MUTED),
          ("(FoundryEvals-ready)", F_SMALL, MUTED)], GREEN, fill="#eef7ee")

    # ---- TOOLS & KNOWLEDGE (bottom band) ---------------------------------
    tools = (400, 600, 1330, 800)
    panel(d, tools, "Tools & Knowledge  (typed Python functions)", PURPLE)
    tool_defs = [
        ("knowledge", "SOPs • topology • Web/Work IQ"),
        ("ticketing", "create / update D365 incident"),
        ("teams", "outage notice • status update"),
        ("dispatch", "find + assign engineer"),
        ("memory", "remember / recall procedural"),
        ("voice", "speak status (Voice Live)"),
    ]
    tx = 420
    tw = (1310 - 420 - 5 * 14) / 6
    for name, desc in tool_defs:
        chip(d, (tx, 658, tx + tw, 788),
             [(name, F_BODY_B, INK), (desc, F_SMALL, MUTED)], PURPLE, fill="#f6f1fa")
        tx += tw + 14

    # ---- STATE STORE -----------------------------------------------------
    state = (40, 600, 320, 800)
    panel(d, state, "State store", GREY)
    chip(d, (60, 655, 300, 790),
         [("./state/*.jsonl", F_BODY_B, INK),
          ("runs • traces", F_SMALL, MUTED),
          ("teams_outbox • voice_outbox", F_SMALL, MUTED),
          ("iq_lookups • optimiser", F_SMALL, MUTED),
          ("foundry_agents • d365_store", F_SMALL, MUTED)], GREY, fill="#f7f7f7")

    # ---- NOC UI ----------------------------------------------------------
    ui = (400, 850, 1330, 1010)
    panel(d, ui, "NOC Operations Console", AZURE)
    chip(d, (420, 905, 1310, 995),
         [("FastAPI + HTMX + Tailwind single-page dashboard", F_BODY_B, INK),
          ("active incidents • incident timeline • optimiser scores • Teams card preview • JSON API + /healthz",
           F_SMALL, MUTED)], AZURE, fill="#eef5fb")

    # ---- DEPLOY ----------------------------------------------------------
    dep = (1370, 850, 1720, 1010)
    panel(d, dep, "Deploy", AZURE_DK)
    chip(d, (1390, 905, 1700, 995),
         [("azd  →  Bicep (infra/main.bicep)", F_BODY_B, INK),
          ("App Service container", F_SMALL, MUTED),
          ("managed identity role grants", F_SMALL, MUTED)], AZURE_DK, fill="#eef5fb")

    # ======================= ARROWS =======================================
    arrow(d, (320, 320), (400, 330), AZURE, label="signals")
    # orchestrator agents -> integrations
    arrow(d, (880, 300), (960, 229), AZURE, label="Teams", label_off=(0, -16))
    arrow(d, (880, 361), (960, 299), TEAL, label="ticket", label_off=(0, 4))
    arrow(d, (880, 431), (960, 369), PURPLE, label="voice", label_off=(0, 6))
    # orchestrator <-> tools
    arrow(d, (620, 540), (620, 600), PURPLE, label="invoke tools", label_off=(78, -10))
    arrow(d, (740, 600), (740, 540), PURPLE)
    # tools -> state
    arrow(d, (400, 700), (320, 700), GREY, label="persist", label_off=(0, -16))
    # UI reads state
    arrow(d, (400, 930), (320, 760), AZURE, label="reads", label_off=(0, -14))
    # Observability fed from orchestrator via the top gutter (clean orthogonal)
    poly_arrow(d, [(660, 150), (660, 126), (1500, 126), (1500, 150)], AMBER,
               dashed=True, label="OTel spans", label_at=(1080, 126), label_off=(0, -18))
    # Optimiser reads run records from the state store via the top gutter
    poly_arrow(d, [(280, 600), (280, 112), (1650, 112), (1650, 360)], GREEN,
               dashed=True, label="reads runs.jsonl", label_at=(1180, 112),
               label_off=(0, -18))

    out = Path(__file__).resolve().parents[1] / "docs" / "images" / "architecture-diagram.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG")
    return out


if __name__ == "__main__":
    path = main()
    print(f"Wrote {path}")
