from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx


def validate_public_url(url: str, *, resolve: bool = True) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("only public HTTP(S) URLs are allowed")
    if parsed.username or parsed.password:
        raise ValueError("URL credentials are not allowed")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith((".local", ".internal")):
        raise ValueError("private or local URLs are not allowed")

    addresses: set[str] = set()
    try:
        addresses.add(str(ipaddress.ip_address(hostname)))
    except ValueError:
        if resolve:
            try:
                addresses.update(
                    item[4][0]
                    for item in socket.getaddrinfo(hostname, parsed.port or 443)
                )
            except OSError as exc:
                raise ValueError("URL hostname could not be resolved") from exc
    for value in addresses:
        address = ipaddress.ip_address(value)
        if not address.is_global:
            raise ValueError("private or local URLs are not allowed")
    return url


class _ReadableHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._tag: str | None = None
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        self._tag = tag
        if re.fullmatch(r"h[1-6]", tag):
            self.parts.append("\n" + "#" * int(tag[1]) + " ")
        elif tag in {"p", "div", "section", "article", "br", "pre", "blockquote"}:
            self.parts.append("\n")
        elif tag == "li":
            self.parts.append("\n- ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self._ignored_depth:
            self._ignored_depth -= 1
        if self._tag == tag:
            self._tag = None

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        text = re.sub(r"\s+", " ", data).strip()
        if not text:
            return
        if self._tag == "title" and not self.title:
            self.title = text
        self.parts.append(text + " ")

    def markdown(self) -> str:
        rendered = "".join(self.parts)
        rendered = re.sub(r"[ \t]+\n", "\n", rendered)
        rendered = re.sub(r"\n{3,}", "\n\n", rendered)
        return rendered.strip()


@dataclass(frozen=True, slots=True)
class FetchedPage:
    url: str
    title: str
    text: str
    content_type: str
    etag: str | None
    last_modified: str | None


def fetch_web_page(url: str, *, max_bytes: int, timeout: float = 20) -> FetchedPage:
    current = url
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=timeout) as client:
        for _ in range(6):
            validate_public_url(current)
            with client.stream(
                "GET",
                current,
                headers={"User-Agent": "InfraResearch/0.3"},
            ) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("redirect response did not include a location")
                    current = urljoin(current, location)
                    continue
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").split(";", 1)[0]
                if content_type not in {
                    "text/html",
                    "text/plain",
                    "text/markdown",
                    "application/xhtml+xml",
                }:
                    raise ValueError(f"unsupported web content type: {content_type or 'unknown'}")
                content = bytearray()
                for block in response.iter_bytes():
                    content.extend(block)
                    if len(content) > max_bytes:
                        raise ValueError("web page exceeds configured size limit")
                encoding = response.encoding or "utf-8"
                decoded = bytes(content).decode(encoding, errors="replace")
                if content_type in {"text/html", "application/xhtml+xml"}:
                    parser = _ReadableHTML()
                    parser.feed(decoded)
                    text = parser.markdown()
                    title = parser.title
                else:
                    text = decoded.strip()
                    title = ""
                if not text:
                    raise ValueError("web page contained no readable text")
                return FetchedPage(
                    url=str(response.url),
                    title=title,
                    text=text,
                    content_type=content_type,
                    etag=response.headers.get("etag"),
                    last_modified=response.headers.get("last-modified"),
                )
        raise ValueError("web page exceeded the redirect limit")
