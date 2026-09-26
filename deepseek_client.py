import os, json, re, asyncio, httpx
from openai import AsyncOpenAI
from bs4 import BeautifulSoup

try:
    from duckduckgo_search import AsyncDDGS
    ASYNC_SEARCH = True
except ImportError:
    ASYNC_SEARCH = False
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS

try:
    from fake_useragent import UserAgent
    _UA = UserAgent()
    _random_ua = lambda: _UA.random
except ImportError:
    _random_ua = lambda: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"

DEFAULT_MODEL = "deepseek-v4-flash"
MAX_TURNS     = 15

SKIP_DOMAINS = frozenset([
    'dnb.com', 'yellowpages', 'yelp.com', 'linkedin.com', 'facebook.com',
    'bloomberg.com', 'zoominfo.com', 'crunchbase.com', 'glassdoor.com',
    'indeed.com', 'scribd.com', 'opencorporates.com', 'kompass.com',
    'b2bhint.com', 'volza.com', 'bizorg.su', 'panjiva.com',
    'importgenius.com', 'zauba.com', 'trademap.org', 'europages.com',
    'alibaba.com', 'made-in-china.com', 'globalsources.com', 'thomasnet.com',
    'manta.com', 'hoovers.com', 'spoke.com', 'corporationwiki.com',
    'buzzfile.com', 'owler.com', 'datanyze.com', 'apollo.io',
    'instagram.com', 'twitter.com', 'x.com', 'youtube.com',
    'tiktok.com', 'pinterest.com', 'wikipedia.org', 'reddit.com',
])

JUNK_EMAIL_WORDS = ['example', 'test', 'sample', 'your@', 'domain', 'wix',
                    'wordpress', 'sentry', 'schema', 'noreply', 'no-reply',
                    '.png', '.jpg', '.gif', 'sentry.io', 'cloudflare',
                    'placeholder', '@example', '@test']

EMAIL_RE = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')

CONTACT_PATHS = [
    "/contact", "/contact-us", "/contacts", "/en/contact", "/en/contact-us",
    "/iletisim", "/tr/iletisim", "/kontakt", "/de/kontakt",
    "/contacto", "/es/contacto", "/about/contact", "/about-us/contact",
    "/contact.html", "/contactus", "/reach-us", "/get-in-touch",
    "/about", "/about-us", "/en/about", "/en/about-us",
    "/impressum", "/imprint",
]

PHONE_PATTERNS = [
    r'\d{10,15}\+', r'\+\d{10,15}',
    r'\+\d{1,3}[\s\-]?\d{2,4}[\s\-]?\d{3,4}[\s\-]?\d{3,4}',
    r'\+\d{1,3}[\s\-]?\(\d+\)[\s\-]?[\d\s\.\-]+',
    r'(?:tel|phone|fax|call|mobile|whatsapp|gsm|telefon|telefono)[\s:]+([+\d\s\-()./]+)',
    r'0\d{9,12}',
    r'(?:\+90|0)?\s?[2-5]\d{2}\s?\d{3}\s?\d{2}\s?\d{2}',
    r'(?:\+\d{1,3})?\s?\(0?\d{2,4}\)\s?[\d\s\.\-]{6,}',
    r'href="tel:([^"]+)"',
    r'href="whatsapp://send\?phone=(\d+)"',
]

DEFAULT_CONTACT_KW = ["Contact", "İletişim", "Kontakt", "Contacto", "Contato"]
DEFAULT_ADDRESS_KW = ["Address", "Adres", "Adresse", "Dirección", "Endereço"]

_COUNTRY_KW = {
    "TR": {"contact_page": ["Contact", "İletişim"], "address": ["Address", "Adres"]},
    "DE": {"contact_page": ["Contact", "Kontakt"], "address": ["Address", "Adresse"]},
    "ES": {"contact_page": ["Contact", "Contacto"], "address": ["Address", "Dirección"]},
    "BR": {"contact_page": ["Contact", "Contato"], "address": ["Address", "Endereço"]},
    "PT": {"contact_page": ["Contact", "Contato"], "address": ["Address", "Endereço"]},
    "FR": {"contact_page": ["Contact"], "address": ["Address", "Adresse"]},
    "IT": {"contact_page": ["Contact", "Contatti"], "address": ["Address", "Indirizzo"]},
}

_FETCH_SEMAPHORE = asyncio.Semaphore(12)
_PROBE_CONCURRENCY = 8

TOOLS = [
    {"type": "function", "function": {
        "name": "web_search",
        "description": "Search the web for company contact info.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Search query"}
        }, "required": ["query"]}
    }},
    {"type": "function", "function": {
        "name": "fetch_page",
        "description": "Fetch a webpage.",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string", "description": "URL to fetch"}
        }, "required": ["url"]}
    }},
]

def _filter_emails(emails):
    cleaned = set()
    for e in emails:
        e = e.strip().lower()
        if e and not any(w in e for w in JUNK_EMAIL_WORDS):
            if not e.endswith('.css') and not e.endswith('.js') and '@' in e:
                cleaned.add(e)
    return list(cleaned)

def _clean_phones(raw_phones):
    seen, out = set(), []
    for p in raw_phones:
        cleaned = re.sub(r'[^\d+]', '', str(p))
        if cleaned.startswith('00') and len(cleaned) > 10:
            cleaned = '+' + cleaned[2:]
        if len(cleaned) >= 10 and cleaned not in seen:
            seen.add(cleaned)
            if not cleaned.startswith('+') and len(cleaned) > 10:
                cleaned = '+' + cleaned
            out.append(cleaned)
    return out

def _extract_base_url(url):
    parts = url.split('/')
    if len(parts) >= 3: return '/'.join(parts[:3])
    return url

def _is_simple_ascii(name):
    try:
        name.encode('ascii')
        return True
    except UnicodeEncodeError:
        return False

class DeepSeekClient:
    def __init__(self, api_key=None, base_url="https://api.deepseek.com"):
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY")
        self.client = AsyncOpenAI(api_key=self.api_key, base_url=base_url)
        self._http = None

    async def _get_http(self):
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=12.0, follow_redirects=True, headers={"User-Agent": _random_ua()})
        return self._http

    async def close(self):
        if self._http and not self._http.is_closed: await self._http.aclose()

    async def _fix_name_with_ai(self, raw_name, country_hint, callback=None):
        if _is_simple_ascii(raw_name) and country_hint:
            cc = country_hint.strip().upper()[:2]
            kw = _COUNTRY_KW.get(cc, {"contact_page": DEFAULT_CONTACT_KW, "address": DEFAULT_ADDRESS_KW})
            return {"corrected_name": raw_name, "company_name_english": raw_name, "country": country_hint, "country_code": cc, "keywords": kw}
        system = "Given a company name and country hint, output JSON: {'corrected_name':'...','company_name_english':'...','country':'...','country_code':'XX','keywords':{'contact_page':['...'],'address':['...']}}"
        try:
            resp = await self.client.chat.completions.create(model=DEFAULT_MODEL, messages=[{"role": "system", "content": system}, {"role": "user", "content": f"Company: '{raw_name}'. Country: {country_hint or 'Unknown'}"}], response_format={"type": "json_object"})
            return json.loads(resp.choices[0].message.content)
        except Exception:
            return {"corrected_name": raw_name, "company_name_english": raw_name, "country": country_hint or "", "country_code": "", "keywords": {"contact_page": DEFAULT_CONTACT_KW, "address": DEFAULT_ADDRESS_KW}}

    async def extract_company_data(self, system_prompt, buyer_name, country, model=None, callback=None):
        model = model or DEFAULT_MODEL
        ai_meta = await self._fix_name_with_ai(buyer_name, country, callback)
        corrected = ai_meta.get("corrected_name", buyer_name)
        english_name = ai_meta.get("company_name_english", "")
        country_english = ai_meta.get("country", country)
        country_code = ai_meta.get("country_code", "")
        contact_kw = ai_meta.get("keywords", {}).get("contact_page") or DEFAULT_CONTACT_KW
        address_kw = ai_meta.get("keywords", {}).get("address") or DEFAULT_ADDRESS_KW

        enhanced_prompt = system_prompt + f"\n\nCONTEXT:\n- Corrected name: '{corrected}' | English: '{english_name}'\n- Country: '{country_english}' ({country_code})\n- Contact keywords: {contact_kw}\n- BE EFFICIENT: stop searching once you have email+phone+address.\n"
        messages = [{"role": "system", "content": enhanced_prompt}, {"role": "user", "content": f"Find contact info for '{corrected}' in '{country}'."}]
        found_emails, found_phones = set(), set()

        for turn in range(MAX_TURNS):
            try:
                response = await self.client.chat.completions.create(model=model, messages=messages, tools=TOOLS, tool_choice="auto")
                msg = response.choices[0].message
                if not msg.tool_calls: return (self._clean_json(msg.content), turn) if msg.content else (None, turn)
                messages.append(msg)

                async def _exec_tool(tc):
                    args = json.loads(tc.function.arguments)
                    if tc.function.name == "web_search": return tc.id, await self._perform_search(args.get("query", ""), contact_kw, address_kw, callback=callback)
                    elif tc.function.name == "fetch_page": return tc.id, await self._fetch_page_with_retry(args.get("url", ""), address_kw)
                    return tc.id, {"error": "Unknown tool"}

                results = await asyncio.gather(*(_exec_tool(tc) for tc in msg.tool_calls))

                for tc_id, result in results:
                    if isinstance(result, dict):
                        found_emails.update(result.get("emails_found", result.get("all_emails", [])))
                        found_phones.update(result.get("phones_found", result.get("all_phones", [])))
                    messages.append({"role": "tool", "tool_call_id": tc_id, "content": json.dumps(result, ensure_ascii=False)})

                if found_emails and found_phones and turn >= 2:
                    messages.append({"role": "user", "content": f"You have found emails and phones. Return the JSON now with all data collected. Set company_name_english='{english_name}', country_english='{country_english}', country_code='{country_code}'."})
            except Exception:
                return None, turn
        
        messages.append({"role": "user", "content": f"STOP. Return JSON now with whatever data you found. Missing fields → null/[]. company_name_english='{english_name}', country_english='{country_english}', country_code='{country_code}'."})
        try:
            final = await self.client.chat.completions.create(model=model, messages=messages)
            return self._clean_json(final.choices[0].message.content), MAX_TURNS
        except Exception:
            return None, MAX_TURNS

    async def _run_ddgs_search(self, query, max_results=8):
        if ASYNC_SEARCH:
            async with AsyncDDGS() as ddgs: return [r async for r in ddgs.text(query, max_results=max_results)]
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: list(DDGS(timeout=15).text(query, max_results=max_results)))

    async def _perform_search(self, query, contact_kw, address_kw, callback=None, max_retries=1):
        results = None
        for attempt in range(max_retries + 1):
            try:
                results = await self._run_ddgs_search(query)
                break
            except Exception: await asyncio.sleep(0.5 * (attempt + 1))
        if not results: return [{"error": "No results."}]
        output = [{"title": r.get("title", ""), "snippet": r.get("body", r.get("snippet", "")), "url": r.get("href", r.get("link", ""))} for r in results[:6]]
        return output

    async def _fetch_page_with_retry(self, url, address_kw, max_retries=1):
        for attempt in range(max_retries + 1):
            result = await self._fetch_page(url, address_kw)
            if "error" not in result: return result
            await asyncio.sleep(0.5)
        return {"error": "Failed", "emails_found": [], "phones_found": [], "page_text_preview": ""}

    async def _fetch_page(self, url, address_kw):
        async with _FETCH_SEMAPHORE:
            try:
                http = await self._get_http()
                resp = await http.get(url)
                resp.raise_for_status()
                html = resp.text
                soup = BeautifulSoup(html, "html.parser")
                emails = list(set(EMAIL_RE.findall(html)))
                for a in soup.find_all("a", href=re.compile(r"^mailto:", re.I)):
                    mailto = a.get("href", "").replace("mailto:", "").split("?")[0].strip()
                    if "@" in mailto and mailto not in emails: emails.append(mailto)
                emails = _filter_emails(emails)
                phones_raw = []
                for pat in PHONE_PATTERNS:
                    for m in re.findall(pat, html, re.IGNORECASE):
                        if isinstance(m, str): phones_raw.append(m)
                return {"url": url, "emails_found": list(set(emails))[:15], "phones_found": _clean_phones(phones_raw)[:10], "page_text_preview": soup.get_text(separator=" ")[:1000]}
            except Exception as e:
                return {"error": str(e)}

    def _clean_json(self, text):
        if not text: return None
        text = text.strip()
        if "```" in text:
            for marker in ["```json", "```"]:
                if marker in text:
                    start = text.find(marker) + len(marker)
                    end = text.rfind("```")
                    if end > start: text = text[start:end].strip()
                    break
        i, j = text.find("{"), text.rfind("}")
        if i != -1 and j > i: text = text[i:j+1]
        return text
