import argparse
import sys
import urllib.request
import xml.etree.ElementTree as ET


DEFAULT_FEED_URL = "https://news.yahoo.co.jp/rss/topics/top-picks.xml"


def fetch_articles(url, limit):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "cui-news/1.0", "Accept": "application/rss+xml, application/atom+xml, application/xml"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        root = ET.fromstring(response.read())

    articles = []
    for item in root.findall(".//item") + root.findall(".//{*}entry"):
        title = item.findtext("title") or item.findtext("{*}title")
        link_element = item.find("link")
        if link_element is None:
            link_element = item.find("{*}link")
        link = ""
        if link_element is not None:
            link = link_element.get("href") or link_element.text or ""
        if title and link:
            articles.append((title.strip(), link.strip()))
        if len(articles) >= limit:
            break
    return articles


def main():
    parser = argparse.ArgumentParser(description="Fetch and display articles from an RSS or Atom news feed.")
    parser.add_argument("--url", default=DEFAULT_FEED_URL, help="RSS or Atom feed URL")
    parser.add_argument("--limit", type=int, default=10, help="maximum number of articles to display")
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be greater than zero")

    try:
        articles = fetch_articles(args.url, args.limit)
    except (OSError, ET.ParseError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    if not articles:
        print("No articles found.")
        return 0

    for number, (title, link) in enumerate(articles, start=1):
        print(f"{number}. {title}\n   {link}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
