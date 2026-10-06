"""
Aluminium profile BUYER scraper (v4).
Optimized for finding B2B customers (Furniture, LED, Construction, Facades).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from curl_cffi import requests as c_requests
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
SERPER_API_KEY = os.getenv("SERPER_API_KEY")
NOMINATIM_UA = os.getenv("NOMINATIM_UA", "aluminium-scraper/4.0 (contact: you@example.com)")

MAX_CANDIDATE_SITES = int(os.getenv("MAX_CANDIDATE_SITES", "30"))
SERPER_PAGES = int(os.getenv("SERPER_PAGES", "2"))        
SCRAPE_WORKERS = int(os.getenv("SCRAPE_WORKERS", "6"))    
AI_WORKERS = int(os.getenv("AI_WORKERS", "4"))
MAX_TEXT_CHARS = int(os.getenv("MAX_TEXT_CHARS", "12000"))

SERPER_URL = "https://google.serper.dev/search"

client = None
if DEEPSEEK_API_KEY and DEEPSEEK_API_KEY != "your_deepseek_api_key_here":
    client = OpenAI(
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
        timeout=60,
        max_retries=2,
    )

EXCLUDED_DOMAINS = {
    "alibaba.com", "made-in-china.com", "globalsources.com", "indiamart.com",
    "exportersindia.com", "tradeindia.com", "ec21.com", "europages.com",
    "kompass.com", "wlw.de", "yellowpages.com", "thomasnet.com", "dnb.com",
    "zoominfo.com", "crunchbase.com", "yelp.com", "yell.com", "cylex.com",
    "infobel.com", "hotfrog.com", "tradekey.com", "ecvv.com", "dhgate.com",
    "aliexpress.com", "amazon.com", "ebay.com", "olx.com",
    "linkedin.com", "facebook.com", "youtube.com", "pinterest.com",
    "instagram.com", "twitter.com", "x.com", "wikipedia.org", "tiktok.com",
    "reddit.com", "quora.com", "trustpilot.com", "medium.com", "scribd.com",
}
DIRECTORY_HINT = re.compile(
    r"yellowpages|directory|businesslist|firmenwissen|panoramafirm|pagesjaunes|"
    r"paginasamarillas|goldenpages|firmy\.|katalog|rehber", re.I
)

EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+")
PHONE_RE = re.compile(r"\+\d{1,3}[\s().-]*\d(?:[\s().-]*\d){6,12}")
CONTACT_HINT = re.compile(
    r"contact|kontakt|iletisim|iletişim|impressum|about|hakkimizda|hakkımızda|"
    r"contacto|contatti|contatto|nous-contacter|o-nas|uber-uns|über-uns|"
    r"اتصل|تواصل|من-نحن", re.I
)
CONTACT_FIRST = re.compile(r"contact|kontakt|iletisim|impressum|contacto|contatt", re.I)
GUESS_PATHS = ("/contact", "/contact-us", "/kontakt", "/iletisim", "/impressum", "/contacto")

BAD_EMAIL_SUFFIX = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js", ".woff", ".woff2")
BAD_EMAIL_PARTS = ("example.com", "sentry", "wixpress", "domain.com", "yourdomain",
                   "email.com", "u003e", "noreply", "no-reply")
PREFERRED_PREFIX = ("info", "sales", "sale", "export", "office", "contact", "mail",
                    "hello", "commercial", "enquiries", "inquiry", "purchasing")

REQ_HEADERS = {"Accept-Language": "en-US,en;q=0.9"}
IMPERSONATE = ["chrome120", "chrome110"]


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def root_domain(url: str) -> str:
    try:
        netloc = urlparse(url if "://" in url else "http://" + url).netloc.lower()
        netloc = netloc.split("@")[-1].split(":")[0]
        if netloc.startswith("www."):
            netloc = netloc[4:]
        parts = netloc.split(".")
        if len(parts) > 2 and parts[-2] in ("co", "com", "net", "org", "gov", "edu", "ac") and len(parts[-1]) == 2:
            return ".".join(parts[-3:])
        return ".".join(parts[-2:])
    except Exception:
        return ""

def generate_company_id(key: str) -> str:
    return hashlib.md5((key or "").strip().lower().encode("utf-8")).hexdigest()

def format_address(addr) -> str | None:
    if not addr or str(addr).lower() in ("none", "null", "n/a", ""):
        return None
    cleaned = re.sub(r"\s+", " ", str(addr)).strip(" ,;|·-–—\n\r")
    return cleaned if len(cleaned) > 5 else None

def safe_json(content: str) -> dict:
    content = (content or "").strip()
    content = re.sub(r"^```(?:json)?|```$", "", content, flags=re.M).strip()
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
                pass
    return {}

def _ai_json(system: str, user: str, max_tokens: int = 1000, attempts: int = 2) -> dict:
    if not client:
        return {}
    for i in range(attempts):
        try:
            r = client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                temperature=0.1,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            data = safe_json(r.choices[0].message.content)
            if data:
                return data
        except Exception as e:
            print(f"AI error (attempt {i + 1}): {e}")
            time.sleep(1.5 * (i + 1))
    return {}


# --------------------------------------------------------------------------
# Step 1: AI generates localized keywords (Targeting BUYERS)
# --------------------------------------------------------------------------
def _fallback_keywords(location: str, category: str) -> list:
    return [
        f"commercial furniture manufacturers {location}",
        f"LED lighting installers {location}",
        f"architectural facade contractors {location}",
        f"interior fit out companies {location}",
        f"custom kitchen cabinet makers {location}"
    ]

def generate_localized_keywords(location: str, category: str) -> dict:
    cat_text = f"specifically {category}" if category and category != "all" else "such as LED lighting companies, furniture makers, facade engineers, window/door installers, and interior fit-out firms"
    prompt = f"""Target location: {location}.
Generate 8 effective Google search queries to find companies that BUY and USE aluminium profiles ({cat_text}).
CRITICAL: We are looking for their CUSTOMERS (the buyers), NOT the aluminium extruders/manufacturers themselves.
Rules:
- At least 5 queries in the NATIVE/LOCAL language of the location, 2 in English.
- Focus on end-users: "interior contractors", "LED lighting manufacturers", "facade systems", "custom furniture makers", "glass partition installers".
- Include the location (city or country) in each query.
- Do NOT target directories, marketplaces or news.
Also return the ISO 3166-1 alpha-2 country code (lowercase) and the main ISO 639-1 language code.
Return JSON ONLY: {{"gl": "xx", "hl": "xx", "keywords": ["query1", "query2", ...]}}"""

    res = _ai_json("You are an expert B2B lead-generation researcher.", prompt, 500)
    kws = [k.strip() for k in (res.get("keywords") or []) if isinstance(k, str) and k.strip()]
    kws += _fallback_keywords(location, category)          
    kws = list(dict.fromkeys(kws))[:10]

    gl = str(res.get("gl") or "").lower()
    hl = str(res.get("hl") or "").lower()
    return {
        "keywords": kws,
        "gl": gl if re.fullmatch(r"[a-z]{2}", gl) else None,
        "hl": hl if re.fullmatch(r"[a-z]{2}(-[a-z]{2,4})?", hl) else None,
    }


# --------------------------------------------------------------------------
# Step 2: Search via Serper.dev
# --------------------------------------------------------------------------
def _serper(query: str, page: int, gl, hl, location) -> list:
    if not SERPER_API_KEY:
        print("WARNING: SERPER_API_KEY is missing!")
        return []
    payload = {"q": query, "num": 10, "page": page}
    if gl: payload["gl"] = gl
    if hl: payload["hl"] = hl
    if location: payload["location"] = location
    headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}

    for attempt in range(3):
        try:
            r = requests.post(SERPER_URL, headers=headers, json=payload, timeout=15)
            if r.status_code == 400 and "location" in payload:
                payload.pop("location")
                continue
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code != 200:
                return []
            return [it["link"] for it in r.json().get("organic", []) if it.get("link")]
        except Exception:
            time.sleep(1.0 * (attempt + 1))
    return []

def get_candidate_urls(location: str, category: str):
    meta = generate_localized_keywords(location, category)
    keywords, gl, hl = meta["keywords"], meta["gl"], meta["hl"]
    print(f"Buyer Keywords ({gl}/{hl}): {keywords}")

    tasks = [(kw, p) for kw in keywords for p in range(1, SERPER_PAGES + 1)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda t: _serper(t[0], t[1], gl, hl, location), tasks))

    origins: dict[str, str] = {}
    score: dict[str, float] = {}
    for (kw, page), links in zip(tasks, results):
        for i, link in enumerate(links):
            url = link.split("#")[0]
            if not url or url.lower().endswith(".pdf"): continue
            dom = root_domain(url)
            if not dom or dom in EXCLUDED_DOMAINS or DIRECTORY_HINT.search(dom): continue
            pos = (page - 1) * 10 + i
            score[dom] = score.get(dom, 0.0) + 1.0 / (pos + 3)   
            if dom not in origins:
                p = urlparse(url)
                origins[dom] = f"{p.scheme or 'https'}://{p.netloc}"

    ranked = sorted(origins, key=lambda d: score[d], reverse=True)[:MAX_CANDIDATE_SITES]
    return [origins[d] for d in ranked], gl


# --------------------------------------------------------------------------
# Step 3: Scrape
# --------------------------------------------------------------------------
def _fetch_cf(url: str, timeout: float = 15):
    for imp in IMPERSONATE:                      
        try:
            r = c_requests.get(url, impersonate=imp, headers=REQ_HEADERS, timeout=timeout, allow_redirects=True)
        except Exception:
            continue
        if r.status_code in (403, 429, 503): continue
        if r.status_code >= 400: return None, None
        ctype = r.headers.get("Content-Type", "text/html").lower()
        if "html" not in ctype and "xml" not in ctype: return None, None
        return BeautifulSoup(r.content, "html.parser"), str(r.url)
    return None, None

def _decode_cfemail(enc: str) -> str:
    try:
        key = int(enc[:2], 16)
        return "".join(chr(int(enc[i:i + 2], 16) ^ key) for i in range(2, len(enc), 2))
    except Exception: return ""

def _deobfuscate(text: str) -> str:
    text = re.sub(r"\s*[\[\(\{]\s*(?:at|AT)\s*[\]\)\}]\s*", "@", text)
    text = re.sub(r"\s*[\[\(\{]\s*(?:dot|DOT)\s*[\]\)\}]\s*", ".", text)
    return text

def _clean_emails(raw: set, site_domain: str) -> list:
    good = []
    for e in raw:
        e = e.strip(".,;:<>()[]\"' ").lower()
        if "@" not in e or e.endswith(BAD_EMAIL_SUFFIX) or any(b in e for b in BAD_EMAIL_PARTS): continue
        local, dom = e.split("@", 1)
        if not re.fullmatch(r"[a-z]{2,}", dom.split(".")[-1]): continue
        good.append(e)

    def rank(e: str) -> int:
        local, dom = e.split("@", 1)
        r = 0 if site_domain and dom.endswith(site_domain) else 2
        if not local.startswith(PREFERRED_PREFIX): r += 1
        return r

    good = list(dict.fromkeys(good))
    good.sort(key=rank)
    return good[:3]

def _clean_phones(raw: list) -> list:
    out, seen = [], set()
    for ph in raw:
        ph = re.sub(r"\s+", " ", ph).strip(" .,-;")
        digits = re.sub(r"\D", "", ph)
        if 8 <= len(digits) <= 15 and digits not in seen:
            seen.add(digits)
            out.append(ph)
    return out[:3]

def _scrape(origin: str) -> dict:
    soup, final_url = _fetch_cf(origin)
    if not soup: return {}
    site_dom = root_domain(final_url)
    title = (soup.title.get_text(strip=True) if soup.title else "")[:200]
    meta = soup.find("meta", attrs={"name": "description"})
    meta_desc = (meta.get("content") or "").strip()[:300] if meta else ""

    pages = [soup]
    seen = {final_url.rstrip("/")}
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.lower().startswith(("mailto:", "tel:", "javascript:", "#")): continue
        if CONTACT_HINT.search(href) or CONTACT_HINT.search(a.get_text(" ")):
            link = urljoin(final_url, href).split("#")[0]
            if root_domain(link) != site_dom or link.rstrip("/") in seen: continue
            seen.add(link.rstrip("/"))
            links.append(link)
    links.sort(key=lambda l: 0 if CONTACT_FIRST.search(l) else 1)   
    for link in links[:2]:
        s2, _ = _fetch_cf(link)
        if s2: pages.append(s2)

    if len(pages) == 1:                                              
        for path in GUESS_PATHS[:3]:
            s2, _ = _fetch_cf(urljoin(final_url, path), timeout=8)
            if s2:
                pages.append(s2)
                break

    texts, emails, phones = [], set(), []
    for p in pages:
        for a in p.select("a[href^='mailto:']"): emails.add(a["href"][7:].split("?")[0])
        for el in p.select("[data-cfemail]"): emails.add(_decode_cfemail(el.get("data-cfemail", "")))
        for a in p.select("a[href*='/cdn-cgi/l/email-protection#']"): emails.add(_decode_cfemail(a["href"].split("#")[-1]))
        for a in p.select("a[href^='tel:']"): phones.append(a["href"][4:])
        for t in p(["script", "style", "noscript", "svg"]): t.decompose()
        txt = re.sub(r"\s+", " ", p.get_text(" "))
        phones.extend(PHONE_RE.findall(txt))
        emails.update(EMAIL_RE.findall(_deobfuscate(txt)))
        if len(txt) > 5000: txt = txt[:3500] + " ... " + txt[-1500:]
        texts.append(txt)

    return {
        "url": final_url,
        "title": f"{title} | {meta_desc}" if meta_desc else title,
        "text": " ".join(texts)[:MAX_TEXT_CHARS],
        "emails": _clean_emails(emails, site_dom),
        "phones": _clean_phones(phones),
    }

def scrape_company_site(origin: str) -> dict:
    try: return _scrape(origin)
    except Exception: return {}


# --------------------------------------------------------------------------
# Step 4: Contextual AI extraction (Targeting BUYERS)
# --------------------------------------------------------------------------
def extract_company(page: dict, location: str, category: str) -> dict | None:
    cat = f"Preferred industry: {category}." if category not in ("", "all", None) else ""
    prompt = f"""Analyze this website text and extract company details.
Target location/market: {location}
{cat}

VERIFICATION RULES FOR BUYERS:
1. "is_potential_buyer": true ONLY if this company operates in an industry that typically PURCHASES and USES aluminium profiles (e.g., LED lighting manufacturers, interior fit-out companies, furniture makers, construction/facade contractors, window/door installers).
   FALSE if they are an aluminium extruder/manufacturer (they make profiles, so they are your competitor, not your customer) or if they are a directory/news site.
2. "is_in_target_location": "yes" if the company is based in the target city/country, "no" ONLY if clearly based elsewhere, "unknown" if unclear.

Return JSON ONLY:
{{
  "name": str|null,
  "address": str|null,
  "city": str|null,
  "country": str|null,
  "is_potential_buyer": bool,
  "is_in_target_location": "yes"|"no"|"unknown",
  "company_type": "contractor"|"furniture_maker"|"lighting"|"facade"|"other_buyer",
  "main_categories": [str],
  "description": str
}}
Notes: "main_categories" should list their actual business focus (e.g., "LED Lighting", "Kitchen Cabinets", "Facade Engineering").

URL: {page['url']}
TITLE: {page['title']}
TEXT: {page['text']}"""

    data = _ai_json("You are a B2B sales data extractor finding CUSTOMERS.", prompt, 800)
    
    # We now filter for buyers, not manufacturers
    if not data.get("name") or not data.get("is_potential_buyer"):
        return None

    loc_flag = str(data.get("is_in_target_location", "unknown")).strip().lower()
    if loc_flag in ("no", "false"):
        return None
    data["location_verified"] = loc_flag in ("yes", "true")

    data["website"] = page["url"]
    data["emails"] = page["emails"]
    data["phones"] = page["phones"]
    data["source"] = "web_verified"
    return data

def _safe_extract(page: dict, location: str, category: str):
    try: return extract_company(page, location, category)
    except Exception: return None


# --------------------------------------------------------------------------
# Step 5: Robust geocoding
# --------------------------------------------------------------------------
_geo_cache: dict = {}
_last_geo = 0.0

def _geo_wait():
    global _last_geo
    delta = time.time() - _last_geo
    if delta < 1.1: time.sleep(1.1 - delta)
    _last_geo = time.time()

def geocode_nominatim(query: str, country_code: str | None = None):
    if not query: return None
    key = (query, country_code)
    if key in _geo_cache: return _geo_cache[key]
    params = {"q": query, "format": "json", "limit": 1}
    if country_code: params["countrycodes"] = country_code
    result = None
    try:
        _geo_wait()
        r = requests.get("https://nominatim.openstreetmap.org/search", params=params, headers={"User-Agent": NOMINATIM_UA}, timeout=10)
        if r.status_code == 200 and r.json():
            j = r.json()[0]
            result = (float(j["lat"]), float(j["lon"]))
    except Exception: pass
    _geo_cache[key] = result
    return result

def geocode_company(comp: dict, country_code: str | None = None):
    addr, city, country = comp.get("address"), comp.get("city"), comp.get("country")
    if addr:
        full = addr if (city and city.lower() in addr.lower()) else ", ".join(filter(None, [addr, city, country]))
        coords = geocode_nominatim(full, country_code) or geocode_nominatim(addr, country_code)
        if coords: return coords, "exact"

    city_query = f"{city or ''}, {country or ''}".strip(" ,")
    if city_query:
        coords = geocode_nominatim(city_query, country_code)
        if coords: return coords, "city_level"
    return (None, None), "none"


# --------------------------------------------------------------------------
# Main entry
# --------------------------------------------------------------------------
def run_scraper(location: str, category: str = "all") -> list:
    print(f"=== Searching '{location}' for BUYERS (category: {category}) ===")

    urls, cc = get_candidate_urls(location, category)
    print(f"Found {len(urls)} candidate websites.")
    if not urls: return []

    print("Scraping websites...")
    with ThreadPoolExecutor(max_workers=SCRAPE_WORKERS) as pool:
        pages = [p for p in pool.map(scrape_company_site, urls) if p and p.get("text")]
    print(f"Successfully scraped {len(pages)} sites.")

    print("AI is verifying potential buyers...")
    with ThreadPoolExecutor(max_workers=AI_WORKERS) as pool:
        extracted = list(pool.map(lambda p: _safe_extract(p, location, category), pages))

    default_country = location.split(",")[-1].strip()
    final, seen = [], set()
    for c in extracted:
        if not c: continue
        dom = root_domain(c["website"])
        if dom in seen: continue
        seen.add(dom)

        c["address"] = format_address(c.get("address"))
        c["country"] = c.get("country") or default_country
        c["location_string"] = f"{c.get('city') or ''}, {c['country']}".strip(" ,")

        coords, geo_type = geocode_company(c, cc)
        c["latitude"], c["longitude"] = coords
        c["geo_precision"] = geo_type

        c["email"] = c["emails"][0] if c["emails"] else None
        c["phone"] = c["phones"][0] if c["phones"] else None
        c["name"] = str(c["name"]).strip()
        c["buyer_name"] = c["name"]
        c["destination_country"] = c["country"]
        c["main_categories"] = c.get("main_categories") or []
        c["sub_categories"] = []
        c["ai_status"] = "scraped" if (c["email"] or c["phone"]) else "pending"
        c["is_matrix"] = False
        c["created_at"] = int(time.time())
        c["company_id"] = generate_company_id(dom)
        final.append(c)

    final.sort(key=lambda c: (not c["location_verified"], not (c["email"] or c["phone"])))
    print(f"Done! Verified {len(final)} BUYERS targeting {location}.")
    return final

def search_new_companies(location_query: str, category: str = "all") -> list:
    return run_scraper(location_query, category=category)

if __name__ == "__main__":
    import sys
    loc = sys.argv[1] if len(sys.argv) > 1 else "Warsaw, Poland"
    print(f"Testing scraper for BUYERS in '{loc}'...")
    results = search_new_companies(loc)

    for i, r in enumerate(results[:5], 1):
        print(f"\n[{i}] {r['name']}  (Type: {r.get('company_type')})")
        print(f"    Website: {r.get('website')}")
        print(f"    Email:   {r.get('email')}")
