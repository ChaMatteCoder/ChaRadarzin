from __future__ import annotations

from django.test import SimpleTestCase, override_settings

from monitoring.preview import ProductPreviewError, fetch_preview_html
from monitoring.url_safety import (
    ExactProductUrlError,
    ensure_public_dns,
    validate_exact_product_url,
)


class FakeResponse:
    def __init__(self, *, status=200, body=b"<html>ok</html>", headers=None):
        self.status_code = status
        self._body = body
        self.headers = headers or {"Content-Type": "text/html; charset=utf-8"}
        self.encoding = "utf-8"
        self.closed = False

    def iter_content(self, chunk_size):
        yield self._body

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def public_resolver(*_args, **_kwargs):
    return [(2, 1, 6, "", ("8.8.8.8", 443))]


class ExactUrlSafetyTest(SimpleTestCase):
    def test_canonicalizes_only_exact_supported_product_urls(self):
        amazon = validate_exact_product_url(
            "https://www.amazon.com.br/gp/product/b07yd579wm?ref_=tracking&th=1"
        )
        kabum = validate_exact_product_url(
            "https://www.kabum.com.br/produto/167492/ssd-crucial?utm_source=x&seller_offer_id=55"
        )

        self.assertEqual(amazon.canonical_url, "https://www.amazon.com.br/dp/B07YD579WM?th=1")
        self.assertEqual(
            kabum.canonical_url,
            "https://www.kabum.com.br/produto/167492/ssd-crucial?seller_offer_id=55",
        )

    def test_rejects_ssrf_and_lookalike_shapes(self):
        invalid = (
            "http://www.amazon.com.br/dp/B07YD579WM",
            "https://127.0.0.1/dp/B07YD579WM",
            "https://www.amazon.com.br@127.0.0.1/dp/B07YD579WM",
            "https://evilamazon.com.br/dp/B07YD579WM",
            "https://www.amazon.com.br:8443/dp/B07YD579WM",
            "https://www.kabum.com.br/busca/ssd",
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ExactProductUrlError):
                validate_exact_product_url(value)

    def test_dns_guard_rejects_private_addresses(self):
        private_resolver = lambda *_args, **_kwargs: [(2, 1, 6, "", ("169.254.169.254", 443))]
        with self.assertRaises(ExactProductUrlError) as context:
            ensure_public_dns("www.amazon.com.br", resolver=private_resolver)
        self.assertEqual(context.exception.code, "NON_PUBLIC_ADDRESS")

    def test_fetcher_disables_redirects_and_closes_response(self):
        response = FakeResponse(status=302, headers={"Location": "http://127.0.0.1/"})
        session = FakeSession(response)
        with self.assertRaises(ProductPreviewError) as context:
            fetch_preview_html(
                "https://www.amazon.com.br/dp/B07YD579WM",
                session=session,
                resolver=public_resolver,
            )
        self.assertEqual(context.exception.code, "REDIRECT_REJECTED")
        self.assertFalse(session.calls[0][1]["allow_redirects"])
        self.assertTrue(response.closed)

    @override_settings(PRODUCT_PREVIEW_MAX_BYTES=8)
    def test_fetcher_caps_decompressed_response(self):
        response = FakeResponse(body=b"0123456789")
        with self.assertRaises(ProductPreviewError) as context:
            fetch_preview_html(
                "https://www.amazon.com.br/dp/B07YD579WM",
                session=FakeSession(response),
                resolver=public_resolver,
            )
        self.assertEqual(context.exception.code, "RESPONSE_TOO_LARGE")
