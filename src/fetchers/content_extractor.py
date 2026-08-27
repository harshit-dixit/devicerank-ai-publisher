"""Extracts clean article text, metadata, and featured images from web pages."""

import re
from typing import Dict, Optional
import requests
from bs4 import BeautifulSoup
from src.utils.logger import logger

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36 DeviceRankPublisher/1.0"
)


class ContentExtractor:
    """Helper class to fetch full article body and high-res OpenGraph images."""

    @staticmethod
    def extract(url: str, timeout: int = 10) -> Dict[str, Optional[str]]:
        """
        Fetches web page and extracts:
        - full text content
        - meta description
        - OpenGraph image (og:image)
        """
        result = {
            "text": None,
            "meta_description": None,
            "og_image": None,
            "author": None,
        }

        try:
            headers = {"User-Agent": USER_AGENT}
            response = requests.get(url, headers=headers, timeout=timeout)
            if response.status_code != 200:
                logger.debug(f"Failed to fetch content from {url}, status: {response.status_code}")
                return result

            soup = BeautifulSoup(response.text, "html.parser")

            # Extract og:image
            og_img = soup.find("meta", property="og:image") or soup.find("meta", attrs={"name": "twitter:image"})
            if og_img and og_img.get("content"):
                result["og_image"] = og_img["content"].strip()

            # Extract meta description
            desc = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", property="og:description")
            if desc and desc.get("content"):
                result["meta_description"] = desc["content"].strip()

            # Extract author
            author = soup.find("meta", attrs={"name": "author"}) or soup.find("meta", property="article:author")
            if author and author.get("content"):
                result["author"] = author["content"].strip()

            # Remove unwanted tags
            for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form", "svg"]):
                tag.decompose()

            # Find main content container if possible
            main_container = (
                soup.find("article")
                or soup.find("main")
                or soup.find("div", class_=re.compile(r"post|article|content|entry", re.I))
                or soup.body
            )

            if main_container:
                # Extract paragraphs
                paragraphs = main_container.find_all(["p", "h2", "h3", "li"])
                cleaned_text = "\n\n".join(
                    p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 25
                )
                result["text"] = cleaned_text[:5000]  # Cap at 5k characters

        except Exception as e:
            logger.debug(f"Error extracting content from {url}: {e}")

        return result
