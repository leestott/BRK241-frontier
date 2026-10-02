"""Render the high-level FibreOps scenario in docs/images/architecture.png."""
from pathlib import Path

from PIL import Image, ImageDraw

from gen_architecture_diagram import (
    AMBER,
    AZURE,
    AZURE_DK,
    BG,
    GREEN,
    GREY,
    INK,
    MUTED,
    PURPLE,
    TEAL,
    _font,
    arrow,
    chip,
    panel,
)


def main() -> Path:
    image = Image.new("RGB", (1760, 1080), BG)
    draw = ImageDraw.Draw(image)
    title = _font(38, bold=True)
    subtitle = _font(19)
    body = _font(16)
    detail = _font(14)

    draw.text((40, 30), "FibreOps — Autonomous Fibre Outage Response",
              font=title, fill=INK)
    draw.text((42, 78),
              "Managed Foundry text agents + Voice Agents Preview | Entra-protected NOC console",
              font=subtitle, fill=MUTED)

    panel(draw, (40, 135, 350, 245), "NOC operator", AZURE)
    chip(draw, (60, 190, 330, 230),
         [("Incident dashboard + microphone", body, INK)], AZURE)
    panel(draw, (410, 135, 960, 245), "NOC console", AZURE_DK)
    chip(draw, (430, 190, 940, 230),
         [("FastAPI + HTMX | authenticated WebSocket voice proxy", body, INK)],
         AZURE_DK)
    panel(draw, (1030, 135, 1710, 245), "Microsoft Foundry", PURPLE)
    chip(draw, (1050, 190, 1690, 230),
         [("Hosted Prompt Agents (gpt-5.4-mini) + managed realtime voice agent",
           body, INK)], PURPLE)

    panel(draw, (40, 300, 350, 665), "Fibre telemetry", AZURE)
    chip(draw, (60, 360, 330, 460),
         [("Azure Event Hubs", body, INK),
          ("or synthetic OLT signals", detail, MUTED)], AZURE)
    chip(draw, (60, 480, 330, 625),
         [("Loss of light", body, INK),
          ("Attenuation / BER anomalies", detail, MUTED),
          ("Node + customer impact", detail, MUTED)], TEAL)

    panel(draw, (410, 300, 960, 665), "Outage response agent system", AZURE_DK)
    chip(draw, (430, 360, 940, 435),
         [("1. IncidentAnalysisAgent", body, INK),
          ("Classify + ground on Foundry IQ", detail, MUTED)], PURPLE)
    chip(draw, (430, 455, 940, 530),
         [("2. NetOpsCoordinatorAgent", body, INK),
          ("Ticket + Teams outage notice", detail, MUTED)], PURPLE)
    chip(draw, (430, 550, 940, 625),
         [("3. FieldDispatchAgent", body, INK),
          ("Book engineer + status update", detail, MUTED)], PURPLE)

    panel(draw, (1030, 300, 1710, 665), "Channels and field response", TEAL)
    chip(draw, (1050, 360, 1690, 435),
         [("Microsoft Teams", body, INK),
          ("Adaptive Card notifications", detail, MUTED)], AZURE)
    chip(draw, (1050, 455, 1690, 530),
         [("D365 Field Service (mock)", body, INK),
          ("Ticket + engineer booking", detail, MUTED)], GREEN)
    chip(draw, (1050, 550, 1690, 625),
         [("Foundry Voice Agent (Preview)", body, INK),
          ("Duplex speech via NOC proxy; incident status text from tools",
           detail, MUTED)], PURPLE)

    panel(draw, (40, 735, 350, 985), "Optimiser", GREEN)
    chip(draw, (60, 800, 330, 945),
         [("Evaluate each run", body, INK),
          ("Rubric + improvement suggestions", detail, MUTED)], GREEN)
    panel(draw, (410, 735, 1200, 985), "Tools and knowledge", PURPLE)
    chip(draw, (430, 800, 1180, 945),
         [("Azure AI Search / Foundry IQ knowledge base", body, INK),
          ("SOPs + topology | memory | Teams | dispatch | status outbox",
           detail, MUTED)], PURPLE)
    panel(draw, (1260, 735, 1710, 985), "Observability", AMBER)
    chip(draw, (1280, 800, 1690, 945),
         [("OpenTelemetry + Application Insights", body, INK),
          ("Runs and incident timelines", detail, MUTED)], AMBER)

    arrow(draw, (350, 190), (410, 190), AZURE)
    arrow(draw, (960, 190), (1030, 190), PURPLE)
    arrow(draw, (350, 485), (410, 485), AZURE, label="signals")
    arrow(draw, (960, 390), (1030, 390), AZURE, label="notice")
    arrow(draw, (960, 485), (1030, 485), GREEN, label="ticket")
    arrow(draw, (960, 580), (1030, 580), PURPLE, label="voice status")
    arrow(draw, (690, 665), (690, 735), PURPLE)
    arrow(draw, (430, 665), (330, 735), GREEN, label="runs")
    arrow(draw, (1485, 665), (1485, 735), AMBER)

    output = Path(__file__).resolve().parents[1] / "docs" / "images" / "architecture.png"
    image.save(output, "PNG")
    return output


if __name__ == "__main__":
    print(f"Wrote {main()}")
