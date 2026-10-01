"""
Aluminium profile company scraper (v2).

Pipeline:
  1. (optional) AI suggests company NAMES only (never trusted for details)
  2. DuckDuckGo finds candidate websites
  3. Each website (homepage + contact page) is scraped
  4. AI extracts facts ONLY from the scraped text
  5. Address is geocoded with Nominatim (OpenStreetMap)

Entry points used by app.py:
  search_new_companies(location_query, category="all")
  run_scraper(location, category="all")
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from ddgs import DDGS
from openai import OpenAI

load_dotenv()

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
NOMINATIM_UA = os.getenv("NOMINATIM_UA", "aluminium-scraper/2.0 (contact: you@example.com)")

MAX_CANDIDATE_SITES = int(os.getenv("MAX_CANDIDATE_SITES", "25"))
USE_AI_NAME_SUGGESTIONS = os.getenv("USE_AI_NAME_SUGGESTIONS", "1") == "1"
MAX_AI_NAMES = 10

client = None
if DEEPSEEK_API_KEY and DEEPSEEK_API_KEY != "your_deepseek_api_key_here":
    client = OpenAI(
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
        timeout=60,
        max_retries=2,
    )

SEARCH_KEYWORDS = [
    "LED Profile", "LED Aluminium Profile", "LED Strip Profile",
    "Tile Trim Profile", "Tile Edge Profile", "Ceramic Tile Trim",
    "Furniture Profiles", "Cabinet/Kitchen Profiles",
    "Glass Profiles", "Shower & Glass Profiles",
    "Decorative Profiles", "Wall & Ceiling Profiles",
    "Small/Light Aluminium Extrusion",
]

# Root domains to ignore (directories, marketplaces, social sites)
EXCLUDED_DOMAINS = {
    "alibaba.com", "made-in-china.com", "globalsources.com",
    "indiamart.com", "exportersindia.com", "tradeindia.com",
    "ec21.com", "europages.com", "europages.co.uk", "kompass.com",
    "wlw.de", "yellowpages.com", "linkedin.com", "facebook.com",
    "youtube.com", "pinterest.com", "instagram.com", "twitter.com",
    "x.com", "wikipedia.org", "amazon.com", "ebay.com", "tiktok.com",
    "reddit.com", "quora.com", "aliexpress.com", "dhgate.com",
}

COUNTRY_REGION_MAP = {
    "argentina": "ar-es", "australia": "au-en", "austria": "at-de",
    "belgium": "be-nl", "brazil": "br-pt", "bulgaria": "bg-bg",
    "canada": "ca-en", "chile": "cl-es", "china": "cn-zh",
    "colombia": "co-es", "croatia": "hr-hr", "czech republic": "cz-cs",
    "czechia": "cz-cs", "denmark": "dk-da", "estonia": "ee-et",
    "finland": "fi-fi", "france": "fr-fr", "germany": "de-de",
    "greece": "gr-el", "hong kong": "hk-tzh", "hungary": "hu-hu",
    "india": "in-en", "indonesia": "id-id", "ireland": "ie-en",
    "israel": "il-he", "italy": "it-it", "japan": "jp-jp",
    "south korea": "kr-kr", "korea": "kr-kr", "latvia": "lv-lv",
    "lithuania": "lt-lt", "malaysia": "my-ms", "mexico": "mx-es",
    "netherlands": "nl-nl", "holland": "nl-nl", "new zealand": "nz-en",
    "norway": "no-no", "peru": "pe-es", "philippines": "ph-en",
    "poland": "pl-pl", "portugal": "pt-pt", "romania": "ro-ro",
    "russia": "ru-ru", "singapore": "sg-en", "slovakia": "sk-sk",
    "slovak republic": "sk-sk", "slovenia": "sl-sl", "south africa": "za-en",
    "spain": "es-es", "sweden": "se-sv", "switzerland": "ch-de",
    "taiwan": "tw-tzh", "thailand": "th-th", "turkey": "tr-tr",
    "türkiye": "tr-tr", "turkiye": "tr-tr", "ukraine": "ua-uk",
    "united kingdom": "uk-en", "uk": "uk-en", "britain": "uk-en",
    "great britain": "uk-en", "united states": "us-en", "usa": "us-en",
    "u.s.": "us-en", "u.s.a.": "us-en", "united states of america": "us-en",
    "venezuela": "ve-es", "vietnam": "vn-vi",
    # Arab League & others
    "algeria": "xa-en", "egypt": "xa-en", "iraq": "xa-en",
    "jordan": "xa-en", "kuwait": "xa-en", "lebanon": "xa-en",
    "morocco": "xa-en", "oman": "xa-en", "palestine": "xa-en",
    "qatar": "xa-en", "saudi arabia": "xa-en", "ksa": "xa-en",
    "syria": "xa-en", "tunisia": "xa-en", "united arab emirates": "xa-en",
    "uae": "xa-en", "dubai": "xa-en", "yemen": "xa-en",
}

UA_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en,*;q=0.5",
}

EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
CONTACT_HINT = re.compile(
    r"contact|kontakt|iletisim|iletişim|impressum|about|hakkimizda|hakkımızda|"
    r"contacto|contatti|contatto|nous-contacter|o-nas|uber-uns|über-uns",
    re.I,
)
BAD_EMAIL_SUFFIX = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js")
BAD_EMAIL_PARTS = ("example.com", "sentry", "wixpress", "domain.com", "yourdomain", "email.com")


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _fold(s: str) -> str:
    """Lowercase and strip diacritics for tolerant matching."""
    s = (s or "").lower().replace("ı", "i")
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c))


def root_domain(url: str) -> str:
    """www.shop.example.co.uk -> example.co.uk"""
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


def guess_region(location: str) -> str:
    loc = _fold(location)
    for country in sorted(COUNTRY_REGION_MAP, key=len, reverse=True):
        if re.search(rf"\b{re.escape(_fold(country))}\b", loc):
            return COUNTRY_REGION_MAP[country]
    return "wt-wt"


def location_tokens(location: str) -> list:
    parts = re.split(r"[,/|]", location or "")
    return [_fold(p.strip()) for p in parts if p.strip()]


def generate_company_id(name: str) -> str:
    return hashlib.md5((name or "").strip().lower().encode("utf-8")).hexdigest()


def format_address(addr) -> str | None:
    """Clean a raw address string or dict into one line, or None."""
    if not addr:
        return None
    if isinstance(addr, dict):
        parts = []
        street = addr.get("street") or addr.get("street_address") or addr.get("line1")
        num = str(addr.get("number") or addr.get("building_number") or "").strip()
        if street and num and num not in str(street):
            parts.append(f"{street} {num}")
        elif street:
            parts.append(str(street))
        zone = addr.get("industrial_zone") or addr.get("osb") or addr.get("zone")
        if zone:
            parts.append(str(zone))
        postcode = str(addr.get("postal_code") or addr.get("zip") or "").strip()
        city = str(addr.get("city") or addr.get("town") or "").strip()
        if postcode and city:
            parts.append(f"{postcode} {city}")
        elif city:
            parts.append(city)
        country = str(addr.get("country") or "").strip()
        if country:
            parts.append(country)
        addr = ", ".join(p for p in parts if p)
    cleaned = re.sub(r"\s+", " ", str(addr)).strip(" ,;|·-–—\n\r")
    if len(cleaned) < 6 or cleaned.lower() in ("none", "null", "n/a", "not available"):
        return None
    return cleaned


def safe_json(content: str) -> dict:
    """Parse model output, tolerating code fences and surrounding text."""
    content = (content or "").strip()
    content = re.sub(r"^```(?:json)?|```$", "", content, flags=re.M).strip()
    if not content:
        return {}
    try:
        data = json.loads(content)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", content, re.S)
        if m:
            try:
                data = json.loads(m.group(0))
                return data if isinstance(data, dict) else {}
            except json.JSONDecodeError:
                return {}
        return {}


def _ai_json(system: str, user: str, max_tokens: int = 1000, temperature: float = 0) -> dict:
    if not client:
        return {}
    try:
        r = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return safe_json(r.choices[0].message.content)
    except Exception as e:
        print(f"AI error: {e}")
        return {}


# --------------------------------------------------------------------------
# Step 1 (optional): AI suggests company NAMES only
# --------------------------------------------------------------------------
def suggest_company_names(location: str, category: str) -> list:
    """Names and URLs. Every URL is later verified by scraping."""
    if not client:
        return []
    cat = "" if category in ("", "all", None) else f" Focus: {category}."
    data = _ai_json(
        "You are a B2B market researcher. Return only valid JSON.",
        f"List up to {MAX_AI_NAMES} well-known companies that manufacture or distribute "
        f"aluminium profiles (LED profiles, tile trims, furniture/kitchen profiles, "
        f"architectural extrusions) in {location}.{cat}\n"
        f"Only include companies you are confident exist. Provide their official website URLs.\n"
        f'Return JSON: {{"companies": [{{"name": "Company A", "url": "https://companya.com"}}]}}',
        max_tokens=600,
        temperature=0.2,
    )
    return data.get("companies") or []


# --------------------------------------------------------------------------
# Step 2: find candidate websites
# --------------------------------------------------------------------------
def _ddg(query: str, region: str, max_results: int = 10) -> list:
    try:
        with DDGS(timeout=10) as d:
            return d.text(query, region=region, max_results=max_results) or []
    except Exception as e:
        print(f"Search failed for '{query}': {e}")
        return []


def _accept_url(url: str, found: dict) -> None:
    if not url or url.lower().endswith(".pdf"):
        return
    dom = root_domain(url)
    if not dom or dom in EXCLUDED_DOMAINS or dom in found:
        return
    p = urlparse(url)
    found[dom] = f"{p.scheme or 'https'}://{p.netloc}"  # company homepage origin


def search_urls(location: str, category: str, extra_names: list) -> list:
    region = guess_region(location)
    loc = location.replace(",", " ").strip()
    found: dict = {}

    # (a) verify AI-suggested names via search and include AI-provided URLs
    for comp in extra_names:
        if isinstance(comp, dict) and comp.get("url"):
            _accept_url(comp["url"], found)
            
        name = comp.get("name") if isinstance(comp, dict) else str(comp)
        if name and name.strip():
            for r in _ddg(f"{name} {loc} official website", region, 3):
                _accept_url((r.get("href") or "").split("#")[0], found)
                if root_domain(r.get("href") or "") in found:
                    break
            time.sleep(0.7)

    # (b) keyword searches
    if category and category.lower() != "all":
        keywords = [category] + SEARCH_KEYWORDS[:3]
    else:
        keywords = SEARCH_KEYWORDS[:8]

    for kw in keywords:
        if len(found) >= MAX_CANDIDATE_SITES:
            break
        for r in _ddg(f"{kw} manufacturer {loc}", region, 10):
            _accept_url((r.get("href") or "").split("#")[0], found)
        time.sleep(0.7)

    return list(found.values())[:MAX_CANDIDATE_SITES]


# --------------------------------------------------------------------------
# Step 3: scrape each site (homepage + contact page)
# --------------------------------------------------------------------------
def _fetch(url: str, timeout: float = 7):
    try:
        r = requests.get(url, headers=UA_HEADERS, timeout=timeout)
        if r.status_code >= 400 or "html" not in r.headers.get("Content-Type", "html"):
            return None, None
        return BeautifulSoup(r.content, "html.parser"), r.url
    except Exception:
        return None, None


def _clean_emails(raw: set, site_domain: str) -> list:
    good = []
    for e in raw:
        e = e.strip(".,;:").lower()
        if e.endswith(BAD_EMAIL_SUFFIX) or any(b in e for b in BAD_EMAIL_PARTS):
            continue
        good.append(e)
    # company-domain emails first
    good.sort(key=lambda e: 0 if site_domain and e.split("@")[-1].endswith(site_domain) else 1)
    return list(dict.fromkeys(good))[:3]


def scrape_company_site(origin: str) -> dict:
    soup, final_url = _fetch(origin)
    if not soup:
        return {}

    pages = [soup]
    seen_links = {final_url}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        if CONTACT_HINT.search(href) or CONTACT_HINT.search(a.get_text(" ")):
            link = urljoin(final_url, href)
            if root_domain(link) != root_domain(final_url) or link in seen_links:
                continue
            seen_links.add(link)
            s2, _ = _fetch(link)
            if s2:
                pages.append(s2)
            if len(pages) >= 3:
                break

    texts, emails, phones = [], set(), []
    title = (soup.title.get_text(strip=True) if soup.title else "")[:200]
    for p in pages:
        for a in p.select("a[href^='mailto:']"):
            emails.add(a["href"][7:].split("?")[0])
        for a in p.select("a[href^='tel:']"):
            ph = a["href"][4:].strip()
            if ph and ph not in phones:
                phones.append(ph)
        for t in p(["script", "style", "noscript", "svg"]):
            t.decompose()
        txt = re.sub(r"\s+", " ", p.get_text(" "))
        texts.append(txt)
        emails.update(EMAIL_RE.findall(txt))

    return {
        "url": final_url,
        "title": title,
        "text": " ".join(texts)[:7000],
        "emails": _clean_emails(emails, root_domain(final_url)),
        "phones": phones[:3],
    }


# --------------------------------------------------------------------------
# Step 4: AI extracts only what the page says
# --------------------------------------------------------------------------
def extract_company(page: dict, location: str, category: str) -> dict | None:
    cat = "" if category in ("", "all", None) else f"Preferred category: {category}.\n"
    data = _ai_json(
        "You extract facts from website text. Return only valid JSON. Never invent data.",
        f"""Extract ONE company from this website text. Use ONLY facts in the text.
If a field is not in the text, use null. Do not guess.
Target area: {location}
{cat}
Return JSON:
{{"name": str|null,
  "address": str|null,
  "city": str|null,
  "country": str|null,
  "is_aluminium_profile_business": bool,
  "company_type": "manufacturer"|"distributor"|"fabricator"|"other",
  "main_categories": [str],
  "sub_categories": [str],
  "description": str}}

"is_aluminium_profile_business" is true only if the company makes or sells aluminium
profiles/extrusions (LED, tile trim, furniture, glass, architectural, etc).
"address" must be the physical street address printed on the page (street, postal code, city).

URL: {page['url']}
TITLE: {page['title']}
TEXT: {page['text']}""",
        max_tokens=800,
    )
    if not data.get("name") or not data.get("is_aluminium_profile_business"):
        return None

    data["website"] = page["url"]
    data["emails"] = page["emails"]
    data["phones"] = page["phones"]
    data["source"] = "web_verified"
    return data


def _in_target_location(comp: dict, location: str) -> bool:
    """Keep only companies whose page data matches the searched place."""
    tokens = location_tokens(location)
    if not tokens:
        return True
    hay = _fold(" ".join(str(comp.get(k) or "") for k in ("address", "city", "country")))
    if not hay.strip():
        return False
    return any(re.search(rf"\b{re.escape(t)}\b", hay) for t in tokens)


# --------------------------------------------------------------------------
# Step 5: geocoding (Nominatim, max 1 request/second, cached)
# --------------------------------------------------------------------------
_geo_cache: dict = {}


def geocode_nominatim(query: str):
    if not query:
        return None
    if query in _geo_cache:
        return _geo_cache[query]
    result = None
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": query, "format": "json", "limit": 1},
            headers={"User-Agent": NOMINATIM_UA},
            timeout=10,
        )
        if r.status_code == 200:
            j = r.json()
            if j:
                result = (float(j[0]["lat"]), float(j[0]["lon"]))
    except Exception as e:
        print(f"Geocode failed for '{query}': {e}")
    _geo_cache[query] = result
    time.sleep(1.1)
    return result


def geocode_company(comp: dict):
    """Try full address, then city + country."""
    tries = []
    if comp.get("address"):
        tries.append(comp["address"])
    if comp.get("city"):
        tries.append(f"{comp['city']}, {comp.get('country') or ''}".strip(", "))
    for q in tries:
        coords = geocode_nominatim(q)
        if coords:
            return coords
    return None


# --------------------------------------------------------------------------
# Main entry points
# --------------------------------------------------------------------------
def run_scraper(location: str, category: str = "all") -> list:
    print(f"=== Searching '{location}' (category: {category}) ===")

    names = suggest_company_names(location, category) if USE_AI_NAME_SUGGESTIONS else []
    if names:
        print(f"AI suggested {len(names)} names (will be verified on the web)")

    urls = search_urls(location, category, names)
    print(f"{len(urls)} candidate websites")
    if not urls:
        print("No websites found. DuckDuckGo may be blocking this IP; consider a paid search API.")
        return []

    with ThreadPoolExecutor(max_workers=6) as pool:
        pages = [p for p in pool.map(scrape_company_site, urls) if p and p.get("text")]
    print(f"{len(pages)} sites scraped")

    with ThreadPoolExecutor(max_workers=4) as pool:
        extracted = list(pool.map(lambda p: extract_company(p, location, category), pages))

    final, seen = [], set()
    for c in extracted:
        if not c:
            continue
        dom = root_domain(c["website"])
        if dom in seen:
            continue
        if not _in_target_location(c, location):
            continue
        seen.add(dom)

        c["address"] = format_address(c.get("address"))
        c["country"] = c.get("country") or location
        c["location_string"] = f"{c.get('city') or ''}, {c['country']}".strip(" ,")

        coords = geocode_company(c)
        c["latitude"], c["longitude"] = coords if coords else (None, None)

        c["email"] = c["emails"][0] if c["emails"] else None
        c["phone"] = c["phones"][0] if c["phones"] else None
        c["name"] = c["name"].strip()
        c["buyer_name"] = c["name"]
        c["destination_country"] = c["country"]
        c["main_categories"] = c.get("main_categories") or []
        c["sub_categories"] = c.get("sub_categories") or []
        c["ai_status"] = "scraped" if (c["email"] or c["phone"]) else "pending"
        c["is_matrix"] = False
        c["created_at"] = int(time.time())
        c["company_id"] = generate_company_id(c["name"] + str(c.get("address")) + c["location_string"])
        final.append(c)

    with_addr = sum(1 for c in final if c.get("address"))
    with_geo = sum(1 for c in final if c.get("latitude") is not None)
    print(f"Done: {len(final)} companies ({with_addr} with address, {with_geo} geocoded)")
    return final


def search_new_companies(location_query: str, category: str = "all") -> list:
    """Called by app.py /api/search_new endpoint."""
    return run_scraper(location_query, category=category)


if __name__ == "__main__":
    import sys

    loc = sys.argv[1] if len(sys.argv) > 1 else "Poland"
    print(f"Testing scraper for '{loc}'...")
    results = search_new_companies(loc)
    print(f"Total: {len(results)}")
    for r in results[:5]:
        print(r["name"], "|", r.get("website"), "|", r.get("address"), "|", r.get("latitude"), r.get("longitude"))
