#!/usr/bin/env python3
import os
import sys
import shutil
import random
import json
from pathlib import Path
from datetime import datetime, timedelta

# ── Defaults (overschrijfbaar via .env) ───────────────────────────────────────

BASE_DIR       = Path("./test-nas")
ARCHIEF_DIR    = "verwerkt"
UITZOEKEN_DIR  = "in_behandeling"
DUMP_DIR       = "ruwe_data"
PRULLENBAK_DIR = "prullenbak"
DATUMLOOS_DIR  = "datumloos"
PORT           = 8765
IMMICH_URL     = "http://localhost:2283"
IMMICH_API_KEY = ""
DEBUG          = False
PRESORT_DEBUG  = False

RECENTS_FILE      = Path(__file__).parent / "recents.json"
THUMBCACHE_DIR    = Path(__file__).parent / ".thumbcache"
DATUMCACHE_FILE   = Path(__file__).parent / ".datumcache.json"
LOG_FILE          = Path(__file__).parent / "declutter.log"

# ── Logging ───────────────────────────────────────────────────────────────────

def _log(msg: str):
    """Schrijft naar stdout én logbestand met tijdstempel."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts}  {msg}"
    print(line)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

# ── .env loader ───────────────────────────────────────────────────────────────

def _laad_env_bestand(pad: Path) -> dict:
    """Leest een .env bestand, geeft {key: value}. Negeert # en lege regels."""
    result = {}
    try:
        for regel in pad.read_text(encoding="utf-8").splitlines():
            regel = regel.strip()
            if not regel or regel.startswith("#"):
                continue
            if "=" in regel:
                key, _, val = regel.partition("=")
                key, val = key.strip(), val.strip()
                if key and val:
                    result[key] = val
    except FileNotFoundError:
        pass
    return result

def _toepas_env(cfg: dict):
    global BASE_DIR, ARCHIEF_DIR, UITZOEKEN_DIR, DUMP_DIR, PRULLENBAK_DIR, DATUMLOOS_DIR, PORT
    global IMMICH_URL, IMMICH_API_KEY, DEBUG, PRESORT_DEBUG
    if "BASE_DIR"        in cfg: BASE_DIR        = Path(cfg["BASE_DIR"])
    if "ARCHIEF_DIR"     in cfg: ARCHIEF_DIR     = cfg["ARCHIEF_DIR"]
    if "UITZOEKEN_DIR"   in cfg: UITZOEKEN_DIR   = cfg["UITZOEKEN_DIR"]
    if "DUMP_DIR"        in cfg: DUMP_DIR        = cfg["DUMP_DIR"]
    if "PRULLENBAK_DIR"  in cfg: PRULLENBAK_DIR  = cfg["PRULLENBAK_DIR"]
    if "DATUMLOOS_DIR"   in cfg: DATUMLOOS_DIR   = cfg["DATUMLOOS_DIR"]
    if "PORT"           in cfg:
        try: PORT = int(cfg["PORT"])
        except ValueError: pass
    if "IMMICH_URL"     in cfg: IMMICH_URL     = cfg["IMMICH_URL"]
    if "IMMICH_API_KEY" in cfg: IMMICH_API_KEY = cfg["IMMICH_API_KEY"]
    if "DEBUG"          in cfg: DEBUG          = cfg["DEBUG"].lower() in ("1", "true", "yes")
    if "PRESORT_DEBUG"  in cfg: PRESORT_DEBUG  = cfg["PRESORT_DEBUG"].lower() in ("1", "true", "yes")

# Laad .env bij import (voor normale modus)
_toepas_env(_laad_env_bestand(Path(__file__).parent / ".env"))

# ── Dependency check ──────────────────────────────────────────────────────────

def check_dependencies():
    ok = True
    if sys.version_info < (3, 8):
        print(f"[FOUT] Python 3.8+ vereist (gevonden: {sys.version})")
        ok = False
    try:
        import PIL  # noqa: F401
    except ImportError:
        print("[FOUT] Pillow niet gevonden. Installeer via: pip install Pillow")
        ok = False
    try:
        import pillow_heif  # noqa: F401
    except ImportError:
        print("[WAARSCHUWING] pillow-heif niet gevonden. HEIC-bestanden worden niet ondersteund. Installeer via: pip install pillow-heif")
    try:
        import rawpy  # noqa: F401
    except ImportError:
        print("[WAARSCHUWING] rawpy niet gevonden. ARW/RAW-thumbnails niet beschikbaar. Installeer via: pip install rawpy")
    if not ok:
        sys.exit(1)

# ── Stap 1: Testdata ──────────────────────────────────────────────────────────

def maak_testdata(aantal=40, progress_cb=None):
    import urllib.request
    if BASE_DIR.exists():
        shutil.rmtree(BASE_DIR)

    for m in [BASE_DIR / DUMP_DIR, BASE_DIR / UITZOEKEN_DIR, BASE_DIR / ARCHIEF_DIR, BASE_DIR / DATUMLOOS_DIR]:
        m.mkdir(parents=True, exist_ok=True)

    dump = BASE_DIR / DUMP_DIR
    foto_paden = []
    for n in range(1, aantal + 1):
        pad = dump / f"foto_{n:03d}.jpg"
        url = f"https://picsum.photos/800/600?random={n}"
        _log(f"  Download {n}/{aantal}: {url}")
        if progress_cb:
            progress_cb(n, aantal, f"Foto {n} van {aantal} downloaden…")
        urllib.request.urlretrieve(url, str(pad))
        foto_paden.append(pad)

        ts_start = datetime(2019, 1, 1).timestamp()
        ts_eind  = datetime(2025, 12, 31).timestamp()
        t = random.uniform(ts_start, ts_eind)
        os.utime(pad, (t, t))

    # Duplicaten (±10% van aantal, minimaal 2)
    n_dup = max(2, aantal // 10)
    for i in range(min(n_dup, len(foto_paden))):
        shutil.copy2(foto_paden[i], dump / f"dup_{i+1:03d}.jpg")

    # 3 naamconflicten (als er genoeg fotos zijn)
    if len(foto_paden) > 12:
        for j in range(3):
            shutil.copy2(foto_paden[10], dump / f"IMG_000{j+1}.jpg")

    _log(f"Testdata aangemaakt: {aantal} foto's in {BASE_DIR}/")

# ── Stap 2: Pre-sorteren ──────────────────────────────────────────────────────

def presort(debug=False, progress_cb=None):
    import time
    bronmappen = [BASE_DIR / DUMP_DIR]
    uitzoeken = BASE_DIR / UITZOEKEN_DIR

    alle_fotos = []
    for bron in bronmappen:
        if bron.exists():
            alle_fotos.extend(zoek_fotos_recursief(bron))
    totaal = len(alle_fotos)

    if totaal == 0:
        _log("Presort: geen foto's gevonden.")
        if progress_cb:
            progress_cb(0, 0, "Geen foto's gevonden.")
        return

    balk_breedte = 20
    verplaatst = 0
    overgeslagen = 0
    start = time.time()

    for i, foto in enumerate(alle_fotos, 1):
        datum = lees_datum(foto, debug=debug)
        doel_map = uitzoeken / datum.strftime("%Y-%m")
        doel_map.mkdir(parents=True, exist_ok=True)
        doel = doel_map / foto.name
        was_conflict = doel.exists()
        teller = 2
        while doel.exists():
            doel = doel_map / f"{foto.stem}_{teller}{foto.suffix}"
            teller += 1
        if was_conflict:
            overgeslagen += 1
        shutil.move(str(foto), doel)
        verplaatst += 1

        label = f"{doel.parent.name}/{doel.name}"
        if progress_cb:
            progress_cb(i, totaal, label)
        else:
            pct = i / totaal
            gevuld = int(pct * balk_breedte)
            balk = "█" * gevuld + "░" * (balk_breedte - gevuld)
            print(f"\r[{balk}] {i}/{totaal} ({pct:.0%}) — {label:<40}", end="", flush=True)

    duur = time.time() - start
    if not progress_cb:
        print(f"\n\n✓ {verplaatst} foto('s) verplaatst naar {uitzoeken}/")
        print(f"  Naamconflicten hernoemd : {overgeslagen}")
        print(f"  Tijd                    : {duur:.1f}s")
    _log(f"[Presort] {verplaatst} verplaatst, {overgeslagen} hernoemd ({duur:.1f}s)")

# ── Bestandstypen ─────────────────────────────────────────────────────────────

FOTO_EXTS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.heic', '.heif', '.arw'}

def zoek_fotos(map_pad: Path):
    """Alle ondersteunde fotobestanden in map_pad (case-insensitive), gesorteerd op mtime."""
    bestanden = [
        f for f in map_pad.iterdir()
        if f.is_file() and f.suffix.lower() in FOTO_EXTS
    ]
    return sorted(bestanden, key=lambda f: f.stat().st_mtime)

def zoek_fotos_recursief(map_pad: Path):
    """Alle ondersteunde fotobestanden recursief onder map_pad."""
    return [
        f for f in map_pad.rglob("*")
        if f.is_file() and f.suffix.lower() in FOTO_EXTS
    ]

# ── Datum lezen + cache ────────────────────────────────────────────────────────

_datumcache: dict = {}

def _laad_datumcache():
    global _datumcache
    try:
        _datumcache = json.loads(DATUMCACHE_FILE.read_text())
    except Exception:
        _datumcache = {}

def _sla_datumcache_op():
    try:
        DATUMCACHE_FILE.write_text(json.dumps(_datumcache))
    except Exception:
        pass

_laad_datumcache()

def lees_datum(pad: Path, debug=False) -> datetime:
    """Leest datum uit cache, dan EXIF, dan bestandsnaam, dan mtime."""
    stat = pad.stat()
    cache_key = f"{pad}|{int(stat.st_mtime)}|{stat.st_size}"
    if cache_key in _datumcache:
        try:
            datum = datetime.strptime(_datumcache[cache_key][0], "%Y-%m-%d %H:%M:%S")
            if debug:
                print(f"  [cache]     {pad.name} → {datum:%Y-%m-%d} ({_datumcache[cache_key][1]})")
            return datum
        except Exception:
            pass
    datum, bron = _lees_datum_intern(pad)
    _datumcache[cache_key] = [datum.strftime("%Y-%m-%d %H:%M:%S"), bron]
    _sla_datumcache_op()
    if debug:
        print(f"  [{bron:<12}] {pad.name} → {datum:%Y-%m-%d}")
    return datum

def _lees_datum_intern(pad: Path):
    """Geeft (datetime, bron_label). Volgorde: EXIF → bestandsnaam → mtime."""
    import re
    # 1-3: EXIF
    try:
        from PIL import Image
        img = Image.open(pad)
        exif = img.getexif()
        labels = {36867: "EXIF-origineel", 36868: "EXIF-digitized", 306: "EXIF-datetime"}
        for tag, label in labels.items():
            raw = exif.get(tag)
            if raw:
                try:
                    return datetime.strptime(raw.strip(), "%Y:%m:%d %H:%M:%S"), label
                except ValueError:
                    continue
    except Exception:
        pass

    # 4: Datum uit bestandsnaam
    naam = pad.stem
    patronen = [
        (r"IMG_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})", "naam-android"),
        (r"IMG-(\d{4})(\d{2})(\d{2})-WA",                     "naam-whatsapp"),
        (r"(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})",      "naam-datetime"),
        (r"(\d{4})-(\d{2})-(\d{2}) (\d{2})\.(\d{2})\.(\d{2})","naam-dots"),
        (r"(\d{4})-(\d{2})-(\d{2})",                           "naam-datum"),
        (r"(\d{4})_(\d{2})_(\d{2})",                           "naam-underscore"),
    ]
    for patroon, label in patronen:
        m = re.search(patroon, naam)
        if m:
            g = m.groups()
            try:
                if len(g) >= 6:
                    return datetime(int(g[0]), int(g[1]), int(g[2]),
                                    int(g[3]), int(g[4]), int(g[5])), label
                else:
                    return datetime(int(g[0]), int(g[1]), int(g[2])), label
            except ValueError:
                continue

    # 5: mtime
    return datetime.fromtimestamp(pad.stat().st_mtime), "mtime"

# ── Recents ───────────────────────────────────────────────────────────────────

def laad_recents():
    try:
        return json.loads(RECENTS_FILE.read_text())
    except Exception:
        return []

def sla_recent_op(album):
    recents = laad_recents()
    if album in recents:
        recents.remove(album)
    recents.insert(0, album)
    recents = recents[:10]
    RECENTS_FILE.write_text(json.dumps(recents))
    return recents

# ── Stap 3–5: Webapp ──────────────────────────────────────────────────────────

def start_server():
    import base64
    import json as _json
    import socket
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
    from urllib.parse import parse_qs, urlparse

    uitzoeken = BASE_DIR / UITZOEKEN_DIR
    archief   = BASE_DIR / ARCHIEF_DIR

    try:
        from PIL import Image
        import io as _io
        HAS_PILLOW = True
    except ImportError:
        HAS_PILLOW = False

    import warnings as _warnings
    try:
        with _warnings.catch_warnings():
            _warnings.simplefilter("ignore")
            import rawpy as _rawpy
        HAS_RAWPY = True
    except ImportError:
        HAS_RAWPY = False

    thumbcache = THUMBCACHE_DIR
    thumbcache.mkdir(parents=True, exist_ok=True)

    _geo_cache: dict = {}

    def _lees_gps(pad: Path):
        if not HAS_PILLOW:
            return None
        try:
            from PIL.ExifTags import TAGS, GPSTAGS
            img = Image.open(pad)
            try:
                exif_raw = img._getexif() or {}
            except Exception:
                exif_raw = {}
            gps_raw = None
            for tag_id, val in exif_raw.items():
                if TAGS.get(tag_id) == 'GPSInfo':
                    gps_raw = val
                    break
            if not gps_raw:
                return None
            gps = {GPSTAGS.get(k, k): v for k, v in gps_raw.items()}
            def _to_dec(coords, ref):
                d, m, s = [float(x) for x in coords]
                dd = d + m / 60 + s / 3600
                if ref in ('S', 'W'):
                    dd = -dd
                return round(dd, 6)
            lat = _to_dec(gps['GPSLatitude'], gps.get('GPSLatitudeRef', 'N'))
            lng = _to_dec(gps['GPSLongitude'], gps.get('GPSLongitudeRef', 'E'))
            return {'lat': lat, 'lng': lng}
        except Exception:
            return None

    def _reverse_geocode(lat: float, lng: float):
        key = f"{lat:.3f},{lng:.3f}"
        if key in _geo_cache:
            return _geo_cache[key]
        try:
            import urllib.request as _ur, json as _j
            url = f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lng}&format=json&accept-language=nl"
            req = _ur.Request(url, headers={'User-Agent': 'Declutter/1.0'})
            data = _j.loads(_ur.urlopen(req, timeout=4).read())
            addr = data.get('address', {})
            city = (addr.get('city') or addr.get('town') or
                    addr.get('village') or addr.get('municipality') or '')
            country = addr.get('country', '')
            result = ', '.join(x for x in [city, country] if x) or None
            _geo_cache[key] = result
            return result
        except Exception:
            return None

    import queue as _queue
    _sse_queues = []
    _sse_lock = threading.Lock()

    def _push_sse(msg):
        with _sse_lock:
            dood = []
            for q in _sse_queues:
                try: q.put_nowait(msg)
                except Exception: dood.append(q)
            for q in dood:
                try: _sse_queues.remove(q)
                except ValueError: pass

    def _auto_presort(pad):
        datum = lees_datum(pad)
        doel_map = (BASE_DIR / UITZOEKEN_DIR) / datum.strftime("%Y-%m")
        doel_map.mkdir(parents=True, exist_ok=True)
        doel = doel_map / pad.name
        teller = 2
        while doel.exists():
            doel = doel_map / f"{pad.stem}_{teller}{pad.suffix}"
            teller += 1
        shutil.move(str(pad), doel)
        _log(f"[Watcher] {pad.name} \u2192 {doel.parent.name}/")
        return doel

    _watcher_pauze = [False]  # mutable flag: True = watcher slaat scan over

    def _watcher():
        import time, re as _re
        bronmappen = [BASE_DIR / DUMP_DIR]
        uits = BASE_DIR / UITZOEKEN_DIR
        datumloos_pad = BASE_DIR / DATUMLOOS_DIR

        def scan():
            gevonden = set()
            for bron in bronmappen:
                if bron.exists():
                    for f in bron.rglob("*"):
                        if f.is_file() and f.suffix.lower() in FOTO_EXTS:
                            gevonden.add(str(f))
            if uits.exists():
                for sub in uits.iterdir():
                    if not sub.is_dir(): continue
                    if _re.match(r'^\d{4}-\d{2}$', sub.name):
                        for f in sub.iterdir():
                            if f.is_file() and f.suffix.lower() in FOTO_EXTS:
                                gevonden.add(str(f))
                    else:
                        for f in sub.rglob("*"):
                            if f.is_file() and f.suffix.lower() in FOTO_EXTS:
                                gevonden.add(str(f))
            return gevonden

        bekende = scan()
        while True:
            time.sleep(5)
            if _watcher_pauze[0]:
                bekende = set()  # reset bekende zodat na pauze alles als nieuw geldt
                continue
            try:
                huidige = scan()
                nieuw = huidige - bekende
                if nieuw:
                    verplaatste = []
                    for pad_str in sorted(nieuw):
                        pad = Path(pad_str)
                        # Datumloos-map nooit pre-sorten
                        in_datumloos = datumloos_pad.exists() and pad.is_relative_to(datumloos_pad)
                        moet_presorten = not in_datumloos and any(
                            pad.is_relative_to(b) for b in bronmappen if b.exists()
                        )
                        if not moet_presorten and not in_datumloos and uits.exists():
                            try:
                                if pad.is_relative_to(uits):
                                    rel = pad.relative_to(uits)
                                    top = rel.parts[0] if rel.parts else ''
                                    if not _re.match(r'^\d{4}-\d{2}$', top):
                                        moet_presorten = True
                            except Exception:
                                pass
                        if moet_presorten:
                            try:
                                doel = _auto_presort(pad)
                                try:
                                    verplaatste.append({
                                        "naam": pad.name,
                                        "van": str(pad.relative_to(BASE_DIR)),
                                        "naar": str(doel.relative_to(BASE_DIR)),
                                    })
                                except Exception:
                                    pass
                            except Exception as e:
                                _log(f"[Watcher] Presort fout {pad.name}: {e}")
                        else:
                            try:
                                verplaatste.append({
                                    "naam": pad.name,
                                    "van": None,
                                    "naar": str(pad.relative_to(BASE_DIR)),
                                })
                            except Exception:
                                pass
                    bekende = scan()
                    _push_sse(_json.dumps({"type": "update", "bestanden": verplaatste, "jm": jaren_maanden()}))
                else:
                    bekende = huidige
            except Exception as e:
                _log(f"[Watcher] Scanfout: {e}")

    threading.Thread(target=_watcher, daemon=True).start()

    def get_thumbnail(pad: Path) -> bytes:
        stat = pad.stat()
        cache_key = f"{pad.name}_{int(stat.st_mtime)}_{stat.st_size}.jpg"
        cache_pad = thumbcache / cache_key
        if cache_pad.exists():
            return cache_pad.read_bytes()

        ext = pad.suffix.lower()

        # ARW (Sony RAW): probeer ingebedde JPEG-preview te extraheren
        if ext == '.arw':
            img = None
            if HAS_RAWPY:
                try:
                    import io as _io
                    with _rawpy.imread(str(pad)) as raw:
                        thumb = raw.extract_thumb()
                    if thumb.format.name == 'JPEG':
                        img = Image.open(_io.BytesIO(thumb.data))
                    elif thumb.format.name == 'BITMAP':
                        img = Image.fromarray(thumb.data)
                except Exception:
                    pass
            if img is None:
                data = _maak_placeholder(pad.name)
                cache_pad.write_bytes(data)
                return data
            # Gevallen door naar Pillow resize hieronder

        # HEIC/HEIF: probeer via pillow-heif of pyheif
        elif ext in ('.heic', '.heif'):
            img = None
            try:
                import pillow_heif
                pillow_heif.register_heif_opener()
                img = Image.open(pad)
            except ImportError:
                pass
            if img is None:
                try:
                    import pyheif
                    import io as _io
                    heif = pyheif.read(pad)
                    img = Image.frombytes(heif.mode, heif.size, heif.data)
                except ImportError:
                    pass
            if img is None:
                data = _maak_placeholder(pad.name)
                cache_pad.write_bytes(data)
                return data
            # Gevallen door naar Pillow resize hieronder
        else:
            img = None  # gewone formaten: Pillow opent ze zelf hieronder

        if HAS_PILLOW:
            try:
                import io as _io
                if img is None:
                    img = Image.open(pad)
                from PIL import ImageOps
                img = ImageOps.exif_transpose(img)
                img = img.convert("RGB")
                img.thumbnail((400, 400))
                buf = _io.BytesIO()
                img.save(buf, format="JPEG", quality=70)
                data = buf.getvalue()
                cache_pad.write_bytes(data)
                return data
            except Exception:
                pass

        # Fallback: ruwe bytes (alleen zinvol voor echte JPEG)
        data = pad.read_bytes()
        cache_pad.write_bytes(data)
        return data

    def _maak_placeholder(naam: str) -> bytes:
        """Grijs placeholder JPEG met bestandsextensie als label."""
        import io as _io
        ext = Path(naam).suffix.upper().lstrip('.')
        if HAS_PILLOW:
            try:
                from PIL import ImageDraw, ImageFont
                img = Image.new("RGB", (200, 200), color=(60, 60, 60))
                draw = ImageDraw.Draw(img)
                draw.text((100, 90), ext, fill=(180, 180, 180), anchor="mm")
                draw.text((100, 115), naam[:20], fill=(120, 120, 120), anchor="mm")
                buf = _io.BytesIO()
                img.save(buf, format="JPEG", quality=70)
                return buf.getvalue()
            except Exception:
                pass
        # Ultra-minimaal grijs JPEG (1x1 pixel, grijs)
        return bytes([
            0xFF,0xD8,0xFF,0xE0,0x00,0x10,0x4A,0x46,0x49,0x46,0x00,0x01,
            0x01,0x00,0x00,0x01,0x00,0x01,0x00,0x00,0xFF,0xDB,0x00,0x43,
            0x00,0x08,0x06,0x06,0x07,0x06,0x05,0x08,0x07,0x07,0x07,0x09,
            0x09,0x08,0x0A,0x0C,0x14,0x0D,0x0C,0x0B,0x0B,0x0C,0x19,0x12,
            0x13,0x0F,0x14,0x1D,0x1A,0x1F,0x1E,0x1D,0x1A,0x1C,0x1C,0x20,
            0x24,0x2E,0x27,0x20,0x22,0x2C,0x23,0x1C,0x1C,0x28,0x37,0x29,
            0x2C,0x30,0x31,0x34,0x34,0x34,0x1F,0x27,0x39,0x3D,0x38,0x32,
            0x3C,0x2E,0x33,0x34,0x32,0xFF,0xC0,0x00,0x0B,0x08,0x00,0x01,
            0x00,0x01,0x01,0x01,0x11,0x00,0xFF,0xC4,0x00,0x1F,0x00,0x00,
            0x01,0x05,0x01,0x01,0x01,0x01,0x01,0x01,0x00,0x00,0x00,0x00,
            0x00,0x00,0x00,0x00,0x01,0x02,0x03,0x04,0x05,0x06,0x07,0x08,
            0x09,0x0A,0x0B,0xFF,0xC4,0x00,0xB5,0x10,0x00,0x02,0x01,0x03,
            0x03,0x02,0x04,0x03,0x05,0x05,0x04,0x04,0x00,0x00,0x01,0x7D,
            0x01,0x02,0x03,0x00,0x04,0x11,0x05,0x12,0x21,0x31,0x41,0x06,
            0x13,0x51,0x61,0x07,0x22,0x71,0x14,0x32,0x81,0x91,0xA1,0x08,
            0x23,0x42,0xB1,0xC1,0x15,0x52,0xD1,0xF0,0x24,0x33,0x62,0x72,
            0x82,0x09,0x0A,0x16,0x17,0x18,0x19,0x1A,0x25,0x26,0x27,0x28,
            0x29,0x2A,0x34,0x35,0x36,0x37,0x38,0x39,0x3A,0x43,0x44,0x45,
            0x46,0x47,0x48,0x49,0x4A,0x53,0x54,0x55,0x56,0x57,0x58,0x59,
            0x5A,0x63,0x64,0x65,0x66,0x67,0x68,0x69,0x6A,0x73,0x74,0x75,
            0x76,0x77,0x78,0x79,0x7A,0x83,0x84,0x85,0x86,0x87,0x88,0x89,
            0x8A,0x92,0x93,0x94,0x95,0x96,0x97,0x98,0x99,0x9A,0xA2,0xA3,
            0xA4,0xA5,0xA6,0xA7,0xA8,0xA9,0xAA,0xB2,0xB3,0xB4,0xB5,0xB6,
            0xB7,0xB8,0xB9,0xBA,0xC2,0xC3,0xC4,0xC5,0xC6,0xC7,0xC8,0xC9,
            0xCA,0xD2,0xD3,0xD4,0xD5,0xD6,0xD7,0xD8,0xD9,0xDA,0xE1,0xE2,
            0xE3,0xE4,0xE5,0xE6,0xE7,0xE8,0xE9,0xEA,0xF1,0xF2,0xF3,0xF4,
            0xF5,0xF6,0xF7,0xF8,0xF9,0xFA,0xFF,0xDA,0x00,0x08,0x01,0x01,
            0x00,0x00,0x3F,0x00,0xFB,0xD3,0xFF,0xD9
        ])

    def jaren_maanden():
        """Geeft dict {jaar: [{naam, aantal}, ...]} van aanwezige submappen."""
        result = {}
        if not uitzoeken.exists():
            return result
        for submap in sorted(uitzoeken.iterdir()):
            if not submap.is_dir():
                continue
            try:
                jaar, _ = submap.name.split("-")
                n = sum(1 for f in submap.iterdir()
                        if f.is_file() and f.suffix.lower() in FOTO_EXTS)
                result.setdefault(jaar, []).append({"naam": submap.name, "aantal": n})
            except ValueError:
                pass
        return result

    # Systeem-/NAS-bestanden die een "lege" map toch niet-leeg maken
    _SYS_NAMEN = {'desktop.ini', 'thumbs.db', '.ds_store', '@eadir', '#recycle'}

    def _heeft_echte_inhoud(pad):
        """True als pad echte bestanden/mappen bevat (geen NAS/OS-systeembestanden)."""
        for f in pad.iterdir():
            if f.name.startswith('.') or f.name.startswith('@'):
                continue
            if f.name.lower() in _SYS_NAMEN:
                continue
            return True
        return False

    def verwijder_lege_mappen():
        """Verwijdert lege YYYY-MM submappen uit in_behandeling."""
        import re
        if uitzoeken.exists():
            for sub in list(uitzoeken.iterdir()):
                if sub.is_dir() and re.match(r'^\d{4}-\d{2}$', sub.name):
                    if not _heeft_echte_inhoud(sub):
                        try:
                            shutil.rmtree(sub)
                            _log(f"[Opruimen] Lege map verwijderd: {sub.name}")
                        except OSError:
                            pass

    def fotos_in_map(maand_key):
        """Geeft lijst van foto-paden gesorteerd op mtime."""
        pad = uitzoeken / maand_key
        if not pad.exists():
            return []
        return zoek_fotos(pad)

    def burst_groepen(fotos):
        """Groepeert foto's. Geeft lijst van dicts met 'fotos' en 'type'.
        type: 'tight' (≤30s), 'moment' (31-300s), 'los' (>300s of enkelvoudig)."""
        if not fotos:
            return []
        # Groepeer op 300s venster
        ruw = []
        huidige = [fotos[0]]
        for foto in fotos[1:]:
            dt = abs(foto.stat().st_mtime - huidige[-1].stat().st_mtime)
            if dt <= 300:
                huidige.append(foto)
            else:
                ruw.append(huidige)
                huidige = [foto]
        ruw.append(huidige)
        # Bepaal type per groep
        result = []
        for groep in ruw:
            if len(groep) < 2:
                type_ = "los"
                span_sec = 0
            else:
                mtimes = [f.stat().st_mtime for f in groep]
                max_gap = max(abs(mtimes[i+1] - mtimes[i]) for i in range(len(mtimes)-1))
                type_ = "tight" if max_gap <= 30 else "moment"
                span_sec = int(max(mtimes) - min(mtimes))
            result.append({"fotos": groep, "type": type_, "span_sec": span_sec})
        return result

    def uniek_doel(doel_map: Path, naam: str) -> Path:
        doel = doel_map / naam
        teller = 2
        stem = Path(naam).stem
        suffix = Path(naam).suffix
        while doel.exists():
            doel = doel_map / f"{stem}_{teller}{suffix}"
            teller += 1
        return doel

    _immich_timer    = None
    _immich_lib_id   = None   # gecached library ID

    def _immich_haal_lib_id():
        """Haalt het eerste library ID op via GET /api/libraries. Cachet in geheugen."""
        nonlocal _immich_lib_id
        if _immich_lib_id:
            return _immich_lib_id
        try:
            import urllib.request, json as _j
            req = urllib.request.Request(
                f"{IMMICH_URL.rstrip('/')}/api/libraries",
                headers={"x-api-key": IMMICH_API_KEY} if IMMICH_API_KEY else {},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                libs = _j.loads(resp.read())
            if libs:
                _immich_lib_id = libs[0]["id"]
                return _immich_lib_id
        except Exception as e:
            _log(f"[Immich] Library ID ophalen mislukt: {e}")
        return None

    def _doe_rescan():
        lib_id = _immich_haal_lib_id()
        if not lib_id:
            _log("[Immich] Geen library ID — rescan overgeslagen.")
            return
        try:
            import urllib.request
            req = urllib.request.Request(
                f"{IMMICH_URL.rstrip('/')}/api/libraries/{lib_id}/scan",
                data=b"{}",
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    **({"x-api-key": IMMICH_API_KEY} if IMMICH_API_KEY else {}),
                },
            )
            urllib.request.urlopen(req, timeout=10)
            _log("[Immich] Rescan getriggerd.")
        except Exception as e:
            _log(f"[Immich] Rescan mislukt: {e}")

    def immich_rescan():
        nonlocal _immich_timer
        if not IMMICH_URL:
            return
        if _immich_timer is not None:
            _immich_timer.cancel()
        _log("[Immich] Rescan gepland over 3600s...")
        _immich_timer = threading.Timer(3600, _doe_rescan)
        _immich_timer.daemon = True
        _immich_timer.start()

    def render_hoofdpagina():

        return f"""<!DOCTYPE html>
<html lang="nl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Declutter</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='6' fill='%231a1a1a'/%3E%3Crect x='3' y='10' width='26' height='17' rx='3' fill='%232a6a9a'/%3E%3Cpath d='M11 10 L13 6 H19 L21 10Z' fill='%232a6a9a'/%3E%3Ccircle cx='16' cy='18.5' r='5' fill='%230d0d0d'/%3E%3Ccircle cx='16' cy='18.5' r='3.5' fill='%234a9abf'/%3E%3Ccircle cx='16' cy='18.5' r='1.5' fill='%23c8eef8'/%3E%3Ccircle cx='24.5' cy='13.5' r='1.5' fill='%23ffc107'/%3E%3C/svg%3E">
<style>
  :root{{--foto-breedte:220px;--left-w:240px;--right-w:210px}}
  *{{box-sizing:border-box}}
  body{{font-family:sans-serif;margin:0;background:#1a1a1a;color:#eee;overflow-x:hidden}}
  /* ── Toolbar ── */
  .toolbar{{position:sticky;top:0;background:#111;padding:.5rem .8rem;display:flex;align-items:center;z-index:20;border-bottom:1px solid #333}}
  .toolbar h1{{margin:0;font-size:1rem;white-space:nowrap;color:#f90;letter-spacing:.04em}}
  input[type=text]{{width:100%;padding:.35rem .6rem;background:#222;border:1px solid #444;color:#eee;border-radius:4px;font-size:.85rem}}
  .album-staat{{position:absolute;right:6px;top:50%;transform:translateY(-50%);width:8px;height:8px;border-radius:50%;display:none}}
  .album-staat.groen{{background:#4c4;display:block}}
  .album-staat.oranje{{background:#f80;display:block}}
  .album-staat.rood{{background:#c44;display:block}}
  button{{padding:.35rem .7rem;border:none;border-radius:4px;cursor:pointer;font-size:.82rem}}
  .btn-reset{{background:#c33;color:#fff}}
  .btn-alles{{background:#383838;color:#ccc}}
  .btn-presort{{background:#1e4a1e;color:#8f8;border:1px solid #2d6a2d}}
  .btn-opruim{{background:#1a2a3a;color:#7af;border:1px solid #2a4a6a}}
  .btn-cache{{background:#3a2a00;color:#f80;border:1px solid #5a4000}}
  .btn-later{{background:#1e1e3a;color:#aaf;border:1px solid #336}}
  .btn-verwijder{{background:#5a1010;color:#faa;border:1px solid #8a2020}}
  .btn-verwijder:disabled{{background:#2a1a1a;color:#664;border-color:#3a1a1a;cursor:default}}
  /* Prullenbak: geen acties beschikbaar */
  .prullenbak-modus .foto-del{{display:none!important}}
  .prullenbak-modus .foto-zoom{{display:none!important}}
  .prullenbak-modus .foto{{cursor:default}}
  /* Prullenbak tree: geen hover-acties */
  .boom-rij.pb-readonly{{cursor:default}}
  .boom-rij.pb-readonly .boom-rij-acties{{display:none!important}}
  #status{{font-size:.72rem;color:#8f8;padding:.25rem .7rem;background:#111;border-top:1px solid #1e1e1e;flex-shrink:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
  /* ── 3-koloms layout ── */
  html,body{{height:100%;overflow:hidden}}
  body{{display:flex;flex-direction:column}}
  #progress-wrap{{height:4px;flex-shrink:0}}
  #progress{{height:4px;background:#4a8abf;transition:width .1s;width:0%}}
  #main-wrap{{flex:1;display:flex;min-height:0;overflow:hidden;flex-direction:column}}
  #cols-wrap{{flex:1;display:flex;min-height:0;overflow:hidden}}
  /* ── Linkerpaneel (treeview) ── */
  #left-panel{{width:var(--left-w);flex-shrink:0;background:#141414;overflow-y:auto;display:flex;flex-direction:column}}
  #left-resizer{{width:5px;flex-shrink:0;cursor:col-resize;background:#2a2a2a;transition:background .1s;z-index:10}}
  #left-resizer:hover,#left-resizer.dragging{{background:#4a8abf}}
  .ts-hdr{{display:flex;align-items:center;gap:.3rem;padding:.35rem .6rem;font-size:.75rem;font-weight:bold;color:#888;cursor:pointer;user-select:none;border-bottom:1px solid #222;background:#111}}
  .ts-hdr:hover{{color:#bbb}}
  .ts-pijl{{font-size:.55rem;transition:transform .15s;display:inline-block;width:10px;text-align:center}}
  .ts-hdr.open .ts-pijl{{transform:rotate(90deg)}}
  .ts-body{{display:none;padding:.2rem 0}}
  .ts-body.open{{display:block}}
  /* Tree nodes */
  .boom-rij{{display:flex;align-items:center;gap:.25rem;padding:2px 0 2px 8px;cursor:pointer;border-radius:3px;margin:0 3px}}
  .boom-rij:hover{{background:#1e1e1e}}
  .boom-pijl{{width:12px;font-size:.5rem;color:#555;flex-shrink:0;text-align:center;user-select:none}}
  .boom-naam{{font-size:.75rem;color:#9bc;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
  .boom-count{{color:#555;font-size:.68rem;font-weight:normal}}
  .boom-naam:hover{{color:#ddf}}
  .boom-naam.actief{{color:#f90;font-weight:bold}}
  .boom-sub{{display:none}}
  .maand-rij{{display:flex;align-items:center;gap:.25rem;padding:2px 0 2px 20px;cursor:pointer;border-radius:3px;margin:0 3px}}
  .maand-rij:hover{{background:#1e1e1e}}
  .maand-rij span{{font-size:.73rem;color:#9bc}}
  .maand-rij.actief span{{color:#f90;font-weight:bold}}
  .jaar-rij{{display:flex;align-items:center;gap:.25rem;padding:.25rem .6rem;cursor:pointer;user-select:none}}
  .jaar-rij:hover .jaar-label{{color:#eee}}
  .jaar-label{{font-size:.72rem;font-weight:bold;color:#666}}
  .jaar-pijl{{font-size:.5rem;color:#444;width:10px;text-align:center}}
  .jaar-sub{{display:none;padding:.1rem 0}}
  /* Activiteitlog in linkerpaneel */
  #ts-activiteit .act-rij{{font-size:.68rem;padding:.2rem .6rem;border-bottom:1px solid #1e1e1e;line-height:1.4}}
  #ts-activiteit .act-rij .act-ts{{color:#444}}
  #ts-activiteit .act-rij .act-naam{{color:#ccc;font-weight:bold}}
  #ts-activiteit .act-rij .act-naar{{color:#4a8a4a}}
  /* ── Middenpaneel (foto's) ── */
  #fotos-wrap{{flex:1;min-width:0;overflow:hidden;display:flex;flex-direction:column}}
  #thumb-toolbar{{display:flex;align-items:center;gap:.5rem;padding:.35rem .7rem;background:#111;border-bottom:1px solid #222;font-size:.72rem;color:#666;flex-shrink:0;position:sticky;top:0;z-index:5}}
  #thumb-toolbar input[type=range]{{width:90px;accent-color:#4a8abf;cursor:pointer}}
  #fotos{{flex:1;padding:.75rem;min-width:0;overflow-y:auto}}
  /* ── Rechterpaneel (selectie) ── */
  #sidebar{{width:var(--right-w);flex-shrink:0;background:#111;border-left:1px solid #2a2a2a;overflow-y:auto;padding:.6rem}}
  #sidebar h3{{margin:0 0 .4rem;font-size:.78rem;color:#888;text-transform:uppercase;letter-spacing:.05em}}
  .sb-item{{display:flex;align-items:center;gap:.35rem;margin-bottom:.35rem;background:#1a1a1a;border-radius:4px;padding:3px 4px}}
  .sb-thumb{{width:34px;height:34px;object-fit:cover;border-radius:3px;flex-shrink:0}}
  .sb-naam{{font-size:.62rem;color:#bbb;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
  .sb-x{{background:none;border:none;color:#c66;cursor:pointer;font-size:.85rem;padding:0 2px;flex-shrink:0}}
  .sb-leeg{{font-size:.72rem;color:#444}}
  /* ── Foto-grid ── */
  .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(var(--foto-breedte),1fr));gap:8px}}
  .dag-header{{font-size:.88rem;font-weight:bold;color:#bbb;padding:.5rem 0 .25rem;border-bottom:1px solid #2a2a2a;margin-bottom:.1rem;grid-column:1/-1}}
  .bladwijzer{{display:flex;align-items:center;gap:.5rem;margin:.5rem 0;color:#c44;font-size:.72rem;font-style:italic}}
  .bladwijzer::before{{content:'';flex:0 0 10px;height:10px;background:#c44;border-radius:50%}}
  .bladwijzer::after{{content:'';flex:1;height:1px;background:#c44}}
  .burst{{border-radius:6px;padding:5px;margin-bottom:6px}}
  .burst.tight{{background:#2d2010}}
  .burst.moment{{background:#1a1a2d}}
  .burst-label{{font-size:.67rem;margin-bottom:3px;cursor:pointer;display:inline-block;padding:2px 5px;border-radius:4px}}
  .burst-label:hover{{opacity:.8}}
  .burst.tight .burst-label{{color:#c8860a}}
  .burst.moment .burst-label{{color:#7788cc}}
  /* ── Lightbox ── */
  #lb{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.93);z-index:100;flex-direction:column;align-items:center;justify-content:center}}
  #lb-inner{{display:flex;flex-direction:column;width:100%;max-width:1200px;height:100vh;padding:1rem;gap:.75rem;box-sizing:border-box}}
  #lb-header{{display:flex;justify-content:space-between;align-items:center;color:#aaa;font-size:.82rem}}
  #lb-teller{{color:#eee;font-weight:bold}}
  #lb-fotos{{display:flex;gap:1rem;flex:1;min-height:0}}
  .lb-foto-wrap{{flex:1;display:flex;flex-direction:column;gap:.4rem;min-width:0}}
  .lb-foto-wrap img{{width:100%;height:calc(100% - 56px);object-fit:contain;border-radius:6px;border:3px solid transparent;cursor:pointer}}
  .lb-foto-wrap img.met-hart{{border-color:#e33}}
  .lb-foto-info{{font-size:.7rem;color:#aaa;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:.3rem}}
  .lb-hart{{background:none;border:2px solid #555;border-radius:50%;width:34px;height:34px;font-size:1rem;cursor:pointer;color:#aaa;flex-shrink:0}}
  .lb-hart.actief{{border-color:#e33;color:#e33}}
  #lb-nav{{display:flex;align-items:center;justify-content:center;gap:1rem}}
  .lb-pijl{{background:#333;border:none;color:#eee;font-size:1.3rem;width:40px;height:40px;border-radius:50%;cursor:pointer}}
  .lb-pijl:disabled{{opacity:.3;cursor:default}}
  #lb-acties{{display:flex;align-items:center;gap:.75rem;flex-wrap:wrap}}
  #lb-info{{flex:1;font-size:.78rem;color:#aaa}}
  .lb-btn-over{{background:#383838;border:none;color:#eee;padding:.4rem .9rem;border-radius:4px;cursor:pointer}}
  .lb-btn-bevestig{{background:#2a7a2a;border:none;color:#eee;padding:.4rem .9rem;border-radius:4px;cursor:pointer;font-weight:bold}}
  .lb-btn-bevestig:disabled{{background:#2a2a2a;color:#555;cursor:default}}
  /* ── Foto-kaarten ── */
  .foto{{cursor:pointer;border:2px solid transparent;border-radius:5px;overflow:hidden;width:100%;background:#222;position:relative}}
  .foto.geselecteerd{{border-color:#f90}}
  .foto img{{width:100%;display:block;min-height:70px;background:#2a2a2a}}
  .naam{{font-size:.58rem;padding:2px 4px;color:#888;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
  .ext-badge{{position:absolute;top:3px;left:3px;background:rgba(0,0,0,.7);color:#fc0;font-size:.52rem;font-weight:bold;padding:1px 3px;border-radius:3px;pointer-events:none;text-transform:uppercase}}
  .foto-del{{position:absolute;top:3px;right:3px;background:rgba(140,0,0,.85);color:#fff;border:none;border-radius:50%;width:18px;height:18px;font-size:.8rem;line-height:1;cursor:pointer;display:flex;align-items:center;justify-content:center;padding:0;opacity:0;transition:opacity .15s;z-index:2}}
  .foto:hover .foto-del{{opacity:1}}
  .foto-zoom,.foto-info-btn{{position:absolute;left:3px;background:rgba(0,0,0,.7);color:#fff;border-radius:3px;width:18px;height:18px;font-size:.7rem;display:flex;align-items:center;justify-content:center;opacity:0;transition:opacity .15s;text-decoration:none;z-index:2;border:none;cursor:pointer;padding:0}}
  .foto-zoom{{top:20px}}
  .foto-info-btn{{top:41px;font-size:.65rem}}
  .foto:hover .foto-zoom,.foto:hover .foto-info-btn{{opacity:1}}
  .foto-tijdstip{{font-size:.52rem;color:#555;margin-left:.25rem}}
  /* ── Info-panel ── */
  #info-panel{{position:fixed;z-index:500;background:#1e1e1e;border:1px solid #444;border-radius:7px;padding:.7rem .9rem;width:260px;font-size:.76rem;color:#ccc;box-shadow:0 4px 24px rgba(0,0,0,.85);display:none}}
  .info-rij{{display:flex;gap:.4rem;margin-bottom:.25rem;align-items:flex-start;line-height:1.4}}
  .info-lbl{{color:#555;white-space:nowrap;min-width:50px;flex-shrink:0}}
  .info-val{{color:#ccc;word-break:break-all}}
  #info-kaart{{width:100%;height:200px;border:0;border-radius:4px;margin-top:.4rem;display:none}}
  .datum{{font-size:.7rem;padding:2px 4px 3px;color:#888}}
  #thumb-toolbar button{{padding:.18rem .45rem;font-size:.7rem}}
  /* ── Activiteitslog (full-width) ── */
  #activiteit-wrap{{flex-shrink:0;border-top:1px solid #222;background:#111}}
  #activiteit-hdr{{padding:.3rem .8rem;font-size:.75rem;font-weight:bold;color:#555;cursor:pointer;user-select:none;display:flex;align-items:center;gap:.3rem}}
  #activiteit-hdr:hover{{color:#888}}
  #activiteit-pijl{{font-size:.5rem;transition:transform .15s}}
  #activiteit-body{{max-height:130px;overflow-y:auto;padding:.2rem 0}}
  #activiteit-lijst{{display:flex;flex-wrap:wrap;gap:0;font-size:.68rem}}
  .act-rij{{padding:2px 10px;border-right:1px solid #1e1e1e;white-space:nowrap}}
  .act-ts{{color:#3a3a3a}}
  .act-naam{{color:#888}}
  .act-naar{{color:#4a7a4a}}
  /* ── Tree drag-over ── */
  .boom-rij.drag-over{{background:#1a3a1a;outline:1px solid #4a8}}
  .maand-rij.drag-over{{background:#1a3a1a;outline:1px solid #4a8}}
  .ts-hdr.drag-over{{background:#1e3a1e;outline:1px solid #4a8}}
  /* ── Tree rename + add ── */
  .boom-rij-acties{{display:none;gap:2px;margin-left:auto}}
  .boom-rij:hover .boom-rij-acties{{display:flex}}
  .boom-rij-btn{{background:none;border:none;color:#555;cursor:pointer;font-size:.65rem;padding:1px 3px;line-height:1;border-radius:2px}}
  .boom-rij-btn:hover{{color:#ccc;background:#333}}
  .ts-hdr-acties{{margin-left:auto;display:flex;gap:2px}}
  .ts-hdr-btn{{background:none;border:none;color:#555;cursor:pointer;font-size:.72rem;padding:1px 4px;border-radius:3px;line-height:1}}
  .ts-hdr-btn:hover{{color:#8f8;background:#1e2e1e}}
  /* ── Info-help knop ── */
  #info-help-btn{{position:fixed;right:1rem;bottom:1rem;width:32px;height:32px;border-radius:50%;background:#2a2a2a;color:#888;border:1px solid #444;font-size:1rem;font-weight:bold;cursor:pointer;z-index:200;display:flex;align-items:center;justify-content:center;line-height:1}}
  #info-help-btn:hover{{background:#383838;color:#ccc}}
  @media(max-width:700px){{
    html,body{{height:auto;overflow:auto}}
    :root{{--foto-breedte:130px;--left-w:100%;--right-w:100%}}
    #main-wrap{{flex-direction:column;overflow:visible}}
    #left-panel{{width:100%!important;max-height:240px;border-right:none;border-bottom:1px solid #2a2a2a}}
    #left-resizer{{display:none}}
    #fotos-wrap{{overflow:visible}}
    #sidebar{{width:100%;border-left:none;border-top:1px solid #2a2a2a}}
  }}
</style>
</head>
<body>
<div class="toolbar">
  <h1>Declutter</h1>
</div>
<div id="progress-wrap"><div id="progress"></div></div>
<div id="main-wrap">
<div id="cols-wrap">
  <!-- Linkerpaneel: treeview -->
  <div id="left-panel">
    <div class="ts-hdr open" id="tsh-behandeling" onclick="toggleTS('behandeling')">
      <span class="ts-pijl">&#9658;</span> In behandeling
    </div>
    <div class="ts-body open" id="tsb-behandeling"><div id="tree-behandeling" style="padding:.2rem 0"><span style="color:#444;font-size:.7rem;padding:4px 10px">Laden…</span></div></div>
    <div class="ts-hdr open" id="tsh-verwerkt" onclick="toggleTS('verwerkt')"
         ondragover="_sectionDragOver(event,this)" ondragleave="_boomDragLeave(this)"
         ondrop="_sectionDrop(event,'verwerkt')">
      <span class="ts-pijl">&#9658;</span> Verwerkt (met datum)
      <span class="ts-hdr-acties"><button class="ts-hdr-btn" title="Nieuwe map in Verwerkt" onclick="event.stopPropagation();toonNieuweMapModal('verwerkt')">+</button></span>
    </div>
    <div class="ts-body open" id="tsb-verwerkt"><div id="tree-verwerkt"></div></div>
    <div class="ts-hdr open" id="tsh-datumloos" onclick="toggleTS('datumloos')"
         ondragover="_sectionDragOver(event,this)" ondragleave="_boomDragLeave(this)"
         ondrop="_sectionDrop(event,'datumloos')">
      <span class="ts-pijl">&#9658;</span> Verwerkt (zonder datum)
      <span class="ts-hdr-acties"><button class="ts-hdr-btn" title="Nieuwe map in Zonder datum" onclick="event.stopPropagation();toonNieuweMapModal('datumloos')">+</button></span>
    </div>
    <div class="ts-body open" id="tsb-datumloos"><div id="tree-datumloos"></div></div>
    <div class="ts-hdr" id="tsh-prullenbak" onclick="toggleTS('prullenbak')">
      <span class="ts-pijl">&#9658;</span> &#128465; Prullenbak
    </div>
    <div class="ts-body" id="tsb-prullenbak"><div id="tree-prullenbak"></div></div>
  </div>
  <div id="left-resizer"></div>
  <!-- Middenpaneel: foto's -->
  <div id="fotos-wrap">
    <div id="thumb-toolbar">
      <span>Grootte:</span>
      <input type="range" id="breedte-slider" min="100" max="400" value="220"
             oninput="sliderVerander(this.value)">
      <span id="breedte-label">220px</span>
      <div style="flex:1"></div>
      <button type="button" class="btn-later" onclick="bewaarLater()" title="Zet selectie in 'Later uitzoeken'">⏳ Later</button>
      <button type="button" id="btn-verwijder" class="btn-verwijder" onclick="verwijderSelectie()" title="Verplaats selectie naar prullenbak" disabled>&#128465; Verwijder</button>
      <button type="button" class="btn-alles" onclick="allesToggle()" title="Alles (de)selecteren">&#9745; Alles</button>
      <button type="button" class="btn-presort" onclick="presortRun()" title="Foto's uit ruwe_data indelen op datum">&#9881; Presort</button>
      <button type="button" class="btn-opruim" onclick="opruimLege()" title="Verwijder lege mappen uit 'In behandeling'">&#129529; Opruimen</button>
      <button type="button" class="btn-cache" onclick="clearThumbs()" title="Thumbnail-cache leegmaken">&#128465; Cache</button>
      <button type="button" class="btn-reset" onclick="resetTest()" title="Testdata opnieuw aanmaken">&#8635; Reset</button>
    </div>
    <div id="fotos"><p style="color:#555;padding:1rem">Kies een map in de boom links.</p></div>
    <div id="status">Kies een map in de boom links.</div>
  </div>
  <!-- Rechterpaneel: geselecteerd -->
  <div id="sidebar"><h3>Geselecteerd</h3><p class="sb-leeg">Nog niets geselecteerd.</p></div>
</div><!-- /cols-wrap -->
<!-- Activiteitslog: full-width onder de 3 kolommen -->
<div id="activiteit-wrap">
  <div id="activiteit-hdr" onclick="toggleActiviteit()">
    <span id="activiteit-pijl">&#9658;</span> Activiteitslog
    <span id="activiteit-count" style="color:#555;margin-left:.5rem;font-weight:normal"></span>
  </div>
  <div id="activiteit-body" style="display:none">
    <div id="activiteit-lijst"><span style="color:#444;font-size:.72rem;padding:4px 10px;display:block">Nog geen activiteit in deze sessie.</span></div>
  </div>
</div>
</div><!-- /main-wrap -->

<!-- Drag-tooltip -->
<div id="drag-tooltip" style="position:fixed;display:none;background:rgba(30,30,30,.92);color:#f90;border:1px solid #555;border-radius:6px;padding:4px 10px;font-size:.78rem;pointer-events:none;z-index:600"></div>

<!-- Nieuwe map modal -->
<div id="nieuwmap-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.75);z-index:300;align-items:center;justify-content:center">
  <div style="background:#1e1e1e;border:1px solid #555;border-radius:8px;padding:1.4rem 1.8rem;max-width:420px;width:90%;color:#eee">
    <h2 style="margin:0 0 .8rem;font-size:1rem;color:#eee" id="nieuwmap-titel">Nieuwe map aanmaken</h2>
    <p style="margin:0 0 .6rem;font-size:.82rem;color:#888" id="nieuwmap-uitleg"></p>
    <div style="position:relative;margin-bottom:.5rem">
      <input type="text" id="nieuwmap-naam" placeholder="2026-04-02 Vakantie  of  Marktplaats/Babybedje"
             oninput="valideerNieuweMap()" style="width:100%;padding:.4rem 2rem .4rem .6rem;background:#222;border:1px solid #444;color:#eee;border-radius:4px;font-size:.88rem">
      <span class="album-staat" id="nieuwmap-staat"></span>
    </div>
    <div id="nieuwmap-hint" style="font-size:.72rem;color:#666;margin-bottom:.8rem;min-height:1.2rem"></div>
    <div style="display:flex;gap:.6rem;justify-content:flex-end">
      <button type="button" onclick="sluitNieuweMapModal()" style="background:#383838;color:#ccc;padding:.45rem 1rem;border:none;border-radius:4px;cursor:pointer">Annuleren</button>
      <button type="button" id="nieuwmap-ok" onclick="bevestigNieuweMap()" style="background:#2a6a2a;color:#eee;padding:.45rem 1rem;border:none;border-radius:4px;cursor:pointer;font-weight:bold" disabled>Aanmaken</button>
    </div>
  </div>
</div>

<!-- Hernoem-modal -->
<div id="hernoem-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.75);z-index:300;align-items:center;justify-content:center">
  <div style="background:#1e1e1e;border:1px solid #555;border-radius:8px;padding:1.4rem 1.8rem;max-width:420px;width:90%;color:#eee">
    <h2 style="margin:0 0 .8rem;font-size:1rem;color:#eee">Map hernoemen</h2>
    <p style="margin:0 0 .5rem;font-size:.78rem;color:#666" id="hernoem-huidig"></p>
    <input type="text" id="hernoem-naam" placeholder="Nieuwe naam"
           oninput="valideerHernoem()" style="width:100%;padding:.4rem .6rem;background:#222;border:1px solid #444;color:#eee;border-radius:4px;font-size:.88rem;margin-bottom:.3rem">
    <div id="hernoem-hint" style="font-size:.72rem;color:#f80;margin-bottom:.8rem;min-height:1.2rem"></div>
    <div style="display:flex;gap:.6rem;justify-content:flex-end">
      <button type="button" onclick="sluitHernoemModal()" style="background:#383838;color:#ccc;padding:.45rem 1rem;border:none;border-radius:4px;cursor:pointer">Annuleren</button>
      <button type="button" id="hernoem-ok" onclick="bevestigHernoem()" style="background:#2a5a8a;color:#eee;padding:.45rem 1rem;border:none;border-radius:4px;cursor:pointer;font-weight:bold" disabled>Hernoemen</button>
    </div>
  </div>
</div>

<!-- Info-knop rechtsonder -->
<button id="info-help-btn" onclick="toonInfoHelp()" title="Uitleg over de interface">?</button>

<!-- Info-help modal -->
<div id="info-help-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.8);z-index:400;align-items:center;justify-content:center;overflow:auto">
  <div style="background:#1e1e1e;border:1px solid #555;border-radius:8px;padding:1.5rem 2rem;max-width:560px;width:90%;color:#ccc;position:relative;margin:auto">
    <button onclick="document.getElementById('info-help-modal').style.display='none'" style="position:absolute;top:.6rem;right:.8rem;background:none;border:none;color:#888;font-size:1.2rem;cursor:pointer">×</button>
    <h2 style="margin:0 0 1rem;color:#eee;font-size:1rem">Declutter — gebruiksaanwijzing</h2>
    <div style="font-size:.82rem;line-height:1.7;display:flex;flex-direction:column;gap:.5rem">
      <div><strong style="color:#f90">Linkerpaneel (boom)</strong><br>
        Klik op een maand of album om de foto's te laden.<br>
        Dubbelklik op een mapnaam om hem te hernoemen, of gebruik het ✎-icoon.<br>
        Sleep een <em>map</em> naar een andere map of sectie-header om hem te verplaatsen.<br>
        Gebruik het <strong>+</strong>-icoon naast een sectie of map voor een nieuwe submap.</div>
      <div><strong style="color:#f90">Foto's selecteren &amp; verplaatsen</strong><br>
        Klik op een thumbnail om te selecteren. <strong>Shift+klik</strong> voor bereik. <strong>☑ Alles</strong> selecteert alles in de huidige weergave.<br>
        Sleep geselecteerde foto's naar een map in de boom — een tooltip toont het aantal.<br>
        <strong>⏳ Later</strong> verplaatst de selectie naar de map 'Later uitzoeken'.</div>
      <div><strong style="color:#f90">Nieuwe map</strong><br>
        Klik <strong>+</strong> naast een sectie of map. Als foto's geselecteerd zijn worden ze er direct naartoe verplaatst.<br>
        <em>Verwerkt (met datum)</em>: begin met een datum, bijv. <code>2026-04-02 Vakantie</code>.<br>
        <em>Zonder datum</em>: geen datum nodig, bijv. <code>Marktplaats/Babybedje</code>.</div>
      <div><strong style="color:#f90">Werkbalk (boven het overzicht)</strong><br>
        <strong>⚙ Presort</strong> — verdeelt foto's uit ruwe_data automatisch op maand.<br>
        <strong>🧹 Opruimen</strong> — verwijdert lege YYYY-MM mappen uit 'In behandeling', zodat je weet welke periodes al afgehandeld zijn.<br>
        <strong>🗑 Cache</strong> — wist de thumbnail-cache (nuttig na handmatige wijzigingen).<br>
        <strong>↺ Reset</strong> — maakt testdata opnieuw aan (wist alle bestanden!).</div>
      <div><strong style="color:#f90">Burst-groepen</strong><br>
        Foto's die snel achter elkaar genomen zijn, worden gegroepeerd. Klik op het label om de lichtbak te openen voor vergelijking.</div>
      <div><strong style="color:#f90">Kleurcodes album-invoer</strong><br>
        <span style="color:#4c4">●</span> Groen = geldig formaat &nbsp;
        <span style="color:#f80">●</span> Oranje = vrije tekst zonder datum &nbsp;
        <span style="color:#c44">●</span> Rood = ongeldige datum</div>
      <div><strong style="color:#f90">Sneltoetsen (lichtbak)</strong><br>
        ← → bladeren &nbsp;·&nbsp; H = hartje &nbsp;·&nbsp; Esc = sluiten</div>
    </div>
  </div>
</div>

<div id="info-panel">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:.6rem">
    <strong id="info-naam" style="font-size:.8rem;color:#eee;word-break:break-all;flex:1;margin-right:.5rem"></strong>
    <button onclick="sluitInfoPanel()" style="background:none;border:none;color:#888;font-size:1.1rem;cursor:pointer;line-height:1;flex-shrink:0">×</button>
  </div>
  <div id="info-inhoud"></div>
  <iframe id="info-kaart" sandbox="allow-scripts allow-same-origin"></iframe>
</div>

<div id="reset-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.75);z-index:200;align-items:center;justify-content:center">
  <div style="background:#1e1e1e;border:1px solid #555;border-radius:8px;padding:1.5rem 2rem;max-width:440px;width:90%;color:#eee">
    <div id="reset-keuze">
      <h2 style="margin:0 0 .8rem;font-size:1.1rem;color:#f66">Reset test — weet je het zeker?</h2>
      <p style="margin:0 0 .6rem;font-size:.9rem;color:#ccc">Dit gaat het volgende doen:</p>
      <ul style="margin:.3rem 0 .8rem;padding-left:1.4rem;font-size:.85rem;color:#aaa;line-height:1.7">
        <li>De volledige map <code style="color:#f90">{BASE_DIR}</code> wordt gewist</li>
        <li>Gekozen aantal foto's worden gedownload van picsum.photos</li>
        <li>Foto's worden ingedeeld op datum via presort</li>
        <li>De pagina wordt herladen</li>
      </ul>
      <p style="margin:0 0 .8rem;font-size:.8rem;color:#888">Bestanden buiten <code style="color:#f90">{BASE_DIR}</code> worden niet aangeraakt.</p>
      <label style="display:flex;align-items:center;gap:.8rem;font-size:.9rem;margin-bottom:1rem">
        <span style="white-space:nowrap;color:#ccc">Aantal foto's:</span>
        <input type="range" id="reset-aantal" min="20" max="400" step="10" value="40"
               oninput="document.getElementById('reset-aantal-label').textContent=this.value"
               style="flex:1;accent-color:#4a8abf;cursor:pointer">
        <span id="reset-aantal-label" style="width:2.5rem;text-align:right;color:#f90;font-weight:bold">40</span>
      </label>
      <div style="display:flex;gap:.7rem;justify-content:flex-end">
        <button type="button" onclick="document.getElementById('reset-modal').style.display='none'" style="background:#444;color:#eee;padding:.5rem 1.1rem;border:none;border-radius:4px;cursor:pointer;font-size:.9rem">Annuleren</button>
        <button type="button" onclick="resetTestBevestigd()" style="background:#c33;color:#fff;padding:.5rem 1.1rem;border:none;border-radius:4px;cursor:pointer;font-size:.9rem;font-weight:bold">Ja, reset</button>
      </div>
    </div>
    <div id="reset-voortgang" style="display:none">
      <h2 style="margin:0 0 1rem;font-size:1rem;color:#ccc">Reset bezig…</h2>
      <div id="reset-fase-label" style="font-size:.8rem;color:#888;margin-bottom:.4rem">Starten…</div>
      <div style="background:#111;border-radius:4px;height:12px;overflow:hidden;margin-bottom:.5rem">
        <div id="reset-balk" style="height:100%;width:0%;background:#4a8abf;transition:width .2s"></div>
      </div>
      <div id="reset-bericht" style="font-size:.78rem;color:#666;white-space:nowrap;overflow:hidden;text-overflow:ellipsis"></div>
    </div>
  </div>
</div>

<div id="lb">
  <div id="lb-inner">
    <div id="lb-header">
      <span id="lb-teller"></span>
      <span style="font-size:.75rem;color:#666">H = hartje &nbsp;·&nbsp; ← → = bladeren &nbsp;·&nbsp; Esc = sluiten</span>
    </div>
    <div id="lb-fotos"></div>
    <div id="lb-nav">
      <button type="button" class="lb-pijl" id="lb-prev" onclick="lbStap(-1)">&#8592;</button>
      <button type="button" class="lb-pijl" id="lb-next" onclick="lbStap(1)">&#8594;</button>
    </div>
    <div id="lb-acties">
      <span id="lb-info"></span>
      <button type="button" class="lb-btn-over" onclick="sluitLightbox()">Sla over</button>
      <button type="button" class="lb-btn-bevestig" id="lb-bevestig" onclick="bevestigLightbox()">Bevestig keuze</button>
    </div>
  </div>
</div>

<script>
// Persistente selectie over mappen heen: pad -> {{naam, thumb}}
const selectie = new Map();
let huidigeMaand = null;
let huidigePad   = null;
let _archiefDir   = '';
let _datumloosDir = '';
let _prullenbakDir = '';
let _herstelde_anker = localStorage.getItem('fc_anker');

// ── Tree state ─────────────────────────────────────────────────────────────
let _treeData = null;  // cache van laatste /tree respons

function toggleTS(sectie) {{
  const hdr  = document.getElementById('tsh-' + sectie);
  const body = document.getElementById('tsb-' + sectie);
  if (!hdr || !body) return;
  const open = body.classList.toggle('open');
  hdr.classList.toggle('open', open);
}}

function _renderMaandBoom(jm) {{
  const container = document.getElementById('tree-behandeling');
  if (!container) return;
  // Totaal over alle maanden → header bijwerken
  const totaal = jm ? Object.values(jm).flat().reduce((s, mo) => s + (typeof mo === 'object' ? (mo.aantal||0) : 0), 0) : 0;
  const hdr = document.getElementById('tsh-behandeling');
  if (hdr) {{
    let lbl = hdr.querySelector('.ts-behandeling-lbl');
    if (!lbl) {{
      lbl = document.createElement('span');
      lbl.className = 'ts-behandeling-lbl';
      hdr.appendChild(lbl);
    }}
    lbl.innerHTML = totaal ? ` <span class="boom-count">(${{totaal}})</span>` : '';
  }}
  if (!jm || !Object.keys(jm).length) {{
    container.innerHTML = '<span style="color:#444;font-size:.7rem;padding:4px 10px;display:block">Geen mappen.</span>';
    return;
  }}
  const MAANDEN = ['jan','feb','mrt','apr','mei','jun','jul','aug','sep','okt','nov','dec'];
  let html = '';
  for (const jaar of Object.keys(jm).sort().reverse()) {{
    const jaarAantal = jm[jaar].reduce((s, mo) => s + (typeof mo === 'object' ? (mo.aantal||0) : 0), 0);
    const jaarCnt = jaarAantal ? ` <span class="boom-count">(${{jaarAantal}})</span>` : '';
    html += `<div class="jaar-rij" onclick="toggleJaar('${{jaar}}')">
      <span class="jaar-pijl" id="jp-${{jaar}}">&#9658;</span>
      <span class="jaar-label">${{jaar}}${{jaarCnt}}</span>
    </div>
    <div class="jaar-sub" id="js-${{jaar}}">`;
    for (const mo of jm[jaar]) {{
      const maand = typeof mo === 'string' ? mo : mo.naam;
      const aantal = (typeof mo === 'object' && mo.aantal) ? mo.aantal : 0;
      const mn = parseInt(maand.slice(5)) - 1;
      const lbl = MAANDEN[mn] || maand.slice(5);
      const maandPad = ('{UITZOEKEN_DIR}/' + maand).replace(/\\\\/g,'/');
      const cntHtml = aantal ? ` <span class="boom-count">(${{aantal}})</span>` : '';
      html += `<div class="maand-rij" id="mr-${{maand}}" data-maand="${{maand}}"
        onclick="laadMaand('${{maand}}',this)"
        ondragover="_boomDragOver(event,this)"
        ondragleave="_boomDragLeave(this)"
        ondrop="_boomDrop(event,'${{maandPad}}')">
        <span>${{lbl}}${{cntHtml}}</span>
      </div>`;
    }}
    html += '</div>';
  }}
  container.innerHTML = html;
  // Open het meest recente jaar automatisch
  const jaren = Object.keys(jm).sort();
  if (jaren.length) _openJaar(jaren[jaren.length - 1]);
}}

function toggleJaar(jaar) {{
  const sub  = document.getElementById('js-' + jaar);
  const pijl = document.getElementById('jp-' + jaar);
  if (!sub) return;
  const open = sub.style.display !== 'block';
  sub.style.display = open ? 'block' : 'none';
  if (pijl) pijl.innerHTML = open ? '&#9660;' : '&#9658;';
}}

function _openJaar(jaar) {{
  const sub  = document.getElementById('js-' + jaar);
  const pijl = document.getElementById('jp-' + jaar);
  if (sub) {{ sub.style.display = 'block'; }}
  if (pijl) pijl.innerHTML = '&#9660;';
}}

function _renderAlbumBoom(nodes, containerId) {{
  const el = document.getElementById(containerId);
  if (!el) return;
  if (!nodes || !nodes.length) {{
    el.innerHTML = '<span style="color:#444;font-size:.7rem;padding:4px 10px;display:block">Leeg.</span>';
    return;
  }}
  el.innerHTML = _renderBoomNodes(nodes, 0);
  el.addEventListener('click', _boomKlik);
}}

function _renderPrullenbakBoom(nodes, containerId) {{
  const el = document.getElementById(containerId);
  if (!el) return;
  if (!nodes || !nodes.length) {{
    el.innerHTML = '<span style="color:#444;font-size:.7rem;padding:4px 10px;display:block">Leeg.</span>';
    return;
  }}
  el.innerHTML = _renderPbNodes(nodes, 0);
  el.addEventListener('click', e => {{
    const naam = e.target.closest('.boom-naam');
    if (naam) bladernInMap(naam.closest('.boom-node').dataset.pad);
    const pijl = e.target.closest('.boom-pijl');
    if (pijl) {{
      const node = pijl.closest('.boom-node');
      const sub  = node && node.querySelector(':scope > .boom-sub');
      if (sub) {{
        const open = sub.style.display === 'block';
        sub.style.display = open ? 'none' : 'block';
        pijl.innerHTML = open ? '&#9658;' : '&#9660;';
      }}
    }}
  }});
}}

function _renderPbNodes(nodes, diepte) {{
  return nodes.map(n => {{
    const esc     = n.pad.replace(/"/g,'&quot;');
    const naamEsc = n.naam.replace(/</g,'&lt;').replace(/>/g,'&gt;');
    const heeftSub = n.submappen && n.submappen.length > 0;
    const sub = heeftSub
      ? `<div class="boom-sub">${{_renderPbNodes(n.submappen, diepte+1)}}</div>` : '';
    const cntHtml = n.aantal ? ` <span class="boom-count">(${{n.aantal}})</span>` : '';
    return `<div class="boom-node" data-pad="${{esc}}">
      <div class="boom-rij pb-readonly" style="padding-left:${{8+diepte*12}}px">
        <span class="boom-pijl">${{heeftSub ? '&#9658;' : '&nbsp;'}}</span>
        <span class="boom-naam">${{naamEsc}}${{cntHtml}}</span>
      </div>${{sub}}</div>`;
  }}).join('');
}}

function _renderBoomNodes(nodes, diepte) {{
  return nodes.map(n => {{
    const esc = n.pad.replace(/"/g,'&quot;');
    const naamEsc = n.naam.replace(/</g,'&lt;').replace(/>/g,'&gt;');
    const heeftSub = n.submappen && n.submappen.length > 0;
    const sub = heeftSub
      ? `<div class="boom-sub">${{_renderBoomNodes(n.submappen, diepte+1)}}</div>` : '';
    const cntHtml = n.aantal ? ` <span class="boom-count">(${{n.aantal}})</span>` : '';
    return `<div class="boom-node" data-pad="${{esc}}">
      <div class="boom-rij" draggable="true" style="padding-left:${{8+diepte*12}}px"
           ondragstart="mapDragStart(event,'${{esc}}')"
           ondragover="_boomDragOver(event,this)"
           ondragleave="_boomDragLeave(this)"
           ondrop="_boomDrop(event,'${{esc}}')">
        <span class="boom-pijl">${{heeftSub ? '&#9658;' : '&nbsp;'}}</span>
        <span class="boom-naam" ondblclick="toonHernoemModal('${{esc}}')">${{naamEsc}}${{cntHtml}}</span>
        <span class="boom-rij-acties">
          <button class="boom-rij-btn" title="Nieuwe submap" onclick="event.stopPropagation();toonNieuweMapModal(null,'${{esc}}')">+</button>
          <button class="boom-rij-btn" title="Hernoemen" onclick="event.stopPropagation();toonHernoemModal('${{esc}}')">✎</button>
        </span>
      </div>${{sub}}</div>`;
  }}).join('');
}}

function _boomKlik(e) {{
  const pijl = e.target.closest('.boom-pijl');
  const naam = e.target.closest('.boom-naam');
  if (pijl) {{
    const node = pijl.closest('.boom-node');
    const sub  = node && node.querySelector(':scope > .boom-sub');
    if (sub) {{
      const open = sub.style.display === 'block';
      sub.style.display = open ? 'none' : 'block';
      pijl.innerHTML    = open ? '&#9658;' : '&#9660;';
    }}
  }} else if (naam) {{
    bladernInMap(naam.closest('.boom-node').dataset.pad);
  }}
}}

async function laadTree(forceer) {{
  if (_treeData && !forceer) return;
  try {{
    const resp = await fetch('/tree');
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const d = await resp.json();
    _treeData = d;
    _archiefDir    = d.archief    || '';
    _datumloosDir  = d.datumloos  || '';
    _prullenbakDir = d.prullenbak || '';
    _renderMaandBoom(d.jm || {{}});
    _renderAlbumBoom(d.archief_boom    || [], 'tree-verwerkt');
    _renderAlbumBoom(d.datumloos_boom  || [], 'tree-datumloos');
    _renderPrullenbakBoom(d.prullenbak_boom || [], 'tree-prullenbak');
  }} catch(err) {{
    console.error('Tree laden mislukt:', err);
    document.getElementById('tree-verwerkt').innerHTML =
      `<span style="color:#c66;font-size:.7rem;padding:4px 10px;display:block">Fout: ${{err.message}}</span>`;
  }}
}}

function _verversTree() {{
  _treeData = null;
  laadTree(true);
}}


// ── Drag foto's en mappen ─────────────────────────────────────────────────────
const _dragTooltip = () => document.getElementById('drag-tooltip');
let _dragMapPad = null;

function fotoDragStart(e, el) {{
  if (!el.classList.contains('geselecteerd')) toggle(el);
  e.dataTransfer.effectAllowed = 'move';
  e.dataTransfer.setData('fc-foto', '1');
  const n = selectie.size || 1;
  const tt = _dragTooltip();
  if (tt) {{
    tt.textContent = n + ' foto' + (n !== 1 ? "'s" : '');
    tt.style.display = 'block';
  }}
}}

function mapDragStart(e, pad) {{
  e.stopPropagation();
  _dragMapPad = pad;
  e.dataTransfer.effectAllowed = 'move';
  e.dataTransfer.setData('fc-map', pad);
  const tt = _dragTooltip();
  if (tt) {{
    tt.textContent = '\U0001F4C1 ' + pad.split('/').pop();
    tt.style.display = 'block';
  }}
}}

document.addEventListener('dragend', () => {{
  _dragMapPad = null;
  const tt = _dragTooltip();
  if (tt) tt.style.display = 'none';
  document.querySelectorAll('.boom-rij.drag-over,.maand-rij.drag-over,.ts-hdr.drag-over').forEach(r => r.classList.remove('drag-over'));
}});

document.addEventListener('dragover', e => {{
  const tt = _dragTooltip();
  if (tt && tt.style.display !== 'none') {{
    tt.style.left = (e.clientX + 16) + 'px';
    tt.style.top  = (e.clientY - 10) + 'px';
  }}
}});

function _boomDragOver(e, rij) {{
  if (!e.dataTransfer.types.includes('fc-foto') && !e.dataTransfer.types.includes('fc-map')) return;
  e.preventDefault();
  e.dataTransfer.dropEffect = 'move';
  rij.classList.add('drag-over');
}}

function _sectionDragOver(e, rij) {{
  if (!e.dataTransfer.types.includes('fc-map')) return;
  e.preventDefault();
  e.dataTransfer.dropEffect = 'move';
  rij.classList.add('drag-over');
}}

function _boomDragLeave(rij) {{
  rij.classList.remove('drag-over');
}}

async function _boomDrop(e, pad) {{
  e.preventDefault();
  document.querySelectorAll('.boom-rij.drag-over,.maand-rij.drag-over,.ts-hdr.drag-over').forEach(r => r.classList.remove('drag-over'));
  if (e.dataTransfer.types.includes('fc-map')) {{
    const vanPad = _dragMapPad; _dragMapPad = null;
    if (!vanPad || vanPad === pad || pad.startsWith(vanPad + '/')) return;
    const r = await fetch('/verplaatsmap', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{van: vanPad, naar: pad}})
    }});
    const d = await r.json();
    document.getElementById('status').textContent = d.bericht || d.fout || '';
    if (d.fout) alert('Fout: ' + d.fout);
    else {{ _verversTree(); if (huidigePad && huidigePad.startsWith(vanPad)) bladernInMap(d.nieuw_pad); }}
    return;
  }}
  if (!e.dataTransfer.types.includes('fc-foto')) return;
  const paden = [...selectie.keys()];
  if (!paden.length) return;
  const r = await fetch('/verplaats', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{paden, pad}})
  }});
  const d = await r.json();
  document.getElementById('status').textContent = d.bericht;
  if (d.jm) verversNav(d.jm);
  selectie.clear();
  updateSidebar();
  _verversTree();
  _herlaadHuidig();
}}

async function _sectionDrop(e, sectie) {{
  e.preventDefault();
  document.querySelectorAll('.ts-hdr.drag-over').forEach(r => r.classList.remove('drag-over'));
  if (!e.dataTransfer.types.includes('fc-map')) return;
  const vanPad = _dragMapPad; _dragMapPad = null;
  if (!vanPad) return;
  const naarRoot = sectie === 'datumloos' ? _datumloosDir : _archiefDir;
  if (!naarRoot) return;
  const r = await fetch('/verplaatsmap', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{van: vanPad, naar: naarRoot}})
  }});
  const d = await r.json();
  document.getElementById('status').textContent = d.bericht || d.fout || '';
  if (d.fout) alert('Fout: ' + d.fout);
  else _verversTree();
}}

const MAANDEN_NL = ['januari','februari','maart','april','mei','juni','juli','augustus','september','oktober','november','december'];
function formatDag(ddmmyyyy) {{
  const [d,m,y] = ddmmyyyy.split('-');
  return `${{parseInt(d)}} ${{MAANDEN_NL[parseInt(m)-1]}} ${{y}}`;
}}

function verversNav(jm) {{
  // Ververst alleen de in-behandeling-sectie van de boom
  if (!jm) return;
  _renderMaandBoom(jm);
}}

async function bladernInMap(pad) {{
  huidigePad   = pad;
  huidigeMaand = null;
  document.querySelectorAll('.maand-rij').forEach(r => r.classList.remove('actief'));
  document.querySelectorAll('.boom-naam').forEach(n => n.classList.remove('actief'));
  const node = [...document.querySelectorAll('.boom-node')].find(n => n.dataset.pad === pad);
  if (node) node.querySelector('.boom-naam').classList.add('actief');
  const inPb = _prullenbakDir && pad.startsWith(_prullenbakDir);
  document.getElementById('fotos-wrap').classList.toggle('prullenbak-modus', !!inPb);
  document.getElementById('status').textContent = 'Huidige map: ' + pad;
  updateSidebar();
  let data;
  try {{
    const resp = await fetch('/fotos?pad=' + encodeURIComponent(pad));
    data = await resp.json();
  }} catch(e) {{
    document.getElementById('fotos').innerHTML = `<p style="color:#c66">Fout: ${{e.message}}</p>`;
    return;
  }}
  renderFotos(data.groepen || []);
}}

function _herlaadHuidig() {{
  if (huidigeMaand) laadMaand(huidigeMaand, null);
  else if (huidigePad) bladernInMap(huidigePad);
}}

// ── Activiteitslog (full-width) ───────────────────────────────────────────────
const recenteActiviteit = [];
function toggleActiviteit() {{
  const body  = document.getElementById('activiteit-body');
  const pijl  = document.getElementById('activiteit-pijl');
  const open = body.style.display === 'block';
  body.style.display = open ? 'none' : 'block';
  if (pijl) pijl.innerHTML = open ? '&#9658;' : '&#9660;';
}}

function voegActiviteitToe(bestanden) {{
  if (!bestanden || !bestanden.length) return;
  const nu = new Date().toLocaleTimeString('nl-NL', {{hour:'2-digit',minute:'2-digit'}});
  for (const b of bestanden) recenteActiviteit.unshift({{...b, ts: nu}});
  if (recenteActiviteit.length > 100) recenteActiviteit.length = 100;
  const el = document.getElementById('activiteit-lijst');
  if (!el) return;
  el.innerHTML = recenteActiviteit.map(b => {{
    const map = b.naar ? b.naar.split('/').slice(0, -1).join('/') : '';
    return `<div class="act-rij">
      <span class="act-ts">${{b.ts}}</span>
      <span class="act-naam"> ${{b.naam}}</span>
      <span class="act-naar"> → ${{map || b.naar || ''}}</span>
    </div>`;
  }}).join('');
  const cnt = document.getElementById('activiteit-count');
  if (cnt) cnt.textContent = '(' + recenteActiviteit.length + ')';
  // Open log
  const body = document.getElementById('activiteit-body');
  if (body && body.style.display !== 'block') toggleActiviteit();
}}

// ── Hernoemen ─────────────────────────────────────────────────────────────────
let _hernoemPad = null;

function toonHernoemModal(pad) {{
  _hernoemPad = pad;
  const naam = pad.split('/').pop();
  document.getElementById('hernoem-huidig').textContent = 'Huidige naam: ' + naam;
  const inp = document.getElementById('hernoem-naam');
  inp.value = naam;
  document.getElementById('hernoem-hint').textContent = '';
  document.getElementById('hernoem-ok').disabled = true;
  document.getElementById('hernoem-modal').style.display = 'flex';
  inp.focus(); inp.select();
}}

function sluitHernoemModal() {{
  document.getElementById('hernoem-modal').style.display = 'none';
  _hernoemPad = null;
}}

function valideerHernoem() {{
  const val = document.getElementById('hernoem-naam').value.trim();
  const huidig = _hernoemPad ? _hernoemPad.split('/').pop() : '';
  const hint = document.getElementById('hernoem-hint');
  const ok   = document.getElementById('hernoem-ok');
  if (!val || val === huidig) {{ hint.textContent = ''; ok.disabled = true; return; }}
  if (/[/\\\\:*?"<>|]/.test(val)) {{
    hint.textContent = 'Naam mag geen / \\ : * ? " < > | bevatten.'; ok.disabled = true; return;
  }}
  hint.textContent = '';
  ok.disabled = false;
}}

async function bevestigHernoem() {{
  const nieuw = document.getElementById('hernoem-naam').value.trim();
  if (!nieuw || !_hernoemPad) return;
  const r = await fetch('/hernoem', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{pad: _hernoemPad, naam: nieuw}})
  }});
  const d = await r.json();
  sluitHernoemModal();
  if (d.fout) {{ alert('Fout: ' + d.fout); return; }}
  document.getElementById('status').textContent = 'Hernoemd naar ' + nieuw;
  _verversTree();
  if (huidigePad && huidigePad.startsWith(_hernoemPad)) bladernInMap(d.nieuw_pad || huidigePad);
  else _herlaadHuidig();
}}

// ── Nieuwe map ────────────────────────────────────────────────────────────────
let _nieuweMapSectie = null;   // 'verwerkt' | 'datumloos' | null
let _nieuweMapOuder  = null;   // pad van oudermap of null

function toonNieuweMapModal(sectie, ouder) {{
  _nieuweMapSectie = sectie;
  _nieuweMapOuder  = ouder || null;
  const inp  = document.getElementById('nieuwmap-naam');
  const uitl = document.getElementById('nieuwmap-uitleg');
  let placeholder = '2026-04-02 Vakantie';
  if (sectie === 'datumloos' || (_nieuweMapOuder && _nieuweMapOuder.startsWith(_datumloosDir))) {{
    placeholder = 'Marktplaats/Babybedje';
    uitl.textContent = 'Vrije mapnaam. Gebruik / voor submappen.';
  }} else if (ouder) {{
    placeholder = 'Subalbum naam';
    uitl.textContent = 'Submap van: ' + ouder;
  }} else {{
    uitl.textContent = 'Begin bij voorkeur met een datum: 2026-04-02 Omschrijving';
  }}
  const aantalSel = selectie.size;
  document.getElementById('nieuwmap-titel').textContent =
    aantalSel > 0 ? `Nieuwe map + ${{aantalSel}} foto'${{aantalSel!==1?'s':''}} verplaatsen` : 'Nieuwe map aanmaken';
  inp.placeholder = placeholder;

  // Datumrange voorvullen vanuit geselecteerde foto's (alleen voor verwerkt-sectie)
  const isDatumloosCtx = sectie === 'datumloos' ||
    (_nieuweMapOuder && _nieuweMapOuder.startsWith(_datumloosDir));
  let voorvul = '';
  if (!isDatumloosCtx && aantalSel > 0) {{
    const datums = [...document.querySelectorAll('.foto.geselecteerd[data-datum]')]
      .map(el => el.dataset.datum).filter(d => d && d.length === 10).sort();
    if (datums.length) {{
      const eerste = datums[0];
      const laatste = datums[datums.length - 1];
      voorvul = eerste === laatste ? eerste + ' ' : eerste + ' - ' + laatste + ' ';
    }}
  }}
  inp.value = voorvul;
  document.getElementById('nieuwmap-hint').textContent = '';
  document.getElementById('nieuwmap-ok').disabled = true;
  document.getElementById('nieuwmap-modal').style.display = 'flex';
  // Cursor aan het einde plaatsen
  inp.focus();
  inp.setSelectionRange(inp.value.length, inp.value.length);
  if (voorvul) valideerNieuweMap();
}}

function sluitNieuweMapModal() {{
  document.getElementById('nieuwmap-modal').style.display = 'none';
  _nieuweMapSectie = null; _nieuweMapOuder = null;
}}

function valideerNieuweMap() {{
  const val  = document.getElementById('nieuwmap-naam').value.trim();
  const hint = document.getElementById('nieuwmap-hint');
  const staat = document.getElementById('nieuwmap-staat');
  const ok   = document.getElementById('nieuwmap-ok');
  if (!val) {{ hint.textContent = ''; staat.className = 'album-staat'; ok.disabled = true; return; }}
  if (/[\\\\:*?"<>|]/.test(val)) {{
    hint.textContent = 'Naam mag geen \\ : * ? " < > | bevatten.'; staat.className = 'album-staat rood'; ok.disabled = true; return;
  }}
  const isDatumloos = _nieuweMapSectie === 'datumloos' ||
    (_nieuweMapOuder && _nieuweMapOuder.startsWith(_datumloosDir));
  if (!isDatumloos && !/^\d{{4}}-\d{{2}}-\d{{2}}/.test(val) && !_nieuweMapOuder) {{
    hint.textContent = 'Tip: begin met een datum voor betere sortering.';
    staat.className = 'album-staat oranje';
  }} else {{
    hint.textContent = '';
    staat.className = 'album-staat groen';
  }}
  ok.disabled = false;
}}

async function bevestigNieuweMap() {{
  const naam = document.getElementById('nieuwmap-naam').value.trim();
  if (!naam) return;
  const paden = [...selectie.keys()];
  const r = await fetch('/maakmap', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{
      naam,
      sectie: _nieuweMapSectie,
      ouder: _nieuweMapOuder,
      paden: paden.length ? paden : []
    }})
  }});
  const d = await r.json();
  sluitNieuweMapModal();
  if (d.fout) {{ alert('Fout: ' + d.fout); return; }}
  document.getElementById('status').textContent = d.bericht;
  if (d.jm) verversNav(d.jm);
  if (paden.length) {{ selectie.clear(); updateSidebar(); _herlaadHuidig(); }}
  _verversTree();
}}

// ── Info-help ──────────────────────────────────────────────────────────────────
function toonInfoHelp() {{
  document.getElementById('info-help-modal').style.display = 'flex';
}}

async function laadMaand(maand, knop) {{
  huidigeMaand = maand;
  huidigePad   = null;
  localStorage.setItem('fc_maand', maand);
  document.getElementById('status').textContent = '';
  document.querySelectorAll('.boom-naam').forEach(n => n.classList.remove('actief'));
  document.querySelectorAll('.maand-rij').forEach(r => r.classList.remove('actief'));
  // Activeer juiste rij in behandeling-boom
  const rij = document.getElementById('mr-' + maand) ||
              document.querySelector(`.maand-rij[data-maand="${{maand}}"]`);
  if (rij) {{
    rij.classList.add('actief');
    // Zorg dat het juiste jaar open staat
    const jaar = maand.slice(0, 4);
    _openJaar(jaar);
    // Zorg dat sectie open is
    const tsb = document.getElementById('tsb-behandeling');
    if (tsb && !tsb.classList.contains('open')) toggleTS('behandeling');
  }}
  // Haal alleen metadata op — snel
  let data;
  try {{
    const resp = await fetch('/fotos?maand=' + maand);
    if (!resp.ok) throw new Error(`Server fout: ${{resp.status}}`);
    data = await resp.json();
  }} catch(e) {{
    document.getElementById('fotos').innerHTML = `<p style="color:#c66">Fout bij laden: ${{e.message}}</p>`;
    return;
  }}
  renderFotos(data.groepen || []);
}}

function renderFotos(groepen) {{
  _laatsteKlik = null;
  const div = document.getElementById('fotos');
  if (!groepen.length) {{ div.innerHTML = "<p style='color:#666'>Geen foto's.</p>"; return; }}
  let html = '';
  huidigeGroepen = groepen;
  let huidigedag = null;
  groepen.forEach((g, gi) => {{
    const fotos = g.fotos;
    const type  = g.type;
    const dagDatum = fotos.length ? fotos[0].datum : null;
    if (dagDatum && dagDatum !== huidigedag) {{
      huidigedag = dagDatum;
      html += `<div class="dag-header">${{formatDag(dagDatum)}}</div>`;
    }}
    if (type === 'tight') {{
      const span = g.span_sec < 60 ? `${{g.span_sec}} seconden` : `${{Math.round(g.span_sec/60)}} minuten`;
      const label = `${{fotos.length}} foto's geschoten binnen ${{span}} — klik om te reviewen`;
      html += `<div class="burst tight"><div class="burst-label" onclick="openLightbox(${{gi}}, event)">${{label}}</div><div class="grid">`;
    }} else if (type === 'moment') {{
      const span = g.span_sec < 60 ? `${{g.span_sec}} seconden` : `${{Math.round(g.span_sec/60)}} minuten`;
      const label = `${{fotos.length}} vergelijkbare foto's geschoten binnen ${{span}} — klik om te reviewen`;
      html += `<div class="burst moment"><div class="burst-label" onclick="openLightbox(${{gi}}, event)">${{label}}</div><div class="grid">`;
    }} else {{
      html += '<div class="grid" style="margin-bottom:8px">';
    }}
    fotos.forEach(f => {{
      const sel = selectie.has(f.rel) ? ' geselecteerd' : '';
      const thumbUrl = '/thumb?pad=' + encodeURIComponent(f.rel);
      const ext = f.naam.includes('.') ? f.naam.split('.').pop().toLowerCase() : '';
      html += `<div class="foto${{sel}}" draggable="true" data-pad="${{f.rel}}" data-naam="${{f.naam}}" data-thumb="${{thumbUrl}}" data-datum="${{f.datum_iso||''}}" data-datum-nl="${{f.datum||''}}" data-grootte="${{f.grootte||0}}" onclick="toggle(this,event)" ondragstart="fotoDragStart(event,this)">
        ${{ext ? `<span class="ext-badge">${{ext}}</span>` : ''}}
        <button class="foto-del" title="Naar prullenbak" onclick="verwijderFoto(event,this)">×</button>
        <button class="foto-zoom" title="Vergroten" onclick="openFotoZoom(event,this)">⤢</button>
        <button class="foto-info-btn" title="Info" onclick="toonFotoInfo(event,this)">ℹ</button>
        <img src="${{thumbUrl}}" alt="${{f.naam}}" draggable="false">
        <div class="naam">${{f.naam}}</div>
        <div class="datum">${{f.datum}}${{f.tijdstip ? `<span class="foto-tijdstip">${{f.tijdstip}}</span>` : ''}}</div></div>`;
    }});
    html += '</div>';
    if (type !== 'los') html += '</div>';
  }});
  div.innerHTML = html;

  // Bladwijzer: herstel na page load
  if (_herstelde_anker) {{
    const anker = _herstelde_anker;
    _herstelde_anker = null;
    setTimeout(() => {{
      const el = [...div.querySelectorAll('.foto')].find(f => f.dataset.pad === anker);
      if (el) {{
        plaatsBladwijzer(el);
        el.scrollIntoView({{block: 'center', behavior: 'smooth'}});
      }}
    }}, 150);
  }}

  // Progress op basis van daadwerkelijk geladen thumbnails
  const imgs = [...div.querySelectorAll('img')];
  const totaal = imgs.length;
  if (!totaal) return;
  const wrap = document.getElementById('progress-wrap');
  const bar = document.getElementById('progress');
  let geladen = 0;
  wrap.style.display = 'block';
  bar.style.width = '0%';
  function tick() {{
    geladen++;
    bar.style.width = Math.round(geladen / totaal * 100) + '%';
    if (geladen >= totaal) setTimeout(() => {{ wrap.style.display = 'none'; bar.style.width = '0%'; }}, 300);
  }}
  imgs.forEach(img => {{
    if (img.complete) {{ tick(); }} else {{ img.addEventListener('load', tick); img.addEventListener('error', tick); }}
  }});
}}

let _laatsteKlik = null;

function toggle(el, e) {{
  const pad = el.dataset.pad;
  const allesFotos = [...document.querySelectorAll('.foto')];

  if (e && e.shiftKey && _laatsteKlik && _laatsteKlik !== el) {{
    // Selecteer bereik tussen _laatsteKlik en el
    const van = allesFotos.indexOf(_laatsteKlik);
    const tot = allesFotos.indexOf(el);
    const [begin, eind] = van < tot ? [van, tot] : [tot, van];
    const aanzetten = !el.classList.contains('geselecteerd');
    allesFotos.slice(begin, eind + 1).forEach(f => {{
      if (aanzetten) {{
        f.classList.add('geselecteerd');
        selectie.set(f.dataset.pad, {{ naam: f.dataset.naam, thumb: f.dataset.thumb }});
      }} else {{
        f.classList.remove('geselecteerd');
        selectie.delete(f.dataset.pad);
      }}
    }});
  }} else {{
    if (el.classList.toggle('geselecteerd')) {{
      selectie.set(pad, {{ naam: el.dataset.naam, thumb: el.dataset.thumb }});
    }} else {{
      selectie.delete(pad);
    }}
  }}

  _laatsteKlik = el;
  updateSidebar();
}}

function deselecteer(pad) {{
  selectie.delete(pad);
  const el = document.querySelector(`.foto[data-pad="${{pad}}"]`);
  if (el) el.classList.remove('geselecteerd');
  updateSidebar();
}}

function updateSidebar() {{
  const sb  = document.getElementById('sidebar');
  const btn = document.getElementById('btn-verwijder');
  const n   = selectie.size;
  const inPb = huidigePad && _prullenbakDir && huidigePad.startsWith(_prullenbakDir);
  if (btn) {{
    btn.disabled = n === 0 || !!inPb;
    btn.textContent = n > 0 ? `\U0001F5D1 Verwijder (${{n}})` : '\U0001F5D1 Verwijder';
  }}
  if (n === 0) {{
    sb.innerHTML = '<h3>Geselecteerd</h3><p class="sb-leeg">Nog niets geselecteerd.</p>';
    return;
  }}
  let html = `<h3>Geselecteerd (${{n}})</h3>`;
  for (const [pad, {{naam, thumb}}] of selectie) {{
    const escapedPad = pad.replace(/'/g, "\\'");
    html += `<div class="sb-item">
      <img class="sb-thumb" src="${{thumb}}" alt="">
      <span class="sb-naam" title="${{naam}}">${{naam}}</span>
      <button type="button" class="sb-x" onclick="deselecteer('${{escapedPad}}')">✕</button>
    </div>`;
  }}
  sb.innerHTML = html;
}}

async function verwijderSelectie() {{
  const paden = [...selectie.keys()];
  if (!paden.length) return;
  const n = paden.length;
  if (!confirm(`Weet je zeker dat je ${{n}} foto${{n !== 1 ? "'s" : ''}} naar de prullenbak wilt verplaatsen?`)) return;
  const r = await fetch('/prullenbak', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{paden, submap: 'losse_items'}})
  }});
  const d = await r.json();
  document.getElementById('status').textContent = `${{d.verplaatst || 0}} foto\u2019s naar prullenbak verplaatst.`;
  if (d.jm) verversNav(d.jm);
  selectie.clear();
  updateSidebar();
  _verversTree();
  _herlaadHuidig();
}}

function allesToggle() {{
  const alle = [...document.querySelectorAll('.foto')];
  const aanZetten = alle.some(e => !e.classList.contains('geselecteerd'));
  alle.forEach(e => {{
    e.classList.toggle('geselecteerd', aanZetten);
    const pad = e.dataset.pad;
    if (aanZetten) selectie.set(pad, {{ naam: e.dataset.naam, thumb: e.dataset.thumb }});
    else selectie.delete(pad);
  }});
  updateSidebar();
}}

async function bewaarLater() {{
  if (!selectie.size) {{ alert("Selecteer eerst foto's"); return; }}
  const paden = [...selectie.keys()];
  const r = await fetch('/verplaats', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{paden, album:'Later uitzoeken'}})}});
  const d = await r.json();
  document.getElementById('status').textContent = d.bericht;
  if (d.jm) verversNav(d.jm);
  selectie.clear();
  updateSidebar();
  _verversTree();
  _herlaadHuidig();
}}

let _infoPad = null;

function _infoRij(lbl, val) {{
  return `<div class="info-rij"><span class="info-lbl">${{lbl}}</span><span class="info-val">${{val}}</span></div>`;
}}

async function toonFotoInfo(e, btn) {{
  e.stopPropagation();
  const rel = btn.closest('.foto').dataset.pad;
  const panel = document.getElementById('info-panel');
  if (_infoPad === rel && panel.style.display !== 'none') {{
    sluitInfoPanel(); return;
  }}
  _infoPad = rel;
  // Positioneer naast de knop
  const rect = btn.getBoundingClientRect();
  const px = Math.min(rect.right + 8, window.innerWidth - 286);
  const py = Math.max(rect.top, 8);
  panel.style.left = px + 'px';
  panel.style.top  = py + 'px';
  panel.style.display = 'block';
  document.getElementById('info-naam').textContent = rel.split('/').pop();
  document.getElementById('info-inhoud').innerHTML = '<span style="color:#555">Laden…</span>';
  document.getElementById('info-kaart').style.display = 'none';
  try {{
    const d = await (await fetch('/info?pad=' + encodeURIComponent(rel))).json();
    let html = '';
    html += _infoRij('Map', d.map || '—');
    html += _infoRij('Datum', d.datum || '—');
    html += _infoRij('Grootte', fmtBytes(d.grootte));
    if (d.gps) {{
      const osmUrl = `https://www.openstreetmap.org/?mlat=${{d.gps.lat}}&mlon=${{d.gps.lng}}#map=15/${{d.gps.lat}}/${{d.gps.lng}}`;
      html += _infoRij('GPS', `<a href="${{osmUrl}}" target="_blank" style="color:#4a8abf">${{d.gps.lat}}, ${{d.gps.lng}}</a>`);
    }}
    if (d.locatie) html += _infoRij('Locatie', d.locatie);
    document.getElementById('info-inhoud').innerHTML = html || '<span style="color:#555">Geen extra info.</span>';
    if (d.gps) {{
      const kaart = document.getElementById('info-kaart');
      const b = 0.008;
      kaart.src = `https://www.openstreetmap.org/export/embed.html?bbox=${{d.gps.lng-b}},${{d.gps.lat-b}},${{d.gps.lng+b}},${{d.gps.lat+b}}&layer=mapnik&marker=${{d.gps.lat}},${{d.gps.lng}}`;
      kaart.style.display = 'block';
    }}
  }} catch(_) {{
    document.getElementById('info-inhoud').innerHTML = '<span style="color:#c66">Fout bij laden.</span>';
  }}
}}

function sluitInfoPanel() {{
  document.getElementById('info-panel').style.display = 'none';
  _infoPad = null;
}}

document.addEventListener('click', function(e) {{
  const panel = document.getElementById('info-panel');
  if (panel && panel.style.display !== 'none' &&
      !panel.contains(e.target) && !e.target.closest('.foto-info-btn')) {{
    sluitInfoPanel();
  }}
}});

async function verwijderFoto(e, btn) {{
  e.stopPropagation();
  const el = btn.closest('.foto');
  const rel = el.dataset.pad;
  el.style.opacity = '.3';
  el.style.pointerEvents = 'none';
  const r = await fetch('/prullenbak', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{paden: [rel], submap: 'losse_items'}})
  }});
  const d = await r.json();
  el.remove();
  selectie.delete(rel);
  updateSidebar();
  if (d.jm) verversNav(d.jm);
}}

function toonVoortgang(d) {{
  const modal = document.getElementById('reset-modal');
  if (modal.style.display !== 'none') {{
    // Reset bezig — toon in modal
    document.getElementById('reset-keuze').style.display = 'none';
    document.getElementById('reset-voortgang').style.display = '';
    const pct = d.totaal > 0 ? Math.round(d.stap / d.totaal * 100) : 0;
    document.getElementById('reset-balk').style.width = pct + '%';
    document.getElementById('reset-fase-label').textContent =
      (d.fase === 'download' ? 'Downloaden' : 'Presorteren') + ` (${{d.stap}}/${{d.totaal}})`;
    document.getElementById('reset-bericht').textContent = d.bericht || '';
  }} else {{
    // Standalone presort — toon in voortgangsbalk bovenin
    const pct = d.totaal > 0 ? Math.round(d.stap / d.totaal * 100) : 0;
    document.getElementById('progress').style.width = pct + '%';
    document.getElementById('status').textContent = `Presorteren: ${{d.stap}}/${{d.totaal}} — ${{d.bericht}}`;
  }}
}}

function resetTest() {{
  document.getElementById('reset-keuze').style.display = '';
  document.getElementById('reset-voortgang').style.display = 'none';
  document.getElementById('reset-modal').style.display = 'flex';
}}

async function resetTestBevestigd() {{
  const aantal = parseInt(document.getElementById('reset-aantal').value);
  document.getElementById('fotos').innerHTML = '';
  selectie.clear();
  updateSidebar();
  huidigeMaand = null;
  await fetch('/reset', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{aantal}})
  }});
  // Wacht op reset_klaar via SSE — modal blijft open en toont voortgang
}}

async function presortRun() {{
  document.getElementById('progress').style.width = '0%';
  document.getElementById('status').textContent = 'Presort starten…';
  await fetch('/presort', {{method: 'POST'}});
}}

async function clearThumbs() {{
  document.getElementById('status').textContent = 'Cache wissen…';
  const r = await fetch('/clearthumbs', {{method: 'POST'}});
  const d = await r.json();
  document.getElementById('status').textContent = d.ok ? 'Cache gewist.' : 'Fout bij wissen.';
}}

async function opruimLege() {{
  const r = await fetch('/opruim', {{method: 'POST'}});
  const d = await r.json();
  document.getElementById('status').textContent = d.bericht;
  if (d.verwijderd > 0) {{ verversNav(d.jm || {{}}); _verversTree(); }}
}}

// ── Zoom-knop → lightbox ─────────────────────────────────────────────────────
function openFotoZoom(e, btn) {{
  e.stopPropagation();
  const el = btn.closest('.foto');
  lb_groep = [{{
    rel:     el.dataset.pad,
    naam:    el.dataset.naam,
    datum:   el.dataset.datumNl || el.dataset.datum || '',
    grootte: parseInt(el.dataset.grootte || '0'),
  }}];
  lb_index = 0;
  lb_hartjes.clear();
  document.getElementById('lb').style.display = 'flex';
  document.body.style.overflow = 'hidden';
  renderLightbox();
}}

// ── Lightbox ──────────────────────────────────────────────────────────────────
let huidigeGroepen = [];   // wordt gezet in renderFotos
let lb_groep = [];         // fotos in huidige lightbox
let lb_index = 0;          // startindex huidige pagina
const lb_hartjes = new Set(); // rel-paden met hartje

function lbPerPagina() {{ return window.innerWidth < 600 ? 1 : 2; }}

function fmtBytes(b) {{
  if (b < 1024) return b + ' B';
  if (b < 1024*1024) return (b/1024).toFixed(0) + ' KB';
  return (b/1024/1024).toFixed(1) + ' MB';
}}

function openLightbox(gi, evt) {{
  evt.stopPropagation();
  lb_groep = huidigeGroepen[gi].fotos;
  lb_index = 0;
  lb_hartjes.clear();
  document.getElementById('lb').style.display = 'flex';
  document.body.style.overflow = 'hidden';
  renderLightbox();
}}

function sluitLightbox() {{
  document.getElementById('lb').style.display = 'none';
  document.body.style.overflow = '';
}}

function lbStap(richting) {{
  const pp = lbPerPagina();
  const nieuw = lb_index + richting * pp;
  if (nieuw < 0 || nieuw >= lb_groep.length) return;
  lb_index = nieuw;
  renderLightbox();
}}

function lbToggleHart(rel) {{
  if (lb_hartjes.has(rel)) lb_hartjes.delete(rel);
  else lb_hartjes.add(rel);
  renderLightbox();
}}

function renderLightbox() {{
  const pp = lbPerPagina();
  const totaal = lb_groep.length;
  const fotos = lb_groep.slice(lb_index, lb_index + pp);
  const eind = Math.min(lb_index + pp, totaal);

  document.getElementById('lb-teller').textContent =
    `foto ${{lb_index + 1}}–${{eind}} van ${{totaal}}`;

  // Foto-panelen
  const wrap = document.getElementById('lb-fotos');
  wrap.innerHTML = fotos.map(f => {{
    const metHart = lb_hartjes.has(f.rel);
    const hartKls = metHart ? ' actief' : '';
    const imgKls  = metHart ? ' met-hart' : '';
    const fullUrl = '/full?pad=' + encodeURIComponent(f.rel);
    return `<div class="lb-foto-wrap">
      <img src="${{fullUrl}}" class="${{imgKls}}" onclick="lbToggleHart('${{f.rel.replace(/'/g,"\\\\'")}}')" title="Klik of H voor hartje">
      <div class="lb-foto-info">
        <span>${{f.naam}}<br><span style="color:#666">${{f.datum}} · ${{fmtBytes(f.grootte)}}</span></span>
        <button type="button" class="lb-hart${{hartKls}}" onclick="lbToggleHart('${{f.rel.replace(/'/g,"\\\\'")}}')" title="H">❤</button>
      </div>
    </div>`;
  }}).join('');

  // Navigatie
  document.getElementById('lb-prev').disabled = lb_index <= 0;
  document.getElementById('lb-next').disabled = lb_index + pp >= totaal;

  // Info + bevestig-knop
  const bewaard = lb_hartjes.size;
  const weg     = totaal - bewaard;
  document.getElementById('lb-info').textContent =
    bewaard > 0
      ? `${{bewaard}} bewaard, ${{weg}} naar prullenbak`
      : 'Geef minstens 1 foto een hartje om te bevestigen';
  document.getElementById('lb-bevestig').disabled = bewaard === 0;
}}

async function bevestigLightbox() {{
  const bewaard  = new Set(lb_hartjes);
  const weg      = lb_groep.map(f => f.rel).filter(r => !bewaard.has(r));
  if (weg.length > 0) {{
    const resp = await fetch('/prullenbak', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{paden: weg, submap: 'duplicaten'}})
    }});
    const d = await resp.json();
    if (d.jm) verversNav(d.jm);
    _verversTree();
  }}
  sluitLightbox();
  _herlaadHuidig();
}}

// Toetsenbord
document.addEventListener('keydown', e => {{
  if (document.getElementById('lb').style.display !== 'flex') return;
  if (e.key === 'ArrowLeft')  {{ lbStap(-1); e.preventDefault(); }}
  if (e.key === 'ArrowRight') {{ lbStap(1);  e.preventDefault(); }}
  if (e.key === 'Escape') sluitLightbox();
  if (e.key === 'h' || e.key === 'H') {{
    // Hart geven aan alle zichtbare foto's op huidige pagina
    const pp = lbPerPagina();
    lb_groep.slice(lb_index, lb_index + pp).forEach(f => lb_hartjes.add(f.rel));
    renderLightbox();
  }}
}});

// Swipe (mobiel)
(function() {{
  let tx = 0;
  const el = document.getElementById('lb');
  el.addEventListener('touchstart', e => {{ tx = e.touches[0].clientX; }}, {{passive:true}});
  el.addEventListener('touchend', e => {{
    const dx = e.changedTouches[0].clientX - tx;
    if (Math.abs(dx) > 50) lbStap(dx < 0 ? 1 : -1);
  }}, {{passive:true}});
}})();

// Breedte slider + persistence
function sliderVerander(val) {{
  document.documentElement.style.setProperty('--foto-breedte', val + 'px');
  document.getElementById('breedte-label').textContent = val + 'px';
  localStorage.setItem('fc_breedte', val);
}}
(function() {{
  const opgeslagen = localStorage.getItem('fc_breedte');
  if (opgeslagen) {{
    const s = document.getElementById('breedte-slider');
    if (s) {{ s.value = opgeslagen; sliderVerander(opgeslagen); }}
  }}
}})();

// ── Resizable left-panel ──────────────────────────────────────────────────────
(function() {{
  const resizer = document.getElementById('left-resizer');
  const panel   = document.getElementById('left-panel');
  if (!resizer || !panel) return;
  const LS_KEY = 'fc_left_w';
  const opgeslagenW = localStorage.getItem(LS_KEY);
  if (opgeslagenW) document.documentElement.style.setProperty('--left-w', opgeslagenW + 'px');
  let startX, startW;
  resizer.addEventListener('mousedown', e => {{
    startX = e.clientX;
    startW = panel.offsetWidth;
    resizer.classList.add('dragging');
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
    e.preventDefault();
  }});
  function onMove(e) {{
    const w = Math.max(140, Math.min(520, startW + e.clientX - startX));
    document.documentElement.style.setProperty('--left-w', w + 'px');
  }}
  function onUp() {{
    resizer.classList.remove('dragging');
    document.removeEventListener('mousemove', onMove);
    document.removeEventListener('mouseup', onUp);
    localStorage.setItem(LS_KEY, panel.offsetWidth);
  }}
}})();

// Bladwijzer
function plaatsBladwijzer(el) {{
  document.querySelectorAll('.bladwijzer').forEach(b => b.remove());
  const fotos = document.getElementById('fotos');
  let node = el;
  while (node.parentElement && node.parentElement !== fotos) node = node.parentElement;
  const bw = document.createElement('div');
  bw.className = 'bladwijzer';
  bw.textContent = 'hier gebleven';
  fotos.insertBefore(bw, node);
}}

// Scroll positie + anker opslaan
let _scrollTimer = null;
document.addEventListener('scroll', (ev) => {{
  if (!ev.target.closest || !ev.target.closest('#fotos')) return;
  clearTimeout(_scrollTimer);
  _scrollTimer = setTimeout(() => {{
    if (!huidigeMaand) return;
    const eerst = [...document.querySelectorAll('.foto')].find(f => f.getBoundingClientRect().bottom > 80);
    if (eerst) localStorage.setItem('fc_anker', eerst.dataset.pad);
  }}, 600);
}}, {{passive: true, capture: true}});

// Tree laden bij start + herstel sessie
laadTree(true).then(() => {{
  const maand = localStorage.getItem('fc_maand');
  if (maand) laadMaand(maand, null);
}});

// SSE live updates
(function() {{
  function verbind() {{
    const es = new EventSource('/events');
    es.addEventListener('message', function(e) {{
      try {{
        const d = JSON.parse(e.data);
        if (d.type === 'update') {{
          if (d.jm) verversNav(d.jm);
          if (d.bestanden && d.bestanden.length) voegActiviteitToe(d.bestanden);
          if (huidigeMaand) laadMaand(huidigeMaand, null);
        }} else if (d.type === 'progress') {{
          toonVoortgang(d);
        }} else if (d.type === 'reset_klaar') {{
          location.reload();
        }} else if (d.type === 'reset_fout') {{
          document.getElementById('reset-fase-label').textContent = 'Fout!';
          document.getElementById('reset-bericht').textContent = d.bericht || 'Onbekende fout.';
          document.getElementById('reset-bericht').style.color = '#f66';
        }} else if (d.type === 'presort_klaar') {{
          document.getElementById('progress').style.width = '0%';
          document.getElementById('status').textContent = 'Presort klaar.';
          if (d.jm) verversNav(d.jm);
          _verversTree();
        }} else if (d.type === 'presort_fout') {{
          document.getElementById('progress').style.width = '0%';
          document.getElementById('status').textContent = 'Presort fout: ' + (d.bericht || '?');
        }}
      }} catch(_) {{}}
    }});
    es.addEventListener('error', function() {{ es.close(); setTimeout(verbind, 5000); }});
  }}
  verbind();
}})();
</script>
</body>
</html>"""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass

        def send_json(self, data, code=200):
            body = _json.dumps(data).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urlparse(self.path)

            if parsed.path == "/info":
                params = parse_qs(parsed.query)
                rel = params.get("pad", [""])[0]
                try:
                    pad = (BASE_DIR / rel).resolve()
                    if not pad.is_relative_to(BASE_DIR.resolve()) or not pad.is_file():
                        self.send_json({"fout": "niet gevonden"}, 404); return
                except Exception:
                    self.send_json({"fout": "ongeldig pad"}, 400); return
                stat = pad.stat()
                datum = lees_datum(pad)
                gps = _lees_gps(pad)
                locatie = _reverse_geocode(gps['lat'], gps['lng']) if gps else None
                self.send_json({
                    "map": str(pad.parent.relative_to(BASE_DIR)),
                    "grootte": stat.st_size,
                    "datum": datum.strftime("%d-%m-%Y %H:%M"),
                    "gps": gps,
                    "locatie": locatie,
                })
                return

            if parsed.path == "/thumb":
                params = parse_qs(parsed.query)
                rel = params.get("pad", [""])[0]
                try:
                    pad = (BASE_DIR / rel).resolve()
                    if not pad.is_relative_to(BASE_DIR.resolve()):
                        raise ValueError("Pad buiten BASE_DIR")
                except (ValueError, OSError):
                    self.send_response(403); self.end_headers(); return
                if not pad.exists() or not pad.is_file():
                    self.send_response(404); self.end_headers(); return
                data = get_thumbnail(pad)
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", len(data))
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                self.wfile.write(data)
                return

            if parsed.path == "/full":
                params = parse_qs(parsed.query)
                rel = params.get("pad", [""])[0]
                try:
                    pad = (BASE_DIR / rel).resolve()
                    if not pad.is_relative_to(BASE_DIR.resolve()):
                        raise ValueError("Pad buiten BASE_DIR")
                except (ValueError, OSError):
                    self.send_response(403); self.end_headers(); return
                if not pad.exists() or not pad.is_file():
                    self.send_response(404); self.end_headers(); return
                # Serveer origineel, converteer non-JPEG naar JPEG via Pillow/rawpy
                ext = pad.suffix.lower()
                if ext in ('.jpg', '.jpeg'):
                    data = pad.read_bytes()
                elif ext == '.arw' and HAS_RAWPY:
                    # ARW: extraheer ingebedde JPEG-preview op maximale grootte
                    try:
                        import io as _io
                        with _rawpy.imread(str(pad)) as raw:
                            thumb = raw.extract_thumb()
                        if thumb.format.name == 'JPEG':
                            data = thumb.data
                        elif thumb.format.name == 'BITMAP':
                            img = Image.fromarray(thumb.data).convert("RGB")
                            buf = _io.BytesIO()
                            img.save(buf, format="JPEG", quality=92)
                            data = buf.getvalue()
                        else:
                            data = _maak_placeholder(pad.name)
                    except Exception:
                        data = _maak_placeholder(pad.name)
                else:
                    try:
                        import io as _io
                        img = Image.open(pad)
                        from PIL import ImageOps
                        img = ImageOps.exif_transpose(img).convert("RGB")
                        buf = _io.BytesIO()
                        img.save(buf, format="JPEG", quality=90)
                        data = buf.getvalue()
                    except Exception:
                        data = _maak_placeholder(pad.name)
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", len(data))
                self.send_header("Cache-Control", "public, max-age=3600")
                self.end_headers()
                self.wfile.write(data)
                return

            if parsed.path == "/fotos":
                params = parse_qs(parsed.query)
                maand    = params.get("maand", [""])[0]
                pad_par  = params.get("pad",   [""])[0]
                try:
                    if pad_par:
                        pad_abs = (BASE_DIR / pad_par).resolve()
                        if not pad_abs.is_relative_to(BASE_DIR.resolve()):
                            raise ValueError("Pad buiten BASE_DIR")
                        fotos = zoek_fotos(pad_abs) if pad_abs.is_dir() else []
                    else:
                        fotos = fotos_in_map(maand)
                    groepen_raw = burst_groepen(fotos)
                    groepen_out = []
                    for g in groepen_raw:
                        items = []
                        for f in g["fotos"]:
                            rel = str(f.relative_to(BASE_DIR))
                            datum = lees_datum(f)
                            grootte = f.stat().st_size
                            items.append({"rel": rel, "naam": f.name, "datum": datum.strftime("%d-%m-%Y"), "datum_iso": datum.strftime("%Y-%m-%d"), "tijdstip": datum.strftime("%H:%M"), "grootte": grootte})
                        groepen_out.append({"fotos": items, "type": g["type"], "span_sec": g.get("span_sec", 0)})
                    self.send_json({"groepen": groepen_out})
                except Exception as e:
                    import traceback
                    traceback.print_exc()
                    self.send_json({"fout": str(e), "groepen": []}, 500)
                return

            if parsed.path == "/jm":
                self.send_json(jaren_maanden())
                return

            if parsed.path == "/tree":
                _skip = {'@eadir', '#recycle', '.recycle', '.ds_store', 'thumbs.db'}
                def _boom(pad, diepte=0):
                    if diepte > 6:
                        return []
                    result = []
                    try:
                        for sub in sorted(pad.iterdir(), key=lambda x: x.name.lower()):
                            if sub.is_dir() and not sub.name.startswith(('.', '@', '#')) \
                                    and sub.name.lower() not in _skip:
                                try:
                                    aantal = sum(1 for f in sub.rglob("*")
                                                 if f.is_file() and f.suffix.lower() in FOTO_EXTS)
                                except Exception:
                                    aantal = 0
                                result.append({
                                    "naam": sub.name,
                                    "pad": str(sub.relative_to(BASE_DIR)),
                                    "submappen": _boom(sub, diepte + 1),
                                    "aantal": aantal,
                                })
                    except Exception:
                        pass
                    return result

                datumloos_pad = BASE_DIR / DATUMLOOS_DIR
                datumloos_pad.mkdir(parents=True, exist_ok=True)
                prullenbak_pad = BASE_DIR / PRULLENBAK_DIR
                self.send_json({
                    "archief": ARCHIEF_DIR,
                    "archief_boom": _boom(archief),
                    "datumloos": DATUMLOOS_DIR,
                    "datumloos_boom": _boom(datumloos_pad),
                    "prullenbak": PRULLENBAK_DIR,
                    "prullenbak_boom": _boom(prullenbak_pad) if prullenbak_pad.exists() else [],
                    "jm": jaren_maanden(),
                })
                return

            if parsed.path == "/events":
                q = _queue.Queue()
                with _sse_lock:
                    _sse_queues.append(q)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()
                try:
                    while True:
                        try:
                            msg = q.get(timeout=20)
                            self.wfile.write(f"data: {msg}\n\n".encode())
                            self.wfile.flush()
                        except _queue.Empty:
                            self.wfile.write(b": heartbeat\n\n")
                            self.wfile.flush()
                except Exception:
                    pass
                finally:
                    with _sse_lock:
                        try: _sse_queues.remove(q)
                        except ValueError: pass
                return

            html = render_hoofdpagina().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", len(html))
            self.end_headers()
            self.wfile.write(html)

        def do_POST(self):
            path = urlparse(self.path).path
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b""

            if path == "/verplaats":
                data = _json.loads(body)
                base_resolved = BASE_DIR.resolve()
                # Ondersteuning voor zowel 'album' (→ archief/album) als 'pad' (→ BASE_DIR/pad)
                if "pad" in data:
                    raw_pad = data["pad"]
                    try:
                        doel_map = (BASE_DIR / raw_pad).resolve()
                        if not doel_map.is_relative_to(base_resolved):
                            self.send_json({"fout": "pad buiten BASE_DIR"}, 400); return
                    except Exception:
                        self.send_json({"fout": "ongeldig pad"}, 400); return
                else:
                    album = data["album"]
                    doel_map = archief / Path(album)
                doel_map.mkdir(parents=True, exist_ok=True)
                count = 0
                for rel in data["paden"]:
                    try:
                        src = (BASE_DIR / rel).resolve()
                        if not src.is_relative_to(base_resolved):
                            continue
                    except (ValueError, OSError):
                        continue
                    if src.exists():
                        dst = uniek_doel(doel_map, src.name)
                        shutil.move(str(src), dst)
                        count += 1
                album_label = str(doel_map.relative_to(BASE_DIR))
                sla_recent_op(album_label)
                verwijder_lege_mappen()
                immich_rescan()
                self.send_json({
                    "bericht": f"{count} foto('s) verplaatst naar '{album_label}'",
                    "jm": jaren_maanden(),
                })

            elif path == "/prullenbak":
                data = _json.loads(body)
                submap = data.get("submap", "losse_items")
                if submap not in ("duplicaten", "losse_items"):
                    submap = "losse_items"
                prullenbak = BASE_DIR / PRULLENBAK_DIR / submap
                prullenbak.mkdir(parents=True, exist_ok=True)
                base_resolved = BASE_DIR.resolve()
                count = 0
                for rel in data["paden"]:
                    try:
                        src = (BASE_DIR / rel).resolve()
                        if not src.is_relative_to(base_resolved):
                            continue
                    except (ValueError, OSError):
                        continue
                    if src.exists():
                        dst = uniek_doel(prullenbak, src.name)
                        shutil.move(str(src), dst)
                        count += 1
                verwijder_lege_mappen()
                immich_rescan()
                self.send_json({"verplaatst": count, "jm": jaren_maanden()})

            elif path == "/recents":
                data = _json.loads(body)
                recents = laad_recents()
                if "verwijder" in data:
                    album = data["verwijder"]
                    if album in recents:
                        recents.remove(album)
                    RECENTS_FILE.write_text(_json.dumps(recents))
                    self.send_json({"recents": recents})
                elif "volgorde" in data:
                    volgorde = [str(a) for a in data["volgorde"] if isinstance(a, str)]
                    RECENTS_FILE.write_text(_json.dumps(volgorde[:10]))
                    self.send_json({"recents": volgorde[:10]})
                else:
                    self.send_json({"fout": "onbekende actie"}, 400)

            elif path == "/reset":
                req = _json.loads(body) if body else {}
                aantal = max(20, min(400, int(req.get("aantal", 40))))

                def doe_reset(n=aantal):
                    _watcher_pauze[0] = True
                    try:
                        def dl_cb(stap, tot, bericht):
                            _push_sse(_json.dumps({"type": "progress", "fase": "download",
                                                   "stap": stap, "totaal": tot, "bericht": bericht}))
                        def ps_cb(stap, tot, bericht):
                            _push_sse(_json.dumps({"type": "progress", "fase": "presort",
                                                   "stap": stap, "totaal": tot, "bericht": bericht}))
                        maak_testdata(aantal=n, progress_cb=dl_cb)
                        presort(progress_cb=ps_cb)
                        _push_sse(_json.dumps({"type": "reset_klaar", "jm": jaren_maanden()}))
                    except Exception as _exc:
                        import traceback as _tb
                        _log(f"[Reset] Fout: {_exc}\n{_tb.format_exc()}")
                        _push_sse(_json.dumps({"type": "reset_fout", "bericht": str(_exc)}))
                    finally:
                        _watcher_pauze[0] = False

                threading.Thread(target=doe_reset, daemon=True).start()
                self.send_json({"ok": True})

            elif path == "/presort":
                def doe_presort():
                    try:
                        def ps_cb(stap, tot, bericht):
                            _push_sse(_json.dumps({"type": "progress", "fase": "presort",
                                                   "stap": stap, "totaal": tot, "bericht": bericht}))
                        presort(progress_cb=ps_cb)
                        _push_sse(_json.dumps({"type": "presort_klaar", "jm": jaren_maanden()}))
                    except Exception as _exc:
                        import traceback as _tb
                        _log(f"[Presort] Fout: {_exc}\n{_tb.format_exc()}")
                        _push_sse(_json.dumps({"type": "presort_fout", "bericht": str(_exc)}))

                threading.Thread(target=doe_presort, daemon=True).start()
                self.send_json({"ok": True})

            elif path == "/clearthumbs":
                gewist = 0
                for p in [THUMBCACHE_DIR, DATUMCACHE_FILE]:
                    if p.exists():
                        if p.is_dir():
                            shutil.rmtree(p)
                        else:
                            p.unlink()
                        gewist += 1
                THUMBCACHE_DIR.mkdir(exist_ok=True)
                _log(f"[Cache] {gewist} cache-item(s) gewist")
                self.send_json({"ok": True})

            elif path == "/opruim":
                import re as _re
                verwijderd = 0
                if uitzoeken.exists():
                    for sub in list(uitzoeken.iterdir()):
                        if sub.is_dir() and _re.match(r'^\d{4}-\d{2}$', sub.name):
                            if not _heeft_echte_inhoud(sub):
                                try:
                                    shutil.rmtree(sub)
                                    _log(f"[Opruimen] Lege map verwijderd: {sub.name}")
                                    verwijderd += 1
                                except OSError as oe:
                                    _log(f"[Opruimen] Fout bij verwijderen {sub.name}: {oe}")
                bericht = f"{verwijderd} lege map(pen) verwijderd." if verwijderd else "Geen lege mappen gevonden."
                self.send_json({"bericht": bericht, "verwijderd": verwijderd, "jm": jaren_maanden()})

            elif path == "/hernoem":
                data = _json.loads(body)
                rel  = data.get("pad", "")
                naam = data.get("naam", "").strip()
                if not rel or not naam:
                    self.send_json({"fout": "pad of naam ontbreekt"}, 400); return
                import re as _re
                if _re.search(r'[/\\:*?"<>|]', naam):
                    self.send_json({"fout": "ongeldige tekens in naam"}, 400); return
                try:
                    oud = (BASE_DIR / rel).resolve()
                    if not oud.is_relative_to(BASE_DIR.resolve()) or not oud.is_dir():
                        self.send_json({"fout": "map niet gevonden"}, 404); return
                    nieuw = oud.parent / naam
                    if nieuw.exists():
                        self.send_json({"fout": f"'{naam}' bestaat al"}); return
                    oud.rename(nieuw)
                    nieuw_rel = str(nieuw.relative_to(BASE_DIR))
                    _log(f"[Hernoem] {rel} → {nieuw_rel}")
                    self.send_json({"ok": True, "nieuw_pad": nieuw_rel})
                except Exception as e:
                    self.send_json({"fout": str(e)}, 500)

            elif path == "/maakmap":
                data   = _json.loads(body)
                naam   = data.get("naam", "").strip()
                sectie = data.get("sectie")  # 'verwerkt' | 'datumloos' | None
                ouder  = data.get("ouder")   # pad relatief aan BASE_DIR of None
                paden  = data.get("paden", [])
                if not naam:
                    self.send_json({"fout": "naam ontbreekt"}, 400); return
                import re as _re
                if _re.search(r'[\\:*?"<>|]', naam):
                    self.send_json({"fout": "ongeldige tekens"}, 400); return
                base_resolved = BASE_DIR.resolve()
                try:
                    if ouder:
                        basis = (BASE_DIR / ouder).resolve()
                        if not basis.is_relative_to(base_resolved):
                            self.send_json({"fout": "ouder buiten BASE_DIR"}, 400); return
                    elif sectie == "datumloos":
                        basis = BASE_DIR / DATUMLOOS_DIR
                    else:
                        basis = archief  # standaard: verwerkt
                    doel_map = basis / Path(naam)
                    doel_map.mkdir(parents=True, exist_ok=True)
                    nieuw_rel = str(doel_map.relative_to(BASE_DIR))
                    count = 0
                    for rel in paden:
                        try:
                            src = (BASE_DIR / rel).resolve()
                            if not src.is_relative_to(base_resolved): continue
                        except (ValueError, OSError): continue
                        if src.exists():
                            dst = uniek_doel(doel_map, src.name)
                            shutil.move(str(src), dst)
                            count += 1
                    if count:
                        verwijder_lege_mappen()
                        immich_rescan()
                    _log(f"[NieuweMap] {nieuw_rel} ({count} foto's)")
                    self.send_json({
                        "bericht": f"Map '{naam}' aangemaakt" + (f", {count} foto('s) verplaatst" if count else ""),
                        "nieuw_pad": nieuw_rel,
                        "jm": jaren_maanden(),
                    })
                except Exception as e:
                    self.send_json({"fout": str(e)}, 500)

            elif path == "/verplaatsmap":
                data     = _json.loads(body)
                van_rel  = data.get("van", "").strip()
                naar_rel = data.get("naar", "").strip()
                if not van_rel or not naar_rel:
                    self.send_json({"fout": "van of naar ontbreekt"}, 400); return
                base_resolved = BASE_DIR.resolve()
                try:
                    van_abs  = (BASE_DIR / van_rel).resolve()
                    naar_abs = (BASE_DIR / naar_rel).resolve()
                    if not van_abs.is_relative_to(base_resolved) or not van_abs.is_dir():
                        self.send_json({"fout": "bronmap niet gevonden"}, 404); return
                    if not naar_abs.is_relative_to(base_resolved):
                        self.send_json({"fout": "doelmap buiten BASE_DIR"}, 400); return
                    # Voorkom neerzetten in zichzelf of een submap
                    if naar_abs == van_abs or naar_abs.is_relative_to(van_abs):
                        self.send_json({"fout": "kan map niet in zichzelf verplaatsen"}, 400); return
                    naar_abs.mkdir(parents=True, exist_ok=True)
                    doel = uniek_doel(naar_abs, van_abs.name)
                    shutil.move(str(van_abs), doel)
                    nieuw_rel = str(doel.relative_to(BASE_DIR))
                    verwijder_lege_mappen()
                    _log(f"[VerplaatsMap] {van_rel} → {nieuw_rel}")
                    self.send_json({"bericht": f"Map verplaatst naar '{naar_rel}'", "nieuw_pad": nieuw_rel})
                except Exception as e:
                    self.send_json({"fout": str(e)}, 500)

            else:
                self.send_json({"fout": "onbekend pad"}, 404)

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        lan_ip = s.getsockname()[0]
        s.close()
    except Exception:
        lan_ip = "onbekend"

    print(f"Declutter draait op:")
    print(f"  http://localhost:{PORT}")
    print(f"  http://{lan_ip}:{PORT}")

    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()


# ── Entrypoint ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = set(sys.argv[1:])

    if "--clearcache" in args:
        for p in [THUMBCACHE_DIR, DATUMCACHE_FILE]:
            if p.exists():
                if p.is_dir():
                    shutil.rmtree(p)
                else:
                    p.unlink()
        print("Cache gewist.")
        sys.exit(0)

    check_dependencies()
    start_server()
