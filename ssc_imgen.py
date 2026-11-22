#!/usr/bin/env python3
"""ssc-imgen - make a printable access card (like the Wi-Fi card in a hotel room) for an SSC Server.

The card shows the SSC logo, the server address (ip:port), a user name, a password, optional notes and a QR code that
opens the address on a phone.  Nothing is stored or sent anywhere: the password only ends up on the picture you make,
so treat the picture like the password itself (hand it over, then delete it).

  ssc-imgen --user anna                         address is detected, the password is asked for (not shown)
  ssc-imgen --user anna --notes "Parents: sign in on the TV in the hall" -o anna.png
  ssc-imgen --address 192.168.1.50:8080 --user gast --password-stdin < pw.txt --dark
  ssc-imgen --user anna -o anna.pdf             .pdf, .png and .jpg work
  ssc-imgen --demo --network x --notes x        a sample card with [REDACTED] banners over every value (safe to show or share)
  ssc-imgen --user anna --network "Home WiFi"   shows the Wi-Fi network to join, top right
  ssc-imgen --user anna --cellularwarn          adds "Connect to WiFi instead of mobile data to access SSC" (in the card's language)
  ssc-imgen --user anna --lang de               card in German (en de fr es it pt nl pl cs sv da tr; default: system language)

Needs Pillow (python3-pil); the QR code needs segno (python3-segno) and is left out without it.
"""
import argparse
import getpass
import os
import re
import socket
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
W, H = 1500, 900                      # 5 x 3 inch at 300 dpi
ACCENT = (79, 70, 229)

THEMES = {
    "light": dict(bg=(255, 255, 255), border=(214, 218, 230), text=(20, 22, 34), label=(110, 116, 135), soft=(244, 245, 250), head_text=(255, 255, 255)),
    "dark": dict(bg=(20, 22, 32), border=(52, 56, 74), text=(240, 242, 250), label=(150, 156, 178), soft=(32, 35, 50), head_text=(255, 255, 255)),
}

SANS = ["/System/Library/Fonts/Helvetica.ttc", "/System/Library/Fonts/SFNS.ttf", "/Library/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans.ttf", "C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf"]
SANS_BOLD = ["/System/Library/Fonts/Helvetica.ttc", "/Library/Fonts/Arial Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
             "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf", "C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf"]
MONO = ["/System/Library/Fonts/Menlo.ttc", "/System/Library/Fonts/Courier.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSansMono-Bold.ttf", "C:/Windows/Fonts/consolab.ttf", "C:/Windows/Fonts/consola.ttf"]


# Card texts. Latin-script languages only: the built-in fonts cover them on every system.
STRINGS = {
    "en": dict(address="ADDRESS", user="USERNAME", password="PASSWORD", notes="NOTES", scan="Scan to open",
               private="Keep this card private - it contains a password.",
               cellular="Connect to WiFi instead of mobile data to access SSC",
               network="NETWORK"),
    "de": dict(address="ADRESSE", user="BENUTZERNAME", password="PASSWORT", notes="HINWEISE", scan="Zum Öffnen scannen",
               private="Diese Karte vertraulich behandeln - sie enthält ein Passwort.",
               cellular="Für den Zugriff auf SSC mit dem WLAN statt mit mobilen Daten verbinden",
               network="NETZWERK"),
    "fr": dict(address="ADRESSE", user="NOM D'UTILISATEUR", password="MOT DE PASSE", notes="NOTES", scan="Scanner pour ouvrir",
               private="Gardez cette carte confidentielle : elle contient un mot de passe.",
               cellular="Connectez-vous au Wi-Fi plutôt qu'aux données mobiles pour accéder à SSC",
               network="RÉSEAU"),
    "es": dict(address="DIRECCIÓN", user="USUARIO", password="CONTRASEÑA", notes="NOTAS", scan="Escanea para abrir",
               private="Mantén esta tarjeta en privado: contiene una contraseña.",
               cellular="Conéctate al Wi-Fi en lugar de usar datos móviles para acceder a SSC",
               network="RED"),
    "it": dict(address="INDIRIZZO", user="NOME UTENTE", password="PASSWORD", notes="NOTE", scan="Scansiona per aprire",
               private="Tieni questa carta riservata: contiene una password.",
               cellular="Collegati al Wi-Fi invece che ai dati mobili per accedere a SSC",
               network="RETE"),
    "pt": dict(address="ENDEREÇO", user="USUÁRIO", password="SENHA", notes="NOTAS", scan="Digitalize para abrir",
               private="Mantenha este cartão em sigilo: ele contém uma senha.",
               cellular="Conecte-se ao Wi-Fi em vez de usar dados móveis para acessar o SSC",
               network="REDE"),
    "nl": dict(address="ADRES", user="GEBRUIKERSNAAM", password="WACHTWOORD", notes="OPMERKINGEN", scan="Scan om te openen",
               private="Houd deze kaart privé: er staat een wachtwoord op.",
               cellular="Maak verbinding met wifi in plaats van mobiele data om SSC te openen",
               network="NETWERK"),
    "pl": dict(address="ADRES", user="NAZWA UŻYTKOWNIKA", password="HASŁO", notes="UWAGI", scan="Zeskanuj, aby otworzyć",
               private="Zachowaj tę kartę w tajemnicy - zawiera hasło.",
               cellular="Połącz się z Wi-Fi zamiast używać danych mobilnych, aby uzyskać dostęp do SSC",
               network="SIEĆ"),
    "cs": dict(address="ADRESA", user="UŽIVATELSKÉ JMÉNO", password="HESLO", notes="POZNÁMKY", scan="Naskenujte pro otevření",
               private="Uchovejte tuto kartu v soukromí - obsahuje heslo.",
               cellular="Pro přístup k SSC se připojte k Wi-Fi místo mobilních dat",
               network="SÍŤ"),
    "sv": dict(address="ADRESS", user="ANVÄNDARNAMN", password="LÖSENORD", notes="ANTECKNINGAR", scan="Skanna för att öppna",
               private="Håll kortet privat - det innehåller ett lösenord.",
               cellular="Anslut till Wi-Fi i stället för mobildata för att komma åt SSC",
               network="NÄTVERK"),
    "da": dict(address="ADRESSE", user="BRUGERNAVN", password="ADGANGSKODE", notes="NOTER", scan="Scan for at åbne",
               private="Hold kortet privat - det indeholder en adgangskode.",
               cellular="Opret forbindelse til Wi-Fi i stedet for mobildata for at få adgang til SSC",
               network="NETVÆRK"),
    "tr": dict(address="ADRES", user="KULLANICI ADI", password="PAROLA", notes="NOTLAR", scan="Açmak için tarayın",
               private="Bu kartı gizli tutun - bir parola içerir.",
               cellular="SSC'ye erişmek için mobil veri yerine Wi-Fi'ye bağlanın",
               network="AĞ"),
}


def pick_language(lang="auto"):
    """'de', 'de_AT.UTF-8' and 'de-DE' all give 'de'; 'auto' follows the system language and falls back to English."""
    if not lang or lang == "auto":
        for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
            v = os.environ.get(var, "")
            if v and v not in ("C", "POSIX"):
                code = re.split(r"[_.@:-]", v)[0].lower()
                if code in STRINGS:
                    return code
        return "en"
    code = re.split(r"[_.@:-]", str(lang))[0].lower()
    if code not in STRINGS:
        raise CardError(f"language '{lang}' is not available; choose from: {', '.join(sorted(STRINGS))}")
    return code


class CardError(Exception):
    pass


def _pil():
    try:
        from PIL import Image, ImageDraw, ImageFont
        return Image, ImageDraw, ImageFont
    except ImportError:
        raise CardError("Pillow is missing: pip install pillow  (Debian: apt install python3-pil)")


def load_font(candidates, size, bold=False):
    _, _, ImageFont = _pil()
    for path in candidates:
        if os.path.exists(path):
            try:
                f = ImageFont.truetype(path, size)
                if bold and path.endswith((".ttc", "SFNS.ttf")):
                    try:
                        f.set_variation_by_name("Bold")
                    except Exception:
                        try:
                            f = ImageFont.truetype(path, size, index=1)
                        except Exception:
                            pass
                return f
            except OSError:
                continue
    try:
        return ImageFont.load_default(size)          # Pillow 10.1+: a scalable built-in font
    except TypeError:
        return ImageFont.load_default()


def fit_font(draw, text, candidates, max_width, start, minimum=22, bold=False):
    """The biggest size <= start at which `text` still fits in max_width (long passwords shrink instead of overflowing)."""
    size = start
    while size > minimum:
        f = load_font(candidates, size, bold)
        if draw.textlength(text, font=f) <= max_width:
            return f
        size -= 2
    return load_font(candidates, minimum, bold)


def clip_text(draw, text, font, max_width):
    """`text`, or its start plus '...' if it is wider than max_width."""
    if draw.textlength(text, font=font) <= max_width:
        return text
    while text and draw.textlength(text + "...", font=font) > max_width:
        text = text[:-1]
    return text + "..."


def check_network(name):
    """A Wi-Fi network name (SSID): 1-32 bytes, no control characters."""
    name = str(name).strip()
    if not name or len(name.encode("utf-8")) > 32 or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise CardError("the network name should be 1-32 characters without control characters")
    return name


def wrap(draw, text, font, max_width, max_lines):
    """Word-wrap `text` (explicit newlines are kept); anything beyond max_lines ends in an ellipsis."""
    lines = []
    for para in str(text).replace("\\n", "\n").splitlines() or [""]:
        cur = ""
        for word in para.split(" "):
            test = (cur + " " + word).strip()
            if draw.textlength(test, font=font) <= max_width or not cur:
                cur = test
                while draw.textlength(cur, font=font) > max_width and len(cur) > 1:      # one very long word: break it
                    cut = len(cur) - 1
                    while cut > 1 and draw.textlength(cur[:cut], font=font) > max_width:
                        cut -= 1
                    lines.append(cur[:cut])
                    cur = cur[cut:]
            else:
                lines.append(cur)
                cur = word
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        while lines[-1] and draw.textlength(lines[-1] + "...", font=font) > max_width:
            lines[-1] = lines[-1][:-1]
        lines[-1] += "..."
    return lines


def qr_matrix(url):
    try:
        import segno
    except ImportError:
        return None
    return [[bool(c) for c in row] for row in segno.make(url, error="m", micro=False).matrix]


def rounded_mask(size, radius):
    Image, ImageDraw, _ = _pil()
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius, fill=255)
    return m


REDACTED = "[REDACTED]"


def redact_bar(d, box, th, size=46):
    """An opaque black banner with [REDACTED] on it. Its size never depends on what it hides."""
    x0, y0, x1, y1 = box
    d.rounded_rectangle(box, min(22, (y1 - y0) // 2), fill=(0, 0, 0), outline=(96, 100, 120) if th is THEMES["dark"] else None, width=3)
    f = fit_font(d, REDACTED, MONO, (x1 - x0) - 40, size, minimum=18, bold=True)
    d.text(((x0 + x1) / 2, (y0 + y1) / 2 + 2), REDACTED, font=f, fill=(255, 255, 255), anchor="mm")


def make_card(address, user, password, notes="", title="SSC Server", qr=True, dark=False, scheme="http", logo=None, lang="en", cellular_warn=False, network=None, demo=False):
    """-> a PIL image (1500 x 900) with the card."""
    Image, ImageDraw, ImageFont = _pil()
    if not user or not password:
        raise CardError("a user name and a password are needed")
    if not re.fullmatch(r"[^\s/]+:\d{1,5}|[^\s/:]+", address):
        raise CardError("the address should look like 192.168.1.50:8080")
    T = STRINGS[pick_language(lang)]
    network = check_network(network) if network is not None else None
    th = THEMES["dark" if dark else "light"]
    card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(card)
    d.rounded_rectangle((0, 0, W - 1, H - 1), 56, fill=th["bg"], outline=th["border"], width=4)

    # header band with the logo
    head = Image.new("RGBA", (W, 210), ACCENT + (255,))
    hd = ImageDraw.Draw(head)
    for x in range(W):                                   # a soft left-to-right gradient
        t = x / W
        hd.line((x, 0, x, 210), fill=(int(ACCENT[0] + (110 - ACCENT[0]) * t * .5), int(ACCENT[1] + (90 - ACCENT[1]) * t * .5), int(ACCENT[2] + (240 - ACCENT[2]) * t * .5), 255))
    card.paste(head, (0, 0), rounded_mask((W, 210 + 60), 56).crop((0, 0, W, 210)))
    logo_path = Path(logo) if logo else HERE / "assets" / "icon.png"
    if not logo_path.exists():
        logo_path = Path(getattr(sys, "_MEIPASS", HERE)) / "icon.png"
    if logo_path.exists():
        lg = Image.open(logo_path).convert("RGBA").resize((150, 150), Image.LANCZOS)
        card.paste(lg, (70, 30), rounded_mask((150, 150), 34))
        tx = 250
    else:
        tx = 70
    d.text((tx, 38), title, font=load_font(SANS_BOLD, 66, True), fill=th["head_text"])
    d.text((tx, 126), "Stundl Student Client", font=load_font(SANS, 38), fill=(226, 228, 255))
    if network and demo:
        d.text((W - 70, 62), T["network"], font=load_font(SANS_BOLD, 26, True), fill=(214, 217, 255), anchor="ra")
        redact_bar(d, (W - 70 - 440, 96, W - 70, 166), th, 40)
    elif network:                                          # the Wi-Fi network to join, right-aligned in the header
        tw = d.textlength(title, font=load_font(SANS_BOLD, 66, True))
        room = max(240, (W - 70) - (tx + tw + 60))
        nf = fit_font(d, network, SANS_BOLD, room, 58, minimum=28, bold=True)
        shown = clip_text(d, network, nf, room)
        d.text((W - 70, 62), T["network"], font=load_font(SANS_BOLD, 26, True), fill=(214, 217, 255), anchor="ra")
        d.text((W - 70, 130), shown, font=nf, fill=th["head_text"], anchor="rm")

    # credentials
    left, right = 80, 960 if qr else W - 80
    rows = [(T["address"], address), (T["user"], user), (T["password"], password)]
    y = 228 if cellular_warn else 262
    pitch, box_top, box_h, vsize = (124, 30, 74, 62) if cellular_warn else (150, 34, 84, 70)
    for label, value in rows:
        d.text((left, y), label, font=load_font(SANS_BOLD, 26, True), fill=th["label"])
        if demo:                                          # nothing of the real value is drawn, and the bar is always as wide as the field
            redact_bar(d, (left - 18, y + box_top, right + 18, y + box_top + box_h), th, vsize - 10)
            y += pitch
            continue
        f = fit_font(d, value, MONO, right - left, vsize)
        d.rounded_rectangle((left - 18, y + box_top, right + 18, y + box_top + box_h), 22, fill=th["soft"])
        d.text((left, y + box_top + box_h // 2 + 2), value, font=f, fill=th["text"], anchor="lm")
        y += pitch

    # "use WiFi, not mobile data" warning (amber band with a drawn warning triangle: no emoji font needed)
    if cellular_warn:
        amber, amber_bg, amber_text = ((240, 180, 40), (62, 50, 14), (255, 220, 130)) if dark else ((236, 160, 20), (255, 245, 214), (122, 72, 0))
        wf = load_font(SANS_BOLD, 28, True)
        lines = wrap(d, T["cellular"], wf, right - left - 118, 2)
        bh = 34 + 36 * len(lines)
        by = y - 4
        d.rounded_rectangle((left - 18, by, right + 18, by + bh), 22, fill=amber_bg, outline=amber, width=4)
        cx, cy = left + 36, by + bh // 2
        d.polygon([(cx, cy - 30), (cx + 34, cy + 26), (cx - 34, cy + 26)], fill=amber)
        d.rounded_rectangle((cx - 3, cy - 12, cx + 3, cy + 7), 3, fill=(255, 255, 255))
        d.ellipse((cx - 4, cy + 11, cx + 4, cy + 19), fill=(255, 255, 255))
        for i, line in enumerate(lines):
            d.text((left + 92, by + 17 + i * 36), line, font=wf, fill=amber_text)
        y = by + bh + 8

    # notes (optional)
    if notes and notes.strip():
        d.text((left, y + 6), T["notes"], font=load_font(SANS_BOLD, 26, True), fill=th["label"])
        nf = load_font(SANS, 32)
        if demo:
            redact_bar(d, (left - 18, y + 42, right + 18, y + 112), th, 40)
        else:
            for i, line in enumerate(wrap(d, notes.strip(), nf, W - 2 * left, 2)):
                d.text((left, y + 44 + i * 42), line, font=nf, fill=th["text"])

    # QR code that opens the address
    if qr and demo:                                        # a QR code would give the address away (and its size, the address length)
        qx, qy, side = 1030, 270, 400
        redact_bar(d, (qx, qy, qx + side, qy + side), th, 44)
        d.text((qx + side / 2, qy + side + 34), T["scan"], font=load_font(SANS, 30), fill=th["label"], anchor="mm")
    elif qr:
        m = qr_matrix(f"{scheme}://{address}/")
        if m:
            n = len(m)
            box = 400
            cell = max(2, box // (n + 8))
            side = cell * (n + 8)
            qx, qy = 1010 + (box - side) // 2 + 20, 270
            d.rounded_rectangle((qx, qy, qx + side, qy + side), 28, fill=(255, 255, 255), outline=th["border"], width=3)
            for r, row in enumerate(m):
                for c, on in enumerate(row):
                    if on:
                        d.rectangle((qx + (c + 4) * cell, qy + (r + 4) * cell, qx + (c + 5) * cell - 1, qy + (r + 5) * cell - 1), fill=(20, 22, 34))
            cap = T["scan"]
            cf = load_font(SANS, 30)
            d.text((qx + side / 2, qy + side + 34), cap, font=cf, fill=th["label"], anchor="mm")

    d.text((W / 2, H - 38), T["private"], font=load_font(SANS, 24), fill=th["label"], anchor="mm")
    out = Image.new("RGB", (W, H), (255, 255, 255))
    out.paste(card, (0, 0), card)
    return out


# ------------------------------------------------------------------ finding the address
def lan_ips():
    try:
        r = subprocess.run(["ip", "-j", "-4", "addr"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            sys.path.insert(0, str(HERE / "server"))
            import serverconf
            ips = [a.split("/")[0] for _, a in serverconf.parse_ip_json(r.stdout)]
            if ips:
                return ips
    except (OSError, ImportError, ValueError, subprocess.SubprocessError):
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))             # no packet is sent; this only picks the outgoing interface
        ip = s.getsockname()[0]
        s.close()
        return [ip]
    except OSError:
        return []


def detect_address():
    """-> ('ip:port', 'http'|'https') using the SSC Server settings when present, else this machine's LAN address."""
    port, scheme = 8080, "http"
    try:
        sys.path.insert(0, str(HERE / "server"))
        import serverconf
        cfg = serverconf.load()
        port, scheme = cfg["web"]["port"], "https" if cfg["web"]["https"] else "http"
    except Exception:
        pass
    ips = lan_ips()
    if not ips:
        raise CardError("could not find this machine's IP address - give it with --address 192.168.1.50:8080")
    return f"{ips[0]}:{port}", scheme


def get_password(args):
    if args.password is not None:
        return args.password
    if os.environ.get("SSC_CARD_PASSWORD"):
        return os.environ["SSC_CARD_PASSWORD"]
    if args.password_stdin:
        return sys.stdin.readline().rstrip("\r\n")
    if sys.stdin.isatty():
        return getpass.getpass("Password for the card (not shown): ")
    raise CardError("no password: use --password-stdin, SSC_CARD_PASSWORD or run it in a terminal")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ssc-imgen", description="Make a printable access card for an SSC Server.")
    ap.add_argument("--user", "-u", help="user name (not needed with --demo)")
    ap.add_argument("--password", "-p", help="password (visible in shell history and `ps`: prefer the prompt or --password-stdin)")
    ap.add_argument("--password-stdin", action="store_true", help="read the password from the first line of standard input")
    ap.add_argument("--address", "-a", help="ip:port, e.g. 192.168.1.50:8080 (default: detected)")
    ap.add_argument("--notes", "-n", default="", help="optional notes, e.g. 'Sign in on the TV'; \\n starts a new line")
    ap.add_argument("--notes-file", help="read the notes from a text file")
    ap.add_argument("--title", default="SSC Server", help="heading on the card")
    ap.add_argument("--https", action="store_true", help="the QR code opens https:// instead of http://")
    ap.add_argument("--no-qr", action="store_true", help="leave out the QR code")
    ap.add_argument("--dark", action="store_true", help="dark card")
    ap.add_argument("--demo", action="store_true", help="cover the address, user, password, network and notes (and the QR code) with [REDACTED] banners: safe to show or share")
    ap.add_argument("--network", metavar="NETW-NAME", help="show the Wi-Fi network name to join on the card")
    ap.add_argument("--cellularwarn", action="store_true", help="add a warning: connect to WiFi instead of mobile data to access SSC")
    ap.add_argument("--lang", "-l", default="auto", help="card language: " + ", ".join(sorted(STRINGS)) + " (default: your system language, else en)")
    ap.add_argument("--logo", help="your own logo image instead of the SSC icon")
    ap.add_argument("--output", "-o", default="ssc-card.png", help="file to write: .png, .jpg or .pdf (default ssc-card.png)")
    a = ap.parse_args(argv)
    if not a.user and not a.demo:
        ap.error("--user is required (except with --demo)")
    try:
        if a.demo:                                      # no real values are needed (or read, or looked up) for a demo card
            address, scheme = "0.0.0.0:0", "http"
        else:
            address, scheme = (a.address, "https" if a.https else "http") if a.address else detect_address()
        if a.https:
            scheme = "https"
        notes = Path(a.notes_file).read_text() if a.notes_file else a.notes
        img = make_card(address, a.user or "demo", "demo" if a.demo else get_password(a), notes, a.title, not a.no_qr, a.dark, scheme, a.logo,
                        pick_language(a.lang), a.cellularwarn, a.network, a.demo)
        out = Path(a.output)
        if out.suffix.lower() in (".jpg", ".jpeg"):
            img.save(out, quality=95)
        elif out.suffix.lower() == ".pdf":
            img.save(out, "PDF", resolution=300)
        else:
            img.save(out, "PNG", dpi=(300, 300))
        os.chmod(out, 0o600)                         # the card holds a password: private to you
        if a.demo:
            print(f"Wrote {out}  (demo card: every value is covered by [REDACTED] banners)")
        else:
            print(f"Wrote {out}  ({address})  - keep it private and delete it after handing it over.")
        return 0
    except (CardError, OSError) as e:
        print("ssc-imgen:", e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
