import os
import json
import re
import random
import asyncio
from urllib.parse import urlparse
import httpx
from openai import AsyncOpenAI
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

try:
    from ddgs import DDGS
except ImportError:
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        DDGS = None

# Static UA list: fake_useragent downloads data at import time, which is slow
# and unreliable on Render cold starts.
_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
]
_random_ua = lambda: random.choice(_UAS)

# "deepseek-flash" is not a valid DeepSeek API model. Use deepseek-chat (override via env).
DEFAULT_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
MAX_TURNS = 4
MAX_HTML_BYTES = 2_000_000

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

JUNK_EMAIL_WORDS = ['example', 'sample', 'your@', 'yourname', 'wixpress', 'sentry',
                    'schema.org', 'noreply', 'no-reply', 'cloudflare', 'placeholder',
                    '@test.', 'test@', '@domain.', 'user@domain']

JUNK_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.css',
                   '.js', '.woff', '.woff2', '.ttf', '.eot', '.ico')

EMAIL_RE = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')

# Applied to visible text only (not raw HTML) to avoid matching ids/css/scripts.
PHONE_PATTERNS = [
    r'\+\d{1,3}[\s\-]?\(0?\d{2,4}\)[\s\-]?[\d\s\.\-]{6,}',
    r'\+\d{1,3}[\s\-]?\d{2,4}[\s\-]?\d{3,4}[\s\-]?\d{3,4}',
    r'(?:\+90|0)?\s?[2-5]\d{2}\s?\d{3}\s?\d{2}\s?\d{2}',
]
LABELED_PHONE_RE = re.compile(
    r'(?:tel|phone|fax|mobile|whatsapp|gsm|telefon|telefono)[\s:]+([+\d\s\-()./]{8,})', re.I)

DEFAULT_CONTACT_KW = ["Contact", "İletişim", "Kontakt", "Contacto", "Contato", "Impressum", "Imprint"]
DEFAULT_ADDRESS_KW = ["Address", "Adres", "Adresse", "Dirección", "Endereço"]

_COUNTRY_KW = {
    "TR": {"contact_page": ["Contact", "İletişim"], "address": ["Address", "Adres"]},
    "DE": {"contact_page": ["Contact", "Kontakt", "Impressum"], "address": ["Address", "Adresse", "Anschrift"]},
    "ES": {"contact_page": ["Contact", "Contacto"], "address": ["Address", "Dirección"]},
    "BR": {"contact_page": ["Contact", "Contato"], "address": ["Address", "Endereço"]},
    "PT": {"contact_page": ["Contact", "Contato"], "address": ["Address", "Endereço"]},
    "FR": {"contact_page": ["Contact", "Coordonnées"], "address": ["Address", "Adresse"]},
    "IT": {"contact_page": ["Contact", "Contatti"], "address": ["Address", "Indirizzo"]},
}

TOOLS = [
    {"type": "function", "function": {
        "name": "web_search",
        "description": "Search the web for company contact info, official website, email, and phone.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Search query"}
        }, "required": ["query"]}
    }},
    {"type": "function", "function": {
        "name": "fetch_page",
        "description": "Fetch a webpage to extract contact details, emails, phones, and address.",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string", "description": "URL to fetch"}
        }, "required": ["url"]}
    }},
]


def _filter_emails(emails):
    cleaned = set()
    for e in emails:
        e = str(e).strip().lower().strip(".,;:<>()[]\"'")
        if not e or '@' not in e:
            continue
        if any(w in e for w in JUNK_EMAIL_WORDS):
            continue
        if any(e.endswith(ext) for ext in JUNK_EXTENSIONS):
            continue
        parts = e.split('@')
        if len(parts) == 2 and parts[0] and '.' in parts[1]:
            cleaned.add(e)
    return sorted(cleaned)


def _clean_phones(raw_phones):
    seen, out = set(), []
    for p in raw_phones:
        p = str(p).strip()
        cleaned = re.sub(r'[^\d+]', '', p)
        if cleaned.startswith('00') and len(cleaned) > 10:
            cleaned = '+' + cleaned[2:]
        if 8 <= len(re.sub(r'\D', '', cleaned)) <= 15 and cleaned not in seen:
            seen.add(cleaned)
            out.append(p)
    return out


def _is_simple_ascii(name):
    try:
        name.encode('ascii')
        return True
    except UnicodeEncodeError:
        return False


class DeepSeekClient:
    def __init__(self, api_key=None, base_url="https://api.deepseek.com"):
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY")
        if not self.api_key:
            raise RuntimeError("DEEPSEEK_API_KEY is not set (add it in Render > Environment).")
        self.client = AsyncOpenAI(api_key=self.api_key, base_url=base_url,
                                  timeout=60.0, max_retries=2)
        self._http = None
        self._http_loop = None
        self._sem = None
        self._sem_loop = None

    def _get_sem(self):
        # Semaphores are bound to an event loop; create lazily per loop.
        loop = asyncio.get_running_loop()
        if self._sem is None or self._sem_loop is not loop:
            self._sem = asyncio.Semaphore(10)
            self._sem_loop = loop
        return self._sem

    async def _get_http(self):
        loop = asyncio.get_running_loop()
        if self._http is None or self._http.is_closed or self._http_loop is not loop:
            self._http = httpx.AsyncClient(
                timeout=httpx.Timeout(12.0, connect=6.0),
                follow_redirects=True,
                headers={"User-Agent": _random_ua(),
                         "Accept-Language": "en-US,en;q=0.9"},
            )
            self._http_loop = loop
        return self._http

    async def close(self):
        if self._http and not self._http.is_closed:
            try:
                await self._http.aclose()
            except Exception:
                pass
        self._http = None
        try:
            await self.client.close()
        except Exception:
            pass

    async def _fix_name_with_ai(self, raw_name, country_hint, callback=None):
        if _is_simple_ascii(raw_name) and country_hint:
            cc = country_hint.strip().upper()[:2]
            kw = _COUNTRY_KW.get(cc, {"contact_page": DEFAULT_CONTACT_KW, "address": DEFAULT_ADDRESS_KW})
            return {
                "corrected_name": raw_name,
                "company_name_english": raw_name,
                "country": country_hint,
                "country_code": cc,
                "keywords": kw,
            }
        system = ("Given a company name and country hint, output JSON: "
                  "{'corrected_name':'...','company_name_english':'...','country':'...',"
                  "'country_code':'XX','keywords':{'contact_page':['...'],'address':['...']}}")
        try:
            resp = await self.client.chat.completions.create(
                model=DEFAULT_MODEL,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": f"Company: '{raw_name}'. Country: {country_hint or 'Unknown'}"},
                ],
                response_format={"type": "json_object"},
            )
            data = json.loads(resp.choices[0].message.content)
            return data if isinstance(data, dict) else {}
        except Exception as e:
            print(f"[DeepSeekClient._fix_name_with_ai] notice: {e}")
            return {
                "corrected_name": raw_name,
                "company_name_english": raw_name,
                "country": country_hint or "",
                "country_code": "",
                "keywords": {"contact_page": DEFAULT_CONTACT_KW, "address": DEFAULT_ADDRESS_KW},
            }

    @staticmethod
    def _assistant_dict(msg):
        d = {"role": "assistant", "content": msg.content or ""}
        if msg.tool_calls:
            d["tool_calls"] = [{
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"},
            } for tc in msg.tool_calls]
        return d

    async def extract_company_data(self, system_prompt, buyer_name, country, model=None, callback=None):
        model = model or DEFAULT_MODEL
        ai_meta = await self._fix_name_with_ai(buyer_name, country, callback)
        corrected = ai_meta.get("corrected_name") or buyer_name
        english_name = ai_meta.get("company_name_english") or buyer_name
        country_english = ai_meta.get("country") or country
        country_code = ai_meta.get("country_code") or ""
        kws = ai_meta.get("keywords") or {}
        contact_kw = kws.get("contact_page") or DEFAULT_CONTACT_KW
        address_kw = kws.get("address") or DEFAULT_ADDRESS_KW

        enhanced_prompt = (
            system_prompt +
            f"\n\nCONTEXT:\n"
            f"- Company: '{corrected}' (English: '{english_name}')\n"
            f"- Country: '{country_english}' ({country_code})\n"
            f"- Target keywords: {contact_kw}\n"
            f"- Goal: Find official email, phone, website, and physical address.\n"
            f"- When finished, return a valid JSON object with keys:\n"
            f"  company_name_english, country_english, country_code, email, emails_found (list), "
            f"phone, phones_found (list), website, address.\n"
        )
        messages = [
            {"role": "system", "content": enhanced_prompt},
            {"role": "user", "content": f"Find contact info (email, phone, website, address) for '{corrected}' in '{country}'."},
        ]

        found_emails, found_phones = set(), set()
        found_website = None
        final_json_str = None
        turn_count = 0

        async def _exec_tool(tc):
            try:
                raw = tc.function.arguments
                args = json.loads(raw) if isinstance(raw, str) and raw else (raw or {})
            except Exception:
                return tc.id, {"error": "Invalid tool arguments"}
            try:
                if tc.function.name == "web_search":
                    return tc.id, await self._perform_search(args.get("query", ""), contact_kw, address_kw)
                if tc.function.name == "fetch_page":
                    return tc.id, await self._fetch_page_with_retry(args.get("url", ""), address_kw)
            except Exception as e:
                return tc.id, {"error": str(e)}
            return tc.id, {"error": "Unknown tool"}

        for turn in range(MAX_TURNS):
            turn_count = turn + 1
            try:
                response = await self.client.chat.completions.create(
                    model=model, messages=messages, tools=TOOLS, tool_choice="auto")
                msg = response.choices[0].message
                if not msg.tool_calls:
                    final_json_str = self._clean_json(msg.content)
                    break

                messages.append(self._assistant_dict(msg))
                results = await asyncio.gather(*(_exec_tool(tc) for tc in msg.tool_calls))

                for tc_id, result in results:
                    if isinstance(result, dict) and "error" not in result:
                        found_emails.update(result.get("emails_found", []))
                        found_phones.update(result.get("phones_found", []))
                        if result.get("emails_found") and not found_website and result.get("url"):
                            p = urlparse(result["url"])
                            if p.scheme and p.netloc:
                                found_website = f"{p.scheme}://{p.netloc}"
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": json.dumps(result, ensure_ascii=False)[:8000],
                    })

                if found_emails and found_phones and turn >= 1:
                    messages.append({"role": "user",
                                     "content": "Return the final JSON now with all collected data."})
            except Exception as e:
                print(f"[DeepSeekClient.extract_company_data turn {turn}] error: {e}")
                break

        if not final_json_str:
            # Ensure no dangling tool_calls without responses before the final request.
            messages.append({
                "role": "user",
                "content": ("Stop searching now and return only a JSON object with: company_name_english, "
                            "country_english, country_code, email, emails_found, phone, phones_found, "
                            "website, address. Use null or empty list for missing values."),
            })
            try:
                final = await self.client.chat.completions.create(model=model, messages=messages)
                final_json_str = self._clean_json(final.choices[0].message.content)
            except Exception as e:
                print(f"[DeepSeekClient final completion] error: {e}")

        normalized = {
            "company_name_english": english_name,
            "country_english": country_english,
            "country_code": country_code,
            "emails_found": sorted(found_emails),
            "phones_found": sorted(found_phones),
            "email": next(iter(sorted(found_emails)), None),
            "phone": next(iter(sorted(found_phones)), None),
            "website": found_website,
            "address": None,
        }

        if final_json_str:
            try:
                parsed = json.loads(final_json_str)
                if isinstance(parsed, dict):
                    llm_emails = parsed.get("emails_found") or []
                    if isinstance(llm_emails, str):
                        llm_emails = [llm_emails]
                    if parsed.get("email"):
                        llm_emails = list(llm_emails) + [parsed["email"]]
                    valid_llm_emails = _filter_emails(llm_emails)
                    found_emails.update(valid_llm_emails)

                    llm_phones = parsed.get("phones_found") or []
                    if isinstance(llm_phones, str):
                        llm_phones = [llm_phones]
                    if parsed.get("phone"):
                        llm_phones = list(llm_phones) + [parsed["phone"]]
                    found_phones.update(_clean_phones(llm_phones))

                    llm_email = _filter_emails([parsed["email"]]) if parsed.get("email") else []
                    normalized["emails_found"] = sorted(found_emails)
                    normalized["phones_found"] = sorted(found_phones)
                    normalized["email"] = (llm_email[0] if llm_email else normalized["email"])
                    normalized["phone"] = parsed.get("phone") or normalized["phone"]
                    normalized["website"] = parsed.get("website") or found_website
                    normalized["address"] = parsed.get("address")
                    if parsed.get("company_name_english"):
                        normalized["company_name_english"] = parsed["company_name_english"]
            except Exception as e:
                print(f"[DeepSeekClient parse JSON] error: {e}")

        return json.dumps(normalized, ensure_ascii=False), turn_count

    def _ddgs_sync(self, query, max_results):
        # Newer ddgs picks backends itself; older duckduckgo_search needs a fallback.
        try:
            return list(DDGS(timeout=12).text(query, max_results=max_results))
        except TypeError:
            return list(DDGS().text(query, max_results=max_results))

    async def _run_ddgs_search(self, query, max_results=8):
        if not DDGS or not query:
            return []
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._ddgs_sync, query, max_results)

    async def _perform_search(self, query, contact_kw, address_kw, callback=None, max_retries=2):
        results = []
        for attempt in range(max_retries + 1):
            try:
                results = await self._run_ddgs_search(query)
                if results:
                    break
            except Exception as e:
                # DDG often rate-limits datacenter IPs (Render); back off and retry.
                print(f"[DeepSeekClient._perform_search] attempt {attempt}: {e}")
            await asyncio.sleep(1.0 * (attempt + 1))

        if not results:
            return {"error": "No results found."}

        output = []
        for r in results:
            url = r.get("href") or r.get("link") or ""
            domain = ""
            if url:
                try:
                    domain = urlparse(url).netloc.lower()
                except Exception:
                    pass
            if any(skip in domain for skip in SKIP_DOMAINS):
                continue
            output.append({
                "title": r.get("title", ""),
                "snippet": r.get("body") or r.get("snippet", ""),
                "url": url,
            })
            if len(output) >= 5:
                break

        return {"results": output} if output else {"error": "No non-directory results found."}

    async def _fetch_page_with_retry(self, url, address_kw, max_retries=1):
        last = {"error": "Failed to fetch page"}
        for _ in range(max_retries + 1):
            last = await self._fetch_page(url, address_kw)
            if "error" not in last:
                return last
            await asyncio.sleep(0.5)
        return {"error": last.get("error", "Failed to fetch page"), "url": url,
                "emails_found": [], "phones_found": [], "page_text_preview": ""}

    async def _fetch_page(self, url, address_kw):
        if not url or not url.startswith(("http://", "https://")):
            return {"error": "Invalid URL", "url": url}
        async with self._get_sem():
            try:
                http = await self._get_http()
                resp = await http.get(url)
                resp.raise_for_status()
                ctype = resp.headers.get("content-type", "")
                if "html" not in ctype and "text" not in ctype:
                    return {"error": f"Unsupported content type: {ctype}", "url": url}
                html = resp.text[:MAX_HTML_BYTES]
                soup = BeautifulSoup(html, "html.parser")

                emails = []
                for a in soup.find_all("a", href=re.compile(r"^mailto:", re.I)):
                    mailto = a.get("href", "")[7:].split("?")[0].strip()
                    if "@" in mailto:
                        emails.append(mailto)

                phones_raw = []
                for a in soup.find_all("a", href=re.compile(r"^(?:tel:|whatsapp://send\?phone=)", re.I)):
                    val = re.sub(r'^(?:tel:|whatsapp://send\?phone=)', '', a.get("href", ""),
                                 flags=re.I).split('?')[0].strip()
                    if val:
                        phones_raw.append(val)

                for tag in soup(["script", "style", "noscript"]):
                    tag.decompose()
                text = re.sub(r'\s+', ' ', soup.get_text(separator=" ")).strip()

                emails += EMAIL_RE.findall(text)
                emails = _filter_emails(emails)

                phones_raw += LABELED_PHONE_RE.findall(text)
                for pat in PHONE_PATTERNS:
                    phones_raw += re.findall(pat, text)
                phones = _clean_phones(phones_raw)

                return {
                    "url": str(resp.url),
                    "emails_found": emails[:10],
                    "phones_found": phones[:10],
                    "page_text_preview": text[:1200],
                }
            except Exception as e:
                return {"error": str(e) or e.__class__.__name__, "url": url}

    def _clean_json(self, text):
        if not text:
            return None
        text = text.strip()
        if "```" in text:
            for marker in ["```json", "```"]:
                if marker in text:
                    start = text.find(marker) + len(marker)
                    end = text.rfind("```")
                    if end > start:
                        text = text[start:end].strip()
                    break
        i, j = text.find("{"), text.rfind("}")
        if i != -1 and j > i:
            text = text[i:j + 1]
        return text
