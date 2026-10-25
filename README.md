# cui-news

A Python command-line tool that fetches headlines from an RSS or Atom news feed.

## Usage

Run with the default Yahoo! Japan headlines feed:

```sh
python3 news.py
```

Choose another feed and limit the number of headlines:

```sh
python3 news.py --url https://example.com/feed.xml --limit 5
```

The script uses only the Python standard library.
