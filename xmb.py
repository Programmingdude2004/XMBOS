#!/usr/bin/env python3
"""Custom XMB-style launcher. Run: python3 xmb.py [--windowed]
Config: ~/.config/xmb/config.json (created on first run, edit freely).
Items: {"name","cmd"[,"confirm":true]}. A category with "feeds":[urls] shows RSS/Atom headlines."""
import array, colorsys, glob, html, json, math, os, re, shlex, subprocess, sys, threading, time
import urllib.request
import xml.etree.ElementTree as ET
import pygame

CFG = os.path.expanduser("~/.config/xmb/config.json")
DEFAULT = [
    {"name": "Settings", "items": [
        {"name": "Reboot", "cmd": "systemctl reboot", "confirm": True},
        {"name": "Shut Down", "cmd": "systemctl poweroff", "confirm": True},
        {"name": "Exit to Terminal", "cmd": "@quit", "confirm": True}]},
    {"name": "Games", "items": [
        {"name": "RetroArch", "cmd": "retroarch"},
        {"name": "Steam", "cmd": "steam -gamepadui"},
        {"name": "PPSSPP", "cmd": "ppsspp"},
        {"name": "Dolphin", "cmd": "dolphin-emu"}]},
    {"name": "Media", "items": [
        {"name": "Videos", "cmd": "mpv --fs ~/Videos"},
        {"name": "Music", "cmd": "mpv --no-video ~/Music"}]},
    {"name": "Network", "items": [
        {"name": "Wi-Fi", "cmd": "nmtui"},
        {"name": "Browser", "cmd": "firefox --kiosk"}]},
    {"name": "News", "feeds": [
        "https://hnrss.org/frontpage",
        "https://hackaday.com/blog/feed/",
        "https://feeds.bbci.co.uk/news/rss.xml"]},
]
BROWSER = os.environ.get("XMB_BROWSER", "xdg-open")

def load():
    try:
        with open(CFG) as f:
            return json.load(f)
    except Exception:
        os.makedirs(os.path.dirname(CFG), exist_ok=True)
        with open(CFG, "w") as f:
            json.dump(DEFAULT, f, indent=2)
        return json.loads(json.dumps(DEFAULT))

cats = load()
for c in cats:
    c.setdefault("items", [])
    if "feeds" in c:
        c["items"] = [{"name": "Loading…", "desc": "", "link": ""}]

# ---------------------------------------------------------------- RSS / Atom
def strip(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()

def parse_feed(data):
    root = ET.fromstring(data)
    ns = "{http://www.w3.org/2005/Atom}"
    title = strip(root.findtext("channel/title") or root.findtext(ns + "title"))
    out = []
    for it in root.iter("item"):
        out.append((strip(it.findtext("title")), strip(it.findtext("description")), (it.findtext("link") or "").strip()))
    for en in root.iter(ns + "entry"):
        l = en.find(ns + "link")
        out.append((strip(en.findtext(ns + "title")),
                    strip(en.findtext(ns + "summary") or en.findtext(ns + "content")),
                    l.get("href", "") if l is not None else ""))
    return title, out

def refresh_feeds():
    for c in cats:
        if "feeds" not in c:
            continue
        items = []
        for url in c["feeds"]:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "XMB/1.0"})
                title, ents = parse_feed(urllib.request.urlopen(req, timeout=10).read())
                items += [{"name": n, "desc": d, "link": l, "src": title} for n, d, l in ents[:8] if n]
            except Exception:
                pass
        c["items"] = items or [{"name": "No connection: feeds unavailable", "desc": "", "link": ""}]

def feed_loop():
    while True:
        refresh_feeds()
        time.sleep(900)

# ---------------------------------------------------------------- battery
_bat = (0.0, None)
def battery():
    global _bat
    if time.time() - _bat[0] > 10:
        val = None
        for p in sorted(glob.glob("/sys/class/power_supply/BAT*")):
            try:
                val = (int(open(p + "/capacity").read()), open(p + "/status").read().strip())
                break
            except Exception:
                pass
        _bat = (time.time(), val)
    return _bat[1]

# ---------------------------------------------------------------- init
os.environ.setdefault("SDL_JOYSTICK_HIDAPI_STEAMDECK", "1")  # native Deck pad, incl. grips
pygame.init()
pygame.mouse.set_visible(False)
from pygame._sdl2 import controller as sdlc
sdlc.init()
pads, joys = {}, {}  # mapped controllers (Deck, Xbox, PS...) and unmapped fallback joysticks

# ---------------------------------------------------------------- sound (synthesized, no asset files)
RATE = 22050
def synth(notes, dur=0.07, vol=0.25):
    buf = array.array("h")
    n = int(RATE * dur)
    for f in notes:
        for i in range(n):
            buf.append(int(math.sin(2 * math.pi * f * i / RATE) * (1 - i / n) ** 2 * vol * 32767))
    return pygame.mixer.Sound(buffer=buf.tobytes())

def chord(freqs, dur, vol=0.5):
    buf = array.array("h")
    n = int(RATE * dur)
    for i in range(n):
        env = min(1.0, i / (0.08 * n)) * (1 - i / n) ** 1.5
        v = sum(math.sin(2 * math.pi * f * i / RATE) for f in freqs) / len(freqs)
        buf.append(int(v * env * vol * 32767))
    return pygame.mixer.Sound(buffer=buf.tobytes())

SND = {}
for drv in (None, "pipewire", "pulseaudio", "alsa"):  # try the default, then fall back
    try:
        if drv:
            os.environ["SDL_AUDIODRIVER"] = drv
        pygame.mixer.quit()
        pygame.mixer.init(RATE, -16, 1, 512)
        break
    except Exception:
        continue
if pygame.mixer.get_init():
    SND = {"move": synth([880], 0.04, 0.15), "cat": synth([660, 990], 0.05, 0.18),
           "ok": synth([660, 880, 1320]), "back": synth([660, 440], 0.06),
           "launch": synth([440, 660, 880, 1320], 0.09),
           "boot": chord([261.6, 329.6, 392.0, 523.3], 2.8, 0.6),
           "gameboot": chord([392.0, 587.3, 784.0], 1.1, 0.5)}
    # drop your own sounds in ~/.config/xmb/sounds/<name>.wav to replace any of these
    for name in list(SND):
        wav = os.path.expanduser(f"~/.config/xmb/sounds/{name}.wav")
        if os.path.exists(wav):
            try:
                SND[name] = pygame.mixer.Sound(wav)
            except Exception:
                pass

def snd(name):
    s = SND.get(name)
    if s:
        s.play()

# ---------------------------------------------------------------- controllers
def add_pad(i):
    try:
        j = pygame.joystick.Joystick(i)
        jid = j.get_instance_id()
        if jid in pads or jid in joys:
            return
        if sdlc.is_controller(i):
            pads[jid] = sdlc.Controller(i)
        else:
            joys[jid] = j
    except Exception:
        pass

def pad_dir():
    x = y = 0.0
    for c in pads.values():
        x += c.get_axis(pygame.CONTROLLER_AXIS_LEFTX) / 32768
        y += c.get_axis(pygame.CONTROLLER_AXIS_LEFTY) / 32768
        x += c.get_button(pygame.CONTROLLER_BUTTON_DPAD_RIGHT) - c.get_button(pygame.CONTROLLER_BUTTON_DPAD_LEFT)
        y += c.get_button(pygame.CONTROLLER_BUTTON_DPAD_DOWN) - c.get_button(pygame.CONTROLLER_BUTTON_DPAD_UP)
    for j in joys.values():
        if j.get_numhats():
            hx, hy = j.get_hat(0)
            x += hx; y -= hy
        if j.get_numaxes() > 1:
            x += j.get_axis(0); y += j.get_axis(1)
    if abs(x) < 0.6 and abs(y) < 0.6:
        return (0, 0)
    return (int(math.copysign(1, x)), 0) if abs(x) > abs(y) else (0, int(math.copysign(1, y)))

for i in range(pygame.joystick.get_count()):
    add_pad(i)
flags = 0 if "--windowed" in sys.argv else pygame.FULLSCREEN
screen = pygame.display.set_mode((1280, 720) if flags == 0 else (0, 0), flags)
W, H = screen.get_size()
S = H / 720  # UI scale
font = lambda n: pygame.font.SysFont("notosans,dejavusans,arial", int(n * S))
F_CAT, F_ITEM, F_CLK, F_ICO, F_SM, F_BIG = font(22), font(26), font(24), font(30), font(20), font(40)
F_HUGE = font(84)

# background gradient, hue follows the month like the PS3 XMB
hue = (time.localtime().tm_mon - 1) / 12
c1 = [int(v * 255) for v in colorsys.hsv_to_rgb(hue, 0.75, 0.75)]
c2 = [int(v * 255) for v in colorsys.hsv_to_rgb(hue, 0.9, 0.18)]
bg = pygame.Surface((W, H))
for y in range(H):
    k = y / H
    pygame.draw.line(bg, [int(c1[i] * (1 - k) + c2[i] * k) for i in range(3)], (0, y), (W, y))
layer = pygame.Surface((W, H), pygame.SRCALPHA)

def waves(t):
    screen.blit(bg, (0, 0))
    for k in range(3):
        layer.fill((0, 0, 0, 0))
        base = H * (0.55 + 0.09 * k)
        pts = [(x, base + math.sin(x / (220 * S) + t * (0.4 + 0.15 * k) + k * 2) * 40 * S
                + math.sin(x / (90 * S) + t * 0.7) * 12 * S) for x in range(0, W + 24, 24)]
        pygame.draw.polygon(layer, (255, 255, 255, 22 + k * 10), pts + [(W, H), (0, H)])
        screen.blit(layer, (0, 0))

def text(s, f, pos, alpha=255, anchor="midleft"):
    surf = f.render(s, True, (255, 255, 255))
    surf.set_alpha(max(0, min(255, int(alpha))))
    r = surf.get_rect()
    setattr(r, anchor, pos)
    screen.blit(surf, r)
    return r

def fit(s, f, w):
    if f.size(s)[0] <= w:
        return s
    while len(s) > 1 and f.size(s + "…")[0] > w:
        s = s[:-1]
    return s + "…"

def wrap(s, f, w):
    lines, cur = [], ""
    for word in s.split():
        t2 = (cur + " " + word).strip()
        if f.size(t2)[0] <= w:
            cur = t2
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines

def icon(name, cx, cy, size, alpha, sel):
    s = pygame.Surface((size * 2, size * 2), pygame.SRCALPHA)
    pygame.draw.rect(s, (255, 255, 255, int(alpha * 0.25)), (0, 0, size * 2, size * 2), border_radius=int(size * 0.5))
    pygame.draw.rect(s, (255, 255, 255, int(alpha)), (0, 0, size * 2, size * 2), width=max(2, int(3 * S)), border_radius=int(size * 0.5))
    l = F_ICO.render(name[0].upper(), True, (255, 255, 255))
    l.set_alpha(int(alpha))
    s.blit(l, l.get_rect(center=(size, size)))
    screen.blit(s, s.get_rect(center=(cx, cy)))

def draw_battery(right_x, cy):
    b = battery()
    if not b:
        return right_x
    pct, st = b
    w, h = 34 * S, 16 * S
    r = text(f"{pct}%", F_SM, (right_x, cy), 220, "midright")
    box = pygame.Rect(r.left - 10 * S - w, cy - h / 2, w, h)
    col = (120, 230, 140) if st == "Charging" else (255, 90, 90) if pct <= 15 else (255, 255, 255)
    pygame.draw.rect(screen, col, box, width=max(2, int(2 * S)), border_radius=int(3 * S))
    pygame.draw.rect(screen, col, (box.right, cy - 4 * S, 3 * S, 8 * S))
    pygame.draw.rect(screen, col, (box.x + 3 * S, box.y + 3 * S, max(1, (w - 6 * S) * pct / 100), h - 6 * S))
    return box.left - 20 * S

# ---------------------------------------------------------------- state & actions
ci = 1 if len(cats) > 1 else 0
sel = [0] * len(cats)
ax = float(ci)
ay = [0.0] * len(cats)
mode, ov = None, None  # None | "confirm" | "reader"

def move(dx, dy):
    global ci
    if mode:
        return
    old = (ci, sel[ci])
    if dx:
        ci = max(0, min(len(cats) - 1, ci + dx))
    if dy:
        sel[ci] = max(0, min(len(cats[ci]["items"]) - 1, sel[ci] + dy))
    if ci != old[0]:
        snd("cat")
    elif sel[ci] != old[1]:
        snd("move")

def run(cmd):
    if cmd == "@quit":
        pygame.quit(); sys.exit()
    if cmd == "@desktop":  # the login script starts Plasma when this flag exists
        open(os.path.expanduser("~/.xmb-desktop"), "w").close()
        pygame.quit(); sys.exit()
    # let the launched app own the controller, then take it back
    for c in list(pads.values()):
        try:
            c.quit()
        except Exception:
            pass
    pads.clear(); joys.clear()
    subprocess.run(cmd, shell=True)
    for i in range(pygame.joystick.get_count()):
        add_pad(i)
    pygame.event.clear()

def confirm():
    """A / Enter"""
    global mode, ov
    if mode == "confirm":
        mode = None; snd("launch"); run(ov["cmd"])
    elif mode == "reader":
        mode = None; snd("ok")
        if ov.get("link"):
            run(f"{BROWSER} {shlex.quote(ov['link'])}")
    else:
        it = cats[ci]["items"][sel[ci]]
        if "link" in it:
            mode, ov = "reader", it; snd("ok")
        elif it.get("confirm"):
            mode, ov = "confirm", it; snd("ok")
        elif "cmd" in it:
            gameboot(it["name"]); run(it["cmd"])

def cancel():
    """B / Esc / Backspace"""
    global mode
    if mode:
        mode = None
    snd("back")

def overlay():
    dim = pygame.Surface((W, H), pygame.SRCALPHA)
    dim.fill((0, 0, 0, 160))
    screen.blit(dim, (0, 0))
    pw, ph = W * 0.62, H * (0.30 if mode == "confirm" else 0.62)
    box = pygame.Rect(0, 0, pw, ph); box.center = (W / 2, H / 2)
    p = pygame.Surface(box.size, pygame.SRCALPHA)
    pygame.draw.rect(p, (20, 20, 40, 210), p.get_rect(), border_radius=int(22 * S))
    pygame.draw.rect(p, (255, 255, 255, 180), p.get_rect(), width=max(2, int(2 * S)), border_radius=int(22 * S))
    screen.blit(p, box)
    if mode == "confirm":
        text(f"{ov['name']}?", F_BIG, (box.centerx, box.centery - 26 * S), 255, "center")
        text("A  Confirm        B  Cancel", F_CLK, (box.centerx, box.bottom - 50 * S), 230, "center")
    else:
        pad = 32 * S
        y = box.top + pad
        for ln in wrap(ov["name"], F_ITEM, pw - 2 * pad)[:3]:
            y += text(ln, F_ITEM, (box.left + pad, y), 255, "topleft").height + 4 * S
        y += 6 * S
        if ov.get("src"):
            y += text(ov["src"], F_SM, (box.left + pad, y), 150, "topleft").height + 14 * S
        lines = wrap(ov.get("desc") or "No summary available.", F_SM, pw - 2 * pad)
        room = int((box.bottom - 80 * S - y) / (F_SM.get_height() + 4 * S))
        for ln in lines[:max(1, room)]:
            y += text(ln, F_SM, (box.left + pad, y), 210, "topleft").height + 4 * S
        text("A  Open in browser        B  Back", F_CLK, (box.centerx, box.bottom - 36 * S), 230, "center")

# ---------------------------------------------------------------- coldboot / gameboot
def skippable():
    for e in pygame.event.get():
        if e.type in (pygame.KEYDOWN, pygame.CONTROLLERBUTTONDOWN, pygame.MOUSEBUTTONDOWN):
            return True
    return False

def coldboot():
    """Startup animation: waves fade in from black, logo, chime. Any button skips."""
    snd("boot")
    t0 = time.time()
    black = pygame.Surface((W, H))
    while True:
        el = time.time() - t0
        if el > 3.4 or skippable():
            break
        waves(el)
        a = min(1.0, el / 1.2) * (1.0 if el < 2.5 else max(0.0, (3.4 - el) / 0.9))
        text("XMBOS", F_HUGE, (W / 2, H * 0.45), 255 * a, "center")
        black.set_alpha(int(255 * max(0.0, 1 - el / 0.8)))
        screen.blit(black, (0, 0))
        pygame.display.flip()
        clock.tick(60)
    if pygame.mixer.get_init():
        pygame.mixer.stop()
    pygame.event.clear()

def gameboot(name):
    """Short transition when launching a game or app."""
    snd("gameboot")
    t0 = time.time()
    black = pygame.Surface((W, H))
    while time.time() - t0 < 1.1:
        el = time.time() - t0
        waves(t + el)
        text(fit(name, F_BIG, W * 0.8), F_BIG, (W / 2, H / 2), 255 * min(1.0, el / 0.4), "center")
        black.set_alpha(int(255 * min(1.0, max(0.0, (el - 0.5) / 0.6))))
        screen.blit(black, (0, 0))
        pygame.display.flip()
        pygame.event.pump()
        clock.tick(60)

# ---------------------------------------------------------------- main loop
threading.Thread(target=feed_loop, daemon=True).start()
last_dir, hold = (0, 0), 0.0
clock = pygame.time.Clock()
t = 0.0
if "--no-boot" not in sys.argv:
    coldboot()
while True:
    dt = clock.tick(60) / 1000
    t += dt
    for e in pygame.event.get():
        if e.type == pygame.QUIT:
            sys.exit()
        elif e.type == pygame.KEYDOWN:
            if e.key == pygame.K_LEFT: move(-1, 0)
            elif e.key == pygame.K_RIGHT: move(1, 0)
            elif e.key == pygame.K_UP: move(0, -1)
            elif e.key == pygame.K_DOWN: move(0, 1)
            elif e.key in (pygame.K_RETURN, pygame.K_z): confirm()
            elif e.key in (pygame.K_BACKSPACE, pygame.K_x): cancel()
            elif e.key == pygame.K_ESCAPE:
                if mode: cancel()
                elif "--windowed" in sys.argv: sys.exit()
        elif e.type == pygame.JOYDEVICEADDED:
            add_pad(e.device_index)
        elif e.type == pygame.JOYDEVICEREMOVED:
            pads.pop(e.instance_id, None)
            joys.pop(e.instance_id, None)
        elif e.type == pygame.CONTROLLERBUTTONDOWN:
            b = e.button
            if b == pygame.CONTROLLER_BUTTON_A: confirm()
            elif b == pygame.CONTROLLER_BUTTON_B: cancel()
            elif b == pygame.CONTROLLER_BUTTON_LEFTSHOULDER: move(-1, 0)
            elif b == pygame.CONTROLLER_BUTTON_RIGHTSHOULDER: move(1, 0)
        elif e.type == pygame.JOYBUTTONDOWN and e.instance_id in joys:
            if e.button == 0: confirm()
            elif e.button == 1: cancel()

    # d-pad / left stick with hold-to-repeat
    d = pad_dir()
    if d != last_dir:
        if d != (0, 0):
            move(*d); hold = 0.4
        last_dir = d
    elif d != (0, 0):
        hold -= dt
        if hold <= 0:
            move(*d); hold = 0.1

    sel[ci] = max(0, min(sel[ci], len(cats[ci]["items"]) - 1))
    k = min(1, dt * 10)
    ax += (ci - ax) * k
    for i in range(len(cats)):
        ay[i] += (sel[i] - ay[i]) * k

    waves(t)
    # status bar: clock + battery, top right
    r = text(time.strftime("%a %d/%m  %H:%M"), F_CLK, (W - 40 * S, 40 * S), 220, "midright")
    draw_battery(r.left - 28 * S, 40 * S)

    gap, x0, y_cat = 170 * S, W * 0.22, H * 0.24
    for i, c in enumerate(cats):
        x = x0 + (i - ax) * gap
        near = max(0.0, 1 - abs(i - ax) * 0.45)
        big = 34 * S + 12 * S * max(0, 1 - abs(i - ax))
        icon(c["name"], x, y_cat, big, 255 * near, i == ci)
        text(c["name"], F_CAT, (x, y_cat + 62 * S), 255 * near * (1 if i == ci else 0.55), "midtop")

    # items for the selected category, hanging below its icon
    x = x0 + (ci - ax) * gap
    for j, it in enumerate(cats[ci]["items"]):
        dj = j - ay[ci]
        y = H * 0.46 + dj * 84 * S + (-30 * S if dj < -0.5 else 30 * S if dj > 0.5 else 0)
        a = 255 * max(0.0, 1 - abs(dj) * 0.28)
        size = 28 * S + 8 * S * max(0, 1 - abs(dj))
        icon(it["name"], x, y, size, a, j == sel[ci])
        lx = x + size + 22 * S
        text(fit(it["name"], F_ITEM, W - lx - 40 * S), F_ITEM, (lx, y), a * (1 if j == sel[ci] else 0.7))

    text("A  Select      B  Back", F_SM, (W - 40 * S, H - 30 * S), 150, "midright")
    if mode:
        overlay()
    pygame.display.flip()
