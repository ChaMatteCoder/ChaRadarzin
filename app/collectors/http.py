from __future__ import annotations

import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"
)


class FetchError(RuntimeError):
    pass


def canonicalize_product_url(url: str) -> str:
    parts = urlsplit(url)
    hostname = (parts.hostname or "").casefold()
    if hostname == "amazon.com.br" or hostname.endswith(".amazon.com.br"):
        asin = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", parts.path, re.IGNORECASE)
        if asin:
            query = parse_qs(parts.query)
            kept_query = urlencode({"th": query["th"][0]}) if query.get("th") else ""
            return f"https://www.amazon.com.br/dp/{asin.group(1).upper()}" + (
                f"?{kept_query}" if kept_query else ""
            )
    if hostname == "kabum.com.br" or hostname.endswith(".kabum.com.br"):
        query = parse_qs(parts.query)
        kept_query = (
            urlencode({"seller_offer_id": query["seller_offer_id"][0]})
            if query.get("seller_offer_id")
            else ""
        )
        return urlunsplit(("https", "www.kabum.com.br", parts.path, kept_query, ""))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def fetch_html(url: str, *, timeout: float = 20.0, retries: int = 1) -> str:
    canonical_url = canonicalize_product_url(url)
    request = Request(
        canonical_url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "pt-BR,pt;q=0.9",
            "Cache-Control": "no-cache",
        },
    )
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urlopen(request, timeout=timeout) as response:
                payload = response.read()
                charset = response.headers.get_content_charset() or "utf-8"
                html = payload.decode(charset, errors="replace")
                hostname = (urlsplit(canonical_url).hostname or "").casefold()
                if hostname.endswith("amazon.com.br") and "productTitle" not in html:
                    last_error = FetchError(
                        "A Amazon retornou uma tela intermediaria em vez do produto"
                    )
                    if attempt < retries:
                        time.sleep(0.6 * (attempt + 1))
                        continue
                    raise last_error
                return html
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(0.6 * (attempt + 1))
    raise FetchError(f"Falha ao baixar {canonical_url}: {last_error}")
