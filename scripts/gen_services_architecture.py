"""Generate the FibreOps *services* architecture diagram (PNG).

Swim-lane style (Client → Application → Agent Framework Orchestration →
External Services) with numbered nodes, a legend, an Azure services band and a
security/governance box — modelled on the BRK241 reference deck diagram.

Pure-Pillow renderer (only ``Pillow`` required)::

    .\\.venv\\Scripts\\python.exe scripts\\gen_services_architecture.py

Output: docs/images/services-architecture.png
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------
BG = "#ffffff"
INK = "#1f2733"
MUTED = "#5c6470"
LANE_BG = "#eef5fc"
LANE_BORDER = "#cfe0f2"
AZURE = "#0078d4"
AZURE_DK = "#0a5ca8"
PURPLE = "#7a5cc7"
TEAL = "#0a9b8a"
GREEN = "#3a9b35"
AMBER = "#b8860b"
BADGE = "#1f6fc4"
SHADOW = "#dde6f0"

W, H = 1680, 1060


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    for name in (("arialbd.ttf" if bold else "arial.ttf"),
                 ("seguisb.ttf" if bold else "segoeui.ttf"),
                 ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


F_TITLE = _font(34, bold=True)
F_SUB = _font(18)
F_LANE = _font(17, bold=True)
F_NODE = _font(14, bold=True)
F_SMALL = _font(12)
F_TINY = _font(11)
F_BADGE = _font(13, bold=True)
F_LBL = _font(12)


def tsize(d, t, f):
    l, tp, r, b = d.textbbox((0, 0), t, font=f)
    return r - l, b - tp


def ctext(d, cx, y, text, font, fill):
    w, _ = tsize(d, text, font)
    d.text((cx - w / 2, y), text, font=font, fill=fill)


def wrap(d, text, font, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if tsize(d, trial, font)[0] <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def caption(d, cx, y, text, font, fill, max_w=158):
    for ln in wrap(d, text, font, max_w):
        ctext(d, cx, y, ln, font, fill)
        y += tsize(d, ln, font)[1] + 2
    return y


# ---------------------------------------------------------------------------
# Mini icons (simple flat glyphs evoking the Azure icon set)
# ---------------------------------------------------------------------------
def ic_user(d, cx, cy, s, c):
    r = s * 0.26
    d.ellipse((cx - r, cy - s * 0.5, cx + r, cy - s * 0.5 + 2 * r), fill=c)
    d.pieslice((cx - s * 0.45, cy - s * 0.02, cx + s * 0.45, cy + s * 0.9),
               180, 360, fill=c)


def ic_server(d, cx, cy, s, c):
    w, h = s * 0.8, s * 0.34
    for off in (-h * 1.1, h * 0.1):
        d.rounded_rectangle((cx - w / 2, cy + off, cx + w / 2, cy + off + h),
                            radius=4, fill=c)
        d.ellipse((cx + w / 2 - 12, cy + off + h / 2 - 3,
                   cx + w / 2 - 6, cy + off + h / 2 + 3), fill="#ffffff")


def ic_event(d, cx, cy, s, c):
    r = s * 0.2
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=c)
    for a in (0, 120, 240):
        px = cx + math.cos(math.radians(a)) * s * 0.45
        py = cy + math.sin(math.radians(a)) * s * 0.45
        d.line((cx, cy, px, py), fill=c, width=3)
        d.ellipse((px - 5, py - 5, px + 5, py + 5), fill=c)


def ic_robot(d, cx, cy, s, c):
    w = s * 0.72
    d.line((cx, cy - s * 0.55, cx, cy - s * 0.35), fill=c, width=3)
    d.ellipse((cx - 4, cy - s * 0.62, cx + 4, cy - s * 0.54), fill=c)
    d.rounded_rectangle((cx - w / 2, cy - s * 0.35, cx + w / 2, cy + s * 0.45),
                        radius=8, fill=c)
    for ex in (-w * 0.22, w * 0.22):
        d.ellipse((cx + ex - 5, cy - 6, cx + ex + 5, cy + 4), fill="#ffffff")


def ic_chat(d, cx, cy, s, c):
    w, h = s * 0.85, s * 0.62
    d.rounded_rectangle((cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2),
                        radius=9, fill=c)
    d.polygon([(cx - w * 0.18, cy + h / 2), (cx - w * 0.02, cy + h / 2),
               (cx - w * 0.28, cy + h / 2 + 9)], fill=c)
    for ex in (-w * 0.22, 0, w * 0.22):
        d.ellipse((cx + ex - 3, cy - 3, cx + ex + 3, cy + 3), fill="#ffffff")


def ic_book(d, cx, cy, s, c):
    w, h = s * 0.7, s * 0.78
    d.rounded_rectangle((cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2),
                        radius=4, fill=c)
    d.line((cx, cy - h / 2 + 3, cx, cy + h / 2 - 3), fill="#ffffff", width=2)
    for yy in (-h * 0.2, 0, h * 0.2):
        d.line((cx - w / 2 + 6, cy + yy, cx - 4, cy + yy), fill="#ffffff", width=2)
        d.line((cx + 4, cy + yy, cx + w / 2 - 6, cy + yy), fill="#ffffff", width=2)


def ic_search(d, cx, cy, s, c):
    r = s * 0.3
    ox, oy = cx - s * 0.12, cy - s * 0.12
    d.ellipse((ox - r, oy - r, ox + r, oy + r), outline=c, width=4)
    d.line((ox + r * 0.7, oy + r * 0.7, cx + s * 0.4, cy + s * 0.4), fill=c, width=4)


def ic_gear(d, cx, cy, s, c):
    r = s * 0.3
    for a in range(0, 360, 45):
        px = cx + math.cos(math.radians(a)) * r * 1.35
        py = cy + math.sin(math.radians(a)) * r * 1.35
        d.ellipse((px - 4, py - 4, px + 4, py + 4), fill=c)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=c)
    d.ellipse((cx - r * 0.4, cy - r * 0.4, cx + r * 0.4, cy + r * 0.4), fill="#ffffff")


def ic_teams(d, cx, cy, s, c):
    w = s * 0.8
    d.rounded_rectangle((cx - w / 2, cy - w / 2, cx + w / 2, cy + w / 2),
                        radius=8, fill="#5059c9")
    ctext(d, cx, cy - 11, "T", _font(int(s * 0.7), bold=True), "#ffffff")


def ic_dynamics(d, cx, cy, s, c):
    w = s * 0.78
    d.rounded_rectangle((cx - w / 2, cy - w / 2, cx + w / 2, cy + w / 2),
                        radius=8, fill="#0b53ce")
    g = w * 0.22
    for dx in (-g, g):
        for dy in (-g, g):
            d.ellipse((cx + dx - 4, cy + dy - 4, cx + dx + 4, cy + dy + 4),
                      fill="#ffffff")


def ic_speaker(d, cx, cy, s, c):
    d.polygon([(cx - s * 0.4, cy - s * 0.18), (cx - s * 0.12, cy - s * 0.18),
               (cx + s * 0.08, cy - s * 0.4), (cx + s * 0.08, cy + s * 0.4),
               (cx - s * 0.12, cy + s * 0.18), (cx - s * 0.4, cy + s * 0.18)],
              fill=c)
    for rr in (s * 0.28, s * 0.45):
        d.arc((cx + s * 0.02 - rr, cy - rr, cx + s * 0.02 + rr, cy + rr),
              -55, 55, fill=c, width=3)


def ic_foundry(d, cx, cy, s, c):
    r = s * 0.42
    pts = [(cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a)))
           for a in (-90, -18, 54, 126, 198)]
    d.polygon(pts, outline=c, width=4)
    d.ellipse((cx - 5, cy - 5, cx + 5, cy + 5), fill=c)


def ic_shield(d, cx, cy, s, c):
    w, h = s * 0.7, s * 0.85
    d.polygon([(cx, cy - h / 2), (cx + w / 2, cy - h / 2 + 6),
               (cx + w / 2, cy + h * 0.1), (cx, cy + h / 2),
               (cx - w / 2, cy + h * 0.1), (cx - w / 2, cy - h / 2 + 6)], fill=c)
    d.line((cx - 7, cy, cx - 1, cy + 8), fill="#ffffff", width=3)
    d.line((cx - 1, cy + 8, cx + 9, cy - 7), fill="#ffffff", width=3)


# ---------------------------------------------------------------------------
def lane_header(d, x0, x1, label, icon_fn):
    y0, y1 = 20, 60
    d.rounded_rectangle((x0, y0, x1, y1), radius=10, fill=LANE_BG, outline=LANE_BORDER, width=2)
    icon_fn(d, x0 + 24, (y0 + y1) / 2, 22, AZURE)
    d.text((x0 + 44, y0 + 11), label, font=F_LANE, fill=AZURE_DK)


def node(d, cx, top, icon_fn, accent, badge, cap, tile_w=96, tile_h=76):
    x0, y0, x1, y1 = cx - tile_w / 2, top, cx + tile_w / 2, top + tile_h
    d.rounded_rectangle((x0 + 3, y0 + 4, x1 + 3, y1 + 4), radius=12, fill=SHADOW)
    d.rounded_rectangle((x0, y0, x1, y1), radius=12, fill="#ffffff", outline=accent, width=2)
    icon_fn(d, cx, (y0 + y1) / 2, 34, accent)
    # numbered badge
    bx, by = x0 + 2, y0 + 2
    d.ellipse((bx - 11, by - 11, bx + 11, by + 11), fill=BADGE, outline="#ffffff", width=2)
    ctext(d, bx, by - 8, str(badge), F_BADGE, "#ffffff")
    caption(d, cx, y1 + 6, cap, F_SMALL, INK)
    return (x0, y0, x1, y1)


def arrowhead(d, p0, p1, color, width=2, size=10):
    x0, y0 = p0
    x1, y1 = p1
    ang = math.atan2(y1 - y0, x1 - x0)
    for da in (math.radians(25), -math.radians(25)):
        d.line((x1, y1, x1 - size * math.cos(ang + da), y1 - size * math.sin(ang + da)),
               fill=color, width=width)


def dline(d, p0, p1, color, width, dash=9, gap=6):
    x0, y0 = p0
    x1, y1 = p1
    total = math.hypot(x1 - x0, y1 - y0)
    if total == 0:
        return
    dx, dy = (x1 - x0) / total, (y1 - y0) / total
    n = 0.0
    while n < total:
        s = min(n + dash, total)
        d.line((x0 + dx * n, y0 + dy * n, x0 + dx * s, y0 + dy * s), fill=color, width=width)
        n += dash + gap


def flow(d, p0, p1, color=INK, width=3, dashed=False, label=None, loff=(0, -16)):
    if dashed:
        dline(d, p0, p1, color, width)
    else:
        d.line((p0[0], p0[1], p1[0], p1[1]), fill=color, width=width)
    arrowhead(d, p0, p1, color, width)
    if label:
        mx, my = (p0[0] + p1[0]) / 2 + loff[0], (p0[1] + p1[1]) / 2 + loff[1]
        tw, th = tsize(d, label, F_LBL)
        d.rectangle((mx - tw / 2 - 3, my - 1, mx + tw / 2 + 3, my + th + 1), fill="#ffffff")
        ctext(d, mx, my, label, F_LBL, MUTED)


def main() -> Path:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    # Title
    d.text((36, 76), "FibreOps — Autonomous Fibre Outage Response · Services Architecture",
           font=F_TITLE, fill=INK)

    # Lane headers
    lane_header(d, 30, 250, "CLIENT", ic_user)
    lane_header(d, 270, 560, "APPLICATION LAYER", ic_server)
    lane_header(d, 580, 1230, "AGENT FRAMEWORK ORCHESTRATION", ic_robot)
    lane_header(d, 1250, 1650, "EXTERNAL SERVICES", ic_foundry)

    # Orchestration dashed container
    cont = (582, 150, 1232, 760)
    d.rounded_rectangle(cont, radius=16, fill="#f6f9fe", outline="#bcd4ee", width=2)
    dline(d, (cont[0], cont[1]), (cont[2], cont[1]), AZURE, 2)
    dline(d, (cont[2], cont[1]), (cont[2], cont[3]), AZURE, 2)
    dline(d, (cont[2], cont[3]), (cont[0], cont[3]), AZURE, 2)
    dline(d, (cont[0], cont[3]), (cont[0], cont[1]), AZURE, 2)
    d.ellipse((600, 168, 624, 192), fill=BADGE, outline="#ffffff", width=2)
    ctext(d, 612, 172, "3", F_BADGE, "#ffffff")
    d.text((636, 166), "Agent Framework Workflow", font=F_NODE, fill=INK)
    d.text((636, 188), "Orchestrator — handle_signal loop (Microsoft Agent Framework)",
           font=F_SMALL, fill=MUTED)

    # ---- Nodes -----------------------------------------------------------
    # CLIENT
    n1 = node(d, 140, 360, ic_user, AZURE, 1, "NOC Operator / Foundry Playground")
    # APPLICATION LAYER
    n2 = node(d, 415, 280, ic_server, AZURE, 2, "NOC Console — FastAPI + HTMX · Demo CLI")
    n3 = node(d, 415, 470, ic_event, AZURE, 3, "Telemetry ingest — Event Hub · generator")
    # AGENT lane — top pipeline row
    n4 = node(d, 690, 250, ic_robot, PURPLE, 4, "IncidentAnalysisAgent")
    n5 = node(d, 855, 250, ic_chat, PURPLE, 5, "NetOpsCoordinatorAgent")
    n6 = node(d, 1020, 250, ic_chat, PURPLE, 6, "FieldDispatchAgent")
    n7 = node(d, 1150, 250, ic_gear, TEAL, 7, "Integration tools (FunctionTool)")
    # AGENT lane — bottom knowledge row
    n8 = node(d, 720, 560, ic_book, TEAL, 8, "Knowledge — SOPs + topology")
    n9 = node(d, 920, 560, ic_search, TEAL, 9, "Web IQ / Work IQ search")
    # EXTERNAL SERVICES
    n10 = node(d, 1450, 210, ic_teams, AZURE, 10, "Microsoft Teams (Adaptive Cards)")
    n11 = node(d, 1450, 370, ic_dynamics, GREEN, 11, "D365 Field Service (mock)")
    n12 = node(d, 1450, 530, ic_speaker, PURPLE, 12, "Azure AI Voice Live")
    n13 = node(d, 1450, 680, ic_foundry, AZURE_DK, 13, "Microsoft Foundry Agent Service")

    def L(n):  # left-center
        return (n[0], (n[1] + n[3]) / 2)

    def R(n):
        return (n[2], (n[1] + n[3]) / 2)

    def T(n):
        return ((n[0] + n[2]) / 2, n[1])

    def B(n):
        return ((n[0] + n[2]) / 2, n[3])

    # ---- Flows -----------------------------------------------------------
    flow(d, R(n1), L(n2), AZURE, label="interact")
    flow(d, (n2[0], n2[3] - 6), (n1[2], n1[1] + 18), AZURE, dashed=True,
         label="responses", loff=(0, 6))
    flow(d, R(n2), (n4[0], n4[1] + 18), AZURE_DK, label="orchestrate", loff=(0, -16))
    flow(d, R(n3), (n4[0], n4[3] - 16), AZURE_DK, label="signals", loff=(0, 8))
    flow(d, R(n4), L(n5), PURPLE)
    flow(d, R(n5), L(n6), PURPLE)
    flow(d, R(n6), L(n7), PURPLE)
    # agents <-> knowledge/search tools
    flow(d, (676, 366), T(n8), TEAL, label="SOP lookup", loff=(-2, 0))
    flow(d, (706, 366), T(n9), TEAL, label="Web/Work IQ", loff=(40, 0))
    # integration tools -> external services
    flow(d, R(n7), L(n10), AZURE, dashed=True, label="notice / update", loff=(0, -16))
    flow(d, R(n7), L(n11), GREEN, dashed=True, label="ticket / booking", loff=(0, -14))
    flow(d, R(n7), L(n12), PURPLE, dashed=True, label="SSML speech", loff=(0, 8))
    # Foundry hosts the agents
    flow(d, L(n13), (cont[2], cont[3] - 30), AZURE_DK, dashed=True,
         label="hosts Prompt Agents", loff=(0, 16))
    # Work IQ remote connection (external data)
    flow(d, B(n9), (1280, 760), TEAL, dashed=True, label="Microsoft 365 / web", loff=(0, -16))

    # ============ BOTTOM BAND ============================================
    band_y = 800
    # Legend
    lg = (30, band_y, 360, 1010)
    d.rounded_rectangle(lg, radius=12, fill=LANE_BG, outline=LANE_BORDER, width=2)
    d.text((50, band_y + 14), "LEGEND", font=F_LANE, fill=AZURE_DK)
    d.line((52, band_y + 62, 120, band_y + 62), fill=INK, width=3)
    arrowhead(d, (52, band_y + 62), (120, band_y + 62), INK, 3)
    d.text((132, band_y + 53), "Orchestration flow", font=F_SMALL, fill=INK)
    dline(d, (52, band_y + 100), (120, band_y + 100), MUTED, 3)
    arrowhead(d, (52, band_y + 100), (120, band_y + 100), MUTED, 3)
    d.text((132, band_y + 91), "External data flow", font=F_SMALL, fill=INK)
    d.text((50, band_y + 135), "①–⑬  numbered service nodes", font=F_SMALL, fill=MUTED)
    d.text((50, band_y + 160), "Backends: hosted · foundry · local", font=F_SMALL, fill=MUTED)

    # Azure services & components
    sv = (380, band_y, 1230, 1010)
    d.rounded_rectangle(sv, radius=12, fill=LANE_BG, outline=LANE_BORDER, width=2)
    d.text((400, band_y + 14), "AZURE SERVICES & COMPONENTS", font=F_LANE, fill=AZURE_DK)
    comps = [
        (ic_server, "App Service /\nCompute"),
        (ic_foundry, "Microsoft\nFoundry"),
        (ic_robot, "Foundry\nAgent Service"),
        (ic_event, "Azure\nEvent Hubs"),
        (ic_search, "Application\nInsights"),
        (ic_teams, "Microsoft\nTeams"),
        (ic_dynamics, "Dynamics 365\nField Service"),
        (ic_speaker, "Azure AI\nVoice Live"),
    ]
    cx0, step = 440, 100
    for i, (icon, lbl) in enumerate(comps):
        cx = cx0 + i * step
        icon(d, cx, band_y + 78, 30, AZURE)
        yy = band_y + 108
        for ln in lbl.split("\n"):
            ctext(d, cx, yy, ln, F_TINY, MUTED)
            yy += 14

    # Security & governance
    sec = (1250, band_y, 1650, 1010)
    d.rounded_rectangle(sec, radius=12, fill="#eef7ee", outline="#cbe6c9", width=2)
    ic_shield(d, 1282, band_y + 34, 30, GREEN)
    d.text((1306, band_y + 18), "Security & Governance", font=F_LANE, fill="#256e22")
    body = ("Managed identity (DefaultAzureCredential), Microsoft Entra ID, and "
            "RBAC role grants (scripts/grant-mi-roles.ps1) applied across all "
            "components. Secrets via env / Key Vault.")
    yy = band_y + 64
    for ln in wrap(d, body, F_SMALL, 372):
        d.text((1268, yy), ln, font=F_SMALL, fill=INK)
        yy += 19

    out = Path(__file__).resolve().parents[1] / "docs" / "images" / "services-architecture.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG")
    return out


if __name__ == "__main__":
    print(f"Wrote {main()}")
