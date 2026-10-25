import unittest
from unittest.mock import MagicMock, patch

import news


class FetchArticlesTests(unittest.TestCase):
    @patch("news.urllib.request.urlopen")
    def test_fetches_rss_articles_up_to_limit(self, urlopen):
        response = MagicMock()
        response.read.return_value = b"""<rss><channel>
            <item><title>First</title><link>https://example.com/1</link></item>
            <item><title>Second</title><link>https://example.com/2</link></item>
        </channel></rss>"""
        urlopen.return_value.__enter__.return_value = response

        articles = news.fetch_articles("https://example.com/feed", 1)

        self.assertEqual(articles, [("First", "https://example.com/1")])

    @patch("news.urllib.request.urlopen")
    def test_fetches_atom_articles(self, urlopen):
        response = MagicMock()
        response.read.return_value = b"""<feed xmlns="http://www.w3.org/2005/Atom">
            <entry><title>Atom headline</title><link href="https://example.com/atom"/></entry>
        </feed>"""
        urlopen.return_value.__enter__.return_value = response

        articles = news.fetch_articles("https://example.com/feed", 10)

        self.assertEqual(articles, [("Atom headline", "https://example.com/atom")])


if __name__ == "__main__":
    unittest.main()
