import time
import re
import random
import requests
from urllib.parse import urlparse

try:
    from ddgs import DDGS
except ImportError:
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        DDGS = None

NOMINATIM_HEADERS = {
    'User-Agent': 'MikyIntelligencePlatform/2.0 (mousaibo98r@gmail.com)'
}

SKIP_DOMAINS = frozenset([
    'wikipedia.org', 'linkedin.com', 'facebook.com', 'twitter.com', 'x.com',
    'youtube.com', 'instagram.com', 'pinterest.com', 'reddit.com', 'yelp.com',
    'yellowpages', 'glassdoor.com', 'indeed.com'
])

EMAIL_RE = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
PHONE_RE = re.compile(r'(?:\+\d{1,3}[\s\-]?)?(?:\(?\d{2,4}\)?[\s\-]?)?\d{3,4}[\s\-]?\d{3,4}')

def _clean_company_name(title, url):
    """Extract a clean, readable company/brand name from search result title and URL."""
    domain_brand = ''
    if url:
        try:
            domain = urlparse(url).netloc.lower().replace('www.', '')
            parts_d = domain.split('.')
            if len(parts_d) >= 2:
                domain_brand = parts_d[0]
        except Exception:
            pass

    junk_words = {
        'wikipedia', 'linkedin', 'facebook', 'glassdoor', 'indeed', 'companies hiring',
        'largest companies', 'best', 'top', 'list', 'category', 'jobs', 'offerings',
        'guide', 'solutions', 'directory', 'portal'
    }
    parts = [p.strip() for p in re.split(r'[\-\|\–\—\:]', title) if p.strip()]

    # If domain brand appears in one of the parts, that's likely the company name
    if domain_brand and len(domain_brand) > 2:
        for p in parts:
            if domain_brand in p.lower():
                return p

    # Otherwise look for a concise, clean part
    for p in parts:
        lower_p = p.lower()
        if not any(w in lower_p for w in junk_words) and 1 <= len(p.split()) <= 4 and len(p) <= 35:
            return p

    if domain_brand and len(domain_brand) > 2 and domain_brand not in ('en', 'de', 'wikipedia', 'medium', 'youtube'):
        return domain_brand.capitalize()

    return parts[0] if parts else 'Unknown Company'


def search_new_companies(location_query):
    """Search for companies in the specified location using DDGS and geocode them via OpenStreetMap."""
    if not DDGS:
        print("Error: DDGS package not available.")
        return []

    # Try search queries
    queries = [
        f"companies in {location_query}",
        f"top businesses directory {location_query}"
    ]
    
    results = []
    for query in queries:
        try:
            res = list(DDGS(timeout=12).text(query, max_results=12))
            if res:
                results.extend(res)
                break
        except Exception as e:
            print(f"DDGS search error for query '{query}': {e}")

    if not results:
        return []

    session = requests.Session()
    
    # 1. Geocode base location once to provide fallback coordinates
    base_lat, base_lng = None, None
    try:
        geo_url = "https://nominatim.openstreetmap.org/search"
        base_resp = session.get(
            geo_url,
            params={'q': location_query, 'format': 'json', 'limit': 1},
            headers=NOMINATIM_HEADERS,
            timeout=6
        )
        if base_resp.status_code == 200:
            data = base_resp.json()
            if data:
                base_lat = float(data[0]['lat'])
                base_lng = float(data[0]['lon'])
    except Exception as e:
        print(f"Base location geocode error for {location_query}: {e}")

    companies = []
    seen_names = set()

    for r in results:
        title = r.get('title', '')
        url = r.get('href') or r.get('link') or ''
        body = r.get('body') or r.get('snippet') or ''

        # Skip unwanted domains
        domain = urlparse(url).netloc.lower() if url else ''
        if any(skip in domain for skip in SKIP_DOMAINS):
            continue

        name = _clean_company_name(title, url)
        if not name or name.lower() in seen_names:
            continue
        seen_names.add(name.lower())

        # Attempt exact geocoding with small delay to respect Nominatim policy
        lat, lng = None, None
        try:
            time.sleep(0.6)
            geocode_url = "https://nominatim.openstreetmap.org/search"
            params = {
                'q': f"{name}, {location_query}",
                'format': 'json',
                'limit': 1
            }
            geo_resp = session.get(geocode_url, params=params, headers=NOMINATIM_HEADERS, timeout=5)
            if geo_resp.status_code == 200:
                geo_data = geo_resp.json()
                if geo_data:
                    lat = float(geo_data[0]['lat'])
                    lng = float(geo_data[0]['lon'])
        except Exception as e:
            print(f"Geocode lookup error for {name}: {e}")

        # Fallback to base location coordinates with slight jitter so markers don't overlap
        if (lat is None or lng is None) and base_lat is not None and base_lng is not None:
            lat = round(base_lat + random.uniform(-0.02, 0.02), 6)
            lng = round(base_lng + random.uniform(-0.02, 0.02), 6)

        # Quick snippet extraction for email / phone if present
        emails = EMAIL_RE.findall(body)
        email = emails[0] if emails else ''
        phones = PHONE_RE.findall(body)
        phone = phones[0] if phones else ''

        companies.append({
            'name': name,
            'location_string': location_query,
            'latitude': lat,
            'longitude': lng,
            'website': url,
            'description': body[:300] if body else '',
            'email': email,
            'phone': phone,
            'ai_status': 'pending'
        })

    return companies