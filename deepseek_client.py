import os
import json
import re
import asyncio
from urllib.parse import urlparse
import httpx
from openai import AsyncOpenAI
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# Ensure environment variables are loaded
load_dotenv()

try:
    from ddgs import DDGS
except ImportError:
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        DDGS = None

try:
    from fake_useragent import UserAgent
    _UA = UserAgent()
    _random_ua = lambda: _UA.random
except Exception:
    _random_ua = lambda: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

DEFAULT_MODEL = "deepseek-flash"
MAX_TURNS     = 4

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
                    'sentry.io', 'cloudflare', 'placeholder', '@example', '@test']

JUNK_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.css',
                   '.js', '.woff', '.woff2', '.ttf', '.eot', '.ico')

EMAIL_RE = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')

PHONE_PATTERNS = [
    r'\+\d{1,3}[\s\-]?\(0?\d{2,4}\)[\s\-]?[\d\s\.\-]{6,}',
    r'\+\d{1,3}[\s\-]?\d{2,4}[\s\-]?\d{3,4}[\s\-]?\d{3,4}',
    r'(?:\+90|0)?\s?[2-5]\d{2}\s?\d{3}\s?\d{2}\s?\d{2}',
    r'(?:tel|phone|fax|mobile|whatsapp|gsm|telefon|telefono)[\s:]+([+\d\s\-()./]{8,})',
    r'\b(?:\+?[0-9]{1,3}[-.\s]?)?\(?[0-9]{2,4}\)?[-.\s]?[0-9]{3,4}[-.\s]?[0-9]{3,4}\b'
]

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

_FETCH_SEMAPHORE = asyncio.Semaphore(10)

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
        e = e.strip().lower()
        if not e or '@' not in e:
            continue
        if any(w in e for w in JUNK_EMAIL_WORDS):
            continue
        if any(e.endswith(ext) for ext in JUNK_EXTENSIONS):
            continue
        parts = e.split('@')
        if len(parts) == 2 and '.' in parts[1]:
            cleaned.add(e)
    return list(cleaned)

def _clean_phones(raw_phones):
    seen, out = set(), []
    for p in raw_phones:
        cleaned = re.sub(r'[^\d+]', '', str(p)).strip()
        if cleaned.startswith('00') and len(cleaned) > 10:
            cleaned = '+' + cleaned[2:]
        if len(cleaned) >= 8 and cleaned not in seen:
            seen.add(cleaned)
            out.append(p.strip())
    return out

def _extract_base_url(url):
    parts = url.split('/')
    if len(parts) >= 3:
        return '/'.join(parts[:3])
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
            self._http = httpx.AsyncClient(
                timeout=12.0,
                follow_redirects=True,
                headers={"User-Agent": _random_ua()}
            )
        return self._http

    async def close(self):
        if self._http and not self._http.is_closed:
            try:
                await self._http.aclose()
            except Exception:
                pass
            self._http = None

    async def _fix_name_with_ai(self, raw_name, country_hint, callback=None):
        if _is_simple_ascii(raw_name) and country_hint:
            cc = country_hint.strip().upper()[:2]
            kw = _COUNTRY_KW.get(cc, {"contact_page": DEFAULT_CONTACT_KW, "address": DEFAULT_ADDRESS_KW})
            return {
                "corrected_name": raw_name,
                "company_name_english": raw_name,
                "country": country_hint,
                "country_code": cc,
                "keywords": kw
            }
        system = "Given a company name and country hint, output JSON: {'corrected_name':'...','company_name_english':'...','country':'...','country_code':'XX','keywords':{'contact_page':['...'],'address':['...']}}"
        try:
            resp = await self.client.chat.completions.create(
                model=DEFAULT_MODEL,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": f"Company: '{raw_name}'. Country: {country_hint or 'Unknown'}"}
                ],
                response_format={"type": "json_object"}
            )
            return json.loads(resp.choices[0].message.content)
        except Exception as e:
            print(f"[DeepSeekClient._fix_name_with_ai] notice: {e}")
            return {
                "corrected_name": raw_name,
                "company_name_english": raw_name,
                "country": country_hint or "",
                "country_code": "",
                "keywords": {"contact_page": DEFAULT_CONTACT_KW, "address": DEFAULT_ADDRESS_KW}
            }

    async def extract_company_data(self, system_prompt, buyer_name, country, model=None, callback=None):
        model = model or DEFAULT_MODEL
        ai_meta = await self._fix_name_with_ai(buyer_name, country, callback)
        corrected = ai_meta.get("corrected_name", buyer_name)
        english_name = ai_meta.get("company_name_english", buyer_name)
        country_english = ai_meta.get("country", country)
        country_code = ai_meta.get("country_code", "")
        contact_kw = ai_meta.get("keywords", {}).get("contact_page") or DEFAULT_CONTACT_KW
        address_kw = ai_meta.get("keywords", {}).get("address") or DEFAULT_ADDRESS_KW

        enhanced_prompt = (
            system_prompt +
            f"\n\nCONTEXT:\n"
            f"- Company: '{corrected}' (English: '{english_name}')\n"
            f"- Country: '{country_english}' ({country_code})\n"
            f"- Target keywords: {contact_kw}\n"
            f"- Goal: Find official email, phone, website, and physical address.\n"
            f"- When finished or calling final stop, return a valid JSON object with keys:\n"
            f"  company_name_english, country_english, country_code, email, emails_found (list), phone, phones_found (list), website, address.\n"
        )
        messages = [
            {"role": "system", "content": enhanced_prompt},
            {"role": "user", "content": f"Find contact info (email, phone, website, address) for '{corrected}' in '{country}'."}
        ]

        found_emails = set()
        found_phones = set()
        found_website = None

        final_json_str = None
        turn_count = 0

        for turn in range(MAX_TURNS):
            turn_count = turn + 1
            try:
                response = await self.client.chat.completions.create(
                    model=model,
                    messages=messages,
                    tools=TOOLS,
                    tool_choice="auto"
                )
                msg = response.choices[0].message
                if not msg.tool_calls:
                    final_json_str = self._clean_json(msg.content)
                    break

                messages.append(msg)

                async def _exec_tool(tc):
                    args = json.loads(tc.function.arguments) if isinstance(tc.function.arguments, str) else tc.function.arguments
                    if tc.function.name == "web_search":
                        res = await self._perform_search(args.get("query", ""), contact_kw, address_kw)
                        return tc.id, res
                    elif tc.function.name == "fetch_page":
                        res = await self._fetch_page_with_retry(args.get("url", ""), address_kw)
                        return tc.id, res
                    return tc.id, {"error": "Unknown tool"}

                results = await asyncio.gather(*(_exec_tool(tc) for tc in msg.tool_calls))

                for tc_id, result in results:
                    if isinstance(result, dict):
                        for em in result.get("emails_found", []):
                            found_emails.add(em)
                        for ph in result.get("phones_found", []):
                            found_phones.add(ph)
                        if result.get("url") and not found_website:
                            found_website = result.get("url")
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": json.dumps(result, ensure_ascii=False)
                    })

                # If we have both email and phone, ask to finalize
                if found_emails and found_phones and turn >= 1:
                    messages.append({
                        "role": "user",
                        "content": "Return the final JSON now with all collected data."
                    })

            except Exception as e:
                print(f"[DeepSeekClient.extract_company_data turn {turn}] error: {e}")
                break

        # If not finalized yet, request final output
        if not final_json_str:
            messages.append({
                "role": "user",
                "content": (
                    f"Stop search now and return a JSON object with: company_name_english, "
                    f"country_english, country_code, email, emails_found, phone, phones_found, website, address. "
                    f"Use null or empty list for missing values."
                )
            })
            try:
                final = await self.client.chat.completions.create(model=model, messages=messages)
                final_json_str = self._clean_json(final.choices[0].message.content)
            except Exception as e:
                print(f"[DeepSeekClient final completion] error: {e}")

        # Normalize and merge parsed results
        normalized_data = {
            "company_name_english": english_name,
            "country_english": country_english,
            "country_code": country_code,
            "emails_found": list(found_emails),
            "phones_found": list(found_phones),
            "email": next(iter(found_emails), None),
            "phone": next(iter(found_phones), None),
            "website": found_website,
            "address": None
        }

        if final_json_str:
            try:
                parsed = json.loads(final_json_str)
                if isinstance(parsed, dict):
                    # Merge LLM findings with scraper findings
                    llm_emails = parsed.get("emails_found") or []
                    if isinstance(llm_emails, str): llm_emails = [llm_emails]
                    if parsed.get("email"): llm_emails.append(parsed["email"])
                    found_emails.update(_filter_emails(llm_emails))

                    llm_phones = parsed.get("phones_found") or []
                    if isinstance(llm_phones, str): llm_phones = [llm_phones]
                    if parsed.get("phone"): llm_phones.append(parsed["phone"])
                    found_phones.update(llm_phones)

                    normalized_data["emails_found"] = list(found_emails)
                    normalized_data["phones_found"] = list(found_phones)
                    normalized_data["email"] = parsed.get("email") or next(iter(found_emails), None)
                    normalized_data["phone"] = parsed.get("phone") or next(iter(found_phones), None)
                    normalized_data["website"] = parsed.get("website") or found_website
                    normalized_data["address"] = parsed.get("address")
                    if parsed.get("company_name_english"):
                        normalized_data["company_name_english"] = parsed["company_name_english"]
            except Exception as e:
                print(f"[DeepSeekClient parse JSON] error: {e}")

        return json.dumps(normalized_data, ensure_ascii=False), turn_count

    async def _run_ddgs_search(self, query, max_results=8):
        if not DDGS or not query:
            return []
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(
                None,
                lambda: list(DDGS(timeout=12).text(query, max_results=max_results))
            )
        except Exception as e:
            print(f"[DeepSeekClient._run_ddgs_search] error: {e}")
            return []

    async def _perform_search(self, query, contact_kw, address_kw, callback=None, max_retries=1):
        results = None
        for attempt in range(max_retries + 1):
            try:
                results = await self._run_ddgs_search(query)
                if results:
                    break
            except Exception:
                await asyncio.sleep(0.5 * (attempt + 1))

        if not results:
            return [{"error": "No results found."}]

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
                "url": url
            })
            if len(output) >= 5:
                break

        return output if output else [{"error": "No non-directory results found."}]

    async def _fetch_page_with_retry(self, url, address_kw, max_retries=1):
        for attempt in range(max_retries + 1):
            result = await self._fetch_page(url, address_kw)
            if "error" not in result:
                return result
            await asyncio.sleep(0.5)
        return {"error": "Failed to fetch page", "emails_found": [], "phones_found": [], "page_text_preview": ""}

    async def _fetch_page(self, url, address_kw):
        async with _FETCH_SEMAPHORE:
            try:
                http = await self._get_http()
                resp = await http.get(url)
                resp.raise_for_status()
                html = resp.text
                soup = BeautifulSoup(html, "html.parser")

                # 1. Emails from mailto links
                emails = []
                for a in soup.find_all("a", href=re.compile(r"^mailto:", re.I)):
                    mailto = a.get("href", "").replace("mailto:", "").split("?")[0].strip()
                    if "@" in mailto:
                        emails.append(mailto)

                # Emails from raw text
                for m in EMAIL_RE.findall(html):
                    emails.append(m)

                emails = _filter_emails(emails)

                # 2. Phones from tel links
                phones_raw = []
                for a in soup.find_all("a", href=re.compile(r"^(?:tel:|whatsapp://send\?phone=)", re.I)):
                    href_val = a.get("href", "")
                    clean_val = re.sub(r'^(?:tel:|whatsapp://send\?phone=)', '', href_val, flags=re.I).split('?')[0].strip()
                    if clean_val:
                        phones_raw.append(clean_val)

                # Phones from regex patterns
                for pat in PHONE_PATTERNS:
                    for m in re.findall(pat, html, re.IGNORECASE):
                        if isinstance(m, str):
                            phones_raw.append(m)

                phones = _clean_phones(phones_raw)

                # 3. Clean text preview
                text = soup.get_text(separator=" ")
                text_clean = re.sub(r'\s+', ' ', text)[:1200].strip()

                return {
                    "url": url,
                    "emails_found": emails[:10],
                    "phones_found": phones[:10],
                    "page_text_preview": text_clean
                }
            except Exception as e:
                return {"error": str(e), "url": url}

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
            text = text[i:j+1]
        return text
