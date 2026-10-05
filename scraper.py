"""
Aluminium profile company scraper (v2 - PRO VERSION).
Optimized for Cloud Servers (Render/AWS) with Cloudflare Bypass & High Precision.

Pipeline:
  1. AI generates LOCALIZED keywords for the target country/city.
  2. Google Search (via Serper.dev) finds candidate websites (Bypasses IP Blocks).
  3. Scrape pages using curl_cffi (Bypasses Cloudflare).
  4. AI extracts facts & verifies location contextually.
  5. Geocoding with fallback (Exact -> City Level).
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
SERPER_API_KEY = os.getenv("SERPER_API_KEY") # 🔴 مفتاح Serper للبحث
NOMINATIM_UA = os.getenv("NOMINATIM_UA", "aluminium-scraper/2.0 (contact: you@example.com)")

MAX_CANDIDATE_SITES = int(os.getenv("MAX_CANDIDATE_SITES", "25"))
MAX_WORKERS = 3 # تم التقليل ليتناسب مع سيرفرات Render المجانية

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
    "kompass.com", "wlw.de", "yellowpages.com", "linkedin.com", "facebook.com",
    "youtube.com", "pinterest.com", "instagram.com", "twitter.com", "x.com", 
    "wikipedia.org", "amazon.com", "ebay.com", "tiktok.com", "reddit.com", 
    "quora.com", "aliexpress.com", "dhgate.com", "trustpilot.com"
}

EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
CONTACT_HINT = re.compile(
    r"contact|kontakt|iletisim|iletişim|impressum|about|hakkimizda|hakkımızda|"
    r"contacto|contatti|contatto|nous-contacter|o-nas|uber-uns|über-uns", re.I
)
BAD_EMAIL_SUFFIX = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js")
BAD_EMAIL_PARTS = ("example.com", "sentry", "wixpress", "domain.com", "yourdomain", "email.com")

UA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en,*;q=0.5",
}

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
        if len(parts) > 2 and parts[-2] in ("co", "com", "net", "org") and len(parts[-1]) == 2:
            return ".".join(parts[-3:])
        return ".".join(parts[-2:])
    except Exception:
        return ""

def generate_company_id(name: str) -> str:
    return hashlib.md5((name or "").strip().lower().encode("utf-8")).hexdigest()

def format_address(addr) -> str | None:
    if not addr or str(addr).lower() in ("none", "null", "n/a", ""):
        return None
    cleaned = re.sub(r"\s+", " ", str(addr)).strip(" ,;|·-–—\n\r")
    return cleaned if len(cleaned) > 5 else None

def safe_json(content: str) -> dict:
    content = (content or "").strip()
    content = re.sub(r"^```(?:json)?|```$", "", content, flags=re.M).strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", content, re.S)
        if m:
            try: return json.loads(m.group(0))
            except: pass
    return {}

def _ai_json(system: str, user: str, max_tokens: int = 1000) -> dict:
    if not client: return {}
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
        return safe_json(r.choices[0].message.content)
    except Exception as e:
        print(f"AI error: {e}")
        return {}

# --------------------------------------------------------------------------
# Step 1: AI generates localized keywords
# --------------------------------------------------------------------------
def generate_localized_keywords(location: str, category: str) -> list:
    """Generate search keywords in the native language of the target location."""
    cat_text = f"focusing on {category}" if category and category != "all" else "aluminium profile/extrusion manufacturers"
    prompt = f"""Target location: {location}.
Generate 5 highly effective Google search queries in the NATIVE/LOCAL language of this location to find direct {cat_text} and fabricators. 
Include the location name in the queries.
Return JSON ONLY: {{"keywords": ["query1", "query2", ...]}}"""
    
    res = _ai_json("You are an expert B2B SEO researcher.", prompt, 300)
    return res.get("keywords") or [f"Aluminium profile manufacturer {location}"]

# --------------------------------------------------------------------------
# Step 2: Search via Serper.dev (Google API)
# --------------------------------------------------------------------------
def _search_google(query: str, max_results: int = 10) -> list:
    if not SERPER_API_KEY:
        print("WARNING: SERPER_API_KEY is missing! Cannot search Google.")
        return []
    url = "https://google.serper.dev/search"
    payload = {"q": query, "num": max_results}
    headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=10)
        return [{"href": item.get("link")} for item in r.json().get("organic", [])]
    except Exception as e:
        print(f"Serper Search failed for '{query}': {e}")
        return []

def get_candidate_urls(location: str, category: str) -> list:
    keywords = generate_localized_keywords(location, category)
    print(f"Generated Local Keywords: {keywords}")
    
    found = {}
    for kw in keywords:
        if len(found) >= MAX_CANDIDATE_SITES: break
        for r in _search_google(kw, 8):
            url = (r.get("href") or "").split("#")[0]
            if not url or url.lower().endswith(".pdf"): continue
            dom = root_domain(url)
            if dom and dom not in EXCLUDED_DOMAINS and dom not in found:
                p = urlparse(url)
                found[dom] = f"{p.scheme or 'https'}://{p.netloc}"
        time.sleep(0.5)
    return list(found.values())[:MAX_CANDIDATE_SITES]

# --------------------------------------------------------------------------
# Step 3: Scrape using curl_cffi (Cloudflare bypass)
# --------------------------------------------------------------------------
def _fetch_cf(url: str, timeout: float = 8):
    try:
        # impersonate="chrome120" is the magic that bypasses Cloudflare/Anti-bots
        r = c_requests.get(url, impersonate="chrome120", headers=UA_HEADERS, timeout=timeout)
        if r.status_code >= 400 or "html" not in r.headers.get("Content-Type", "html"):
            return None, None
        return BeautifulSoup(r.content, "html.parser"), r.url
    except Exception:
        return None, None

def _clean_emails(raw: set, site_domain: str) -> list:
    good = []
    for e in raw:
        e = e.strip(".,;:").lower()
        if e.endswith(BAD_EMAIL_SUFFIX) or any(b in e for b in BAD_EMAIL_PARTS): continue
        good.append(e)
    good.sort(key=lambda e: 0 if site_domain and e.split("@")[-1].endswith(site_domain) else 1)
    return list(dict.fromkeys(good))[:3]

def scrape_company_site(origin: str) -> dict:
    soup, final_url = _fetch_cf(origin)
    if not soup: return {}

    pages = [soup]
    seen_links = {final_url}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith(("mailto:", "tel:", "javascript:", "#")): continue
        if CONTACT_HINT.search(href) or CONTACT_HINT.search(a.get_text(" ")):
            link = urljoin(final_url, href)
            if root_domain(link) != root_domain(final_url) or link in seen_links: continue
            seen_links.add(link)
            s2, _ = _fetch_cf(link)
            if s2: pages.append(s2)
            if len(pages) >= 3: break

    texts, emails, phones = [], set(), []
    title = (soup.title.get_text(strip=True) if soup.title else "")[:200]
    
    for p in pages:
        for a in p.select("a[href^='mailto:']"): emails.add(a["href"][7:].split("?")[0])
        for a in p.select("a[href^='tel:']"):
            ph = a["href"][4:].strip()
            if ph and ph not in phones: phones.append(ph)
        for t in p(["script", "style", "noscript", "svg"]): t.decompose()
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
# Step 4: Contextual AI Extraction
# --------------------------------------------------------------------------
def extract_company(page: dict, location: str, category: str) -> dict | None:
    cat = f"Preferred category: {category}." if category not in ("", "all", None) else ""
    prompt = f"""Analyze this website text to extract company details.
Target Location/Market: {location}
{cat}

IMPORTANT VERIFICATION:
1. "is_aluminium_profile_business": True ONLY if they manufacture or sell aluminium profiles/extrusions.
2. "is_in_target_location": True if the text indicates they physically operate in or explicitly serve {location} (check addresses, cities, phone codes).

Return JSON ONLY:
{{
  "name": str|null,
  "address": str|null,
  "city": str|null,
  "country": str|null,
  "is_aluminium_profile_business": bool,
  "is_in_target_location": bool,
  "company_type": "manufacturer"|"distributor"|"fabricator"|"other",
  "main_categories": [str],
  "description": str
}}

URL: {page['url']}
TITLE: {page['title']}
TEXT: {page['text']}"""

    data = _ai_json("You are an expert data extractor. Extract facts strictly from text.", prompt, 800)
    
    # Strict filtering based on AI's contextual understanding
    if not data.get("name") or not data.get("is_aluminium_profile_business") or not data.get("is_in_target_location"):
        return None

    data["website"] = page["url"]
    data["emails"] = page["emails"]
    data["phones"] = page["phones"]
    data["source"] = "web_verified"
    return data

# --------------------------------------------------------------------------
# Step 5: Robust Geocoding
# --------------------------------------------------------------------------
_geo_cache = {}
def geocode_nominatim(query: str):
    if not query: return None
    if query in _geo_cache: return _geo_cache[query]
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": query, "format": "json", "limit": 1},
            headers={"User-Agent": NOMINATIM_UA},
            timeout=10,
        )
        if r.status_code == 200 and r.json():
            j = r.json()[0]
            result = (float(j["lat"]), float(j["lon"]))
            _geo_cache[query] = result
            time.sleep(1.1)
            return result
    except: pass
    time.sleep(1.1)
    return None

def geocode_company(comp: dict):
    """Fallback logic: Try full address -> Try City+Country"""
    if comp.get("address"):
        coords = geocode_nominatim(comp["address"])
        if coords: return coords, "exact"
        
    city_query = f"{comp.get('city') or ''}, {comp.get('country') or ''}".strip(" ,")
    if city_query:
        coords = geocode_nominatim(city_query)
        if coords: return coords, "city_level"
        
    return (None, None), "none"

# --------------------------------------------------------------------------
# Main Entry
# --------------------------------------------------------------------------
def run_scraper(location: str, category: str = "all") -> list:
    print(f"=== Searching '{location}' (category: {category}) ===")

    urls = get_candidate_urls(location, category)
    print(f"Found {len(urls)} candidate websites.")
    if not urls: return []

    print("Scraping websites... (Bypassing Cloudflare)")
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        pages = [p for p in pool.map(scrape_company_site, urls) if p and p.get("text")]
    print(f"Successfully scraped {len(pages)} sites.")

    print("AI is extracting and verifying details...")
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        extracted = list(pool.map(lambda p: extract_company(p, location, category), pages))

    final, seen = [], set()
    for c in extracted:
        if not c: continue
        dom = root_domain(c["website"])
        if dom in seen: continue
        seen.add(dom)

        c["address"] = format_address(c.get("address"))
        c["country"] = c.get("country") or location
        c["location_string"] = f"{c.get('city') or ''}, {c['country']}".strip(" ,")

        coords, geo_type = geocode_company(c)
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
        c["company_id"] = generate_company_id(c["name"] + str(c.get("address")) + c["location_string"])
        
        # Remove internal flags from final payload
        c.pop("is_in_target_location", None)
        final.append(c)

    print(f"✅ Done! Verified {len(final)} companies targeting {location}.")
    return final

def search_new_companies(location_query: str, category: str = "all") -> list:
    """Called by app.py /api/search_new endpoint."""
    return run_scraper(location_query, category=category)

if __name__ == "__main__":
    import sys
    loc = sys.argv[1] if len(sys.argv) > 1 else "Warsaw, Poland"
    print(f"Testing PRO scraper for '{loc}'...")
    results = search_new_companies(loc)
    
    for i, r in enumerate(results[:5], 1):
        print(f"\n[{i}] {r['name']}")
        print(f"    Website: {r.get('website')}")
        print(f"    Email:   {r.get('email')}")
        print(f"    Address: {r.get('address')} (Geo: {r.get('geo_precision')})")
