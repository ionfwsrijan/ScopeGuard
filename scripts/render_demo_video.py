from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageFilter


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "demo" / "video"
W, H, FPS = 3840, 2160, 60
BG = (245, 244, 239)
BRAND_BG = (17, 18, 15)
PANEL = (255, 255, 252)
LINE = (224, 225, 217)
WHITE = (25, 27, 22)
MUTED = (91, 95, 83)
DIM = (123, 126, 116)
LAV = (197, 239, 67)
TEAL = (91, 125, 47)
AMBER = (194, 137, 32)
RED = (181, 73, 65)
FONT = r"C:\Windows\Fonts\Candara.ttf"
FONT_B = r"C:\Windows\Fonts\bahnschrift.ttf"
FONT_M = r"C:\Windows\Fonts\consola.ttf"

SCENES = [
    (240, "Scope Guard. Every extra scope item gets a price."),
    (540, "Before a $120,000 launch, Northwind’s client asks for mobile, a rush, twenty percent off, and unlimited liability."),
    (1020, "The live workspace brings the Northwind engagement, current packet, signed order, and audit status into one view. Open the case to compare each priced commitment, see who must approve, and spot any stale signatures."),
    (1320, "Each request has a clear decision, price, evidence, and required approvers. Mobile becomes a beta, rush is limited to one week, the discount narrows to twelve percent, and unlimited liability is refused. Those boundaries become the terms that delivery can rely on."),
    (720, "Switch coordinates Scout, Pricer, Red Team, Clerk, and Delivery. The deterministic core prices the work; named humans approve the packet’s exact hash."),
    (720, "The first twenty-percent discount cuts gross margin to 27.5%, below the 30% floor. Red Team blocks it. The 12% revision protects $3,840."),
    (1080, "The signed order contains four priced items, an issuer signature, and the client’s separate countersignature. Verification recomputes its body hash, receipt ID, amounts, and signatures. Any edit would break the hash; only accepted terms pass to delivery."),
    (240, "Scope Guard. Priced. Approved. Proven."),
]


def f(size: int, bold: bool = False, mono: bool = False):
    font_obj = ImageFont.truetype(FONT_M if mono else (FONT_B if bold else FONT), size)
    if bold and not mono:
        font_obj.set_variation_by_axes([650, 92])
    return font_obj


def rr(d, box, fill, outline=LINE, radius=28, width=3):
    d.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def label(d, xy, text, size=34, color=WHITE, bold=False, mono=False, anchor=None):
    d.text(xy, text, font=f(size, bold, mono), fill=color, anchor=anchor)


def wrap_lines(draw, text, font_obj, width):
    lines = []
    for para in text.split("\n"):
        row = ""
        for word in para.split():
            candidate = f"{row} {word}".strip()
            if draw.textbbox((0, 0), candidate, font=font_obj)[2] <= width:
                row = candidate
            else:
                if row:
                    lines.append(row)
                row = word
        if row:
            lines.append(row)
    return lines


def paragraph(d, xy, text, size=46, color=MUTED, width=1350, gap=17, bold=False):
    font_obj = f(size, bold)
    x, y = xy
    for line in wrap_lines(d, text, font_obj, width):
        d.text((x, y), line, font=font_obj, fill=color)
        y += size + gap
    return y


def chrome_header(d, num, section):
    # Always leave room for the real interface and its key content.
    d.rectangle((0, 0, W, 156), fill=(*BRAND_BG, 245))
    d.rounded_rectangle((58, 30, 106, 80), radius=13, fill=(39, 42, 31), outline=(75, 83, 54), width=2)
    label(d, (82, 55), "SG", 19, LAV, True, True, "mm")
    label(d, (124, 43), "SCOPE GUARD", 32, (250, 250, 244), True)
    label(d, (126, 92), "SCOPE MANAGEMENT  /  FLIP THE SWITCH 2026", 20, (145, 150, 133), True, True)
    label(d, (W - 105, 58), f"{num:02d}  /  08", 27, (230, 232, 220), True, True, "ra")
    label(d, (W - 105, 104), section.upper(), 21, LAV, True, True, "ra")
    d.line((100, 155, W - 100, 155), fill=LINE, width=2)


def title_slide(section, title, subtitle, number):
    im = Image.new("RGB", (W, H), BG).convert("RGBA")
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    g = ImageDraw.Draw(glow)
    g.ellipse((2200, -700, 4350, 1400), fill=(155, 194, 55, 34))
    g.ellipse((-900, 1050, 1100, 2950), fill=(94, 132, 48, 24))
    glow = glow.filter(ImageFilter.GaussianBlur(210))
    im = Image.alpha_composite(im, glow).convert("RGB")
    d = ImageDraw.Draw(im)
    chrome_header(d, number, section)
    label(d, (170, 330), section.upper(), 29, TEAL, True, True)
    paragraph(d, (160, 440), title, 118, WHITE, 2100, 10, True)
    paragraph(d, (170, 820), subtitle, 48, MUTED, 1700, 18)
    d.line((155, H - 154, W - 155, H - 154), fill=LINE, width=3)
    d.line((155, H - 154, 155 + int((W - 310) * number / 8), H - 154), fill=LAV, width=8)
    label(d, (166, H - 102), "EVERY ‘ONE MORE THING’ GETS A PRICE.", 22, DIM, True, True)
    label(d, (W - 166, H - 102), "DETERMINISTIC CORE  ·  HUMAN CONSENT  ·  VERIFIABLE HANDOFF", 22, DIM, True, True, "ra")
    return im


def brand_slide(slogan: str) -> Image.Image:
    """Minimal four-second brand lockup using the app's lime crosshair mark."""
    im = Image.new("RGB", (W, H), BRAND_BG).convert("RGBA")
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((970, 580, 2790, 1580), fill=(142, 177, 43, 32))
    gd.ellipse((1140, 720, 2060, 1430), fill=(64, 103, 30, 24))
    glow = glow.filter(ImageFilter.GaussianBlur(220))
    im = Image.alpha_composite(im, glow).convert("RGB")
    d = ImageDraw.Draw(im)
    # The app's crosshair symbol, enlarged for a simple branded opening/close.
    x, y, size = 1748, 515, 344
    d.rounded_rectangle((x, y, x + size, y + size), radius=92, fill=LAV)
    cx, cy = x + size // 2, y + size // 2
    d.ellipse((cx - 70, cy - 70, cx + 70, cy + 70), outline=BRAND_BG, width=10)
    d.line((cx, cy - 105, cx, cy + 105), fill=BRAND_BG, width=10)
    d.line((cx - 105, cy, cx + 105, cy), fill=BRAND_BG, width=10)
    brand_font = f(124, True)
    first, second = "scope", "guard"
    first_w = d.textbbox((0, 0), first, font=brand_font)[2]
    second_w = d.textbbox((0, 0), second, font=brand_font)[2]
    brand_x = (W - first_w - second_w - 6) // 2
    d.text((brand_x, 980), first, font=brand_font, fill=(250, 250, 244))
    d.text((brand_x + first_w + 6, 980), second, font=brand_font, fill=LAV)
    label(d, (W // 2, 1145), "COMMITMENT CONTROL", 23, (145, 150, 133), True, True, "ma")
    d.line((1465, 1240, 2375, 1240), fill=(54, 58, 45), width=3)
    label(d, (W // 2, 1325), slogan, 44, (228, 231, 218), False, False, "ma")
    return im


def tag(d, x, y, text, color):
    font_obj = f(24, True, True)
    tw = d.textbbox((0, 0), text, font=font_obj)[2]
    d.rounded_rectangle((x, y, x + tw + 48, y + 58), radius=27, fill=color)
    d.text((x + 24, y + 12), text, font=font_obj, fill=BG)


def make_slides():
    OUT.mkdir(parents=True, exist_ok=True)
    slides = []

    slides.append(brand_slide("Every one more thing gets a price."))

    im = title_slide("The business risk", "Four small asks.\nA real margin decision.", "The client wants native mobile, a faster launch, a 20% discount, and unlimited liability. The work arrives just before launch on a fixed fee.", 2)
    d = ImageDraw.Draw(im)
    asks = [("MOBILE", "Native iOS + Android", LAV), ("RUSH", "Two weeks sooner", AMBER), ("RATE", "20% off", RED), ("TERMS", "Unlimited liability", TEAL)]
    for i, (code, title, color) in enumerate(asks):
        x = 175 + i * 810
        rr(d, (x, 1250, x + 730, 1535), PANEL, radius=30)
        tag(d, x + 34, 1285, code, color)
        label(d, (x + 34, 1405), title, 39, WHITE, True)
    slides.append(im)

    for num, key, section, title in [
        (3, "live-01-dashboard.png", "Live product walkthrough", "Workspace overview"),
        (4, "live-02-engagement.png", "Live product walkthrough", "Inspect the priced packet"),
        (7, "live-03-receipt.png", "Live product walkthrough", "Signed order · verification passes"),
    ]:
        path = OUT / key
        if not path.exists():
            raise SystemExit(f"Required real-app capture is missing: {path}")
        source = Image.open(path).convert("RGB")
        if source.size != (W, H):
            raise SystemExit(f"Expected native 4K app capture for {key}; got {source.size}")
        # These are direct 4K Chrome captures of the running Scope Guard demo.
        scene = source.convert("RGBA")
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        ld.rounded_rectangle((100, H - 142, 1050, H - 64), radius=28, fill=(*BRAND_BG, 232), outline=(66, 74, 47), width=2)
        label(ld, (140, H - 120), f"{num:02d}  /  LIVE APP  ·  {title.upper()}", 22, TEAL, True, True)
        scene = Image.alpha_composite(scene, layer).convert("RGB")
        slides.append(scene)

    im = title_slide("The independent challenge", "The first answer\ngets blocked.", "At 20% off, gross margin falls to 27.5%, below the 30% floor. The independent Red Team blocks the packet; policy narrows the discount to 12%.", 6)
    d = ImageDraw.Draw(im)
    rr(d, (175, 1175, 1680, 1585), (255, 235, 232), outline=(220, 145, 134), radius=32)
    label(d, (235, 1228), "PACKET V1  ·  BLOCKED", 26, RED, True, True)
    label(d, (235, 1310), "27.50%", 78, WHITE, True)
    label(d, (235, 1432), "MARGIN  ·  BELOW THE 30% FLOOR", 25, RED, True, True)
    rr(d, (1780, 1175, 3440, 1585), (238, 246, 220), outline=(160, 185, 90), radius=32)
    label(d, (1840, 1228), "REVISED PACKET V2  ·  APPROVED", 26, TEAL, True, True)
    label(d, (1840, 1310), "$42,240", 78, WHITE, True)
    label(d, (1840, 1432), "$3,840 MARGIN PROTECTED  ·  12% DISCOUNT", 25, TEAL, True, True)
    slides.insert(4, im)

    im = title_slide("How it fits together", "Agents coordinate.\nThe core decides.", "Switch agents coordinate the workflow around a deterministic pricing core. Human signatures bind to the exact packet hash, and the linked delivery room receives only the signed scope.", 5)
    d = ImageDraw.Draw(im)
    tag(d, 175, 1080, "DETERMINISTIC REPLAY", LAV)
    nodes = [("SWITCH", "Coordinate"), ("SCOUT", "Evidence"), ("PRICER", "Deterministic price"), ("RED TEAM", "Challenge"), ("HUMANS", "Approve hash"), ("CLERK", "Seal + verify"), ("DELIVERY", "Signed scope")]
    x0, gap, cw, y = 115, 29, 490, 1190
    for i, (head, body) in enumerate(nodes):
        x = x0 + i * (cw + gap)
        rr(d, (x, y, x + cw, y + 272), PANEL, radius=26)
        label(d, (x + 31, y + 36), head, 25, (TEAL, TEAL, AMBER, RED, TEAL, TEAL, TEAL)[i], True, True)
        paragraph(d, (x + 30, y + 102), body, 38, WHITE, cw - 60, 9, True)
        if i < len(nodes) - 1:
            d.line((x + cw + 5, y + 136, x + cw + gap - 6, y + 136), fill=DIM, width=5)
            d.polygon([(x + cw + gap - 5, y + 136), (x + cw + gap - 22, y + 124), (x + cw + gap - 22, y + 148)], fill=LAV)
    slides.insert(4, im)

    slides.append(brand_slide("Priced. Approved. Proven."))

    for i, slide in enumerate(slides, 1):
        slide.save(OUT / f"slide-{i:02d}.png", optimize=True)
    for stale in OUT.glob("slide-[0-9][0-9].png"):
        if int(stale.stem[-2:]) > len(slides):
            stale.unlink()
    write_narration()
    return len(slides)


def srt_time(frames: int) -> str:
    ms = round(frames * 1000 / FPS)
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_narration():
    (OUT / "narration.txt").write_text("\n\n".join(line for _, line in SCENES) + "\n", encoding="utf-8")
    segment_dir = OUT / "elevenlabs-scenes"
    segment_dir.mkdir(parents=True, exist_ok=True)
    for i, (_, line) in enumerate(SCENES, 1):
        (segment_dir / f"scene-{i:02d}.txt").write_text(line + "\n", encoding="utf-8")
    entries = []
    cursor = 0
    for i, (frames, line) in enumerate(SCENES, 1):
        entries.extend([str(i), f"{srt_time(cursor)} --> {srt_time(cursor + frames)}", line, ""])
        cursor += frames
    (OUT / "narration.srt").write_text("\n".join(entries), encoding="utf-8")


def render(ffmpeg: Path, count: int):
    clips = OUT / "_clips"
    clips.mkdir(exist_ok=True)
    names = []
    for i, (frames, _) in enumerate(SCENES, 1):
        clip = clips / f"scene-{i:02d}.mp4"
        names.append(clip)
        zoom = "min(zoom+0.000035,1.025)"
        vf = f"zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={W}x{H}:fps={FPS},setsar=1,format=yuv420p"
        args = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y", "-loop", "1", "-framerate", str(FPS), "-i", str(OUT / f"slide-{i:02d}.png"), "-vf", vf, "-frames:v", str(frames), "-an", "-r", str(FPS), "-c:v", "h264_nvenc", "-preset", "p6", "-tune", "hq", "-rc", "vbr", "-cq", "14", "-b:v", "45M", "-maxrate", "65M", "-bufsize", "85M", "-profile:v", "high", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(clip)]
        print(f"Encoding true 4K scene {i}/{count}", flush=True)
        subprocess.run(args, check=True)
    concat = OUT / "_clips.txt"
    concat.write_text("ffconcat version 1.0\n" + "\n".join(f"file '{p.as_posix()}'" for p in names) + "\n", encoding="utf-8")
    target = OUT / "scopeguard-demo-4k60-silent.mp4"
    print("Joining native-4K scenes", flush=True)
    subprocess.run([str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", "-movflags", "+faststart", str(target)], check=True)
    shutil.rmtree(clips)
    concat.unlink(missing_ok=True)
    print(f"Finished: {target}", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--slides-only", action="store_true")
    parser.add_argument("--ffmpeg", type=Path)
    opts = parser.parse_args()
    count = make_slides()
    if opts.slides_only:
        return
    ffmpeg = opts.ffmpeg
    if ffmpeg is None:
        try:
            import imageio_ffmpeg
        except ImportError as exc:
            raise SystemExit("Install imageio-ffmpeg or pass --ffmpeg") from exc
        ffmpeg = Path(imageio_ffmpeg.get_ffmpeg_exe())
    render(ffmpeg, count)


if __name__ == "__main__":
    main()
