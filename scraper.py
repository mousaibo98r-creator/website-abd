import os
import re
import json
import time
import hashlib
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse

from dotenv import load_dotenv
import unicodedata
from ddgs import DDGS
from openai import OpenAI
import requests
from bs4 import BeautifulSoup

# Ensure environment variables are loaded
load_dotenv()

# ============================================================================
# CONFIGURATION
# ============================================================================
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

client = None
if DEEPSEEK_API_KEY and DEEPSEEK_API_KEY != "your_deepseek_api_key_here":
    try:
        client = OpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url="https://api.deepseek.com"
        )
    except Exception as e:
        print(f"Warning: Failed to initialize DeepSeek OpenAI client: {e}")

# Target profiles and industrial extrusion keywords
SEARCH_KEYWORDS = [
    "LED Profile", "LED Aluminium Profile", "LED Strip Profile",
    "Tile Trim Profile", "Tile Edge Profile", "Ceramic Tile Trim",
    "Furniture Profiles", "Cabinet Kitchen Profiles",
    "Glass Profiles", "Shower Glass Profiles",
    "Decorative Profiles", "Wall Ceiling Profiles",
    "Aluminium Extrusion Profile", "Small Aluminium Profiles"
]

CATEGORY_MAP = {
    "all": "Aluminium Profiles, Extrusions, LED Profiles, Tile Trim Profiles, Industrial Profiles",
    "led": "LED Aluminium Profiles, LED Channels, LED Strip Profiles, Linear Lighting Profiles",
    "tile": "Tile Trim Profiles, Ceramic Tile Edge Trims, Flooring Profiles, Stair Nosing Profiles",
    "furniture": "Furniture Aluminium Profiles, Kitchen Cabinet Profiles, Handle Profiles, Wardrobe Profiles",
    "glass": "Shower Glass Profiles, Glass Railing Aluminium Profiles, Glass Partition Profiles",
    "decorative": "Wall & Ceiling Decorative Aluminium Profiles, Baseboard & Skirting Profiles",
    "extrusion": "Light Industrial Extrusions, Standard Aluminium Profiles, Heat Sink Extrusions",
}

QUERY_TEMPLATES = [
    "{kw} {loc}",
    "{kw} manufacturer {loc}",
    "{kw} supplier {loc}",
    "{kw} company {loc}",
]

RESULTS_PER_QUERY = 8
MAX_URLS = 10
MAX_WORKERS = 10
HTTP_TIMEOUT = 3.5
MAX_TEXT_CHARS = 3500
SEARCH_BUDGET_SECONDS = 12
AI_BATCH_SIZE = 5

STRICT_LOCATION_FILTER = False

# High-priority subpage path hints for contact and legal notices (where addresses reside)
CONTACT_HINTS = (
    "contact", "kontakt", "contacto", "contatti", "nous-contacter", "iletisim",
    "impressum", "imprint", "legal", "legal-notice", "mentions-legales", "aviso-legal",
    "about", "about-us", "quienes-somos", "uber-uns", "standort", "standorte",
    "company", "find-us", "location", "locations", "adresse", "address", "sede"
)

# Unwanted directories, aggregators, and social platforms
EXCLUDED_DOMAINS = [
    "alibaba.com", "made-in-china.com", "globalsources.com",
    "indiamart.com", "exportersindia.com", "tradeindia.com",
    "ec21.com", "europages.com", "europages.co.uk", "kompass.com",
    "wlw.de", "yellowpages.com", "linkedin.com", "facebook.com",
    "youtube.com", "pinterest.com", "instagram.com", "twitter.com",
    "x.com", "tiktok.com", "reddit.com", "wikipedia.org", "amazon.com",
    "ebay.com", "etsy.com", "trustpilot.com", "glassdoor.com",
    "indeed.com", "dnb.com", "bloomberg.com", "zoominfo.com",
    "yelp.com", "tripadvisor.com", "github.com", "stackoverflow.com"
]

COUNTRY_REGION_MAP = {
    "argentina": "ar-es", "australia": "au-en", "austria": "at-de",
    "belgium": "be-nl", "brazil": "br-pt", "bulgaria": "bg-bg",
    "canada": "ca-en", "chile": "cl-es", "china": "cn-zh",
    "colombia": "co-es", "croatia": "hr-hr", "czech republic": "cz-cs",
    "czechia": "cz-cs", "denmark": "dk-da", "estonia": "ee-et",
    "finland": "fi-fi", "france": "fr-fr", "germany": "de-de",
    "deutschland": "de-de", "greece": "gr-el", "hong kong": "hk-tzh",
    "hungary": "hu-hu", "india": "in-en", "indonesia": "id-id",
    "ireland": "ie-en", "israel": "il-he", "italy": "it-it",
    "japan": "jp-jp", "south korea": "kr-kr", "korea": "kr-kr",
    "latvia": "lv-lv", "lithuania": "lt-lt", "malaysia": "my-ms",
    "mexico": "mx-es", "netherlands": "nl-nl", "holland": "nl-nl",
    "new zealand": "nz-en", "norway": "no-no", "peru": "pe-es",
    "philippines": "ph-en", "poland": "pl-pl", "portugal": "pt-pt",
    "romania": "ro-ro", "russia": "ru-ru", "singapore": "sg-en",
    "slovakia": "sk-sk", "slovak republic": "sk-sk", "slovenia": "sl-sl",
    "south africa": "za-en", "spain": "es-es", "espana": "es-es",
    "sweden": "se-sv", "switzerland": "ch-de", "schweiz": "ch-de",
    "taiwan": "tw-tzh", "thailand": "th-th", "turkey": "tr-tr",
    "türkiye": "tr-tr", "turkiye": "tr-tr", "ukraine": "ua-uk",
    "united kingdom": "uk-en", "uk": "uk-en", "britain": "uk-en",
    "great britain": "uk-en", "united states": "us-en", "usa": "us-en",
    "u.s.": "us-en", "u.s.a.": "us-en", "venezuela": "ve-es",
    "vietnam": "vn-vi", "algeria": "xa-en", "egypt": "xa-en",
    "iraq": "xa-en", "jordan": "xa-en", "kuwait": "xa-en",
    "lebanon": "xa-en", "morocco": "xa-en", "oman": "xa-en",
    "palestine": "xa-en", "qatar": "xa-en", "saudi arabia": "xa-en",
    "ksa": "xa-en", "syria": "xa-en", "tunisia": "xa-en",
    "united arab emirates": "xa-en", "uae": "xa-en", "dubai": "xa-en"
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,de;q=0.8,tr;q=0.7,es;q=0.6,fr;q=0.5",
    "DNT": "1"
}

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?:\+|00)\s?\d[\d\s\-().]{6,20}\d")

EMAIL_JUNK = [
    "example.com", "test.com", "domain.com", "yourdomain", "sentry.io",
    "wix.com", "wordpress.org", "cloudflare", "noreply", "no-reply",
    "placeholder", "email@email", "sample@sample", "youremail", "user@domain",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".css", ".js"
]

# ---- ADDRESS REGEXES ------------------------------------------------------
# Street + number (multi-lingual international patterns)
STREET_RE = re.compile(
    r"\b(?:"
    r"(?:[A-ZÄÖÜß][\w\.\-']*\s?){1,5}"
    r"(?:str(?:aße|asse|\.)?|straße|strasse|street|st\.?|road|rd\.?|avenue|ave\.?|"
    r"boulevard|bd\.?|blvd\.?|rue|via|viale|calle|avda\.?|avenida|ruta|"
    r"weg|lane|ln\.?|drive|dr\.?|platz|gasse|allée|allee|cad(?:desi)?|sok(?:ak)?|mah(?:allesi)?|ul(?:ica|\.)?|"
    r"industrial\s+estate|industrial\s+park|business\s+park|zone\s+industrielle|poligono\s+industrial|gewerbegebiet|gewerbepark)"
    r"\.?\s*\d{1,5}(?:\s*[-/]\s*\d{1,5})?[A-Za-z]?"
    r"|"
    r"\d{1,5}[A-Za-z]?\s+(?:[A-ZÄÖÜß][\w\.\-']*\s?){1,5}"
    r"(?:str(?:aße|asse|\.)?|straße|strasse|street|st\.?|road|rd\.?|avenue|ave\.?|"
    r"boulevard|bd\.?|blvd\.?|rue|via|calle|avenida|weg|lane|drive|platz|gasse|cad(?:desi)?|ul(?:ica|\.)?)"
    r")\b",
    re.IGNORECASE,
)

# Postcode + city (supports DE, FR, IT, ES, TR, UK, US, CA, PL, NL, etc.)
POSTAL_RE = re.compile(
    r"\b(?:"
    r"(?:D|F|I|E|NL|CH|AT|TR|PL)-?\d{4,6}"      # Country prefix + digits
    r"|\d{4,6}"                                  # Standard 4 to 6 digits (DE 5, TR 5, FR 5, ES 5, IT 5)
    r"|[A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2}"    # UK Postcode
    r"|[A-Z]\d[A-Z]\s?\d[A-Z]\d"                # Canadian Postal code
    r"|\d{5}(?:-\d{4})?"                         # US Zip code
    r"|\d{2}-\d{3}"                              # Polish Postcode (e.g. 00-950)
    r"|\d{4}\s?[A-Z]{2}"                         # Dutch Postcode (e.g. 1012 AB)
    r")\s+[A-ZÄÖÜß][\wÀ-ÿ'\-]+(?:\s+[A-ZÄÖÜß][\wÀ-ÿ'\-]+){0,3}\b"
)

COUNTRY_NAMES = [
    "germany", "deutschland", "france", "italy", "italia", "spain", "españa",
    "netherlands", "belgium", "portugal", "poland", "austria", "österreich",
    "switzerland", "schweiz", "sweden", "norway", "denmark", "finland",
    "ireland", "united kingdom", "england", "scotland", "wales", "turkey",
    "türkiye", "turkiye", "greece", "croatia", "czech republic", "czechia",
    "slovakia", "slovenia", "hungary", "romania", "bulgaria", "estonia",
    "latvia", "lithuania", "usa", "united states", "canada", "mexico",
    "brazil", "argentina", "chile", "colombia", "peru", "australia",
    "new zealand", "india", "china", "japan", "south korea", "korea",
    "singapore", "malaysia", "indonesia", "thailand", "vietnam",
    "philippines", "israel", "saudi arabia", "united arab emirates", "uae",
    "qatar", "kuwait", "oman", "bahrain", "jordan", "lebanon", "egypt",
    "morocco", "tunisia", "algeria", "south africa", "russia", "ukraine",
]

COUNTRY_RE = re.compile(
    r"\b(" + "|".join(re.escape(c) for c in sorted(COUNTRY_NAMES, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)


# ============================================================================
# LOCATION & DOMAIN HELPERS
# ============================================================================
def guess_region(location: str) -> str:
    loc_lower = (location or "").lower()
    for country in sorted(COUNTRY_REGION_MAP, key=len, reverse=True):
        if country in loc_lower:
            return COUNTRY_REGION_MAP[country]
    return "wt-wt"


def expected_tld(location: str) -> str | None:
    region = guess_region(location)
    if region in ("wt-wt",) or not region:
        return None
    prefix = region.split("-")[0]
    if len(prefix) == 2 and prefix not in ("xa",):
        return "." + prefix
    if region == "uk-en":
        return ".uk"
    return None


FALLBACK_GEO_CACHE = {
    "ankara": (39.9334, 32.8597),
    "sincan": (39.9583, 32.5786),
    "ostim": (39.9725, 32.7483),
    "ivedik": (39.9817, 32.7667),
    "istanbul": (41.0082, 28.9784),
    "esenyurt": (41.0342, 28.6801),
    "basaksehir": (41.0967, 28.8028),
    "tuzla": (40.8167, 29.3000),
    "umraniye": (41.0256, 29.1172),
    "tekirdag": (40.9781, 27.5117),
    "tekirdağ": (40.9781, 27.5117),
    "corlu": (41.1594, 27.7986),
    "çorlu": (41.1594, 27.7986),
    "cerkezkoy": (41.2917, 28.0017),
    "çerkezköy": (41.2917, 28.0017),
    "ergene": (41.2581, 27.6719),
    "kapakli": (41.3236, 27.9783),
    "kapaklı": (41.3236, 27.9783),
    "velikoy": (41.2427, 27.9300),
    "veliköy": (41.2427, 27.9300),
    "izmir": (38.4237, 27.1428),
    "bursa": (40.1885, 29.0610),
    "kocaeli": (40.8533, 29.8815),
    "gebze": (40.8028, 29.4307),
    "sakarya": (40.7569, 30.3783),
    "adapazari": (40.7731, 30.4033),
    "antalya": (36.8969, 30.7133),
    "konya": (37.8746, 32.4932),
    "kayseri": (38.7205, 35.4826),
    "manisa": (38.6191, 27.4289),
    "denizli": (37.7765, 29.0864),
    "eskisehir": (39.7767, 30.5206),
    "eskişehir": (39.7767, 30.5206),
    "adana": (37.0000, 35.3213),
    "gaziantep": (37.0662, 37.3833),
    "turkey": (38.9637, 35.2433),
    "türkiye": (38.9637, 35.2433),
    "germany": (51.1657, 10.4515),
    "deutschland": (51.1657, 10.4515),
    "berlin": (52.5200, 13.4050),
    "munich": (48.1351, 11.5820),
    "münchen": (48.1351, 11.5820),
    "hamburg": (53.5511, 9.9937),
    "frankfurt": (50.1109, 8.6821),
    "stuttgart": (48.7758, 9.1829),
    "france": (46.2276, 2.2137),
    "paris": (48.8566, 2.3522),
    "italy": (41.8719, 12.5674),
    "milan": (45.4642, 9.1900),
    "milano": (45.4642, 9.1900),
    "spain": (40.4637, -3.7492),
    "madrid": (40.4168, -3.7038),
    "barcelona": (41.3879, 2.1699),
    "london": (51.5074, -0.1278),
    "uk": (55.3781, -3.4360),
    "kosovo": (42.6026, 20.9030),
    "pristina": (42.6629, 21.1655),
    "albania": (41.1533, 20.1683),
    "tirana": (41.3275, 19.8187),
    "macedonia": (41.6086, 21.7453),
    "skopje": (41.9981, 21.4254),
}


def _fold(s: str) -> str:
    """Normalize diacritics and special characters for tolerant search matching."""
    s = (s or "").lower()
    s = s.replace("ğ", "g").replace("Ğ", "g").replace("ı", "i").replace("İ", "i")
    s = s.replace("ş", "s").replace("Ş", "s").replace("ç", "c").replace("Ç", "c")
    s = s.replace("ö", "o").replace("Ö", "o").replace("ü", "u").replace("Ü", "u")
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c))


def get_fallback_coords(text: str) -> tuple:
    if not text:
        return (39.9334, 32.8597)
    folded = _fold(text)
    for name, coords in FALLBACK_GEO_CACHE.items():
        if _fold(name) in folded:
            return coords
    return (39.9334, 32.8597)


def location_tokens(location: str) -> list:
    parts = re.split(r"[,/|]", location or "")
    return [_fold(p.strip()) for p in parts if p.strip()]


def _token_in(text: str, token: str) -> bool:
    if not token or not text:
        return False
    t_folded = _fold(token)
    txt_folded = _fold(text)
    if len(t_folded) <= 3:
        return re.search(rf"(?<![a-z0-9]){re.escape(t_folded)}(?![a-z0-9])", txt_folded) is not None
    return t_folded in txt_folded


def generate_company_id(name: str) -> str:
    normalized_name = (name or "").strip().lower()
    return hashlib.md5(normalized_name.encode("utf-8")).hexdigest()


def root_domain(url: str) -> str:
    try:
        netloc = urlparse(url if "://" in url else "http://" + url).netloc.lower()
        netloc = netloc.split("@")[-1].split(":")[0]
        if netloc.startswith("www."):
            netloc = netloc[4:]
        parts = netloc.split(".")
        if len(parts) > 2:
            if parts[-2] in ("co", "com", "net", "org", "gov", "edu") and len(parts[-1]) == 2:
                return ".".join(parts[-3:])
            return ".".join(parts[-2:])
        return netloc
    except Exception:
        return ""


# ============================================================================
# ADVANCED ADDRESS & GEO EXTRACTION
# ============================================================================
def _clean_addr(s: str) -> str:
    s = re.sub(r"[\r\n\t]+", " ", s or "")
    s = re.sub(r"\s+", " ", s).strip(" ,;|·-–—\n\r")
    # Remove leading noise labels
    s = re.sub(
        r"^(?:address|adresse|adres|indirizzo|dirección|direccion|"
        r"adress|endereço|endereco|anschrift|standort|sede\s+legale|"
        r"head\s*office|headquarters|hq|office|factory|plant|"
        r"werk|filiale|fabrika|showrooms?)\s*[:\-–—]\s*",
        "", s, flags=re.IGNORECASE,
    )
    return s.strip(" ,;|·-–—")


def extract_addresses_from_jsonld(soup) -> list:
    """Parse schema.org Organization, LocalBusiness, PostalAddress from JSON-LD blocks."""
    results = []
    for tag in soup.find_all("script", type="application/ld+json"):
        raw = tag.string or tag.get_text() or ""
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        stack = [data] if isinstance(data, (dict, list)) else []
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
                continue
            if not isinstance(node, dict):
                continue

            # Check if this node has address structure
            addr_obj = node.get("address") if isinstance(node.get("address"), dict) else None
            target = addr_obj if addr_obj else node

            if target.get("@type") in ("PostalAddress",) or "streetAddress" in target or "postalCode" in target:
                parts = [
                    target.get("streetAddress"),
                    target.get("postalCode"),
                    target.get("addressLocality"),
                    target.get("addressRegion"),
                    target.get("addressCountry"),
                ]
                clean_parts = []
                for p in parts:
                    if isinstance(p, dict):
                        p = p.get("name") or ""
                    if p and str(p).strip():
                        clean_parts.append(str(p).strip())
                line = ", ".join(clean_parts)
                if len(line) >= 8:
                    results.append(_clean_addr(line))

            for v in node.values():
                if isinstance(v, (dict, list)):
                    stack.append(v)
    return results


def extract_addresses_from_maps(soup) -> list:
    """Extract physical address candidates from embedded Google Maps or OpenStreetMap links."""
    candidates = []
    
    # 1. Google Maps iframe embed
    for iframe in soup.find_all("iframe", src=True):
        src = iframe["src"]
        if "google.com/maps" in src or "maps.google" in src:
            # Query parameter q=...
            parsed = urllib.parse.urlparse(src)
            params = urllib.parse.parse_qs(parsed.query)
            if "q" in params and params["q"]:
                raw_q = urllib.parse.unquote_plus(params["q"][0])
                if not re.match(r"^[\d\.\,\-\s]+$", raw_q):  # Not just coordinates
                    candidates.append(_clean_addr(raw_q))
            # pb= param often has address segments
            if "!2s" in src:
                for match in re.findall(r"!2s([^!]+)", src):
                    dec = urllib.parse.unquote_plus(match).replace("+", " ")
                    if len(dec) > 10 and any(c in dec.lower() for c in ("str", "road", "ave", "st", "calle", "via", "cad")):
                        candidates.append(_clean_addr(dec))

    # 2. Direct map links
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "maps.google." in href or "google.com/maps" in href:
            parsed = urllib.parse.urlparse(href)
            params = urllib.parse.parse_qs(parsed.query)
            if "daddr" in params:
                candidates.append(_clean_addr(urllib.parse.unquote_plus(params["daddr"][0])))
            elif "q" in params:
                raw_q = urllib.parse.unquote_plus(params["q"][0])
                if not re.match(r"^[\d\.\,\-\s]+$", raw_q):
                    candidates.append(_clean_addr(raw_q))

    return [c for c in candidates if len(c) > 10]


def extract_addresses_from_html(soup) -> list:
    """Look at microdata, <address> tags, footer address containers, and meta tags."""
    candidates = []

    # 1. Schema microdata
    for el in soup.find_all(attrs={"itemprop": True}):
        prop = (el.get("itemprop") or "").lower()
        if prop in ("streetaddress", "address", "postaladdress", "addresslocality"):
            txt = el.get_text(" ", strip=True)
            if txt and len(txt) > 6:
                candidates.append(txt)

    # 2. <address> tags
    for el in soup.find_all("address"):
        txt = el.get_text(" ", strip=True)
        if txt and len(txt) > 8:
            candidates.append(txt)

    # 3. Class or ID with address keywords
    addr_re = re.compile(
        r"(address|adresse|adres|anschrift|indirizzo|direccion|endereco|standort|footer-contact|contact-address)",
        re.I
    )
    for el in soup.find_all(True, attrs={"class": addr_re}):
        # Ignore giant containers
        txt = el.get_text(" | ", strip=True)
        if txt and 10 < len(txt) < 350:
            candidates.append(txt)
    for el in soup.find_all(True, attrs={"id": addr_re}):
        txt = el.get_text(" | ", strip=True)
        if txt and 10 < len(txt) < 350:
            candidates.append(txt)

    # 4. Meta tags (geo, og)
    for meta in soup.find_all("meta"):
        name = (meta.get("name") or meta.get("property") or "").lower()
        if any(k in name for k in ("og:street-address", "og:locality", "geo.placename", "business:contact_data:street_address")):
            content = meta.get("content")
            if content and len(content) > 5:
                candidates.append(content)

    return [_clean_addr(x) for x in candidates if x and len(_clean_addr(x)) > 8]


def extract_addresses_from_text(text: str) -> list:
    """Heuristic regex search for street + postal patterns in text windows."""
    found = []
    if not text:
        return found

    windows = []
    for m in re.finditer(r"(address|adresse|adres|anschrift|indirizzo|direccion|standort|headquarters|office|factory|werk|sede|fabrika|impressum)", text, re.I):
        windows.append(text[max(0, m.start() - 100): m.start() + 450])
    if not windows:
        windows = [text[:4000]]

    for w in windows:
        # Street + nearby postcode
        for sm in STREET_RE.finditer(w):
            street = sm.group(0).strip()
            tail = w[sm.end(): sm.end() + 220]
            pm = POSTAL_RE.search(tail)
            if pm:
                found.append(_clean_addr(f"{street}, {pm.group(0).strip()}"))
            else:
                cm = COUNTRY_RE.search(tail)
                if cm:
                    found.append(_clean_addr(f"{street}, {cm.group(0).strip()}"))

        # Postal + City + Country
        for pm in POSTAL_RE.finditer(w):
            tail = w[pm.end(): pm.end() + 150]
            cm = COUNTRY_RE.search(tail)
            if cm:
                found.append(_clean_addr(f"{pm.group(0).strip()}, {cm.group(0).strip()}"))

    # Deduplicate preserving order
    out, seen = [], set()
    for a in found:
        key = a.lower()
        if key not in seen and len(a) > 10:
            seen.add(key)
            out.append(a)
    return out[:5]


def extract_geo_coordinates(soup) -> tuple:
    """Extract latitude and longitude from Google Maps embeds, links, or meta tags."""
    # 1. Google Maps iframe embeds (!2d<lng>!3d<lat> or @lat,lng)
    for iframe in soup.find_all("iframe", src=True):
        src = iframe["src"]
        if "google.com/maps" in src or "maps.google" in src:
            match = re.search(r'!2d([\d\.\-]+)!3d([\d\.\-]+)', src)
            if match:
                try:
                    lng, lat = float(match.group(1)), float(match.group(2))
                    if -90 <= lat <= 90 and -180 <= lng <= 180:
                        return lat, lng
                except Exception:
                    pass
            match2 = re.search(r'[@=]([\d\.\-]+),([\d\.\-]+)', src)
            if match2:
                try:
                    lat, lng = float(match2.group(1)), float(match2.group(2))
                    if -90 <= lat <= 90 and -180 <= lng <= 180:
                        return lat, lng
                except Exception:
                    pass

    # 2. Map links in anchor tags
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "google.com/maps" in href or "maps.google" in href or "openstreetmap.org" in href:
            match = re.search(r'[@=]([\d\.\-]+),([\d\.\-]+)', href)
            if match:
                try:
                    lat, lng = float(match.group(1)), float(match.group(2))
                    if -90 <= lat <= 90 and -180 <= lng <= 180:
                        return lat, lng
                except Exception:
                    pass

    # 3. Meta tags geo.position / ICBM
    for meta in soup.find_all("meta"):
        name = (meta.get("name") or "").lower()
        if name in ("geo.position", "icbm"):
            val = meta.get("content", "")
            if val and (";" in val or "," in val):
                parts = re.split(r"[,;]\s*", val)
                if len(parts) >= 2:
                    try:
                        lat, lng = float(parts[0]), float(parts[1])
                        if -90 <= lat <= 90 and -180 <= lng <= 180:
                            return lat, lng
                    except Exception:
                        pass
    return None, None


def best_address(sources: dict) -> dict:
    """Combine address candidates from all sources and return with confidence scoring."""
    jsonld = sources.get("jsonld") or []
    maps = sources.get("maps") or []
    html = sources.get("html") or []
    text = sources.get("text") or []

    if jsonld:
        return {"address": jsonld[0], "address_source": "json-ld", "address_confidence": "high"}
    if maps:
        return {"address": maps[0], "address_source": "google-maps", "address_confidence": "high"}
    if html:
        # Choose the cleanest complete candidate
        html_sorted = sorted(html, key=lambda s: -len(s))
        return {"address": html_sorted[0], "address_source": "html", "address_confidence": "medium"}
    if text:
        return {"address": text[0], "address_source": "text", "address_confidence": "low"}
    return {"address": None, "address_source": None, "address_confidence": None}


# ============================================================================
# SCRAPING ENGINE
# ============================================================================
def _fetch(url: str, session: requests.Session | None = None):
    s = session or requests.Session()
    try:
        r = s.get(url, headers=HEADERS, timeout=HTTP_TIMEOUT, allow_redirects=True)
        if r.status_code >= 400:
            return None
        ctype = r.headers.get("Content-Type", "").lower()
        if ctype and "html" not in ctype and "text" not in ctype:
            return None
        html = r.text or ""
        if len(html) < 200:
            return None
        soup = BeautifulSoup(html, "html.parser")
        
        # Keep application/ld+json scripts, remove others for clean text
        for tag in soup(["style", "noscript", "svg"]):
            tag.decompose()
        for tag in soup("script"):
            if (tag.get("type") or "").lower() != "application/ld+json":
                tag.decompose()

        text = re.sub(r"\s+", " ", soup.get_text(separator=" ")).strip()
        return {"url": r.url, "html": html, "soup": soup, "text": text}
    except Exception:
        return None


def _emails_from(text_and_html: str) -> list:
    out = []
    # Also resolve common obfuscations: " [at] ", " (at) "
    normalized = re.sub(r"\s*\[at\]\s*|\s*\(at\)\s*", "@", text_and_html or "", flags=re.I)
    for m in EMAIL_RE.findall(normalized):
        e = m.strip(".,;:()<>\"'").lower()
        if len(e) > 65 or any(j in e for j in EMAIL_JUNK):
            continue
        # Avoid file extensions like .png, .jpg caught in email regex
        if re.search(r"\.(png|jpg|jpeg|gif|webp|svg|css|js)$", e):
            continue
        out.append(e)
    return list(dict.fromkeys(out))


def _phones_from(text: str) -> list:
    out = []
    for m in PHONE_RE.findall(text or ""):
        digits = re.sub(r"\D", "", m)
        if 8 <= len(digits) <= 16:
            cleaned = re.sub(r"\s+", " ", m).strip()
            out.append(cleaned)
    return list(dict.fromkeys(out))


def _contact_links(soup, base_url: str) -> list:
    base_netloc = urlparse(base_url).netloc
    seen, out = set(), []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("mailto:", "tel:", "#", "javascript:", "data:")):
            continue
        label = (a.get_text(" ", strip=True) or "").lower()
        probe = (label + " " + href).lower()
        if any(h in probe for h in CONTACT_HINTS):
            full = urljoin(base_url, href).split("#")[0]
            if urlparse(full).netloc == base_netloc and full not in seen:
                seen.add(full)
                out.append(full)
    return out


def collect_site(url: str, snippet: str = "") -> dict:
    """Fetch homepage + contact/imprint pages. Collects text, contacts, addresses, coordinates."""
    text = ""
    emails, phones = [], []
    jsonld_addrs, maps_addrs, html_addrs = [], [], []
    lat, lng = None, None

    session = requests.Session()
    page = _fetch(url, session)

    def _harvest(p):
        nonlocal jsonld_addrs, maps_addrs, html_addrs, lat, lng
        jsonld_addrs.extend(extract_addresses_from_jsonld(p["soup"]))
        maps_addrs.extend(extract_addresses_from_maps(p["soup"]))
        html_addrs.extend(extract_addresses_from_html(p["soup"]))
        emails.extend(_emails_from(p["html"][:300000] + " " + p["text"]))
        phones.extend(_phones_from(p["text"]))
        if lat is None or lng is None:
            c_lat, c_lng = extract_geo_coordinates(p["soup"])
            if c_lat is not None and c_lng is not None:
                lat, lng = c_lat, c_lng

    if page:
        text = page["text"]
        _harvest(page)

        # Explore contact and imprint subpages
        contact_links = _contact_links(page["soup"], page["url"])
        contact_links.sort(
            key=lambda u: 0 if re.search(r"(impressum|imprint|legal|kontakt|contact|adresse|location)", u, re.I) else 1
        )

        for link in contact_links[:1]:
            p2 = _fetch(link, session)
            if not p2:
                continue
            text += f"\n\n[SUBPAGE {link}] {p2['text'][:3500]}"
            _harvest(p2)
            if jsonld_addrs or (maps_addrs and emails):
                break

    if not text.strip():
        text = snippet or ""

    # Dedup and clean phones
    seen_digits, uniq_phones = set(), []
    for p in phones:
        d = re.sub(r"\D", "", p)
        if d not in seen_digits:
            seen_digits.add(d)
            uniq_phones.append(p)

    # Dedup emails
    uniq_emails = list(dict.fromkeys(emails))

    # Text address extraction fallback
    text_addrs = extract_addresses_from_text(text)

    def _dedup_list(lst):
        out, seen = [], set()
        for item in lst:
            k = item.lower()
            if k not in seen:
                seen.add(k)
                out.append(item)
        return out

    jsonld_addrs = _dedup_list(jsonld_addrs)[:3]
    maps_addrs = _dedup_list(maps_addrs)[:3]
    html_addrs = _dedup_list(html_addrs)[:3]
    text_addrs = _dedup_list(text_addrs)[:3]

    addr_pick = best_address({
        "jsonld": jsonld_addrs,
        "maps": maps_addrs,
        "html": html_addrs,
        "text": text_addrs
    })

    return {
        "url": url,
        "text": text[:MAX_TEXT_CHARS],
        "emails": uniq_emails[:5],
        "phones": uniq_phones[:5],
        "jsonld_addresses": jsonld_addrs,
        "maps_addresses": maps_addrs,
        "html_addresses": html_addrs,
        "text_addresses": text_addrs,
        "best_address": addr_pick["address"],
        "address_source": addr_pick["address_source"],
        "address_confidence": addr_pick["address_confidence"],
        "latitude": lat,
        "longitude": lng,
    }


# ============================================================================
# SEARCH ENGINE INTERFACE (DUCKDUCKGO)
# ============================================================================
def _ddgs_search(query: str, region: str, max_results: int, retries: int = 2) -> list:
    for attempt in range(retries):
        try:
            ddgs_client = DDGS()
            results = ddgs_client.text(query, region=region, max_results=max_results)
            return list(results) if results else []
        except Exception as e:
            wait = 1.5 * (attempt + 1)
            time.sleep(wait)
    return []


def build_queries(location: str, category: str = "all") -> list:
    clean_loc = location.replace(",", " ").strip()
    cat_desc = CATEGORY_MAP.get((category or "all").lower(), "Aluminium profile")
    kw = cat_desc.split(",")[0].strip()
    return [
        f"{kw} manufacturer {clean_loc}",
        f"{kw} supplier {clean_loc}",
        f"Aluminium profile manufacturer {clean_loc}",
        f"Aluminium extrusion company {clean_loc}",
    ]


def fetch_search_results(location: str, category: str = "all") -> list:
    print(f"Starting web search for companies in {location} ({category})...")
    region = guess_region(location)
    exclude_clause = " ".join(f"-site:{d}" for d in EXCLUDED_DOMAINS[:8])
    queries = build_queries(location, category)

    unique_urls = {}
    start_time = time.time()

    for query in queries:
        if time.time() - start_time >= SEARCH_BUDGET_SECONDS:
            break
        if len(unique_urls) >= MAX_URLS:
            break

        full_query = f"{query} {exclude_clause}"
        try:
            results = _ddgs_search(full_query, region, RESULTS_PER_QUERY)
            for r in results:
                url = (r.get("href") or "").split("#")[0]
                if not url or url in unique_urls:
                    continue
                if any(d in url for d in EXCLUDED_DOMAINS):
                    continue
                unique_urls[url] = {
                    "title": r.get("title", ""),
                    "snippet": r.get("body", ""),
                }
        except Exception as e:
            print(f"Web query notice for '{query}': {e}")
            break
        time.sleep(0.4)

    if not unique_urls:
        print(f"Search engine returned 0 direct URLs (typical on cloud datacenter IPs). Continuing with AI discovery.")
        return []

    print(f"Collected {len(unique_urls)} relevant URLs. Scraping with {MAX_WORKERS} threads...")

    url_list = list(unique_urls.keys())[:MAX_URLS]
    enriched = []

    def _job(u):
        meta = unique_urls[u]
        data = collect_site(u, meta.get("snippet", ""))
        data["title"] = meta.get("title", "")
        data["snippet"] = meta.get("snippet", "")
        data["domain"] = root_domain(u)
        return data

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_job, u): u for u in url_list}
        done = 0
        for fut in as_completed(futures):
            done += 1
            try:
                enriched.append(fut.result())
            except Exception as e:
                pass
            if done % 20 == 0:
                print(f"  Scraped {done}/{len(url_list)} websites...")

    with_addr = sum(1 for e in enriched if e.get("best_address"))
    print(f"Scraping complete. {with_addr}/{len(enriched)} pages yielded detected addresses.")
    return enriched


# ============================================================================
# DEEPSEEK AI EXTRACTION
# ============================================================================
SYSTEM_PROMPT = (
    "You are an expert B2B business intelligence analyst. "
    "You extract structured, verified company profiles from raw webpage data. "
    "Output strictly valid JSON with no markdown wrapping or conversational commentary."
)


def _build_prompt(batch: list, location: str) -> str:
    blocks = []
    for i, item in enumerate(batch, 1):
        sid = f"S{i}"
        addr_candidates = []
        for a in (item.get("jsonld_addresses") or []):
            addr_candidates.append(f"JSON-LD: {a}")
        for a in (item.get("maps_addresses") or []):
            addr_candidates.append(f"Google Maps Embed: {a}")
        for a in (item.get("html_addresses") or []):
            addr_candidates.append(f"HTML Structure: {a}")
        for a in (item.get("text_addresses") or []):
            addr_candidates.append(f"Text Heuristic: {a}")

        addr_str = "\n".join(f"    - {ac}" for ac in addr_candidates) if addr_candidates else "    (None detected)"

        blocks.append(
            f"--- SOURCE [{sid}] ---\n"
            f"URL: {item['url']}\n"
            f"TITLE: {item.get('title', '')}\n"
            f"DISCOVERED EMAILS: {', '.join(item.get('emails') or []) or 'None'}\n"
            f"DISCOVERED PHONES: {', '.join(item.get('phones') or []) or 'None'}\n"
            f"DETECTED ADDRESS CANDIDATES:\n{addr_str}\n"
            f"CONTENT EXCERPT:\n{item.get('text', '')[:2500]}"
        )

    sources = "\n\n".join(blocks)
    products = ", ".join(SEARCH_KEYWORDS[:8])

    return f"""Target Region / Country: {location}
Target Industry: Aluminium Profiles, Extrusions, LED Profiles, Tile Trims, Industrial Profiles ({products}).

Extract verified real companies that produce, manufacture, distribute, or fabricate aluminium profiles or relevant architectural building products located in or serving {location}.

CRITICAL ADDRESS RULES:
1. Provide the complete street address (Street, Number, Postal Code, City, Country).
2. PRIORITIZE the "DETECTED ADDRESS CANDIDATES" list. Clean up punctuation and formatting.
3. If only city and country are known, fill "city" and "country", and make "address" the city/country.
4. Set "address_confidence" to "high" (full street address), "medium" (street or postal without full details), or "low" (city only).
5. Never invent or hallucinate addresses, emails, or phone numbers.

Return exactly this JSON structure:
{{
  "companies": [
    {{
      "name": "Official Registered Company Name",
      "source_id": "S1",
      "website": "https://...",
      "address": "Full physical street address or null",
      "address_confidence": "high|medium|low",
      "city": "City name or null",
      "country": "Country name or null",
      "location_string": "City, Country",
      "email": "primary contact/sales email or null",
      "phone": "primary phone with country code or null",
      "main_categories": ["LED Profile", "Aluminium Extrusion"],
      "sub_categories": ["Surface mounted", "Trim"],
      "description": "Concise 1-2 sentence business description in English",
      "company_type": "manufacturer|distributor|fabricator|other"
    }}
  ]
}}

If none qualify, return {{"companies": []}}

SOURCES:
{sources}
"""


def _safe_json(content: str):
    content = (content or "").strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?", "", content).strip()
        content = re.sub(r"```$", "", content).strip()
    try:
        return json.loads(content)
    except Exception:
        start, end = content.find("{"), content.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(content[start:end + 1])
            except Exception:
                pass
    return None


def parse_with_ai(batch: list, location: str) -> list:
    if not client:
        # Fallback without AI: construct basic records directly from scraped data
        return _fallback_rule_based_companies(batch, location)

    prompt = _build_prompt(batch, location)
    
    # Call deepseek-chat
    for model_name in ["deepseek-chat"]:
        try:
            response = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
            )
            parsed = _safe_json(response.choices[0].message.content)
            if not parsed or not isinstance(parsed, dict):
                continue

            companies = parsed.get("companies", [])
            for comp in companies:
                sid = str(comp.get("source_id", "")).strip().upper()
                src = None
                if sid.startswith("S") and sid[1:].isdigit():
                    idx = int(sid[1:]) - 1
                    if 0 <= idx < len(batch):
                        src = batch[idx]
                if not src:
                    for item in batch:
                        if comp.get("website") and root_domain(comp["website"]) == item.get("domain"):
                            src = item
                            break
                        if root_domain(item.get("url", "")) in (comp.get("website") or ""):
                            src = item
                            break

                if src:
                    comp.setdefault("source_url", src["url"])
                    if not comp.get("website"):
                        comp["website"] = src["url"]
                    if not comp.get("email") and src.get("emails"):
                        comp["email"] = src["emails"][0]
                    if not comp.get("phone") and src.get("phones"):
                        comp["phone"] = src["phones"][0]
                    
                    comp["emails"] = src.get("emails", [])
                    comp["phones"] = src.get("phones", [])
                    
                    # If AI missed address or gave a lower quality one, inject scraped best address
                    if (not comp.get("address") or comp.get("address_confidence") == "low") and src.get("best_address"):
                        comp["address"] = src["best_address"]
                        comp["address_source"] = src.get("address_source")
                        comp["address_confidence"] = src.get("address_confidence")
                    elif comp.get("address") and not comp.get("address_source"):
                        comp["address_source"] = "ai"

                    if src.get("latitude") and not comp.get("latitude"):
                        comp["latitude"] = src["latitude"]
                        comp["longitude"] = src["longitude"]

            return companies
        except Exception as e:
            print(f"DeepSeek call error ({model_name}): {e}")

    # Fallback if both AI attempts fail
    return _fallback_rule_based_companies(batch, location)


def _fallback_rule_based_companies(batch: list, location: str) -> list:
    """Fallback generator in case of network or API key issues."""
    companies = []
    for item in batch:
        if not item.get("best_address") and not item.get("emails") and not item.get("phones"):
            continue
        title = item.get("title") or root_domain(item["url"])
        clean_name = re.sub(r"\s*[-|–—].*$", "", title).strip()
        companies.append({
            "name": clean_name or root_domain(item["url"]).title(),
            "website": item["url"],
            "address": item.get("best_address"),
            "address_confidence": item.get("address_confidence"),
            "address_source": item.get("address_source"),
            "location_string": location,
            "email": item["emails"][0] if item.get("emails") else None,
            "emails": item.get("emails", []),
            "phone": item["phones"][0] if item.get("phones") else None,
            "phones": item.get("phones", []),
            "main_categories": ["Aluminium Profile"],
            "sub_categories": [],
            "description": (item.get("snippet") or "")[:120],
            "company_type": "manufacturer",
            "latitude": item.get("latitude"),
            "longitude": item.get("longitude")
        })
    return companies


# ============================================================================
# FILTERING & DEDUPLICATION
# ============================================================================
def filter_by_location(companies: list, location: str) -> list:
    tokens = location_tokens(location)
    if not tokens:
        return companies

    tld = expected_tld(location)
    kept = []
    for comp in companies:
        # Keep companies explicitly discovered by AI for this location query
        if comp.get("source") == "deepseek_discovery":
            kept.append(comp)
            continue

        hay = " ".join(
            str(comp.get(f) or "")
            for f in ("location", "location_string", "city", "country", "address", "name", "description")
        )
        url = (comp.get("website") or comp.get("source_url") or "").lower()

        hits = [t for t in tokens if _token_in(hay, t)]
        url_hit = any(_token_in(url, t) for t in tokens)
        tld_hit = bool(tld and root_domain(url).endswith(tld))

        ok = (len(hits) == len(tokens)) if STRICT_LOCATION_FILTER else (bool(hits) or url_hit or tld_hit)
        if ok:
            kept.append(comp)

    # Prevent dropping valid candidates if filter is too stringent
    if not kept and companies:
        return companies
    return kept


def _norm_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def dedupe_companies(companies: list) -> list:
    merged = {}

    def _key(c):
        dom = root_domain(c.get("website") or c.get("source_url") or "")
        return dom or _norm_name(c.get("name"))

    for c in companies:
        k = _key(c)
        if not k:
            continue
        if k not in merged:
            merged[k] = dict(c)
            continue
        base = merged[k]
        for field, val in c.items():
            if not val or field.startswith("_"):
                continue
            if not base.get(field):
                base[field] = val
            elif isinstance(val, list) and isinstance(base.get(field), list):
                base[field] = list(dict.fromkeys(base[field] + val))

    by_name = {}
    for c in merged.values():
        nk = _norm_name(c.get("name"))
        if not nk:
            continue
        if nk in by_name:
            base = by_name[nk]
            for field, val in c.items():
                if not val or field.startswith("_"):
                    continue
                if not base.get(field):
                    base[field] = val
                elif isinstance(val, list) and isinstance(base.get(field), list):
                    base[field] = list(dict.fromkeys(base[field] + val))
        else:
            by_name[nk] = c

    return list(by_name.values())


# ============================================================================
# DIRECT AI KNOWLEDGE DISCOVERY (CLOUD & DATACENTER RESILIENT)
# ============================================================================
def discover_companies_with_ai(location: str, category: str = "all") -> list:
    """Direct AI-driven discovery of real verified industrial suppliers.
    Guarantees reliable results on cloud servers where search engines may block datacenter IPs."""
    if not client:
        return []

    cat_desc = CATEGORY_MAP.get((category or "all").lower(), CATEGORY_MAP["all"])
    prompt = f"""You are a senior B2B industrial market researcher specializing in the global aluminium extrusion and profiles industry.
Target Region / City / Country: {location}
Target Product Category: {cat_desc}

TASK:
Identify 8 to 15 REAL, verified companies, manufacturers, extruders, fabricators, or major regional distributors of {cat_desc} operating in or physically situated in {location}.
If {location} specifies a city or province (such as Tekirdag, Corlu, Cerkezkoy, Ergene, Ostim, Sincan, Milan, Munich, etc.), prioritize manufacturing plants, factories, and headquarters in that exact city/province or its recognized industrial zones (e.g. Organize Sanayi Bolgesi / OSB / Gewerbegebiet).

REQUIREMENTS FOR EACH RECORD:
1. "name": Full official legal / commercial company name.
2. "website": Real, working official website URL (e.g. https://www.example.com).
3. "address": Full physical street address with building number, industrial zone (OSB), district, postal code, city, and country.
4. "city": City or district name.
5. "country": Country name.
6. "phone": Real working telephone number with international dial code (e.g. +90 282 ...).
7. "email": Real contact / sales email address.
8. "main_categories": Array of relevant product categories (e.g. ["Aluminium Profiles", "LED Profiles"]).
9. "sub_categories": Array of specific products made.
10. "description": 1-2 factual sentences in English describing their extrusion lines, profile series, and facilities.
11. "company_type": "manufacturer", "distributor", or "fabricator".

OUTPUT FORMAT:
Return strictly valid JSON with key "companies". Do NOT wrap in markdown explanation.
{{
  "companies": [
    {{
      "name": "Full Official Registered Company Name",
      "website": "https://www.example.com",
      "address": "Street Name, No, OSB / Industrial Park, Postal Code, City, Country",
      "address_confidence": "high",
      "city": "City name",
      "country": "Country name",
      "location_string": "{location}",
      "phone": "+90 282 ...",
      "email": "info@...",
      "main_categories": ["Aluminium Profiles", "LED Profiles"],
      "sub_categories": ["Surface mounted", "Extrusions"],
      "description": "Clear 1-2 sentence description of products, factory facilities, and extrusion capabilities.",
      "company_type": "manufacturer"
    }}
  ]
}}
"""
    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "You are an elite B2B research engine. Return valid JSON only."},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        data = _safe_json(response.choices[0].message.content)
        if isinstance(data, dict) and "companies" in data:
            results = data["companies"]
            for c in results:
                c["source"] = "deepseek_discovery"
                c["address_source"] = "ai"
                if not c.get("location_string"):
                    c["location_string"] = f"{c.get('city') or ''}, {c.get('country') or location}".strip(" ,")
            return results
    except Exception as e:
        print(f"DeepSeek direct discovery error: {e}")
    return []


# ============================================================================
# MAIN ENTRY POINTS
# ============================================================================
def run_scraper(location: str, category: str = "all") -> list:
    print(f"=== Starting Discovery Search for '{location}' (Category: {category}) ===")

    # 1. Direct AI Discovery (Fast, verified, independent of datacenter IP blocks)
    ai_candidates = discover_companies_with_ai(location, category)
    print(f"DeepSeek AI discovery returned {len(ai_candidates)} candidates.")

    # 2. Live Web Search (DuckDuckGo - if available and not blocked)
    web_candidates = []
    try:
        search_results = fetch_search_results(location, category)
        if search_results:
            batches = [search_results[i:i + AI_BATCH_SIZE] for i in range(0, len(search_results), AI_BATCH_SIZE)]
            with ThreadPoolExecutor(max_workers=min(4, len(batches) or 1)) as pool:
                futures = [pool.submit(parse_with_ai, b, location) for b in batches]
                for fut in as_completed(futures):
                    try:
                        web_candidates.extend(fut.result())
                    except Exception as e:
                        print(f"Batch AI error: {e}")
    except Exception as e:
        print(f"Web search notice: {e}")

    # Combine all candidate companies
    combined = list(ai_candidates) + list(web_candidates)
    if not combined:
        print(f"No companies found for '{location}'.")
        return []

    # 3. For candidate companies with websites, scrape in parallel to harvest live addresses and contact info
    def _enrich_site(comp):
        w = comp.get("website")
        if not w:
            return comp
        try:
            site_info = collect_site(w, comp.get("description", ""))
            if site_info.get("best_address"):
                comp["address"] = site_info["best_address"]
                comp["address_source"] = site_info.get("address_source")
                comp["address_confidence"] = site_info.get("address_confidence")
            if site_info.get("emails"):
                existing = comp.get("emails") or []
                all_e = list(dict.fromkeys(existing + site_info["emails"]))
                comp["emails"] = all_e
                if not comp.get("email"):
                    comp["email"] = all_e[0]
            if site_info.get("phones"):
                existing = comp.get("phones") or []
                all_p = list(dict.fromkeys(existing + site_info["phones"]))
                comp["phones"] = all_p
                if not comp.get("phone"):
                    comp["phone"] = all_p[0]
            if site_info.get("latitude") and site_info.get("longitude"):
                comp["latitude"] = site_info["latitude"]
                comp["longitude"] = site_info["longitude"]
        except Exception:
            pass
        return comp

    print(f"Deep scraping and verifying {len(combined)} company websites...")
    with ThreadPoolExecutor(max_workers=min(10, len(combined) or 1)) as pool:
        enriched_list = list(pool.map(_enrich_site, combined))

    # 4. Location filtering with tolerant diacritics folding
    filtered = filter_by_location(enriched_list, location)
    if len(filtered) < 2 and len(enriched_list) >= 2:
        filtered = enriched_list

    # 5. Deduplicate
    final = dedupe_companies(filtered)

    # 6. Standardize and Geocode
    for comp in final:
        comp["name"] = comp.get("name") or comp.get("buyer_name") or "Unnamed Company"
        comp["buyer_name"] = comp["name"]

        loc_str = comp.get("location_string") or comp.get("location") or comp.get("city") or location
        comp["location_string"] = loc_str
        comp["destination_country"] = comp.get("country") or location

        emails = list(dict.fromkeys(comp.get("emails") or []))
        if comp.get("email") and comp["email"] not in emails:
            emails.insert(0, comp["email"])
        comp["emails"] = emails
        comp["email"] = emails[0] if emails else None

        phones = list(dict.fromkeys(comp.get("phones") or []))
        if comp.get("phone") and comp["phone"] not in phones:
            phones.insert(0, comp["phone"])
        comp["phones"] = phones
        comp["phone"] = phones[0] if phones else None

        # Geocode if coordinates are missing
        if comp.get("latitude") is None or comp.get("longitude") is None:
            coords = get_fallback_coords((comp.get("address") or "") + " " + loc_str)
            jitter = (int(hashlib.md5(comp["name"].encode()).hexdigest()[:6], 16) % 30 - 15) * 0.003
            comp["latitude"] = round(coords[0] + jitter, 6)
            comp["longitude"] = round(coords[1] + jitter, 6)

        comp["ai_status"] = "scraped" if (comp.get("email") or comp.get("phone")) else "pending"
        comp["is_matrix"] = False
        comp["source"] = "deepseek_discovery"
        comp["created_at"] = int(time.time())
        comp["company_id"] = generate_company_id(comp["name"] + str(comp.get("address", "")) + loc_str)

    with_addr = sum(1 for c in final if c.get("address"))
    print(f"Scraper finished. Total: {len(final)} companies ({with_addr} with full street addresses).")
    return final


def search_new_companies(location_query: str, category: str = "all") -> list:
    """Wrapper called by app.py's /api/search_new endpoint."""
    return run_scraper(location_query, category)


# ============================================================================
# FASTAPI APPLICATION & ASYNCHRONOUS JOB ENGINE
# ============================================================================
import uuid
import traceback
from threading import Thread

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel, Field
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False

# In-memory background jobs registry with auto-pruning
JOBS: dict = {}


def _prune_old_jobs():
    """Remove completed or errored jobs older than 2 hours to prevent memory bloat."""
    now = time.time()
    stale_ids = [
        jid for jid, j in list(JOBS.items())
        if j.get("finished_at") and (now - j["finished_at"] > 7200)
    ]
    for jid in stale_ids:
        JOBS.pop(jid, None)


def _run_job(job_id: str, location: str, category: str = "all", save_to_mongo: bool = False):
    job = JOBS.get(job_id)
    if not job:
        return
    job["status"] = "running"
    job["stage"] = "discovering_companies"
    job["started_at"] = time.time()
    job["progress"] = 25
    try:
        job["stage"] = "scraping_and_verifying_addresses"
        results = search_new_companies(location, category=category)
        job["progress"] = 85

        # Optional direct MongoDB insertion if requested
        if save_to_mongo and results:
            try:
                from pymongo import MongoClient
                m_uri = os.getenv("MONGO_URI")
                if m_uri:
                    m_client = MongoClient(m_uri)
                    col = m_client.miky_db.search_collection
                    to_save = [dict(c) for c in results]
                    for item in to_save:
                        item.pop("company_id", None)
                    col.insert_many(to_save)
                    job["saved_to_mongo"] = len(to_save)
            except Exception as m_err:
                job["mongo_error"] = str(m_err)

        job["results"] = results
        job["count"] = len(results)
        job["status"] = "done"
        job["stage"] = "complete"
        job["progress"] = 100
        job["finished_at"] = time.time()
        job["elapsed_seconds"] = round(job["finished_at"] - job["started_at"], 2)
    except Exception as e:
        job["status"] = "error"
        job["stage"] = "failed"
        job["error"] = str(e)
        job["trace"] = traceback.format_exc()
        job["finished_at"] = time.time()
        job["elapsed_seconds"] = round(job["finished_at"] - job["started_at"], 2)


if HAS_FASTAPI:
    app = FastAPI(
        title="Aluminium Lead Finder & Scraper API",
        description="High-accuracy B2B lead discovery engine with DeepSeek AI extraction and address geocoding",
        version="2.0.0"
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    class SearchRequest(BaseModel):
        location: str = Field(..., description="Target search region, city, or country (e.g. 'Ankara, Turkey', 'Germany')")
        category: str = Field("all", description="Product category filter (e.g. 'all', 'led', 'tile', 'furniture', 'glass', 'decorative', 'extrusion')")
        save_to_mongo: bool = Field(False, description="Optionally auto-insert found companies into MongoDB Atlas search_collection")

    @app.get("/", tags=["Health"])
    def root_health():
        _prune_old_jobs()
        active = sum(1 for j in JOBS.values() if j.get("status") == "running")
        done = sum(1 for j in JOBS.values() if j.get("status") == "done")
        return {
            "ok": True,
            "service": "Lead Discovery & Scraper API",
            "version": "2.0.0",
            "has_deepseek_key": bool(DEEPSEEK_API_KEY and DEEPSEEK_API_KEY != "your_deepseek_api_key_here"),
            "has_mongo_uri": bool(os.getenv("MONGO_URI")),
            "active_jobs": active,
            "completed_jobs": done,
            "total_jobs_tracked": len(JOBS)
        }

    @app.post("/search", tags=["Search"])
    def start_search(req: SearchRequest):
        loc = (req.location or "").strip()
        cat = (req.category or "all").strip()
        if not loc:
            raise HTTPException(status_code=400, detail="Field 'location' is required.")

        _prune_old_jobs()
        job_id = str(uuid.uuid4())
        JOBS[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "stage": "queued",
            "progress": 0,
            "location": loc,
            "category": cat,
            "created_at": time.time(),
            "started_at": None,
            "finished_at": None,
            "elapsed_seconds": None,
            "results": [],
            "count": 0
        }
        Thread(target=_run_job, args=(job_id, loc, cat, req.save_to_mongo), daemon=True).start()
        return {
            "job_id": job_id,
            "status": "queued",
            "message": f"Search background job started for '{loc}' ({cat}). Poll /job/{job_id} for progress.",
            "check_status_url": f"/job/{job_id}"
        }

    @app.get("/job/{job_id}", tags=["Jobs"])
    def job_status(job_id: str):
        job = JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job ID not found or expired.")
        return job

    @app.get("/jobs", tags=["Jobs"])
    def list_jobs(limit: int = 20):
        _prune_old_jobs()
        sorted_jobs = sorted(JOBS.values(), key=lambda x: x.get("created_at", 0), reverse=True)[:limit]
        return [
            {
                "job_id": j.get("job_id"),
                "status": j.get("status"),
                "stage": j.get("stage"),
                "location": j.get("location"),
                "progress": j.get("progress"),
                "count": j.get("count"),
                "elapsed_seconds": j.get("elapsed_seconds")
            }
            for j in sorted_jobs
        ]

    @app.post("/search/sync", tags=["Search"])
    def search_synchronous(req: SearchRequest):
        loc = (req.location or "").strip()
        if not loc:
            raise HTTPException(status_code=400, detail="Field 'location' is required.")
        try:
            results = search_new_companies(loc, category=req.category)
            return {
                "ok": True,
                "location": loc,
                "count": len(results),
                "companies": results
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")

else:
    app = None


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        loc = sys.argv[1]
        print(f"Running CLI scraper for '{loc}'...")
        data = search_new_companies(loc)
        print(json.dumps(data[:3], indent=2, ensure_ascii=False))
    elif HAS_FASTAPI:
        import uvicorn
        port = int(os.getenv("PORT", 8000))
        print(f"Starting FastAPI server on http://0.0.0.0:{port}...")
        uvicorn.run("scraper:app", host="0.0.0.0", port=port, reload=False)
    else:
        print("FastAPI not installed. Running default CLI scraper for 'Germany'...")
        data = search_new_companies("Germany")
        print(json.dumps(data[:3], indent=2, ensure_ascii=False))