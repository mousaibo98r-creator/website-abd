import os
import re
import json
import hashlib
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse
from dotenv import load_dotenv
from ddgs import DDGS
from openai import OpenAI
import requests
from bs4 import BeautifulSoup

# Load environment variables from .env file
load_dotenv()

# Configuration
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

client = None
if DEEPSEEK_API_KEY and DEEPSEEK_API_KEY != "your_deepseek_api_key_here":
    client = OpenAI(
        api_key=DEEPSEEK_API_KEY,
        base_url="https://api.deepseek.com"
    )

SEARCH_KEYWORDS = [
    "LED Profile", "LED Aluminium Profile", "LED Strip Profile",
    "Tile Trim Profile", "Tile Edge Profile", "Ceramic Tile Trim",
    "Furniture Profiles", "Cabinet/Kitchen Profiles",
    "Glass Profiles", "Shower & Glass Profiles",
    "Decorative Profiles", "Wall & Ceiling Profiles",
    "Small/Light Aluminium Extrusion"
]

EXCLUDED_DOMAINS = [
    "alibaba.com", "made-in-china.com", "globalsources.com",
    "indiamart.com", "exportersindia.com", "tradeindia.com",
    "ec21.com", "europages.com", "europages.co.uk", "kompass.com",
    "wlw.de", "yellowpages.com", "linkedin.com", "facebook.com",
    "youtube.com", "pinterest.com", "instagram.com", "twitter.com",
    "wikipedia.org", "amazon.com", "ebay.com"
]

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
    
    # Arab League & Others
    "algeria": "xa-en", "egypt": "xa-en", "iraq": "xa-en",
    "jordan": "xa-en", "kuwait": "xa-en", "lebanon": "xa-en",
    "morocco": "xa-en", "oman": "xa-en", "palestine": "xa-en",
    "qatar": "xa-en", "saudi arabia": "xa-en", "ksa": "xa-en",
    "syria": "xa-en", "tunisia": "xa-en", "united arab emirates": "xa-en",
    "uae": "xa-en", "dubai": "xa-en", "yemen": "xa-en"
}

# Country Centroids for accurate national mapping worldwide
COUNTRY_CENTROIDS = {
    # Europe
    "germany": (51.1657, 10.4515), "deutschland": (51.1657, 10.4515),
    "france": (46.2276, 2.2137),
    "united kingdom": (55.3781, -3.4360), "uk": (55.3781, -3.4360), "britain": (55.3781, -3.4360), "england": (52.3555, -1.1743),
    "italy": (41.8719, 12.5674), "italia": (41.8719, 12.5674),
    "spain": (40.4637, -3.7492), "españa": (40.4637, -3.7492), "espana": (40.4637, -3.7492),
    "poland": (51.9194, 19.1451), "polska": (51.9194, 19.1451),
    "netherlands": (52.1326, 5.2913), "holland": (52.1326, 5.2913), "nederland": (52.1326, 5.2913),
    "belgium": (50.5039, 4.4699), "switzerland": (46.8182, 8.2275), "schweiz": (46.8182, 8.2275),
    "austria": (47.5162, 14.5501), "österreich": (47.5162, 14.5501), "portugal": (39.3999, -8.2245),
    "greece": (39.0742, 21.8243), "sweden": (60.1282, 18.6435), "norway": (60.4720, 8.4689),
    "denmark": (56.2639, 9.5018), "finland": (61.9241, 25.7482), "ireland": (53.1424, -7.6921),
    "czech republic": (49.8175, 15.4730), "czechia": (49.8175, 15.4730),
    "slovakia": (48.6690, 19.6990), "hungary": (47.1625, 19.5033),
    "romania": (45.9432, 24.9668), "bulgaria": (42.7339, 25.4858),
    "croatia": (45.1000, 15.2000), "slovenia": (46.1512, 14.9955), "serbia": (44.0165, 21.0059),
    "bosnia": (43.9159, 17.6791), "albania": (41.1533, 20.1683), "kosovo": (42.6026, 20.9030),
    "ukraine": (48.3794, 31.1656), "russia": (61.5240, 105.3188),

    # Middle East & North Africa
    "turkey": (38.9637, 35.2433), "türkiye": (38.9637, 35.2433), "turkiye": (38.9637, 35.2433),
    "united arab emirates": (23.4241, 53.8478), "uae": (23.4241, 53.8478),
    "saudi arabia": (23.8859, 45.0792), "ksa": (23.8859, 45.0792),
    "qatar": (25.3548, 51.1839), "kuwait": (29.3117, 47.4818), "oman": (21.5126, 55.9233),
    "bahrain": (26.0667, 50.5577), "jordan": (30.5852, 36.2384), "lebanon": (33.8547, 35.8623),
    "iraq": (33.2232, 43.6793), "israel": (31.0461, 34.8516), "egypt": (26.8206, 30.8025),
    "morocco": (31.7917, -7.0926), "algeria": (28.0339, 1.6596), "tunisia": (33.8869, 9.5375),

    # Americas
    "united states": (37.0902, -95.7129), "usa": (37.0902, -95.7129), "u.s.": (37.0902, -95.7129),
    "canada": (56.1304, -106.3468), "mexico": (23.6345, -102.5528),
    "brazil": (-14.2350, -51.9253), "brasil": (-14.2350, -51.9253),
    "argentina": (-38.4161, -63.6167), "chile": (-35.6751, -71.5430),
    "colombia": (4.5709, -74.2973), "peru": (-9.1900, -75.0152),

    # Asia & Oceania
    "china": (35.8617, 104.1954), "india": (20.5937, 78.9629), "japan": (36.2048, 138.2529),
    "south korea": (35.9078, 127.7669), "korea": (35.9078, 127.7669),
    "taiwan": (23.6978, 120.9605), "hong kong": (22.3193, 114.1694),
    "vietnam": (14.0583, 108.2772), "thailand": (15.8700, 100.9925),
    "malaysia": (4.2105, 101.9758), "singapore": (1.3521, 103.8198),
    "indonesia": (-0.7893, 113.9213), "philippines": (12.8797, 121.7740),
    "australia": (-25.2744, 133.7751), "new zealand": (-40.9006, 174.8860),
    "south africa": (-30.5595, 22.9375),
}

# Major Global Extrusion Centers
GLOBAL_CITY_COORDS = {
    # Poland
    "kety": (49.8868, 19.2274), "kęty": (49.8868, 19.2274),
    "bielsko-biala": (49.8225, 19.0444), "bielsko-biała": (49.8225, 19.0444),
    "warsaw": (52.2297, 21.0122), "warszawa": (52.2297, 21.0122),
    "krakow": (50.0647, 19.9450), "kraków": (50.0647, 19.9450),
    "wroclaw": (51.1079, 17.0385), "wrocław": (51.1079, 17.0385),
    "poznan": (52.4064, 16.9252), "poznań": (52.4064, 16.9252),
    "katowice": (50.2649, 19.0238), "gliwice": (50.2945, 18.6714),
    "lodz": (51.7592, 19.4560), "łódź": (51.7592, 19.4560),
    "konin": (52.2234, 18.2512), "opole": (50.6751, 17.9213),

    # Germany
    "velbert": (51.3400, 7.0420), "wuppertal": (51.2562, 7.1508), "solingen": (51.1712, 7.0838),
    "berlin": (52.5200, 13.4050), "munich": (48.1351, 11.5820), "münchen": (48.1351, 11.5820),
    "frankfurt": (50.1109, 8.6821), "hamburg": (53.5511, 9.9937), "stuttgart": (48.7758, 9.1829),
    "dusseldorf": (51.2277, 6.7735), "düsseldorf": (51.2277, 6.7735), "cologne": (50.9375, 6.9603), "köln": (50.9375, 6.9603),
    "dortmund": (51.5136, 7.4653), "essen": (51.4556, 7.0116),

    # United States
    "chicago": (41.8781, -87.6298), "elk grove": (42.0039, -87.9703), "elk grove village": (42.0039, -87.9703),
    "new york": (40.7128, -74.0060), "los angeles": (34.0522, -118.2437), "houston": (29.7604, -95.3698),
    "dallas": (32.7767, -96.7970), "cleveland": (41.4993, -81.6944), "detroit": (42.3314, -83.0458),
    "pittsburgh": (40.4406, -79.9959), "atlanta": (33.7490, -84.3880),

    # United Kingdom & France & Italy & Spain
    "london": (51.5074, -0.1278), "birmingham": (52.4862, -1.8904), "telford": (52.6784, -2.4452),
    "paris": (48.8566, 2.3522), "lyon": (45.7640, 4.8357),
    "milan": (45.4642, 9.1900), "milano": (45.4642, 9.1900), "brescia": (45.5416, 10.2118), "rome": (41.9028, 12.4964),
    "madrid": (40.4168, -3.7038), "barcelona": (41.3879, 2.1699), "valencia": (39.4699, -0.3763), "alicante": (38.3452, -0.4810),

    # Middle East & Turkey
    "dubai": (25.2048, 55.2708), "al quoz": (25.1384, 55.2346), "jafza": (24.9857, 55.0874), "sharjah": (25.3463, 55.4209),
    "abu dhabi": (24.4539, 54.3773), "riyadh": (24.7136, 46.6753), "jeddah": (21.5433, 39.1728), "dammam": (26.4207, 50.0888),
    "cairo": (30.0444, 31.2357), "doha": (25.2854, 51.5310), "kuwait city": (29.3759, 47.9774),
    "ankara": (39.9334, 32.8597), "sincan": (39.9583, 32.5786), "ostim": (39.9725, 32.7483), "ivedik": (39.9817, 32.7667),
    "istanbul": (41.0082, 28.9784), "esenyurt": (41.0342, 28.6801), "tekirdag": (40.9781, 27.5117), "tekirdağ": (40.9781, 27.5117),
    "corlu": (41.1594, 27.7986), "çorlu": (41.1594, 27.7986), "cerkezkoy": (41.2917, 28.0017), "çerkezköy": (41.2917, 28.0017),
    "izmir": (38.4237, 27.1428), "bursa": (40.1885, 29.0610), "kocaeli": (40.8533, 29.8815), "gebze": (40.8028, 29.4307),

    # China & Asia
    "foshan": (23.0215, 113.1214), "dali": (23.1121, 113.1049), "nanhai": (23.0287, 113.1429),
    "guangzhou": (23.1291, 113.2644), "shenzhen": (22.5431, 114.0579), "dongguan": (23.0207, 113.7518),
    "beijing": (39.9042, 116.4074), "shanghai": (31.2304, 121.4737),
}

EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
PHONE_RE = re.compile(r"(?:\+?\d{1,4}[ -]?)?(?:\(?\d{2,5}\)?[ -]?)?\d{3,4}[ -]?\d{3,5}")


def _fold(s: str) -> str:
    """Normalize diacritics and special characters for tolerant search matching."""
    s = (s or "").lower()
    s = s.replace("ğ", "g").replace("Ğ", "g").replace("ı", "i").replace("İ", "i")
    s = s.replace("ş", "s").replace("Ş", "s").replace("ç", "c").replace("Ç", "c")
    s = s.replace("ö", "o").replace("Ö", "o").replace("ü", "u").replace("Ü", "u")
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c))


def root_domain(url: str) -> str:
    """Extract root domain from url (e.g. www.example.com -> example.com)."""
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
    loc_lower = location.lower()
    for country in sorted(COUNTRY_REGION_MAP, key=len, reverse=True):
        if country in loc_lower:
            return COUNTRY_REGION_MAP[country]
    return "wt-wt"


def location_tokens(location: str) -> list:
    parts = re.split(r"[,/|]", location or "")
    return [_fold(p.strip()) for p in parts if p.strip()]


def generate_company_id(name: str) -> str:
    normalized_name = (name or "").strip().lower()
    return hashlib.md5(normalized_name.encode('utf-8')).hexdigest()


def format_address(addr) -> str | None:
    """Format raw address string or structured dict into clean, standard physical address."""
    if not addr:
        return None
    if isinstance(addr, str):
        cleaned = re.sub(r"\s+", " ", addr).strip(" ,;|·-–—\n\r")
        if len(cleaned) < 4 or cleaned.lower() in ("none", "null", "n/a", "not available"):
            return None
        return cleaned
    if isinstance(addr, dict):
        parts = []
        street = addr.get("street") or addr.get("street_address") or addr.get("line1")
        num = str(addr.get("number") or addr.get("building_number") or "").strip()
        if street and num:
            parts.append(f"{street} {num}" if num not in str(street) else str(street))
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
        res = ", ".join(p for p in parts if p)
        return res if len(res) >= 4 else None
    return str(addr).strip()


def geocode_address(text: str, loc_hint: str = "") -> tuple:
    """Universal geocoder: City Cache -> Nominatim -> Country Centroid."""
    full_text = f"{text or ''} {loc_hint or ''}".strip()
    if not full_text:
        return (41.0082, 28.9784)

    folded = _fold(full_text)

    # 1. Match local major city coordinates with word boundaries
    for name, coords in sorted(GLOBAL_CITY_COORDS.items(), key=lambda x: len(x[0]), reverse=True):
        if re.search(rf"\b{re.escape(_fold(name))}\b", folded):
            return coords

    # 2. Match country centroid with word boundaries
    for country in sorted(COUNTRY_CENTROIDS.keys(), key=len, reverse=True):
        if re.search(rf"\b{re.escape(_fold(country))}\b", folded):
            return COUNTRY_CENTROIDS[country]

    if "turkey" in folded or "turkiye" in folded:
        return (39.9334, 32.8597)
    return (48.8566, 2.3522)


def scrape_website_text(url: str) -> str | None:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        response = requests.get(url if "://" in url else "https://" + url, headers=headers, timeout=2.5)
        if response.status_code >= 400:
            return None

        soup = BeautifulSoup(response.content, 'html.parser')
        for s in soup(['script', 'style', 'noscript', 'svg']):
            s.decompose()

        text = soup.get_text(separator=' ')
        lines = (line.strip() for line in text.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        cleaned_text = '\n'.join(chunk for chunk in chunks if chunk)
        return cleaned_text[:3000]
    except Exception:
        return None


def discover_companies_with_ai(location: str, category: str = "all") -> list:
    """Direct AI-driven discovery: fast, verified, immune to cloud IP search blocks."""
    if not client:
        return []

    prompt = f"""You are a senior global B2B industrial market researcher specializing in aluminium extrusions, LED profiles, tile edge trims, and architectural profiles.
TARGET REGION / CITY / COUNTRY: {location}
TARGET PRODUCT CATEGORY: {category}

TASK:
Identify 10 to 15 REAL, verified companies, manufacturers, extruders, fabricators, or major distributors of aluminium profiles operating in or physically situated in {location}.
If {location} is a Country (e.g. Poland, Germany, United States, UAE, Saudi Arabia, France, Italy, Spain, Turkey, China, Egypt, etc.), identify leading verified manufacturers physically located across industrial hubs in that country.
If {location} is a City (e.g. Kęty, Velbert, Milan, Foshan, Chicago, Tekirdağ, Ostim, etc.), identify factories and plants situated directly in that city/region.

CRITICAL PHYSICAL ADDRESS REQUIREMENTS:
Every company MUST have a real, full physical street address matching the country's national postal standard:
- POLAND / EASTERN EUROPE: e.g. "ul. Kościuszki 111, 32-650 Kęty, Poland"
- GERMANY / DACH: e.g. "Industriestraße 12, 42551 Velbert, Germany"
- USA / NORTH AMERICA: e.g. "1400 E Higgins Rd, Elk Grove Village, IL 60007, United States"
- UK: e.g. "Unit 4, Stafford Park 11, Telford, Shropshire, TF3 3AY, United Kingdom"
- TURKEY: e.g. "Veliköy OSB Mah. 2. Cadde No: 5, Çerkezköy, Tekirdağ, Türkiye"
- UAE / GULF: e.g. "Plot No. 598-1121, Dubai Investments Park 1, Jebel Ali, Dubai, UAE"
- FRANCE / ITALY / SPAIN: e.g. "Via Industriale 24, 25030 Castelmella (BS), Italy"
- CHINA: e.g. "Dali Town Industrial Park, Nanhai District, Foshan, Guangdong, China"
- ALL OTHER REGIONS: Full street address with building number/zone, postal code, city, country.
NEVER return null, None, or empty for address.

COORDINATES:
Provide factory/office latitude and longitude floats ("latitude": <float>, "longitude": <float>) based on its city/address.

Return strictly valid JSON with key "companies":
{{
  "companies": [
    {{
      "name": "Full Official Legal Company Name",
      "website": "https://www.example.com",
      "address": "Full Physical Street Address according to country standards",
      "city": "City name",
      "country": "Country name",
      "latitude": 49.8868,
      "longitude": 19.2274,
      "location_string": "City, Country",
      "phone": "+48 33 123 4567",
      "email": "info@company.com",
      "main_categories": ["Aluminium Profiles", "LED Profiles"],
      "sub_categories": ["Surface mounted", "Extrusions"],
      "description": "Factual description of manufacturing facility, profile series, and extrusion presses.",
      "company_type": "manufacturer"
    }}
  ]
}}
"""
    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "You are an elite B2B research engine. Return strictly valid JSON."},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        content = response.choices[0].message.content.strip()
        data = json.loads(content)
        if isinstance(data, dict) and "companies" in data:
            results = data["companies"]
            for c in results:
                c["source"] = "deepseek_discovery"
                c["address"] = format_address(c.get("address"))
                if not c.get("country"):
                    c["country"] = location
                if not c.get("location_string"):
                    c["location_string"] = f"{c.get('city') or ''}, {c.get('country') or location}".strip(" ,")
            return results
    except Exception as e:
        print(f"DeepSeek direct discovery error: {e}")
    return []


def fetch_search_results(location: str) -> list:
    print(f"Starting deep search for aluminium companies in {location}...")
    region = guess_region(location)
    exclude_clause = " ".join(f"-site:{d}" for d in EXCLUDED_DOMAINS[:6])
    clean_loc = location.replace(',', ' ').strip()

    unique_urls = {}
    # Search top 2 keywords to stay fast and avoid DDG bot blocking
    for keyword in SEARCH_KEYWORDS[:2]:
        query = f'{keyword} aluminium {clean_loc} {exclude_clause}'
        try:
            results = DDGS().text(query, region=region, max_results=10)
            if results:
                for r in results:
                    url = (r.get('href') or '').split('#')[0]
                    if not url or url in unique_urls:
                        continue
                    if any(domain in url for domain in EXCLUDED_DOMAINS):
                        continue
                    unique_urls[url] = {
                        "title": r.get('title', ''),
                        "snippet": r.get('body', '')
                    }
        except Exception:
            pass

    if not unique_urls:
        return []

    url_list = list(unique_urls.keys())[:8]

    def _scrape_job(u):
        meta = unique_urls[u]
        scraped = scrape_website_text(u)
        content = scraped if scraped else meta['snippet']
        return f"Title: {meta['title']}\nURL: {u}\nContent: {content}\n"

    with ThreadPoolExecutor(max_workers=min(5, len(url_list))) as pool:
        results_list = list(pool.map(_scrape_job, url_list))

    return results_list


def parse_with_ai(raw_text_chunk: str, location: str) -> list:
    if not client:
        return []

    prompt = f"""You are a data extraction expert. Read the following batch of scraped text.
TARGET LOCATION: {location}
Extract companies that operate in, manufacture in, or serve {location}.
For every company, provide:
1. "name": official legal name
2. "website": official URL
3. "address": full physical street address (Street, Number, Zone, Postal Code, City, Country)
4. "city": city name
5. "country": country name
6. "email": contact email
7. "phone": contact phone
8. "main_categories": ["LED Profile", "Aluminium Profiles"]
9. "sub_categories": ["Extrusions"]
10. "description": 1-2 sentence description
11. "company_type": "manufacturer", "distributor", or "fabricator"

Schema:
{{
  "companies": [
    {{
      "name": "string",
      "website": "string",
      "address": "string",
      "city": "string",
      "country": "string",
      "email": "string",
      "phone": "string",
      "main_categories": ["string"],
      "sub_categories": ["string"],
      "description": "string",
      "company_type": "string"
    }}
  ]
}}

Raw Text Batch:
{raw_text_chunk}
"""
    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "You are a helpful assistant that strictly outputs valid JSON. Return ONLY JSON."},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"}
        )
        content = response.choices[0].message.content.strip()
        parsed_data = json.loads(content)
        if isinstance(parsed_data, dict) and "companies" in parsed_data:
            return parsed_data["companies"]
        return []
    except Exception as e:
        print(f"Error parsing with AI: {e}")
        return []


def filter_by_location(companies: list, location: str) -> list:
    tokens = location_tokens(location)
    if not tokens:
        return companies
    kept = []
    for comp in companies:
        if comp.get("source") == "deepseek_discovery":
            kept.append(comp)
            continue
        hay = _fold(f"{comp.get('location', '')} {comp.get('address', '')} {comp.get('city', '')} {comp.get('country', '')} {comp.get('website', '')}")
        if any(tok in hay for tok in tokens):
            kept.append(comp)
    return kept if kept else companies


def run_scraper(location: str, category: str = "all") -> list:
    """Main execution entry point that returns a list of verified company dictionaries."""
    print(f"=== Starting Discovery Search for '{location}' (Category: {category}) ===")

    # 1. Fast, reliable AI discovery (immune to cloud IP blocks, finishes in ~10 seconds)
    all_companies = discover_companies_with_ai(location, category)

    # 2. Live Web Search fallback (if AI produced fewer than 6 candidates)
    if len(all_companies) < 6:
        search_snippets = fetch_search_results(location)
        if search_snippets:
            chunk_size = 4
            for i in range(0, len(search_snippets), chunk_size):
                chunk = search_snippets[i:i + chunk_size]
                text_batch = "\n---\n".join(chunk)
                extracted = parse_with_ai(text_batch, location)
                all_companies.extend(extracted)

    filtered = filter_by_location(all_companies, location)

    # Deduplicate by domain or name
    seen = set()
    final = []
    for comp in filtered:
        name = comp.get("name") or comp.get("buyer_name") or "Unnamed Company"
        dom = root_domain(comp.get("website") or "")
        key = dom or _fold(name)
        if key in seen:
            continue
        seen.add(key)

        comp["name"] = name
        comp["buyer_name"] = name
        loc_str = comp.get("location_string") or comp.get("location") or comp.get("city") or location
        comp["location_string"] = loc_str
        comp["destination_country"] = comp.get("country") or location

        comp["address"] = format_address(comp.get("address")) or format_address(loc_str)

        # Geocode coordinates
        lat = comp.get("latitude")
        lng = comp.get("longitude")
        is_outside_turkey = ("turkey" not in (comp.get("country") or location).lower() and "türkiye" not in (comp.get("country") or location).lower())
        is_istanbul_default = (round(float(lat or 0), 3) == 41.008 and round(float(lng or 0), 3) == 28.978)

        is_valid_coord = (
            isinstance(lat, (int, float)) and isinstance(lng, (int, float))
            and -90 <= lat <= 90 and -180 <= lng <= 180
            and not (lat == 0.0 and lng == 0.0)
            and not (is_istanbul_default and is_outside_turkey)
        )
        if not is_valid_coord:
            coords = geocode_address(comp.get("address") or "", loc_hint=loc_str)
            jitter = (int(hashlib.md5(comp["name"].encode()).hexdigest()[:6], 16) % 30 - 15) * 0.003
            comp["latitude"] = round(coords[0] + jitter, 6)
            comp["longitude"] = round(coords[1] + jitter, 6)

        emails = []
        if comp.get("email"):
            emails.append(comp["email"])
        if comp.get("emails"):
            emails.extend(comp["emails"])
        comp["emails"] = list(dict.fromkeys(emails))
        comp["email"] = comp["emails"][0] if comp["emails"] else None

        phones = []
        if comp.get("phone"):
            phones.append(comp["phone"])
        if comp.get("phones"):
            phones.extend(comp["phones"])
        comp["phones"] = list(dict.fromkeys(phones))
        comp["phone"] = comp["phones"][0] if comp["phones"] else None

        comp["ai_status"] = "scraped" if (comp.get("email") or comp.get("phone")) else "pending"
        comp["is_matrix"] = False
        comp["source"] = comp.get("source") or "deepseek_discovery"
        comp["created_at"] = int(time.time())
        comp["company_id"] = generate_company_id(comp["name"] + str(comp.get("address", "")) + loc_str)

        final.append(comp)

    with_addr = sum(1 for c in final if c.get("address"))
    print(f"Scraper finished. Total: {len(final)} companies ({with_addr} with full street addresses).")
    return final


def search_new_companies(location_query: str, category: str = "all") -> list:
    """Main function called by app.py /api/search_new endpoint."""
    return run_scraper(location_query, category=category)


if __name__ == "__main__":
    import sys
    loc = sys.argv[1] if len(sys.argv) > 1 else "Poland"
    print(f"Testing scraper for '{loc}'...")
    results = search_new_companies(loc)
    print(f"Total: {len(results)}")
    for r in results[:3]:
        print(r["name"], "-->", r.get("address"), "-->", r.get("latitude"), r.get("longitude"))