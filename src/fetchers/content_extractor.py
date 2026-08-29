"""Extracts clean article text, metadata, and featured images from web pages."""

import re
from typing import Dict, Optional
import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry
from bs4 import BeautifulSoup
from config.settings import settings
from src.utils.logger import logger
from src.utils.sanitizer import sanitize_url

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36 DeviceRankPublisher/1.0"
)


def _get_http_session() -> requests.Session:
    """Creates a configured requests.Session with connection pooling and retries."""
    session = requests.Session()
    retries = Retry(
        total=settings.http_max_retries,
        backoff_factor=0.5,
        status_forcelist=[500, 502, 503, 504],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retries, pool_connections=10, pool_maxsize=20)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": USER_AGENT})
    return session


_GLOBAL_SESSION = _get_http_session()


class ContentExtractor:
    """Helper class to fetch full article body and high-res OpenGraph images."""

    @staticmethod
    def extract(url: str, timeout: Optional[int] = None) -> Dict[str, Optional[str]]:
        """Fetches web page and extracts full text content, meta description, and HTTPS og:image."""
        result: Dict[str, Optional[str]] = {
            "text": None,
            "meta_description": None,
            "og_image": None,
            "author": None,
            "final_url": None,
        }

        req_timeout = timeout or settings.http_timeout_seconds

        try:
            response = _GLOBAL_SESSION.get(
                url,
                timeout=(5.0, float(req_timeout)),
                allow_redirects=True,
            )
            if response.status_code != 200:
                logger.debug(f"Failed to fetch content from {url}, status: {response.status_code}")
                return result
            result["final_url"] = str(response.url)

            soup = BeautifulSoup(response.text, "html.parser")

            # Extract og:image
            og_img = soup.find("meta", property="og:image") or soup.find("meta", attrs={"name": "twitter:image"})
            if og_img and og_img.get("content"):
                raw_img = str(og_img["content"]).strip()
                result["og_image"] = sanitize_url(raw_img, enforce_https=True)

            # Extract meta description
            desc = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", property="og:description")
            if desc and desc.get("content"):
                result["meta_description"] = str(desc["content"]).strip()

            # Extract author
            author = soup.find("meta", attrs={"name": "author"}) or soup.find("meta", property="article:author")
            if author and author.get("content"):
                result["author"] = str(author["content"]).strip()

            # Remove unwanted tags
            for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form", "svg", "noscript"]):
                tag.decompose()

            # Find main content container if possible
            main_container = (
                soup.find("article")
                or soup.find("main")
                or soup.find("div", class_=re.compile(r"post|article|content|entry", re.I))
                or soup.body
            )

            if main_container:
                paragraphs = main_container.find_all(["p", "h2", "h3", "li"])
                cleaned_text = "\n\n".join(
                    p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 25
                )
                result["text"] = cleaned_text[:5000]  # Cap at 5k characters

        except Exception as e:
            logger.debug(f"Error extracting content from {url}: {e}")

        return result
