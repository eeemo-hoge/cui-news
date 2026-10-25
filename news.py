#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
news — 各国のニュースを「外 = 海外から見たその国」と「内 = その国の国内メディア」で
       並べて流し、保存・検索・書き出し・印刷する端末ニュースリーダー。

  news                        全フィードを1本のタイムラインで流す (新着が来たら即流れる)
                              起動時は直近10件、更新1回につき新着は10件まで (-n / -m で変更)
  news -c jp,ru               国を絞って流す
  news wall [us cn jp ru]     tmux で画面を4分割して国ごとに流す
                              日本語・英語の見出しは、やわらかい口調・小文字にしてから流す (--no-soft で原文のまま)
  news grep 語 [...]          保存済みの記事を検索
  news export -f pdf ...      text / csv / tsv / md / html / pdf / jsonl に書き出し
  news print ...              印刷 (lp / lpr)
  news open ID                記事をブラウザで開く
  news later                  あとで読む一覧 (流れている画面で「>> ID」と打つと入る)
  news page -o news.html      スマホで読める HTML を1枚書き出す
  news serve --lan            スマホのブラウザ向けに配信 (新着が自動で足される)
  news check                  フィードの疎通確認
  news fetch                  取得して保存だけする (cron / systemd timer 用)
  news feeds | path | countries

保存先はただのテキスト(TSV)なので grep / rg / find / awk がそのまま使える:
  grep -r 'NVIDIA' "$(news path)"
  find "$(news path)" -name 'ru.*' -newermt '2026-09-28'

Python 3.8 以上の標準ライブラリだけで動く。
Android でも Termux でそのまま動く (pkg install python)。狭い画面では自動で2段表示になる。
任意: tmux (wall) / lp,lpr (print) / weasyprint, wkhtmltopdf, chromium (pdf) / translate-shell (翻訳)
"""

import argparse
import codecs
import concurrent.futures as cf
import csv
import email.utils
import fcntl
import gzip
import hashlib
import html
import html.entities
import http.server
import io
import json
import os
import random
import re
import select
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zlib
from datetime import date, datetime, timedelta, timezone

VERSION = "2.4"
UA = "Mozilla/5.0 (X11; Linux x86_64) news-cli/%s (+terminal RSS reader)" % VERSION
MAX_BODY = 12 * 1024 * 1024
SUMMARY_MAX = 280
UMASK = os.umask(0o022)
os.umask(UMASK)

# ─────────────────────────────────────────────────────────────── 国・視点 ──

COUNTRIES = {  # code: (日本語名, 英語名)
    "jp": ("日本", "Japan"), "us": ("アメリカ", "USA"), "cn": ("中国", "China"),
    "ru": ("ロシア", "Russia"), "kr": ("韓国", "South Korea"), "kp": ("北朝鮮", "North Korea"),
    "tw": ("台湾", "Taiwan"), "hk": ("香港", "Hong Kong"), "gb": ("イギリス", "UK"),
    "de": ("ドイツ", "Germany"), "fr": ("フランス", "France"), "it": ("イタリア", "Italy"),
    "es": ("スペイン", "Spain"), "ua": ("ウクライナ", "Ukraine"), "in": ("インド", "India"),
    "ir": ("イラン", "Iran"), "il": ("イスラエル", "Israel"), "tr": ("トルコ", "Türkiye"),
    "br": ("ブラジル", "Brazil"), "au": ("オーストラリア", "Australia"), "ca": ("カナダ", "Canada"),
    "sa": ("サウジアラビア", "Saudi Arabia"), "id": ("インドネシア", "Indonesia"),
    "vn": ("ベトナム", "Vietnam"), "ph": ("フィリピン", "Philippines"), "th": ("タイ", "Thailand"),
    "mx": ("メキシコ", "Mexico"), "za": ("南アフリカ", "South Africa"), "eu": ("EU", "EU"),
    "world": ("世界", "World"),
}
LENS = {"out": ("外", "海外から見た"), "in": ("内", "国内")}
LENS_ORDER = ("out", "in")
CC_COLOR = {"jp": 203, "us": 39, "cn": 214, "ru": 141, "kr": 44, "gb": 75, "de": 178,
            "fr": 111, "ua": 227, "in": 208, "tw": 79, "ir": 173}
PALETTE = [81, 150, 180, 110, 174, 115, 186, 146, 216, 109]
LENS_COLOR = {"out": 81, "in": 150}
WEEKDAY_JA = "月火水木金土日"
DEFAULT_WALL = ["us", "cn", "jp", "ru"]

DEFAULT_FEEDS = r"""# news フィード定義 ─────────────────────────────────────────────
# 1行1フィード。空白区切り:
#
#   国コード  視点  ID  URL  [key=value ...]  表示名
#
# 視点   out = 外: 海外から見たその国 (外国メディア / 外国人向けの英字紙)
#        in  = 内: その国の多くの人が見る国内メディア (経済紙を含む)
#
# オプション (URL と表示名のあいだに書く。値に空白は使えないので \s で書く)
#   match=正規表現   タイトル+概要+URL がこれに一致する記事だけ採る
#                    (世界ニュースのフィードから特定の国の記事だけ抜く用)
#   skip=正規表現    一致した記事を捨てる
#   strip=正規表現   タイトルから消す (末尾のサイト名など)
#   tz=+09:00        日付にタイムゾーンが書かれていないフィードの既定TZ
#
# 国を増やすときは行を足すだけ。組み込みに無い国名は country 行で登録できる:
#   country  np  ネパール  Nepal
#
# 印: ✔ = 2026-09-29 に取得を確認 / ? = 有名なURLだが未確認 (news check で確かめる)

## 日本 ──────────────────────────────────────────────────────────
jp out japantoday   https://japantoday.com/feed  skip=/category/world/  Japan Today
jp out japantimes   https://www.japantimes.co.jp/feed/  The Japan Times
jp in  nhk          https://www3.nhk.or.jp/rss/news/cat0.xml  NHK
jp in  yahoo        https://news.yahoo.co.jp/rss/topics/top-picks.xml  Yahoo!ニュース
jp in  nikkei       https://assets.wor.jp/rss/rdf/nikkei/news.rdf  日経速報
jp in  toyokeizai   https://toyokeizai.net/list/feed/rss  strip=\s*\|[^|]*\|\s*東洋経済オンライン\s*$  東洋経済
#  ✔ japantoday  ? japantimes  ? nhk  ? yahoo  ✔ nikkei(wor.jp の非公式ミラー)  ✔ toyokeizai

## アメリカ ──────────────────────────────────────────────────────
us out aljazeera-us https://www.aljazeera.com/xml/rss/all.xml  match=(?<![\w.])(US|U\.S\.|USA|United\sStates|Americans?|Washington|Trump|Pentagon|White\sHouse|Congress|Wall\sStreet)(?!\w)  Al Jazeera
us out bbc-us       https://feeds.bbci.co.uk/news/world/us_and_canada/rss.xml  BBC US&Canada
us in  npr          https://feeds.npr.org/1001/rss.xml  NPR
us in  nyt          https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml  NYTimes
us in  fox          https://moxie.foxnews.com/google-publisher/latest.xml  Fox News
us in  wsj          https://feeds.content.dowjones.io/public/rss/RSSMarketsMain  WSJ Markets
#  ✔ aljazeera  ? bbc-us  ✔ npr  ? nyt  ✔ fox  ✔ wsj

## 中国 ──────────────────────────────────────────────────────────
cn out scmp         https://www.scmp.com/rss/4/feed  SCMP
cn out aljazeera-cn https://www.aljazeera.com/xml/rss/all.xml  match=(?<!\w)(China|Chinese|Beijing|Xi\sJinping|Taiwan|Hong\sKong)(?!\w)  Al Jazeera
cn in  chinanews    https://www.chinanews.com.cn/rss/scroll-news.xml  tz=+08:00  中新网
cn in  chinanews-cj https://www.chinanews.com.cn/rss/finance.xml  tz=+08:00  中新网财经
cn in  36kr         https://36kr.com/feed  tz=+08:00  36氪
#  ✔ scmp(更新が遅いことがある)  ✔ aljazeera  ✔ chinanews  ✔ chinanews-cj  ✔ 36kr

## ロシア ────────────────────────────────────────────────────────
ru out moscowtimes  https://www.themoscowtimes.com/rss/news  The Moscow Times
ru out meduza-en    https://meduza.io/rss/en/all  Meduza(EN)
ru in  ria          https://ria.ru/export/rss2/archive/index.xml  tz=+03:00  РИА Новости
ru in  tass         https://tass.ru/rss/v2.xml  tz=+03:00  ТАСС
ru in  rbc          https://rssexport.rbc.ru/rbcnews/news/30/full.rss  tz=+03:00  РБК
ru in  lenta        https://lenta.ru/rss/news  tz=+03:00  Lenta.ru
#  ✔ moscowtimes  ✔ meduza-en  ✔ ria  ✔ tass  ✔ rbc  ✔ lenta

## 追加の例 (未確認。行頭の # を外して `news check` で確かめる) ─────────
# kr out koreaherald  https://www.koreaherald.com/common/rss_xml.php?ct=102  Korea Herald
# kr in  yonhap       https://www.yna.co.kr/rss/news.xml  연합뉴스
# gb out aljazeera-gb https://www.aljazeera.com/xml/rss/all.xml  match=(?<![\w.])(UK|U\.K\.|Britain|British|London|Starmer)(?!\w)  Al Jazeera
# gb in  bbc-uk       https://feeds.bbci.co.uk/news/uk/rss.xml  BBC UK
# de in  tagesschau   https://www.tagesschau.de/xml/rss2  tagesschau
# ua out kyivindep    https://kyivindependent.com/news-archive/rss/  Kyiv Independent
# ua in  pravda-ua    https://www.pravda.com.ua/rss/  Українська правда
"""

# ─────────────────────────────────────────────────────────────── パス ──


def config_path():
    if os.environ.get("NEWS_FEEDS"):
        return os.path.expanduser(os.environ["NEWS_FEEDS"])
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "news", "feeds.tsv")


def data_home():
    if os.environ.get("NEWS_HOME"):
        return os.path.expanduser(os.environ["NEWS_HOME"])
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "news")


def archive_root():
    return os.path.join(data_home(), "archive")


def state_dir(*parts):
    d = os.path.join(data_home(), "state", *parts)
    os.makedirs(d, exist_ok=True)
    return d


def atomic_write(path, data):
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data if isinstance(data, bytes) else data.encode("utf-8"))
        os.chmod(tmp, 0o666 & ~UMASK)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class Lock:
    """複数の news プロセス(wall の各ペイン, cron の fetch)が同時に保存しても壊れないように。"""

    def __init__(self, name="archive"):
        self.path = os.path.join(state_dir(), name + ".lock")
        self.f = None

    def __enter__(self):
        self.f = open(self.path, "a")
        fcntl.flock(self.f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()


# ─────────────────────────────────────────────────────────────── 設定 ──


class Feed:
    def __init__(self, cc, lens, fid, url, name, opts, order):
        self.cc, self.lens, self.id, self.url, self.name = cc, lens, fid, url, name
        self.opts = opts
        self.order = order
        self.match = re.compile(opts["match"]) if opts.get("match") else None
        self.skip = re.compile(opts["skip"]) if opts.get("skip") else None
        self.strip = re.compile(opts["strip"]) if opts.get("strip") else None
        self.tz = parse_tz(opts.get("tz"))


class Config:
    def __init__(self, feeds, countries, path, warnings):
        self.feeds = feeds
        self.countries = countries
        self.path = path
        self.warnings = warnings

    def cc_order(self):
        seen = []
        for f in self.feeds:
            if f.cc not in seen:
                seen.append(f.cc)
        return seen

    def select(self, ccs=None, lens=None, ids=None):
        out = []
        for f in self.feeds:
            if ccs and f.cc not in ccs:
                continue
            if lens and f.lens != lens:
                continue
            if ids and f.id not in ids:
                continue
            out.append(f)
        return out


def parse_tz(s):
    if not s:
        return None
    m = re.fullmatch(r"([+-])(\d{1,2}):?(\d{2})?", s.strip())
    if not m:
        return None
    sign = -1 if m.group(1) == "-" else 1
    return timezone(sign * timedelta(hours=int(m.group(2)), minutes=int(m.group(3) or 0)))


def parse_feeds(text, path="<default>"):
    feeds, countries, warnings, ids = [], dict(COUNTRIES), [], set()
    for n, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        tok = s.split()
        if tok[0].lower() == "country":
            if len(tok) >= 3:
                countries[tok[1].lower()] = (tok[2], " ".join(tok[3:]) or tok[2])
            else:
                warnings.append("%s:%d: country 行は `country 国コード 日本語名 [英語名]`" % (path, n))
            continue
        if len(tok) < 4:
            warnings.append("%s:%d: 列が足りない: %s" % (path, n, s))
            continue
        cc, lens, fid, url = tok[0].lower(), tok[1].lower(), tok[2], tok[3]
        if lens not in LENS:
            warnings.append("%s:%d: 視点は out か in: %s" % (path, n, lens))
            continue
        if fid in ids:
            warnings.append("%s:%d: ID が重複: %s" % (path, n, fid))
            continue
        opts, name = {}, []
        for t in tok[4:]:
            m = re.match(r"^([a-z]+)=(.*)$", t)
            if m and not name:
                opts[m.group(1)] = m.group(2)
            else:
                name.append(t)
        try:
            feed = Feed(cc, lens, fid, url, " ".join(name) or fid, opts, len(feeds))
        except re.error as e:
            warnings.append("%s:%d: 正規表現の誤り (%s): %s" % (path, n, fid, e))
            continue
        ids.add(fid)
        feeds.append(feed)
    return feeds, countries, warnings


def load_config(write_default=True):
    path = config_path()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            text = f.read()
    else:
        text = DEFAULT_FEEDS
        if write_default:
            try:
                atomic_write(path, DEFAULT_FEEDS)
                eprint("news: 既定のフィード定義を作りました → %s" % path)
            except OSError:
                pass
    feeds, countries, warnings = parse_feeds(text, path)
    for w in warnings:
        eprint("news: " + w)
    return Config(feeds, countries, path, warnings)


def cname(cfg, cc):
    return cfg.countries.get(cc, (cc.upper(), cc.upper()))[0]


# ─────────────────────────────────────────────────────────────── 取得 ──


class Fetched:
    def __init__(self, url):
        self.url = url
        self.ok = False
        self.status = ""
        self.body = b""
        self.ctype = ""
        self.error = ""
        self.cached = False
        self.ms = 0
        self.entries = []
        self.mode = ""


def _cache_paths(url):
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    d = state_dir("http")
    return os.path.join(d, h + ".json"), os.path.join(d, h + ".body")


def fetch_url(url, force=False, min_age=30, timeout=20):
    """条件付き GET (ETag / Last-Modified)。本文もキャッシュして、304 や
    別プロセスが直前に取ったばかりのときはそれを使う(相手のサーバーに優しく)。"""
    r = Fetched(url)
    t0 = time.time()
    meta_p, body_p = _cache_paths(url)
    meta = {}
    try:
        with open(meta_p, encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, ValueError):
        meta = {}
    have_body = os.path.exists(body_p)

    def from_cache(status):
        with open(body_p, "rb") as f:
            r.body = f.read()
        r.ctype = meta.get("ctype", "")
        r.ok, r.status, r.cached = True, status, True

    if not force and have_body and time.time() - meta.get("fetched_at", 0) < min_age:
        from_cache("cache")
        r.ms = int((time.time() - t0) * 1000)
        return r

    headers = {
        "User-Agent": UA,
        "Accept": "application/rss+xml, application/atom+xml, application/rdf+xml;q=0.9, "
                  "application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5",
        "Accept-Encoding": "gzip, deflate",
    }
    if have_body and not force:
        if meta.get("etag"):
            headers["If-None-Match"] = meta["etag"]
        if meta.get("last_modified"):
            headers["If-Modified-Since"] = meta["last_modified"]
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read(MAX_BODY + 1)
            enc = (resp.headers.get("Content-Encoding") or "").lower()
            r.ctype = resp.headers.get("Content-Type") or ""
            etag, lm, code = resp.headers.get("ETag"), resp.headers.get("Last-Modified"), resp.status
        if len(data) > MAX_BODY:
            raise ValueError("本文が大きすぎる")
        if enc == "gzip" or data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)
        elif enc == "deflate":
            try:
                data = zlib.decompress(data)
            except zlib.error:
                data = zlib.decompress(data, -15)
        r.body, r.ok, r.status = data, True, str(code)
        atomic_write(body_p, data)
        atomic_write(meta_p, json.dumps({"url": url, "etag": etag, "last_modified": lm,
                                         "ctype": r.ctype, "fetched_at": time.time()}))
    except urllib.error.HTTPError as e:
        if e.code == 304 and have_body:
            meta["fetched_at"] = time.time()
            atomic_write(meta_p, json.dumps(meta))
            from_cache("304")
        else:
            r.error = "HTTP %d" % e.code
    except urllib.error.URLError as e:
        r.error = str(getattr(e, "reason", e))
    except Exception as e:  # timeout, 壊れた gzip, 接続リセットなど
        r.error = "%s: %s" % (type(e).__name__, e) if str(e) else type(e).__name__
    r.ms = int((time.time() - t0) * 1000)
    return r


# ─────────────────────────────────────────────────────────────── 解析 ──

CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
TAG_RE = re.compile(r"<[^>]*>")
WS_RE = re.compile(r"\s+")
ENC_ALIASES = {"shift_jis": "cp932", "shift-jis": "cp932", "sjis": "cp932", "x-sjis": "cp932",
               "windows-31j": "cp932", "gb2312": "gb18030", "gbk": "gb18030", "x-gbk": "gb18030",
               "euc-jp": "euc_jis_2004", "x-euc-jp": "euc_jis_2004"}


def decode_body(b, ctype=""):
    if b.startswith(codecs.BOM_UTF8):
        return b[3:].decode("utf-8", "replace")
    if b[:2] in (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE):
        return b.decode("utf-16", "replace")
    cands = ["utf-8"]  # 宣言が嘘のことが多いので、まず UTF-8 で厳密に読めるか試す
    m = re.search(rb"<\?xml[^>]*encoding=[\"']([A-Za-z0-9_.:-]+)", b[:400])
    if m:
        cands.append(m.group(1).decode("ascii"))
    m = re.search(r"charset=[\"']?([A-Za-z0-9_.:-]+)", ctype or "", re.I)
    if m:
        cands.append(m.group(1))
    cands += ["cp932", "gb18030", "cp1251"]
    for enc in cands:
        enc = ENC_ALIASES.get(enc.lower(), enc)
        try:
            return b.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return b.decode("utf-8", "replace")


def _ent(m):
    name = m.group(1)
    if name in ("amp", "lt", "gt", "quot", "apos"):
        return m.group(0)
    cp = html.entities.name2codepoint.get(name)
    return "&#%d;" % cp if cp else "&amp;" + name + ";"


def sanitize_xml(t):
    """よくある壊れ方(&nbsp; などHTMLの実体参照、裸の &)を直す。"""
    t = re.sub(r"&([A-Za-z][A-Za-z0-9]*);", _ent, t)
    return re.sub(r"&(?!#\d+;|#[xX][0-9a-fA-F]+;|[A-Za-z][A-Za-z0-9]*;)", "&amp;", t)


def _local(tag):
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].rsplit(":", 1)[-1]


DATE_PRIMARY = ("pubDate", "date", "published", "issued", "created")
DATE_SECONDARY = ("updated", "modified", "lastBuildDate")
RDF_ABOUT = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about"


def _entries_etree(root):
    out = []
    for el in root.iter():
        if _local(el.tag) not in ("item", "entry"):
            continue
        e = {"title": "", "link": "", "guid": "", "date": "", "date2": "", "summary": "", "content": ""}
        for ch in el:
            ln = _local(ch.tag)
            txt = "".join(ch.itertext())
            if ln == "title" and not e["title"]:
                e["title"] = txt
            elif ln == "link":
                href = ch.get("href")
                rel = ch.get("rel", "alternate")
                if href:
                    if rel == "alternate" or not e["link"]:
                        e["link"] = href
                elif txt.strip() and not e["link"]:
                    e["link"] = txt.strip()
            elif ln in ("guid", "id") and not e["guid"]:
                e["guid"] = txt.strip()
            elif ln in DATE_PRIMARY and not e["date"]:
                e["date"] = txt.strip()
            elif ln in DATE_SECONDARY and not e["date2"]:
                e["date2"] = txt.strip()
            elif ln in ("description", "summary", "anons") and not e["summary"]:
                e["summary"] = txt
            elif ln in ("encoded", "content") and not e["content"]:
                e["content"] = txt
        if not e["link"]:
            about = el.get(RDF_ABOUT) or ""
            if about.startswith("http"):
                e["link"] = about
            elif e["guid"].startswith("http"):
                e["link"] = e["guid"]
        out.append(e)
    return out


ITEM_RE = re.compile(r"<(?:[\w.-]+:)?(item|entry)\b[^>]*>(.*?)</(?:[\w.-]+:)?\1\s*>", re.S | re.I)
CDATA_RE = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.S)


def _rx_tag(block, names):
    for name in names:
        m = re.search(r"<(?:[\w.-]+:)?%s\b([^>]*?)(/?)>(?:(.*?)</(?:[\w.-]+:)?%s\s*>)?" % (name, name),
                      block, re.S | re.I)
        if m:
            attrs, selfclose, body = m.group(1), m.group(2), m.group(3) or ""
            return attrs, CDATA_RE.sub(lambda x: html.escape(x.group(1), quote=False), body)
    return "", ""


def _entries_regex(text):
    """XML として読めないほど壊れたフィード用の最後の手段。"""
    out = []
    for m in ITEM_RE.finditer(text):
        block = m.group(2)
        e = {"title": "", "link": "", "guid": "", "date": "", "date2": "", "summary": "", "content": ""}
        e["title"] = html.unescape(_rx_tag(block, ["title"])[1])
        attrs, body = _rx_tag(block, ["link"])
        hm = re.search(r"href=[\"']([^\"']+)", attrs)
        e["link"] = html.unescape(hm.group(1) if hm else body.strip())
        e["guid"] = html.unescape(_rx_tag(block, ["guid", "id"])[1].strip())
        e["date"] = html.unescape(_rx_tag(block, list(DATE_PRIMARY))[1].strip())
        e["date2"] = html.unescape(_rx_tag(block, list(DATE_SECONDARY))[1].strip())
        e["summary"] = html.unescape(_rx_tag(block, ["description", "summary"])[1])
        if not e["link"] and e["guid"].startswith("http"):
            e["link"] = e["guid"]
        out.append(e)
    return out


def parse_feed(body, ctype=""):
    """戻り値: (entries, mode)  mode = xml | xml+fix | regex"""
    text = decode_body(body, ctype)
    text = re.sub(r"^\s*<\?xml[^>]*\?>", "", text, count=1)
    text = CTRL_RE.sub("", text)
    for mode, t in (("xml", text), ("xml+fix", None)):
        if t is None:
            t = sanitize_xml(text)
        try:
            return _entries_etree(ET.fromstring(t)), mode
        except ET.ParseError:
            continue
    return _entries_regex(text), "regex"


def clean_text(s, limit=None):
    if not s:
        return ""
    s = TAG_RE.sub(" ", s)
    s = html.unescape(s)
    s = TAG_RE.sub(" ", s)
    s = s.replace("​", "").replace("﻿", "")
    s = WS_RE.sub(" ", s).strip()
    if limit and len(s) > limit:
        s = s[: limit - 1].rstrip() + "…"
    return s


def parse_date(s, default_tz=None):
    s = (s or "").strip()
    if not s:
        return None
    dt = None
    try:
        dt = email.utils.parsedate_to_datetime(s)
    except (TypeError, ValueError, IndexError):
        dt = None
    if dt is None:
        t = WS_RE.sub(" ", s)
        t = re.sub(r"Z$", "+00:00", t)
        t = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", t)  # +0800 → +08:00
        t = re.sub(r" ([+-]\d{2}:\d{2})$", r"\1", t)
        t = re.sub(r"(\.\d{6})\d+", r"\1", t)            # 小数秒を6桁に
        try:
            dt = datetime.fromisoformat(t)
        except ValueError:
            for fmt in ("%Y-%m-%d %H:%M:%S%z", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M",
                        "%Y-%m-%d %H:%M", "%Y年%m月%d日 %H:%M", "%d.%m.%Y %H:%M"):
                try:
                    dt = datetime.strptime(t, fmt)
                    break
                except ValueError:
                    continue
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=default_tz or timezone.utc)
    return dt


# ─────────────────────────────────────────────────────────────── 記事 ──


class Item:
    __slots__ = ("time", "cc", "lens", "feed", "source", "title", "link", "summary", "key", "shown_title")

    def __init__(self, time_, cc, lens, feed, source, title, link, summary, key=None):
        self.time, self.cc, self.lens, self.feed = time_, cc, lens, feed
        self.source, self.title, self.link, self.summary = source, title, link, summary
        self.key = key or make_key(feed, link or title)
        self.shown_title = None

    def to_tsv(self):
        f = [self.time.astimezone().isoformat(timespec="seconds"), self.cc, self.lens, self.feed,
             self.source, self.title, self.link, self.summary]
        return "\t".join(WS_RE.sub(" ", x) for x in f) + "\n"

    @classmethod
    def from_tsv(cls, line):
        p = line.rstrip("\n").split("\t")
        if len(p) < 7:
            return None
        try:
            t = datetime.fromisoformat(p[0])
        except ValueError:
            return None
        return cls(t, p[1], p[2], p[3], p[4], p[5], p[6], p[7] if len(p) > 7 else "")

    def local(self):
        return self.time.astimezone()

    def as_dict(self):
        return {"time": self.local().isoformat(timespec="seconds"), "country": self.cc, "lens": self.lens,
                "feed": self.feed, "source": self.source, "title": self.title, "link": self.link,
                "summary": self.summary}


def make_key(feed_id, ident):
    return hashlib.sha1(("%s\0%s" % (feed_id, ident)).encode("utf-8")).hexdigest()[:16]


def build_items(feed, fetched, now=None):
    now = now or datetime.now(timezone.utc)
    items = []
    for e in fetched.entries:
        title = clean_text(e.get("title"))
        if feed.strip:
            title = feed.strip.sub("", title).strip()
        if not title:
            continue
        link = (e.get("link") or "").strip()
        if link and not re.match(r"^[a-z][a-z0-9+.-]*:", link, re.I):
            link = urllib.parse.urljoin(feed.url, link)
        summary = clean_text(e.get("summary") or e.get("content"), SUMMARY_MAX)
        if summary == title:
            summary = ""
        hay = "%s %s %s" % (title, summary, link)
        if feed.match and not feed.match.search(hay):
            continue
        if feed.skip and feed.skip.search(hay):
            continue
        dt = parse_date(e.get("date"), feed.tz) or parse_date(e.get("date2"), feed.tz)
        if dt is None or dt > now + timedelta(minutes=10):
            dt = now
        ident = link or e.get("guid") or title
        items.append(Item(dt, feed.cc, feed.lens, feed.id, feed.name, title, link, summary,
                          make_key(feed.id, ident)))
    return items


def fetch_all(feeds, force=False, min_age=30, workers=8):
    """URL ごとに1回だけ取って解析(同じ URL を複数の国で match 分けしても1回)。"""
    urls = []
    for f in feeds:
        if f.url not in urls:
            urls.append(f.url)

    def job(url):
        r = fetch_url(url, force=force, min_age=min_age)
        if r.ok:
            try:
                r.entries, r.mode = parse_feed(r.body, r.ctype)
            except Exception as e:  # 念のため
                r.ok, r.error = False, "解析失敗: %s" % e
        r.body = b""
        return r

    with cf.ThreadPoolExecutor(max_workers=max(1, min(workers, len(urls)))) as ex:
        results = dict(zip(urls, ex.map(job, urls)))
    return results


def poll(feeds, force=False, min_age=30, save=True):
    """取得 → 記事化 → 保存。戻り値: (全記事, {feed_id: エラー})"""
    results = fetch_all(feeds, force=force, min_age=min_age)
    now = datetime.now(timezone.utc)
    items, errors = [], {}
    for f in feeds:
        r = results[f.url]
        if not r.ok:
            errors[f.id] = r.error or "取得失敗"
            continue
        items.extend(build_items(f, r, now))
    if save and items:
        archive_add(items)
    return items, errors


# ─────────────────────────────────────────────────────────────── 保存 ──


def archive_file(root, dt, cc, lens):
    d = dt.astimezone()
    return os.path.join(root, "%04d" % d.year, "%02d" % d.month, "%02d" % d.day, "%s.%s.tsv" % (cc, lens))


def archive_add(items):
    """保存(重複は捨てる)。戻り値: 新しく保存した記事。"""
    root = archive_root()
    seen_dir = state_dir("seen")
    new = []
    with Lock():
        by_feed = {}
        for it in items:
            by_feed.setdefault(it.feed, []).append(it)
        for fid, its in by_feed.items():
            sp = os.path.join(seen_dir, re.sub(r"[^\w.-]", "_", fid))
            try:
                with open(sp, encoding="ascii") as f:
                    seen = set(f.read().split())
            except OSError:
                seen = set()
            fresh = []
            for it in its:
                if it.key not in seen:
                    seen.add(it.key)
                    fresh.append(it)
            if not fresh:
                continue
            with open(sp, "a", encoding="ascii") as f:
                f.write("".join(it.key + "\n" for it in fresh))
            if len(seen) > 30000:  # 伸び続けないように間引く
                with open(sp, encoding="ascii") as f:
                    keep = f.read().split()[-15000:]
                atomic_write(sp, "\n".join(keep) + "\n")
            new.extend(fresh)
        by_path = {}
        for it in sorted(new, key=lambda x: x.time):
            by_path.setdefault(archive_file(root, it.time, it.cc, it.lens), []).append(it)
        for p, its in by_path.items():
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "a", encoding="utf-8") as f:
                f.write("".join(it.to_tsv() for it in its))
    return new


def iter_archive(since=None, until=None, ccs=None, lenses=None):
    root = archive_root()
    if not os.path.isdir(root):
        return
    d0 = since.astimezone().date() if since else None
    d1 = until.astimezone().date() if until else None
    for y in sorted(os.listdir(root)):
        yp = os.path.join(root, y)
        if not (y.isdigit() and os.path.isdir(yp)):
            continue
        for m in sorted(os.listdir(yp)):
            mp = os.path.join(yp, m)
            if not (m.isdigit() and os.path.isdir(mp)):
                continue
            for dd in sorted(os.listdir(mp)):
                dp = os.path.join(mp, dd)
                if not (dd.isdigit() and os.path.isdir(dp)):
                    continue
                try:
                    day = date(int(y), int(m), int(dd))
                except ValueError:
                    continue
                if (d0 and day < d0) or (d1 and day > d1):
                    continue
                for fn in sorted(os.listdir(dp)):
                    if not fn.endswith(".tsv") or "." not in fn[:-4]:
                        continue
                    cc, lens = fn[:-4].split(".", 1)
                    if (ccs and cc not in ccs) or (lenses and lens not in lenses):
                        continue
                    with open(os.path.join(dp, fn), encoding="utf-8", errors="replace") as f:
                        for line in f:
                            it = Item.from_tsv(line)
                            if it is None:
                                continue
                            if (since and it.time < since) or (until and it.time > until):
                                continue
                            yield it


# ─────────────────────────────────────────────────────────────── 表示 ──


def cw(c):
    if unicodedata.combining(c):
        return 0
    return 2 if unicodedata.east_asian_width(c) in ("W", "F") else 1


def dwidth(s):
    return sum(cw(c) for c in s)


def dtrunc(s, width, ell="…"):
    if dwidth(s) <= width:
        return s
    out, w = [], 0
    for c in s:
        if w + cw(c) > width - 1:
            break
        out.append(c)
        w += cw(c)
    return "".join(out) + ell


def dpad(s, width):
    s = dtrunc(s, width)
    return s + " " * (width - dwidth(s))


def _wordch(c):
    """単語の途中で切りたくない文字 (欧文・キリル文字など)。CJK はどこで切ってもよい。"""
    return (c.isalnum() or c in "-'’.,") and cw(c) == 1


NO_LINE_HEAD = "、。，．・：；？！）」』】〉》〕］｝”’ー…,.!?)]}%"


def wrap_spans(s, width):
    """表示幅で折り返す。CJK は文字単位、欧文は単語の切れ目で。戻り値: [(i, j), ...]"""
    n = len(s)
    if width < 4 or dwidth(s) <= width:
        return [(0, n)]
    spans, i = [], 0
    while i < n:
        w, j, last_sp = 0, i, -1
        while j < n:
            c = s[j]
            if w + cw(c) > width:
                break
            w += cw(c)
            if c == " ":
                last_sp = j
            j += 1
        if j >= n:
            spans.append((i, n))
            break
        if last_sp > i and _wordch(s[j - 1]) and _wordch(s[j]) and (j - last_sp) < width // 2:
            spans.append((i, last_sp))
            i = last_sp
        else:
            if s[j] in NO_LINE_HEAD and j - 1 > i and s[j - 1] not in NO_LINE_HEAD and s[j - 1] != " ":
                j -= 1  # 行頭禁則: 句読点や閉じ括弧で行を始めない (前の1字ごと次の行へ)
            k = j
            while k > i + 1 and s[k - 1] == " ":
                k -= 1
            spans.append((i, k))
            i = j
        while i < n and s[i] == " ":
            i += 1
    return spans


class Style:
    def __init__(self, on):
        self.on = on

    def c(self, n, s, bold=False):
        if not self.on or not s:
            return s
        return "\033[%s38;5;%dm%s\033[0m" % ("1;" if bold else "", n, s)

    def dim(self, s):
        return "\033[2m%s\033[0m" % s if self.on and s else s

    def hl(self, s):
        return "\033[1;7m%s\033[0m" % s if self.on and s else s


def cc_color(cc):
    if cc in CC_COLOR:
        return CC_COLOR[cc]
    return PALETTE[int(hashlib.md5(cc.encode()).hexdigest(), 16) % len(PALETTE)]


def use_color(flag_off, stream=None):
    stream = stream or sys.stdout
    if flag_off or os.environ.get("NO_COLOR"):
        return False
    return stream.isatty()


class Printer:
    """MMO のチャット欄のように1行ずつ流す。"""

    SRC_W = 12
    NARROW = 72  # これより狭い端末 (スマホの Termux など) では2段表示にする

    def __init__(self, cfg, multi_cc, style, hl=None, links=False, osc8=False, truncate=False, orig=False,
                 compact=False):
        self.cfg, self.multi, self.s = cfg, multi_cc, style
        self.force_compact = compact
        self.hl = hl or []
        self.links, self.osc8, self.truncate, self.orig = links, osc8, truncate, orig
        self.cur_date = None
        self.later = set()  # 「あとで読む」に入っている記事の key

    def width(self):
        if not sys.stdout.isatty():
            return 0
        return shutil.get_terminal_size((100, 30)).columns

    def compact(self):
        """2段表示 (1段目: 時刻・国・媒体・ID / 2段目: 見出し) にするか。"""
        return self.force_compact or 0 < self.width() < self.NARROW

    def _show_compact(self, it, text, stamp, tag, tag_plain, ranges, title):
        width = self.width() or 60
        sid = short_id(it)
        mark = it.key in self.later
        room = width - (dwidth(stamp) + 1 + dwidth(tag_plain) + 1 + 1 + len(sid) + (3 if mark else 0))
        src = dtrunc(it.source, max(4, room))
        print(self.s.dim(stamp) + " " + tag + " " + self.s.c(245, src) + " " + self.s.c(110, sid)
              + (" " + self.s.c(221, ">>", True) if mark else ""))
        avail = max(10, width - 2)
        spans = wrap_spans(text, avail)
        if self.truncate and len(spans) > 1:
            i, j = spans[0]
            while j > i and dwidth(text[i:j]) > avail - 1:
                j -= 1
            print("  " + self._paint(text, i, j, ranges, it.link) + "…")
        else:
            for i, j in spans:
                print("  " + self._paint(text, i, j, ranges, it.link))
        if self.orig and title and title != it.title:
            print("  " + self.s.dim(dtrunc(it.title, avail)))
        if self.links and it.link:
            print("  " + self.s.c(242, it.link))
        sys.stdout.flush()
        return bool(ranges)

    def sys_line(self, msg, color=245):
        t = datetime.now().strftime("%H:%M")
        print(self.s.c(color, "%s ─ %s" % (t, msg)), flush=True)

    def day_rule(self, d):
        w = self.width() or 60
        label = " %s (%s) " % (d.strftime("%Y-%m-%d"), WEEKDAY_JA[d.weekday()])
        side = max(3, (min(w, 100) - dwidth(label)) // 2)
        print(self.s.dim("─" * side + label + "─" * side), flush=True)

    def _hl_ranges(self, text):
        rs = []
        for rx in self.hl:
            for m in rx.finditer(text):
                if m.end() > m.start():
                    rs.append((m.start(), m.end()))
        return rs

    def _paint(self, text, i, j, ranges, link):
        out, k = [], i
        marks = sorted((max(a, i), min(b, j)) for a, b in ranges if a < j and b > i)
        for a, b in marks:
            if a < k:
                a = k
            if a >= b:
                continue
            out.append(text[k:a])
            out.append(self.s.hl(text[a:b]))
            k = b
        out.append(text[k:j])
        seg = "".join(out)
        if self.osc8 and link:
            seg = osc8_wrap(link, seg)
        return seg

    def show(self, it, title=None):
        d = it.local()
        if self.cur_date is None or d.date() > self.cur_date:
            self.cur_date = d.date()
            self.day_rule(self.cur_date)
        stamp = d.strftime("%H:%M") if d.date() == self.cur_date else d.strftime("%m/%d %H:%M")
        lens_ch = LENS.get(it.lens, (it.lens,))[0]
        tag_plain = "[%s|%s]" % (it.cc.upper(), lens_ch) if self.multi else "[%s]" % lens_ch
        if self.multi:
            tag = self.s.dim("[") + self.s.c(cc_color(it.cc), it.cc.upper(), True) + self.s.dim("|") \
                + self.s.c(LENS_COLOR.get(it.lens, 250), lens_ch, True) + self.s.dim("]")
        else:
            tag = self.s.dim("[") + self.s.c(LENS_COLOR.get(it.lens, 250), lens_ch, True) + self.s.dim("]")
        if self.compact():
            text = title or it.title
            return self._show_compact(it, text, stamp, tag, tag_plain, self._hl_ranges(text), title)
        src = dpad(it.source, self.SRC_W)
        sid = short_id(it)
        prefix_w = dwidth(stamp) + 1 + dwidth(tag_plain) + 1 + self.SRC_W + 1 + len(sid) + 1
        prefix = self.s.dim(stamp) + " " + tag + " " + self.s.c(245, src) + " " + self.s.c(110, sid) + " "
        if it.key in self.later:
            prefix_w += 3
            prefix += self.s.c(221, ">>", True) + " "
        text = title or it.title
        ranges = self._hl_ranges(text)
        width = self.width()
        avail = width - prefix_w if width else 0
        if self.truncate and avail > 8:
            if dwidth(text) <= avail:
                print(prefix + self._paint(text, 0, len(text), ranges, it.link))
            else:
                j, w = 0, 0
                while j < len(text) and w + cw(text[j]) <= avail - 1:
                    w += cw(text[j])
                    j += 1
                print(prefix + self._paint(text, 0, j, ranges, it.link) + "…")
        elif avail >= 16:
            spans = wrap_spans(text, avail)
            for n, (i, j) in enumerate(spans):
                lead = prefix if n == 0 else " " * prefix_w
                print(lead + self._paint(text, i, j, ranges, it.link))
        else:
            print(prefix + self._paint(text, 0, len(text), ranges, it.link))
        pad = " " * (prefix_w if avail >= 16 else 2)
        if self.orig and title and title != it.title:
            print(pad + self.s.dim(dtrunc(it.title, max(20, avail)) if avail >= 16 else it.title))
        if self.links and it.link:
            print(pad + self.s.c(242, it.link))
        sys.stdout.flush()
        return bool(ranges)


def set_term_title(s):
    if sys.stdout.isatty() and os.environ.get("TERM", "") not in ("linux", "dumb", ""):
        sys.stdout.write("\033]2;%s\033\\" % s)
        sys.stdout.flush()


# ─────────────────────────────────────────────────────────────── リンクを開く ──

ID_LEN = 4


def short_id(it):
    """画面に出す記事ID (key の先頭4桁)。`news open a3f9` や流れている画面で入力して使う。"""
    return it.key[:ID_LEN]


def find_items(prefix, recent=None, days=14):
    """ID の先頭一致で記事を探す。まず画面に出したもの、無ければ保存済み(新しい順)。"""
    prefix = prefix.lower()
    hits = [it for it in (recent or []) if it.key.startswith(prefix)]
    if hits:
        return hits
    since = datetime.now().astimezone() - timedelta(days=days)
    hits, seen = [], set()
    for it in iter_archive(since=since):
        if it.key.startswith(prefix) and it.key not in seen:
            seen.add(it.key)
            hits.append(it)
    hits.sort(key=lambda x: x.time, reverse=True)
    return hits


def browser_cmd(url, browser=None):
    """開くコマンドを決める。--browser / $NEWS_BROWSER / $BROWSER / xdg-open の順。
    {} があればそこに URL、無ければ末尾に付ける。
    例: 'firefox --new-tab'  'tmux new-window w3m {}'  'qutebrowser'"""
    cmd = browser or os.environ.get("NEWS_BROWSER") or os.environ.get("BROWSER")
    if cmd:
        cmd = cmd.split(":")[0] if ":" in cmd and " " not in cmd else cmd  # $BROWSER の a:b 形式
        args = shlex.split(cmd)
    else:
        for c in ("termux-open-url", "xdg-open", "open", "sensible-browser"):  # termux-open-url = Android (Termux)
            if shutil.which(c):
                args = [c]
                break
        else:
            return None
    if "{}" in args:
        return [url if x == "{}" else x for x in args]
    return args + [url]


def open_url(url, browser=None):
    cmd = browser_cmd(url, browser)
    if not cmd:
        return "開くコマンドが無い (xdg-open を入れるか --browser / $NEWS_BROWSER を設定。Termux なら pkg install termux-tools)"
    try:
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        return "%s: %s" % (cmd[0], e)
    return None


def copy_text(text):
    for cmd in (["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "-b", "-i"],
                ["termux-clipboard-set"],  # Android (Termux:API)
                ["tmux", "load-buffer", "-w", "-"] if os.environ.get("TMUX") else None):
        if cmd and shutil.which(cmd[0]):
            try:
                subprocess.run(cmd, input=text, text=True, timeout=5, check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return cmd[0]
            except (OSError, subprocess.SubprocessError):
                continue
    if sys.stdout.isatty():  # OSC 52: 端末(とtmux)が対応していればクリップボードへ
        import base64
        sys.stdout.write("\033]52;c;%s\a" % base64.b64encode(text.encode()).decode())
        sys.stdout.flush()
        return "OSC52"
    return None


# ─────────────────────────────────────────────────────────────── あとで読む ──
# later.tsv: 記事の保存形式と同じ8列 + 保存した時刻。これもただのテキストなので grep できる。


def later_path():
    return os.path.join(data_home(), "later.tsv")


def later_load():
    """あとで読むリスト(保存した順)。"""
    out, seen = [], set()
    try:
        with open(later_path(), encoding="utf-8", errors="replace") as f:
            for line in f:
                it = Item.from_tsv(line)
                if it and it.key not in seen:
                    seen.add(it.key)
                    out.append(it)
    except OSError:
        pass
    return out


def later_add(items):
    """戻り値: 新しく入れた記事。"""
    added = []
    with Lock("later"):
        have = set(it.key for it in later_load())
        os.makedirs(data_home(), exist_ok=True)
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        with open(later_path(), "a", encoding="utf-8") as f:
            for it in items:
                if it.key not in have:
                    have.add(it.key)
                    f.write(it.to_tsv().rstrip("\n") + "\t" + now + "\n")
                    added.append(it)
    return added


def later_remove(prefixes=None):
    """prefixes=None で全部。外した記事は later.done.tsv に移す。戻り値: 外した記事。"""
    with Lock("later"):
        try:
            with open(later_path(), encoding="utf-8", errors="replace") as f:
                rows = [(Item.from_tsv(line), line) for line in f]
        except OSError:
            rows = []
        rows = [(it, line) for it, line in rows if it]
        pre = None if prefixes is None else [x.lower() for x in prefixes]
        gone = [it for it, _ in rows if pre is None or any(it.key.startswith(x) for x in pre)]
        if gone:
            keys = set(it.key for it in gone)
            atomic_write(later_path(), "".join(line for it, line in rows if it.key not in keys))
            now = datetime.now().astimezone().isoformat(timespec="seconds")
            with open(os.path.join(data_home(), "later.done.tsv"), "a", encoding="utf-8") as f:
                f.write("".join(it.to_tsv().rstrip("\n") + "\t" + now + "\n" for it in gone))
    return gone


def osc8_wrap(url, text):
    return "\033]8;;%s\033\\%s\033]8;;\033\\" % (url, text) if url else text


def osc8_ok(off=False):
    """クリックできるリンク(OSC 8)を出してよいか。対応していない端末は無視するだけだが、Linux コンソールは避ける。"""
    return not off and sys.stdout.isatty() and os.environ.get("TERM", "") not in ("linux", "dumb", "")


STREAM_HELP = ("ID で開く (例: a3f9)  >> ID = あとで読むに入れる  << ID = 外す  >> = あとで読む一覧  "
               "p ID = URL表示  y ID = URLをコピー  s ID = 概要  l = 直近のURL一覧  "
               "m = 出さなかった記事を新しい方から出す (m 20 で20件)  q = 終了")


def print_later(printer, items):
    s = printer.s
    if not items:
        printer.sys_line(">> あとで読むは空です (>> ID で入れる)", 242)
        return
    printer.sys_line(">> あとで読む %d件  (ID で開く / << ID で外す)" % len(items), 221)
    for it in items:
        if printer.compact():
            t = dtrunc(it.title, max(10, (printer.width() or 60) - 7))
            print("  %s %s" % (s.c(221, short_id(it), True), osc8_wrap(it.link, t) if printer.osc8 else t))
            continue
        t = dtrunc(it.title, max(30, (printer.width() or 100) - 34))
        if printer.osc8:
            t = osc8_wrap(it.link, t)
        print("  %s %s %s  %s" % (s.c(221, short_id(it), True), s.dim(it.local().strftime("%m/%d %H:%M")),
                                 s.c(245, "[%s|%s]" % (it.cc.upper(), LENS.get(it.lens, (it.lens,))[0])), t))
    sys.stdout.flush()


def stream_command(line, recent, printer, browser):
    """流れている画面に打ち込まれた1行を処理。戻り値 False で終了。"""
    s = printer.s
    parts = line.strip().split()
    if not parts:
        return True
    op, arg = (parts[0].lower(), parts[1] if len(parts) > 1 else "")
    if op in ("q", "quit", "exit"):
        return False
    if op in ("?", "h", "help"):
        printer.sys_line(STREAM_HELP, 110)
        return True
    m = re.fullmatch(r"(>>|<<|>|<|\+|-)\s*(.*)", line.strip())
    if m:
        save, ids = m.group(1) in (">>", ">", "+"), m.group(2).split()
        if not ids:
            print_later(printer, later_load())
            return True
        for pid in ids:
            if not re.fullmatch(r"[0-9a-fA-F]{2,16}", pid):
                printer.sys_line("? ID ではない: %s" % pid, 167)
                continue
            if save:
                hits = list({h.key: h for h in find_items(pid, recent)}.values())
                if len(hits) != 1:
                    printer.sys_line("? ID %s は%s" % (pid, "見つからない" if not hits else
                                     " %d 件あります: %s" % (len(hits), " ".join(h.key[:6] for h in hits[:5]))), 167)
                    continue
                later_add(hits)
                printer.later.add(hits[0].key)
                printer.sys_line(">> あとで読むに入れた: %s" % dtrunc(hits[0].title, 60), 221)
            else:
                gone = later_remove([pid])
                for g in gone:
                    printer.later.discard(g.key)
                printer.sys_line("<< 外した: %s" % dtrunc(gone[0].title, 60) if gone else "? あとで読むに ID %s は無い" % pid,
                                 221 if gone else 167)
        return True
    if op == "l":
        for it in recent[-10:]:
            print("  %s %s  %s" % (s.c(110, short_id(it)), dtrunc(it.title, 50), s.c(242, it.link)))
        sys.stdout.flush()
        return True
    if op in ("o", "p", "y", "s") and arg:
        pid = arg
    elif re.fullmatch(r"[0-9a-fA-F]{2,16}", op):
        op, pid = "o", op
    else:
        printer.sys_line("? 分からない入力: %s  (? で説明)" % line.strip(), 167)
        return True
    hits = list({h.key: h for h in find_items(pid, recent)}.values())
    if not hits:  # 古い記事でも「あとで読む」にあれば開ける
        hits = [h for h in later_load() if h.key.startswith(pid.lower())]
    if not hits:
        printer.sys_line("? ID %s の記事が見つからない" % pid, 167)
        return True
    if len(hits) > 1:
        printer.sys_line("? ID %s は %d 件あります。もう少し長く打ってください: %s"
                         % (pid, len(hits), " ".join(h.key[:6] for h in hits[:5])), 214)
        return True
    it = hits[0]
    if op == "p":
        print("  " + it.link)
    elif op == "s":
        print("  " + (it.summary or "(概要なし)"))
    elif op == "y":
        how = copy_text(it.link)
        printer.sys_line(("コピーしました (%s): %s" % (how, it.link)) if how else "コピーできない (wl-copy か xclip、Termux なら termux-api を入れてください)", 108 if how else 167)
    else:
        if not it.link:
            printer.sys_line("? この記事には URL がありません", 167)
            return True
        err = open_url(it.link, browser)
        printer.sys_line(("! " + err) if err else "→ 開きました: %s" % dtrunc(it.title, 60), 167 if err else 108)
    sys.stdout.flush()
    return True


def wait_input(seconds, on_line):
    """seconds 秒待つあいだ、端末からの入力行を on_line に渡す。on_line が False を返したら False。"""
    end = time.time() + seconds
    interactive = sys.stdin.isatty()
    while True:
        left = end - time.time()
        if left <= 0:
            return True
        if not interactive:
            time.sleep(left)
            return True
        r, _, _ = select.select([sys.stdin], [], [], left)
        if r:
            line = sys.stdin.readline()
            if line == "":  # EOF (Ctrl-D)
                interactive = False
                continue
            if on_line(line) is False:
                return False


# ─────────────────────────────────────────────────────────────── 翻訳 ──

KANA_RE = re.compile(r"[぀-ヿ]")
LETTER_RE = re.compile(r"[A-Za-zЀ-ӿ一-鿿가-힯؀-ۿ]")
ANSI_RE = re.compile(r"\033\[[0-9;]*[A-Za-z]")


class Translator:
    """見出しの翻訳フック。コマンドは標準入力で原文を受け取り標準出力に訳を返すもの。
    例: trans -b :ja (translate-shell)。{} を書けば引数で渡す。結果は state/translate.tsv にキャッシュ。"""

    def __init__(self, cmd):
        self.cmd = cmd
        self.path = os.path.join(state_dir(), "translate.tsv")
        self.cache = {}
        try:
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    p = line.rstrip("\n").split("\t")
                    if len(p) == 2:
                        self.cache[p[0]] = p[1]
        except OSError:
            pass
        self.failed = False

    @staticmethod
    def wanted(text):
        return bool(LETTER_RE.search(text)) and not KANA_RE.search(text)

    def _run(self, text):
        args = shlex.split(self.cmd)
        inp = text
        if "{}" in args:
            args = [text if a == "{}" else a for a in args]
            inp = None
        try:
            r = subprocess.run(args, input=inp, capture_output=True, text=True, timeout=45)
        except (OSError, subprocess.SubprocessError) as e:
            self.failed = str(e)
            return None
        out = WS_RE.sub(" ", ANSI_RE.sub("", r.stdout)).strip()
        return out or None

    def translate(self, texts):
        todo = [t for t in dict.fromkeys(texts) if self.wanted(t) and t not in self.cache]
        if todo and not self.failed:
            with cf.ThreadPoolExecutor(max_workers=4) as ex:
                res = list(ex.map(self._run, todo))
            add = [(t, r) for t, r in zip(todo, res) if r]
            if add:
                with Lock("translate"):
                    with open(self.path, "a", encoding="utf-8") as f:
                        for t, r in add:
                            f.write("%s\t%s\n" % (t, r.replace("\t", " ")))
                self.cache.update(add)
        return {t: self.cache.get(t) for t in texts}


# ─────────────────────────────────────────────────────────── やわらか変換 ──
# 硬い見出しを、友だちに話すような口調にして流す。保存する記事は原文のまま (grep は原文で効く)。
#   rule        辞書と語尾のルールで変換 (既定。オフラインで一瞬)
#   ollama[:M]  手元の Ollama (OLLAMA_HOST、既定 127.0.0.1:11434 / モデル既定 qwen2.5:7b)
#   claude[:M]  Claude API (ANTHROPIC_API_KEY が要る / モデル既定 claude-haiku-4-5)
#   その他      任意のコマンド。標準入力に「番号 TAB 見出し」の行、標準出力に同じ形で返すもの
# 辞書は ~/.config/news/soft.tsv に「硬い語 TAB やわらかい語」で足せる (同じ語なら上書き、空なら変えない)。

SOFT_WORDS = """
内閣総理大臣	国のいちばん偉い人
首相	総理
官房長官	政府のスポークスマン
閣僚	大臣たち
閣議決定	政府で決定
首脳会談	トップ同士の話し合い
会談	話し合い
協議	話し合い
表明	発表
言及	ふれた
示唆	ほのめかした
強調	強く言った
非難	責めた
懸念	心配
警戒	要注意
要請	お願い
措置	対応
方針	つもり
見通し	見込み
検討	考え中
発足	スタート
開始	スタート
終了	おしまい
撤回	取り消し
辞任を表明	辞めると発表
訪米	アメリカ訪問
訪中	中国訪問
訪日	日本訪問
訪露	ロシア訪問
死去	亡くなった
逝去	亡くなった
死亡	亡くなった
負傷	ケガ
日経平均株価	日本の株価
日経平均	日本の株価
NYダウ	アメリカの株価
ダウ平均	アメリカの株価
反落	下がった
続落	また下がった
急落	ガクッと下がった
下落	下がった
低下	下がった
続伸	また上がった
急伸	グンと上がった
急騰	グンと上がった
高騰	値上がり
上昇	上がった
回復	戻った
増加	増えた
減少	減った
拡大	広がった
縮小	小さくなった
利上げ	金利アップ
利下げ	金利ダウン
自社株買い	自社株の買い戻し
最高益	過去いちばんのもうけ
増益	もうけアップ
減益	もうけダウン
前年同期比	去年の同じ時期とくらべて
前年比	去年とくらべて
前月比	先月とくらべて
過去最大	今までで最大
過去最高	今までで最高
過去最低	今までで最低
大幅	大きく
小幅	ちょっとだけ
"""

SOFT_PROMPT = (
    "あなたはニュースの見出しを、友だちに話すようなやわらかい日本語に言い換える係です。\n"
    "- 事実(数字・人や国や会社の名前・誰が何をしたか)は変えない。情報を足さない、減らさない。\n"
    "- 難しい言葉はやさしく言い換える。例: 内閣総理大臣 → 国のいちばん偉い人、反落 → 下がった。\n"
    "- 語尾は「〜らしい！」「〜だって！」「〜みたい！」などの話し言葉に。\n"
    "- 入力は「番号 TAB 見出し」の行。同じ番号を付けて「番号 TAB 言い換え」の行だけを返す。説明は書かない。\n"
    "例:\n1\t内閣総理大臣、来月訪米へ　首脳会談で関税協議\n"
    "→\n1\t国のいちばん偉い人が来月アメリカに行くみたい！トップ同士で関税の話し合いをするんだって\n")

SOFT_PROMPT_EN = (
    "you rewrite english news headlines into super casual, dumbed-down english, like texting a friend "
    "who never reads the news.\n"
    "- easy everyday words only. e.g. 'bilateral ties' -> 'relations', 'plunged' -> 'dropped hard', "
    "'amid' -> 'with'.\n"
    "- short and chill. endings like ', apparently' or ', they say' are fine.\n"
    "- keep the facts: numbers, names of people, countries and companies, who did what. don't add or drop info.\n"
    "- all lowercase. no capital letters at all.\n"
    "- input lines are 'number TAB headline'. reply only with lines 'number TAB rewrite', same numbers. "
    "no explanations.\n"
    "example:\n1\tChina's Wang tells ex-Japan foreign minister bilateral ties need to be normalized\n"
    "->\n1\tchina's wang tells japan's old foreign affairs boss the two countries gotta get back to normal\n")

# 英語の言い換え (小文字にしてから当てる。語の切れ目で)
SOFT_WORDS_EN = """
bilateral ties	relations
bilateral relations	relations
need to be normalized	gotta go back to normal
normalized	back to normal
normalize	get back to normal
prime minister	country's top boss
foreign minister	foreign affairs boss
top diplomat	foreign affairs boss
defense minister	military boss
defence minister	military boss
finance minister	money boss
lawmakers	politicians
legislators	politicians
legislation	new law
administration	government
incumbent	current
coalition	team
inflation	prices going up
recession	economy going bad
tariffs	import taxes
tariff	import tax
sanctions	punishments
interest rates	loan rates
rate hike	rate going up
rate cut	rate going down
monetary policy	money rules
unemployment	people without jobs
equities	stocks
treasury yields	bond rates
plunges	drops hard
plunged	dropped hard
plunge	drop hard
tumbles	falls
tumbled	fell
slumps	drops
slumped	dropped
declines	drops
declined	dropped
surges	jumps
surged	jumped
soars	goes way up
soared	went way up
rallies	bounces back
rallied	bounced back
amid	with
weighs	thinks about
weighed	thought about
antidote	fix
despite	even with
regarding	about
prior to	before
in order to	to
in the wake of	after
approximately	about
announces	says
announced	said
claims	says
claimed	said
alleges	says
alleged	said
accuses	blames
accused	blamed
rebukes	tells off
rebuked	told off
condemns	slams
condemned	slammed
criticizes	slams
criticized	slammed
criticises	slams
criticised	slammed
urges	asks
urged	asked
rejects	says no to
rejected	said no to
approves	okays
approved	okayed
agreement	deal
negotiations	talks
summit	big meeting
militarization	army buildup
casualties	people hurt or killed
fatalities	deaths
injured	hurt
wounded	hurt
residents	people
authorities	officials
investigates	looks into
investigated	looked into
investigation	probe
permission	an ok
commence	start
terminate	end
utilize	use
purchase	buy
acquires	buys
acquired	bought
acquisition	buyout
merger	team-up
revenue	sales
breached	broke into
breach	break-in
cyberattack	hack attack
unprecedented	never-seen-before
significantly	a lot
significant	big
substantial	big
numerous	lots of
additional	more
however	but
therefore	so
"""
_SOFT_ENDS_EN = (", apparently", ", they say")
_EN_RE = re.compile(r"[A-Za-z]")
_NOT_EN_RE = re.compile(r"[぀-ヿ一-鿿Ѐ-ӿ가-힯؀-ۿ]")

_CJK = "一-鿿々〆ヵヶ"
_SOFT_ENDS = ("らしい！", "だって！", "みたい！")
_GODAN_END = set("るうくぐすつぬぶむい")
SOFT_ADVERBS = {"大幅", "小幅", "過去最大", "過去最高", "過去最低"}  # 後ろに漢字が続いても言い換える


def soft_dict():
    """(日本語の辞書, 英語の辞書)。soft.tsv の行は、左が英数字だけなら英語の辞書に入る。"""
    words, en = {}, {}
    for line in SOFT_WORDS.strip().splitlines():
        k, v = line.split("\t")
        words[k] = v
    for line in SOFT_WORDS_EN.strip().splitlines():
        k, v = line.split("\t")
        en[k] = v
    try:
        with open(os.path.join(os.path.dirname(config_path()), "soft.tsv"), encoding="utf-8") as f:
            for line in f:
                if line.startswith("#") or "\t" not in line:
                    continue
                k, v = line.rstrip("\n").split("\t", 1)
                k = k.strip()
                if k and k.isascii():
                    en[k.lower()] = v.strip() or k.lower()
                elif k:
                    words[k] = v.strip() or k
    except OSError:
        pass
    return words, en


def soft_rule_en(text, en=None, _cache={}):
    """英語の見出し: 小文字に → やさしい語に言い換え → 語尾に ', apparently' など。"""
    en = en if en is not None else soft_dict()[1]
    if id(en) not in _cache:
        _cache.clear()
        alt = "|".join(re.escape(k) for k in sorted(en, key=len, reverse=True))
        _cache[id(en)] = re.compile(r"(?<![\w'-])(%s)(?![\w'-])" % alt) if alt else None
    rx = _cache[id(en)]
    s = WS_RE.sub(" ", text).strip().lower()
    if rx:
        s = rx.sub(lambda m: en[m.group(1)], s)
    s = s.rstrip(" .")
    if not s or s[-1] in "?!" or s.endswith(_SOFT_ENDS_EN):
        return s or text
    return s + _SOFT_ENDS_EN[int(hashlib.md5(text.encode()).hexdigest(), 16) % len(_SOFT_ENDS_EN)]


def soft_rule(text, words=None, _cache={}):
    """辞書で言い換え → 見出しの区切りを読点に → 語尾を話し言葉に。"""
    words = words if words is not None else soft_dict()[0]
    key = id(words)
    if key not in _cache:
        # 漢字が後ろに続くとき (上昇率、首相官邸 など) は熟語の一部なので変えない
        alt = "|".join(re.escape(k) + ("" if k in SOFT_ADVERBS else "(?![%s])" % _CJK)
                       for k in sorted(words, key=len, reverse=True))
        _cache.clear()
        _cache[key] = re.compile(alt) if alt else None
    rx = _cache[key]
    s = text.strip()
    head = ""
    m = re.match(r"^\s*[【\[［＜<〈]([^】\]］＞>〉]{1,8})[】\]］＞>〉]\s*", s)
    if m:
        head, s = m.group(1) + "！", s[m.end():]
    # 見出しの区切り (全角スペース、保存時に半角1つになったものも) を読点に。英単語の間の空白はそのまま
    jp = "[%sぁ-ヿ「」『』（）]" % _CJK
    s = re.sub(r"\s*[　]+\s*|\s{2,}|(?<=%s) (?=\S)|(?<=\S) (?=%s)" % (jp, jp), "、", s).strip("、 ").rstrip("。.")
    s = re.sub(r"、{2,}", "、", s)
    tail = [None]  # 見出しの最後の語を言い換えたら、その語で語尾を決める

    def sub(m):
        v = words[m.group(0)]
        if m.end() == len(s):
            tail[0] = v
        return v
    if rx:
        s = rx.sub(sub, s)
    if not s:
        return text
    if s.endswith(_SOFT_ENDS) or s.endswith(("らしい", "だって", "みたい")):
        return head + s
    last, prev = s[-1], s[-2] if len(s) > 1 else ""
    if tail[0] is not None and not tail[0].endswith(("た", "る")):  # 名詞に言い換えた (話し合い / お願い など)
        last = "名"
    if last == "名":
        end = _SOFT_ENDS[int(hashlib.md5(text.encode()).hexdigest(), 16) % 3]
    elif last in "減増" and re.match(r"[0-9０-９%％割倍]", prev or "a"):
        end = "ったらしい！" if last == "減" else "えたらしい！"
    elif last in "！!？?…":
        end = ""
    elif last == "た":
        end = "んだって！"
    elif last == "だ":
        s, end = s[:-1], "なんだって！"
    elif last == "へ" and re.match("[%s゠-ヿ]" % _CJK, prev or "a"):
        s, end = s[:-1], "するみたい！"
    elif last == "に" and prev and not re.match("[ぁ-ゟ]", prev):
        end = "なったらしい！"
    elif last == "か":
        end = "も？"
    elif last == "ず":
        s, end = s[:-1], "ないみたい！"
    elif last in _GODAN_END:  # 上がる / 高い / 決めない など
        end = "んだって！"
    elif last in "」』":
        end = "だって！"
    elif last in "をがはでとも":
        end = "…！"
    else:
        end = _SOFT_ENDS[int(hashlib.md5(text.encode()).hexdigest(), 16) % 3]
    return head + s + end


class Softener:
    """見出しを流す直前にやわらかくする。AI のときは state/soft.tsv にキャッシュ。"""

    def __init__(self, mode, note=None, lower=True):
        self.mode = (mode or "rule").strip()
        self.lower = lower  # 大文字をなくす
        self.kind = self.mode.split(":", 1)[0].lower()
        if self.kind not in ("rule", "ollama", "claude"):
            self.kind = "cmd"
        self.model = self.mode.split(":", 1)[1] if ":" in self.mode and self.kind in ("ollama", "claude") else None
        self.note = note or (lambda msg: eprint("news: " + msg))
        self.words, self.en = soft_dict()
        self.path = os.path.join(state_dir(), "soft.tsv")
        self.cache = {}
        self.retry_at = 0.0
        if self.kind != "rule":
            try:
                with open(self.path, encoding="utf-8") as f:
                    for line in f:
                        p = line.rstrip("\n").split("\t")
                        if len(p) == 3 and p[0] == self.mode:
                            self.cache[p[1]] = p[2]
            except OSError:
                pass

    def label(self):
        return {"rule": "ルール", "ollama": "Ollama", "claude": "Claude", "cmd": "コマンド"}[self.kind]

    @staticmethod
    def lang(text, it=None):
        """'ja' / 'en' / None。日本語 = かなを含むか日本のフィードの漢字見出し。英語 = 英字だけ。
        中国語・ロシア語などは言い換えない (大文字をなくすだけ)。"""
        if KANA_RE.search(text):
            return "ja"
        if it is not None and it.cc == "jp" and re.search("[%s]" % _CJK, text):
            return "ja"
        if _EN_RE.search(text) and not _NOT_EN_RE.search(text):
            return "en"
        return None

    def wanted(self, text, it=None):
        return self.lang(text, it) is not None

    def _rule(self, t, lang):
        return soft_rule_en(t, self.en) if lang == "en" else soft_rule(t, self.words)

    # ── AI に頼む
    def _ask(self, texts, lang="ja"):
        prompt = SOFT_PROMPT_EN if lang == "en" else SOFT_PROMPT
        body = "\n".join("%d\t%s" % (i + 1, WS_RE.sub(" ", t)) for i, t in enumerate(texts))
        if self.kind == "ollama":
            host = os.environ.get("OLLAMA_HOST") or "127.0.0.1:11434"
            if "://" not in host:
                host = "http://" + host
            req = urllib.request.Request(
                host.rstrip("/") + "/api/generate",
                data=json.dumps({"model": self.model or os.environ.get("NEWS_SOFT_MODEL") or "qwen2.5:7b",
                                 "prompt": prompt + "\n" + body, "stream": False,
                                 "options": {"temperature": 0.3}}).encode(),
                headers={"Content-Type": "application/json"})
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 家の中の機械へは直接
            with opener.open(req, timeout=90) as r:
                return json.loads(r.read().decode("utf-8")).get("response", "")
        if self.kind == "claude":
            key = os.environ.get("ANTHROPIC_API_KEY")
            if not key:
                raise RuntimeError("ANTHROPIC_API_KEY が設定されていません")
            base = (os.environ.get("ANTHROPIC_BASE_URL") or "https://api.anthropic.com").rstrip("/")
            req = urllib.request.Request(
                base + "/v1/messages",
                data=json.dumps({"model": self.model or os.environ.get("NEWS_SOFT_MODEL") or "claude-haiku-4-5",
                                 "max_tokens": 200 + 160 * len(texts), "system": prompt,
                                 "messages": [{"role": "user", "content": body}]}).encode(),
                headers={"Content-Type": "application/json", "x-api-key": key,
                         "anthropic-version": "2023-06-01"})
            with urllib.request.urlopen(req, timeout=90) as r:
                data = json.loads(r.read().decode("utf-8"))
            return "".join(c.get("text", "") for c in data.get("content", []) if c.get("type") == "text")
        r = subprocess.run(shlex.split(self.mode), input=prompt + "\n" + body, capture_output=True,
                           text=True, timeout=120)
        if r.returncode != 0 and not r.stdout.strip():
            raise RuntimeError((r.stderr.strip().splitlines() or ["終了コード %d" % r.returncode])[-1])
        return r.stdout

    @staticmethod
    def _parse(out, n):
        got = {}
        lines = [ANSI_RE.sub("", l).strip() for l in out.splitlines()]
        for l in lines:
            m = re.match(r"^(\d{1,3})\s*(?:\t|[.:：)）\]]|\s)\s*(.+)$", l)
            if m and 1 <= int(m.group(1)) <= n:
                got[int(m.group(1)) - 1] = m.group(2).strip()
        if not got:  # 番号を付けずに返してきたら、行数が合うときだけ順番で対応させる
            rest = [l for l in lines if l]
            if len(rest) == n:
                got = dict(enumerate(rest))
        return got

    def soften(self, pairs):
        """pairs: [(見出し, Item), ...] → {見出し: 表示する見出し}。
        日本語・英語は言い換え、それ以外はそのまま。lower なら最後に大文字をなくす。"""
        langs = {}
        for t, it in pairs:
            langs.setdefault(t, self.lang(t, it))
        out = self._soften(langs)
        if self.lower:
            out = {t: v.lower() for t, v in out.items()}
        return out

    def _soften(self, langs):
        todo = [t for t, l in langs.items() if l]
        res = {t: t for t, l in langs.items() if not l}
        if self.kind == "rule":
            res.update((t, self._rule(t, langs[t])) for t in todo)
            return res
        ask = [t for t in todo if t not in self.cache]
        if ask and time.time() >= self.retry_at:
            add = []
            chunks = []
            for lang in ("ja", "en"):
                part = [t for t in ask if langs[t] == lang]
                chunks += [(lang, part[k:k + 20]) for k in range(0, len(part), 20)]
            for lang, chunk in chunks:
                try:
                    got = self._parse(self._ask(chunk, lang), len(chunk))
                except Exception as e:  # 通信・API・コマンドの失敗。しばらくルールで代わりに変換
                    self.retry_at = time.time() + 300
                    msg = e.reason if isinstance(e, urllib.error.URLError) and not isinstance(e, urllib.error.HTTPError) else e
                    self.note("やわらか変換 (%s) 失敗: %s — 5分間はルールで変換します" % (self.mode, msg))
                    break
                add += [(t, got[i].replace("\t", " ")) for i, t in enumerate(chunk) if got.get(i)]
            if add:
                try:
                    with Lock("soft"):
                        with open(self.path, "a", encoding="utf-8") as f:
                            for t, r in add:
                                f.write("%s\t%s\t%s\n" % (self.mode, t.replace("\t", " "), r))
                except OSError:
                    pass
                self.cache.update(add)
        res.update((t, self.cache.get(t) or self._rule(t, langs[t])) for t in todo)
        return res


# ─────────────────────────────────────────────────────────────── 時刻指定 ──


def local_midnight(d):
    return datetime(d.year, d.month, d.day).astimezone()


def parse_when(s, end=False):
    """today / yesterday / 3h / 2d / 1w / 2026-09-29 / 0929 / '2026-09-29 18:00'"""
    s = (s or "").strip().lower()
    now = datetime.now().astimezone()
    if not s:
        return None
    if s == "now":
        return now
    m = re.fullmatch(r"(\d+)\s*(m|min|h|d|w)", s)
    if m:
        n, u = int(m.group(1)), m.group(2)
        delta = {"m": timedelta(minutes=n), "min": timedelta(minutes=n), "h": timedelta(hours=n),
                 "d": timedelta(days=n), "w": timedelta(weeks=n)}[u]
        return now - delta
    d = None
    if s == "today":
        d = now.date()
    elif s == "yesterday":
        d = now.date() - timedelta(days=1)
    else:
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y/%m/%d %H:%M"):
            try:
                return datetime.strptime(s, fmt).astimezone()
            except ValueError:
                pass
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
            try:
                d = datetime.strptime(s, fmt).date()
                break
            except ValueError:
                pass
        if d is None:
            m = re.fullmatch(r"(\d{1,2})[-/]?(\d{2})", s)
            if m:
                d = date(now.year, int(m.group(1)), int(m.group(2)))
    if d is None:
        raise SystemExit("news: 日時が読めない: %r (例: today, 3h, 2d, 2026-09-29)" % s)
    start = local_midnight(d)
    return start + timedelta(days=1) - timedelta(microseconds=1) if end else start


# ─────────────────────────────────────────────────────────────── 書き出し ──


def group_items(cfg, items, newest_first=True):
    order = cfg.cc_order()
    days = {}
    for it in items:
        days.setdefault(it.local().date(), {}).setdefault(it.cc, {}).setdefault(it.lens, []).append(it)
    out = []
    for d in sorted(days, reverse=newest_first):
        ccs = sorted(days[d], key=lambda c: (order.index(c) if c in order else 999, c))
        rows = []
        for cc in ccs:
            lenses = []
            for lens in LENS_ORDER + tuple(k for k in days[d][cc] if k not in LENS_ORDER):
                its = days[d][cc].get(lens)
                if its:
                    lenses.append((lens, sorted(its, key=lambda x: x.time, reverse=newest_first)))
            rows.append((cc, lenses))
        out.append((d, rows))
    return out


def range_label(items):
    if not items:
        return ""
    ds = sorted(it.local().date() for it in items)
    return ds[0].isoformat() if ds[0] == ds[-1] else "%s 〜 %s" % (ds[0], ds[-1])


def lens_label(cfg, cc, lens):
    ch, desc = LENS.get(lens, (lens, lens))
    n = cname(cfg, cc)
    return "%s  %s%s" % (ch, desc, n) if lens == "out" else "%s  %s%s" % (ch, n, desc)


def fmt_text(cfg, items, a):
    lines = []
    title = a.title or "news"
    lines.append("%s  %s  (%d件)" % (title, range_label(items), len(items)))
    lines.append("作成 %s" % datetime.now().astimezone().strftime("%Y-%m-%d %H:%M"))
    width = 76
    for d, rows in group_items(cfg, items, not a.old_first):
        lines.append("")
        lines.append("═" * 3 + " %s (%s) " % (d.isoformat(), WEEKDAY_JA[d.weekday()]) + "═" * 50)
        for cc, lenses in rows:
            lines.append("")
            lines.append("■ %s" % cname(cfg, cc))
            for lens, its in lenses:
                lines.append("  [%s]" % lens_label(cfg, cc, lens))
                for it in its:
                    head = "    %s  %s  " % (it.local().strftime("%H:%M"), dpad(it.source, 12))
                    ind = " " * dwidth(head)
                    for n, (i, j) in enumerate(wrap_spans(it.title, width - dwidth(head))):
                        lines.append((head if n == 0 else ind) + it.title[i:j])
                    if a.summary and it.summary:
                        for i, j in wrap_spans(it.summary, width - dwidth(ind)):
                            lines.append(ind + it.summary[i:j])
                    if not a.no_links and it.link:
                        lines.append(ind + it.link)
    return "\n".join(lines) + "\n"


def fmt_lines(cfg, items, a, style=None):
    style = style or Style(False)
    rx = getattr(a, "_rx", None)
    out = []
    for it in items:
        t = it.title
        if rx and style.on:
            t = rx.sub(lambda m: style.hl(m.group(0)), t)
        tag = "[%s|%s]" % (it.cc.upper(), LENS.get(it.lens, (it.lens,))[0])
        if style.on and osc8_ok():
            t = osc8_wrap(it.link, t)
        row = "%s %s %s %s  %s" % (style.dim(it.local().strftime("%Y-%m-%d %H:%M")),
                                   style.c(cc_color(it.cc), tag), style.c(110, short_id(it)),
                                   style.c(245, it.source), t)
        if not a.no_links and it.link:
            row += "  " + style.c(242, it.link)
        out.append(row)
        if a.summary and it.summary:
            out.append("    " + style.dim(it.summary))
    return "\n".join(out) + ("\n" if out else "")


def fmt_md(cfg, items, a):
    L = ["# %s  %s" % (a.title or "news", range_label(items)), "",
         "_%d件 / 作成 %s_" % (len(items), datetime.now().astimezone().strftime("%Y-%m-%d %H:%M"))]
    esc = lambda s: re.sub(r"([\[\]*_`])", r"\\\1", s)
    for d, rows in group_items(cfg, items, not a.old_first):
        L += ["", "## %s (%s)" % (d.isoformat(), WEEKDAY_JA[d.weekday()])]
        for cc, lenses in rows:
            L += ["", "### %s" % cname(cfg, cc)]
            for lens, its in lenses:
                L += ["", "#### %s" % lens_label(cfg, cc, lens), ""]
                for it in its:
                    t = "[%s](%s)" % (esc(it.title), it.link) if it.link and not a.no_links else esc(it.title)
                    L.append("- %s **%s** %s" % (it.local().strftime("%H:%M"), esc(it.source), t))
                    if a.summary and it.summary:
                        L.append("  <br><small>%s</small>" % html.escape(it.summary))
    return "\n".join(L) + "\n"


HTML_CSS = """
*{box-sizing:border-box}
body{font-family:"Noto Sans CJK JP","Noto Sans JP","Hiragino Sans","Yu Gothic","Noto Sans",sans-serif;
 color:#1b1b1b;background:#fff;margin:22px auto;max-width:1180px;padding:0 16px;font-size:10.5pt;line-height:1.5}
header{border-bottom:3px solid #1b1b1b;padding-bottom:6px;margin-bottom:10px}
h1{font-size:16pt;margin:0;letter-spacing:.02em}
.meta{color:#666;font-size:9pt}
h2{font-size:13pt;margin:22px 0 6px;padding:3px 8px;background:#1b1b1b;color:#fff}
h3{font-size:11.5pt;margin:14px 0 6px;padding-left:8px;border-left:6px solid var(--cc,#888)}
table.cols{width:100%;border-collapse:collapse;table-layout:fixed}
table.cols td{vertical-align:top;padding:0 10px 0 0;width:50%}
table.cols td+td{padding:0 0 0 10px;border-left:1px solid #ddd}
.lens{font-weight:700;font-size:9.5pt;padding:2px 6px;display:inline-block;margin-bottom:4px;border-radius:3px}
.lens.out{background:#e3f1f8;color:#0b5e85}.lens.in{background:#e7f3e5;color:#2c6b2f}
ul{list-style:none;margin:0;padding:0}
li{padding:3px 0;border-bottom:1px dotted #ddd;page-break-inside:avoid}
.t{color:#777;font-variant-numeric:tabular-nums;margin-right:4px;font-size:9pt}
.src{color:#555;font-size:8.5pt;border:1px solid #ccc;border-radius:3px;padding:0 4px;margin-right:4px;white-space:nowrap}
a{color:#1b1b1b;text-decoration:none}a:hover{text-decoration:underline}
.sum{color:#555;font-size:8.8pt;margin-top:1px}
.empty{color:#999;font-size:9pt}
@media print{body{margin:0;max-width:none;padding:0;font-size:9.5pt}h2{page-break-after:avoid}h3{page-break-after:avoid}
 a{color:#000}}
"""


def fmt_html(cfg, items, a):
    e = html.escape
    title = a.title or "news"
    H = ['<!doctype html><html lang="ja"><head><meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width,initial-scale=1">',
         "<title>%s %s</title><style>%s</style></head><body>" % (e(title), e(range_label(items)), HTML_CSS),
         "<header><h1>%s　%s</h1><div class=meta>%d件 ・ 作成 %s ・ 外 = 海外から見たその国 / 内 = その国の国内メディア</div></header>"
         % (e(title), e(range_label(items)), len(items), datetime.now().astimezone().strftime("%Y-%m-%d %H:%M"))]
    for d, rows in group_items(cfg, items, not a.old_first):
        H.append("<h2>%s (%s)</h2>" % (d.isoformat(), WEEKDAY_JA[d.weekday()]))
        for cc, lenses in rows:
            col = cc_color(cc)
            rgb = xterm_rgb(col)
            H.append('<h3 style="--cc:%s">%s</h3>' % (rgb, e(cname(cfg, cc))))
            cols = dict(lenses)
            keys = [k for k in LENS_ORDER if k in cols] + [k for k in cols if k not in LENS_ORDER]
            H.append('<table class="cols"><tr>')
            for lens in keys:
                H.append('<td><div class="lens %s">%s</div><ul>' % (e(lens), e(lens_label(cfg, cc, lens))))
                for it in cols[lens]:
                    t = e(it.title)
                    if it.link and not a.no_links:
                        t = '<a href="%s">%s</a>' % (e(it.link), t)
                    s = '<div class="sum">%s</div>' % e(it.summary) if a.summary and it.summary else ""
                    H.append('<li><span class="t">%s</span><span class="src">%s</span>%s%s</li>'
                             % (it.local().strftime("%H:%M"), e(it.source), t, s))
                H.append("</ul></td>")
            H.append("</tr></table>")
    if not items:
        H.append('<p class="empty">該当する記事はありません。</p>')
    H.append("</body></html>")
    return "\n".join(H)


def xterm_rgb(n):
    if 16 <= n <= 231:
        n -= 16
        v = [0, 95, 135, 175, 215, 255]
        return "rgb(%d,%d,%d)" % (v[n // 36], v[(n // 6) % 6], v[n % 6])
    return "#888"


def fmt_tsv(cfg, items, a):
    head = "time\tcountry\tlens\tfeed\tsource\ttitle\tlink\tsummary\n"
    return head + "".join(it.to_tsv() for it in items)


def fmt_csv(cfg, items, a):
    buf = io.StringIO()
    if a.bom:
        buf.write("﻿")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["time", "country", "country_name", "lens", "feed", "source", "title", "link", "summary"])
    for it in items:
        w.writerow([it.local().isoformat(timespec="seconds"), it.cc, cname(cfg, it.cc), it.lens, it.feed,
                    it.source, it.title, it.link, it.summary])
    return buf.getvalue()


def fmt_jsonl(cfg, items, a):
    return "".join(json.dumps(it.as_dict(), ensure_ascii=False) + "\n" for it in items)


FORMATS = {"lines": fmt_lines, "text": fmt_text, "txt": fmt_text, "md": fmt_md, "markdown": fmt_md,
           "html": fmt_html, "page": None, "tsv": fmt_tsv, "csv": fmt_csv, "jsonl": fmt_jsonl, "json": fmt_jsonl, "pdf": None}
EXT = {"text": "txt", "txt": "txt", "lines": "txt", "md": "md", "markdown": "md", "html": "html", "tsv": "tsv",
       "csv": "csv", "jsonl": "jsonl", "json": "jsonl", "pdf": "pdf"}


def pdf_engines():
    chromes = ["chromium", "chromium-browser", "google-chrome-stable", "google-chrome", "brave", "brave-browser"]
    engines = [("weasyprint", lambda h, o: ["weasyprint", h, o]),
               ("wkhtmltopdf", lambda h, o: ["wkhtmltopdf", "--quiet", "--encoding", "utf-8", "--page-size", "A4",
                                             "-T", "12mm", "-B", "12mm", "-L", "10mm", "-R", "10mm",
                                             "--enable-local-file-access", h, o])]
    for c in chromes:
        engines.append((c, lambda h, o, c=c: [c, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                                              "--print-to-pdf=" + o, "file://" + h]))
    return engines


def html_to_pdf(html_text, out, engine=None):
    cands = [(n, f) for n, f in pdf_engines() if (engine in (None, n)) and shutil.which(n)]
    if not cands:
        names = ", ".join(n for n, _ in pdf_engines()[:3])
        raise SystemExit("news: PDF 変換に使えるものが見つからない (%s, chromium のどれかを入れてください)\n"
                         "      代わりに -f html で書き出してブラウザから印刷もできます" % names)
    with tempfile.TemporaryDirectory() as td:
        hp = os.path.join(td, "news.html")
        with open(hp, "w", encoding="utf-8") as f:
            f.write(html_text)
        errs = []
        for name, mk in cands:
            try:
                r = subprocess.run(mk(hp, os.path.abspath(out)), capture_output=True, text=True, timeout=180)
            except (OSError, subprocess.SubprocessError) as e:
                errs.append("%s: %s" % (name, e))
                continue
            if os.path.exists(out) and os.path.getsize(out) > 0:
                return name
            errs.append("%s: %s" % (name, (r.stderr or r.stdout).strip()[:300]))
    raise SystemExit("news: PDF 変換に失敗\n  " + "\n  ".join(errs))


# ─────────────────────────────────────────────────────────────── コマンド ──


def eprint(*a):
    print(*a, file=sys.stderr, flush=True)


def split_list(vals):
    out = []
    for v in vals or []:
        out += [x.strip().lower() for x in v.split(",") if x.strip()]
    return out


def resolve_ccs(cfg, vals):
    ccs = split_list(vals)
    known = set(f.cc for f in cfg.feeds)
    bad = [c for c in ccs if c not in known]
    if bad:
        raise SystemExit("news: フィードの無い国: %s  (登録済み: %s)" % (", ".join(bad), " ".join(cfg.cc_order())))
    return ccs


def cmd_stream(a):
    cfg = load_config()
    ccs = resolve_ccs(cfg, a.country)
    feeds = cfg.select(ccs or None, a.lens, set(split_list(a.feed)) or None)
    if not feeds:
        raise SystemExit("news: 対象のフィードがありません (news feeds で確認)")
    sel_ccs = [c for c in cfg.cc_order() if any(f.cc == c for f in feeds)]
    style = Style(use_color(a.no_color))
    hl = [re.compile(p, re.I) for p in (a.hl or [])]
    flt = re.compile(a.filter, re.I) if a.filter else None
    printer = Printer(cfg, len(sel_ccs) > 1, style, hl, a.links, osc8_ok(a.no_osc8), a.truncate, a.orig, a.compact)
    printer.later = set(it.key for it in later_load())
    tr = Translator(a.translate) if a.translate else None
    soft = None
    if a.soft and a.soft.lower() not in ("off", "no", "0", "none"):
        soft = Softener(a.soft, note=lambda msg: printer.sys_line("! " + msg, 167), lower=not a.keep_case)

    if not a.pane:  # wall のときはペイン名を tmux 側で付ける
        set_term_title("news: " + " / ".join(cname(cfg, c) for c in sel_ccs))
    if printer.compact():  # 狭い画面では国名だけ1行で
        print(" ".join(style.c(cc_color(cc), ("━ " if n == 0 else "") + cname(cfg, cc), True)
                       for n, cc in enumerate(sel_ccs)), flush=True)
    for cc in ([] if printer.compact() else sel_ccs):
        names = {l: [f.name for f in feeds if f.cc == cc and f.lens == l] for l in LENS_ORDER}
        head = style.c(cc_color(cc), "━━ %s " % cname(cfg, cc), True)
        parts = []
        for l in LENS_ORDER:
            if names[l]:
                parts.append(style.c(LENS_COLOR[l], LENS[l][0], True) + style.dim(" " + ", ".join(names[l])))
        print(head + style.dim(" │ ").join(parts), flush=True)
    if sys.stdout.isatty():
        sys.stdout.write(style.dim("  取得中…") + "\r")
        sys.stdout.flush()

    shown = set()
    fail = {}
    recent = []
    missed = []  # 件数の上限で画面に出さなかった記事 (保存はしてある)。m で出せる
    title = "news: " + " / ".join(cname(cfg, c) for c in sel_ccs)

    def note_missed(items, force=False):
        missed.extend(items)
        del missed[:-200]
        if not a.pane and (items or force):
            set_term_title(title + (" (+%d: m)" % len(missed) if missed else ""))

    def visible(it, shown_title):
        if not flt:
            return True
        return bool(flt.search("%s %s %s %s" % (it.title, shown_title or "", it.summary, it.source)))

    def emit(items, limit=0):
        """古い順に出す。limit > 0 なら新しい方から limit 件だけ出し、出さなかった分を返す。"""
        skipped = []
        if limit > 0 and not flt:  # 絞り込みが無ければ、訳す前に切る
            skipped, items = items[:-limit], items[-limit:]
        titles = {}
        if tr and items:
            titles = tr.translate([it.title for it in items])
        vis = [(it, titles.get(it.title) if tr else None) for it in items]
        vis = [(it, t) for it, t in vis if visible(it, t)]
        if limit > 0 and len(vis) > limit:
            skipped += [it for it, _ in vis[:-limit]]
            vis = vis[-limit:]
        if soft and vis:  # 流す直前にやわらかく (訳したものは訳を)
            st = soft.soften([(t or it.title, it) for it, t in vis])
            vis = [(it, st.get(t or it.title) or t) for it, t in vis]
        for it, t in vis:
            recent.append(it)
            if printer.show(it, t) and a.bell:
                sys.stdout.write("\a")
                sys.stdout.flush()
        del recent[:-2000]
        return skipped

    def report(errors, ok_ids):
        for fid, err in errors.items():
            if fail.get(fid) != err:
                printer.sys_line("! %s 取得失敗: %s" % (fid, err), 167)
            fail[fid] = err
        for fid in list(fail):
            if fid in ok_ids:
                printer.sys_line("✓ %s 復帰" % fid, 108)
                del fail[fid]

    # 1回目: 取得して保存 → 保存済みから直近 N 件を出す
    try:
        items, errors = poll(feeds, min_age=a.min_age)
    except KeyboardInterrupt:
        return 0
    if sys.stdout.isatty():
        sys.stdout.write("\r\033[K")
    fids = set(f.id for f in feeds)
    since = datetime.now().astimezone() - timedelta(days=a.days)
    back = [it for it in iter_archive(since=since, ccs=set(sel_ccs), lenses={a.lens} if a.lens else None)
            if it.feed in fids]
    back.sort(key=lambda x: x.time)
    for it in back:
        shown.add(it.key)
    for it in items:
        shown.add(it.key)
    if a.backlog > 0:
        note_missed(emit(back[-200:], a.backlog))
    report(errors, set(f.id for f in feeds) - set(errors))
    if a.once:
        return 1 if errors and not back else 0
    if sys.stdin.isatty():
        printer.sys_line(("%s+Enter=開く m=続き ?=説明" if printer.compact() else
                          "ID (例 %s) + Enter で開く  m = 続き  ? = 操作一覧")
                         % (short_id(recent[-1]) if recent else "a3f9"), 242)

    def on_line(line):
        w = line.strip().lower().split()
        if w and w[0] in ("m", "more"):
            n = int(w[1]) if len(w) > 1 and w[1].isdigit() and int(w[1]) > 0 else (a.max_new or 3)
            if not missed:
                printer.sys_line("出していない記事はありません", 242)
                return True
            batch = missed[-n:]
            del missed[-n:]
            emit(batch)
            note_missed([], True)
            return True
        return stream_command(line, recent, printer, a.browser)

    try:
        while True:
            if not wait_input(max(5.0, a.interval * random.uniform(0.9, 1.1)), on_line):
                return 0
            items, errors = poll(feeds, min_age=min(a.min_age, a.interval / 2))
            new = sorted((it for it in items if it.key not in shown), key=lambda x: x.time)
            for it in new:
                shown.add(it.key)
            report(errors, set(f.id for f in feeds) - set(errors))
            note_missed(emit(new[-300:], a.max_new))
    except KeyboardInterrupt:
        print()
        return 0


def cmd_fetch(a):
    cfg = load_config()
    feeds = cfg.select(resolve_ccs(cfg, a.country) or None, a.lens, set(split_list(a.feed)) or None)
    items, errors = poll(feeds, force=a.force, min_age=0 if a.force else 20, save=False)
    new = archive_add(items) if items else []
    for fid, err in errors.items():
        eprint("news: %s: %s" % (fid, err))
    if not a.quiet:
        print("%d フィード / 新着 %d 件 → %s" % (len(feeds), len(new), archive_root()))
    return 1 if errors and len(errors) == len(feeds) else 0


def ago(dt):
    s = (datetime.now(timezone.utc) - dt).total_seconds()
    if s < 90:
        return "%d秒前" % max(0, s)
    if s < 5400:
        return "%d分前" % (s // 60)
    if s < 172800:
        return "%d時間前" % (s // 3600)
    return "%d日前" % (s // 86400)


def cmd_check(a):
    cfg = load_config()
    feeds = cfg.select(resolve_ccs(cfg, a.country) or None, a.lens, set(split_list(a.feed)) or None)
    style = Style(use_color(a.no_color))
    results = fetch_all(feeds, force=True)
    now = datetime.now(timezone.utc)
    bad = 0
    print(style.dim(" ".join([dpad("状態", 6), dpad("国", 3), dpad("視点", 4), dpad("ID", 14), dpad("記事", 5),
                              dpad("全体", 5), " " + dpad("最新", 10), dpad("解析", 7), dpad("ms", 6), " 名前 / エラー"])))
    for f in feeds:
        r = results[f.url]
        if not r.ok:
            bad += 1
            print("%s %-3s %s   %-14s %5s %5s  %-10s %-7s %6d  %s" % (
                style.c(167, "FAIL  ", True), f.cc, LENS[f.lens][0], f.id, "-", "-", "-", "-", r.ms,
                style.c(167, r.error)))
            continue
        its = build_items(f, r, now)
        newest = max((it.time for it in its), default=None)
        if not its:
            st, col = "EMPTY ", 214
        elif newest and (now - newest) > timedelta(days=a.stale):
            st, col = "STALE ", 214
        else:
            st, col = "OK    ", 108
        if st != "OK    ":
            bad += 1
        print("%s %-3s %s   %-14s %5d %5d  %s %-7s %6d  %s" % (
            style.c(col, st, True), f.cc, LENS[f.lens][0], f.id, len(its), len(r.entries),
            dpad(ago(newest) if newest else "-", 10), r.mode, r.ms, f.name))
    print(style.dim("\n%d / %d 正常。STALE = 最新記事が%d日より古い。設定: %s" % (len(feeds) - bad, len(feeds), a.stale, cfg.path)))
    return 1 if bad else 0


def cmd_feeds(a):
    if a.default:
        sys.stdout.write(DEFAULT_FEEDS)
        return 0
    if a.path:
        print(config_path())
        return 0
    if a.init:
        p = config_path()
        if os.path.exists(p) and not a.force:
            raise SystemExit("news: もうあります: %s (上書きは --force)" % p)
        atomic_write(p, DEFAULT_FEEDS)
        print(p)
        return 0
    if a.edit:
        load_config()
        ed = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
        return subprocess.call(shlex.split(ed) + [config_path()])
    cfg = load_config()
    style = Style(use_color(a.no_color))
    for cc in cfg.cc_order():
        print(style.c(cc_color(cc), "■ %s (%s)" % (cname(cfg, cc), cc), True))
        for f in cfg.feeds:
            if f.cc != cc:
                continue
            opt = " ".join("%s=%s" % kv for kv in f.opts.items())
            print("  %s %-14s %s  %s%s" % (style.c(LENS_COLOR[f.lens], LENS[f.lens][0], True), f.id,
                                           dpad(f.name, 16), style.dim(f.url), style.dim("  " + opt) if opt else ""))
    print(style.dim("\n%d フィード / %s" % (len(cfg.feeds), cfg.path)))
    return 0


def cmd_countries(a):
    cfg = load_config()
    for cc in cfg.cc_order():
        n = {l: sum(1 for f in cfg.feeds if f.cc == cc and f.lens == l) for l in LENS_ORDER}
        print("%-5s %s  外%d 内%d" % (cc, dpad(cname(cfg, cc), 14), n["out"], n["in"]))
    return 0


def cmd_open(a):
    """news open ID / news open (一覧から選ぶ。fzf があれば fzf)"""
    load_config()
    if a.id:
        hits = find_items(a.id, days=a.days)
        if not hits:
            raise SystemExit("news: ID %s の記事が見つからない (直近%d日)" % (a.id, a.days))
        if len(hits) > 1 and not a.first:
            for it in hits[:10]:
                eprint("  %s  %s %s  %s" % (it.key[:8], it.local().strftime("%m/%d %H:%M"), it.source, it.title))
            raise SystemExit("news: %d 件あります。ID をもう少し長く (上の8桁など) 指定してください" % len(hits))
        chosen = [hits[0]]
    else:
        since = datetime.now().astimezone() - timedelta(days=a.days)
        cfg = load_config(write_default=False)
        items = [it for it in iter_archive(since=since, ccs=set(resolve_ccs(cfg, a.country)) or None)]
        if a.pattern:
            rx = re.compile(a.pattern, re.I)
            items = [it for it in items if rx.search("%s %s %s" % (it.title, it.summary, it.source))]
        items.sort(key=lambda x: x.time, reverse=True)
        items = [it for it in items if it.link][:2000]
        if not items:
            raise SystemExit("news: 記事がありません")
        rows = ["%s\t%s [%s|%s] %s  %s" % (it.key[:8], it.local().strftime("%m/%d %H:%M"), it.cc.upper(),
                                           LENS.get(it.lens, (it.lens,))[0], it.source, it.title) for it in items]
        if shutil.which("fzf") and sys.stdin.isatty():
            r = subprocess.run(["fzf", "--multi", "--with-nth=2..", "--delimiter=\t", "--no-sort",
                                "--prompt=開く記事> ", "--header=Tab で複数選択 / Enter で開く"],
                               input="\n".join(rows), stdout=subprocess.PIPE, text=True)
            keys = [l.split("\t")[0] for l in r.stdout.splitlines() if l.strip()]
        else:
            show = items[:30]
            for n, it in enumerate(show, 1):
                print("%3d  %s" % (n, rows[n - 1].split("\t", 1)[1]))
            try:
                ans = input("番号 (空白区切りで複数可, Enter で中止)> ").split()
            except EOFError:
                ans = []
            keys = [show[int(x) - 1].key[:8] for x in ans if x.isdigit() and 1 <= int(x) <= len(show)]
        by = {it.key[:8]: it for it in items}
        chosen = [by[k] for k in keys if k in by]
        if not chosen:
            return 1
    for it in chosen:
        if a.print_url:
            print(it.link)
        elif a.copy:
            how = copy_text(it.link)
            eprint("news: コピーしました (%s): %s" % (how, it.link) if how else "news: コピーできない (wl-copy か xclip を入れてください)")
        else:
            err = open_url(it.link, a.browser)
            if err:
                raise SystemExit("news: " + err)
            eprint("news: 開きました: %s\n      %s" % (it.title, it.link))
    return 0


# ─────────────────────────────────────────────────────────────── スマホ用ページ ──
# 1ファイルで完結する HTML。外(海外から見た)は左寄せ、内(国内)は右に一段下げて、
# チャットのやり取りのように並べる。news serve で配ると自動で新着が足される。

PAGE_HTML = r"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="dark light">
<title>__TITLE__</title>
<style>
:root{
  --bg:#1a2130; --band:#141a26; --out:#26324a; --in:#2f2b45; --fg:#edf0f5; --mut:#9aa6ba;
  --rule:#34405a; --lens-out:#8fd0ff; --lens-in:#c9b6ff; --new:#f2b84b; --chip:#26324a;
}
@media (prefers-color-scheme:light){:root{
  --bg:#e9edf2; --band:#dde3ea; --out:#ffffff; --in:#f3effb; --fg:#161b26; --mut:#566176;
  --rule:#c5cedb; --lens-out:#0f5f96; --lens-in:#5a3fb0; --new:#a86400; --chip:#ffffff;
}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);
  font-family:"Hiragino Sans","Noto Sans JP","Noto Sans CJK JP","Yu Gothic UI",system-ui,sans-serif;
  font-size:16px;line-height:1.5;font-variant-numeric:tabular-nums}
header{position:sticky;top:0;z-index:5;background:var(--band);border-bottom:1px solid var(--rule);
  padding:calc(8px + env(safe-area-inset-top)) 12px 8px}
.top{display:flex;align-items:baseline;gap:10px}
h1{font-size:19px;margin:0;font-weight:800;letter-spacing:.01em}
#stat{color:var(--mut);font-size:13px;margin-left:auto;text-align:right}
#stat.bad{color:var(--new)}
.chips{display:flex;gap:6px;overflow-x:auto;margin:8px -12px 0;padding:0 12px 2px;scrollbar-width:none}
.chips::-webkit-scrollbar{display:none}
button,input{font:inherit;color:inherit}
.chip{flex:none;border:1px solid var(--rule);background:var(--chip);border-radius:999px;padding:5px 12px;
  font-size:14px;min-height:34px;cursor:pointer}
.chip[aria-pressed=true]{background:var(--fg);color:var(--bg);border-color:var(--fg);font-weight:700}
.chip i{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--c);margin-right:6px}
.tools{display:flex;gap:6px;margin-top:8px;align-items:center}
.seg{display:flex;border:1px solid var(--rule);border-radius:8px;overflow:hidden;flex:none}
.seg button{border:0;background:var(--chip);padding:5px 11px;font-size:14px;min-height:34px;cursor:pointer}
.seg button+button{border-left:1px solid var(--rule)}
.seg button[aria-pressed=true]{background:var(--fg);color:var(--bg);font-weight:700}
#q{flex:1;min-width:0;border:1px solid var(--rule);background:var(--chip);border-radius:8px;padding:5px 10px;
  font-size:16px;min-height:34px}
#q::placeholder{color:var(--mut)}
:focus-visible{outline:2px solid var(--lens-out);outline-offset:2px}
main{padding:4px 10px calc(40px + env(safe-area-inset-bottom));max-width:760px;margin:0 auto}
h2{font-size:13px;font-weight:600;color:var(--mut);margin:16px 2px 8px;display:flex;align-items:center;gap:10px}
h2::after{content:"";flex:1;height:1px;background:var(--rule)}
.row{display:flex;align-items:stretch;margin:0 0 8px;overflow:hidden;
  border-radius:4px 12px 12px 4px;border-left:4px solid var(--c);background:var(--out);width:92%}
.row.in{margin-left:8%;background:var(--in);border-radius:12px 4px 4px 12px;border-left:0;
  border-right:4px solid var(--c)}
.lnk{display:block;flex:1;min-width:0;text-decoration:none;color:inherit;padding:8px 4px 9px 12px}
a.lnk:active{filter:brightness(1.15)}
a.lnk:visited .ttl{color:var(--mut)}
.sv{flex:none;width:46px;border:0;border-left:1px solid var(--rule);background:transparent;color:var(--mut);
  font-size:15px;font-weight:800;letter-spacing:-.05em;cursor:pointer}
.sv[aria-pressed=true]{background:var(--new);color:#1a1300}
#lat[aria-pressed=true]{background:var(--new);color:#1a1300;border-color:var(--new)}
.meta{display:flex;gap:8px;align-items:baseline;font-size:12.5px;color:var(--mut);margin-bottom:2px}
.lens{font-weight:800;font-style:normal;color:var(--lens-out)}
.in .lens{color:var(--lens-in)}
.src{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cc{margin-left:auto;flex:none}
.ttl{display:block;font-size:16px;font-weight:600;line-height:1.45;overflow-wrap:anywhere}
.sum{display:none;font-size:13.5px;color:var(--mut);margin-top:4px;overflow-wrap:anywhere}
body.sum .sum{display:block}
.row.new{animation:arrive 2.4s ease-out 1}
@keyframes arrive{from{box-shadow:0 0 0 2px var(--new)}to{box-shadow:0 0 0 2px transparent}}
@media (prefers-reduced-motion:reduce){.row.new{animation:none;box-shadow:0 0 0 1px var(--new)}}
#fresh{position:fixed;left:50%;transform:translateX(-50%);bottom:calc(16px + env(safe-area-inset-bottom));
  background:var(--new);color:#1a1300;border:0;border-radius:999px;padding:9px 16px;font-weight:700;font-size:14px;
  box-shadow:0 4px 14px rgba(0,0,0,.35);display:none;cursor:pointer}
.empty{color:var(--mut);padding:28px 4px;font-size:15px}
footer{color:var(--mut);font-size:12.5px;padding:14px 4px 0;border-top:1px solid var(--rule);margin-top:18px}
@media (min-width:700px){.row{width:78%}.row.in{margin-left:22%}}
@media print{header{position:static}#fresh,.tools,.chips,.sv{display:none}body{background:#fff;color:#000}
  .row,.row.in{background:#fff;border:1px solid #bbb;width:100%;margin-left:0}}
</style>
</head>
<body>
<header>
  <div class="top"><h1>__TITLE__</h1><div id="stat"></div></div>
  <div class="chips" id="ccs"></div>
  <div class="tools">
    <div class="seg" id="lens">
      <button data-l="" aria-pressed="true">外と内</button><button data-l="out">外</button><button data-l="in">内</button>
    </div>
    <input id="q" type="search" placeholder="検索" autocomplete="off" enterkeyhint="search">
    <button class="chip" id="sumbtn" aria-pressed="false">概要</button>
    <button class="chip" id="lat" aria-pressed="false" title="あとで読む">&gt;&gt; 0</button>
  </div>
</header>
<main id="list"></main>
<button id="fresh"></button>
<script id="m" type="application/json">__META__</script>
<script id="d" type="application/json">__DATA__</script>
<script>
(function(){
  var META = JSON.parse(document.getElementById('m').textContent);
  var DATA = JSON.parse(document.getElementById('d').textContent);
  var st = {cc:'', lens:'', q:'', sum:false, lat:false};
  var later = {};   // k → 記事。「>>」で入れた あとで読む
  if (META.live) { (META.later || []).forEach(function(x){ later[x.k] = x; }); }
  else { try { (JSON.parse(localStorage.getItem('news-later') || '[]') || []).forEach(function(x){ if (x && x.k) later[x.k] = x; }); } catch (e) {} }
  function laterList(){ return Object.keys(later).map(function(k){ return later[k]; }).sort(function(a, b){ return b.t - a.t; }); }
  try { var sv = JSON.parse(localStorage.getItem('news-ui') || '{}');
        if (sv && typeof sv === 'object') { st.cc = sv.cc || ''; st.lens = sv.lens || ''; st.sum = !!sv.sum; } } catch (e) {}
  if (st.cc && !META.countries[st.cc]) st.cc = '';
  var seen = {}, fresh = {}, pending = 0, WD = '日月火水木金土';
  DATA.forEach(function(x){ seen[x.k] = 1; });
  var $ = function(id){ return document.getElementById(id); };
  function esc(s){ return String(s).replace(/[&<>"]/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
  function p2(n){ return (n < 10 ? '0' : '') + n; }
  function save(){ try { localStorage.setItem('news-ui', JSON.stringify(st)); } catch (e) {} }
  function okUrl(u){ return /^https?:\/\//i.test(u || ''); }

  function chips(){
    var h = '<button class="chip" data-c="" aria-pressed="' + (st.cc === '') + '">すべての国</button>';
    META.order.forEach(function(c){
      var m = META.countries[c];
      h += '<button class="chip" data-c="' + esc(c) + '" style="--c:' + m.color + '" aria-pressed="' + (st.cc === c) + '"><i></i>' + esc(m.name) + '</button>';
    });
    $('ccs').innerHTML = h;
    Array.prototype.forEach.call($('lens').children, function(b){ b.setAttribute('aria-pressed', String(b.dataset.l === st.lens)); });
    $('sumbtn').setAttribute('aria-pressed', String(st.sum));
    $('lat').setAttribute('aria-pressed', String(st.lat));
    $('lat').textContent = '>> ' + Object.keys(later).length;
    document.body.classList.toggle('sum', st.sum);
  }

  function render(){
    var q = st.q.trim().toLowerCase(), h = '', day = '', n = 0, SRC = st.lat ? laterList() : DATA;
    for (var i = 0; i < SRC.length; i++) {
      var x = SRC[i];
      if (st.cc && x.c !== st.cc) continue;
      if (st.lens && x.l !== st.lens) continue;
      if (q && (x.h + ' ' + x.s + ' ' + (x.d || '')).toLowerCase().indexOf(q) < 0) continue;
      var t = new Date(x.t * 1000), d = t.getFullYear() + '-' + p2(t.getMonth() + 1) + '-' + p2(t.getDate());
      if (d !== day) { day = d; h += '<h2>' + (t.getMonth() + 1) + '月' + t.getDate() + '日 (' + WD.charAt(t.getDay()) + ')</h2>'; }
      var m = META.countries[x.c] || {name: x.c.toUpperCase(), color: '#888'};
      var open = okUrl(x.u) ? '<a class="lnk" href="' + esc(x.u) + '" target="_blank" rel="noopener noreferrer">' : '<div class="lnk">';
      h += '<div class="row ' + (x.l === 'in' ? 'in' : 'out') + (fresh[x.k] ? ' new' : '') + '" style="--c:' + m.color + '">' + open +
           '<span class="meta"><b class="lens">' + (x.l === 'in' ? '内' : '外') + '</b><time>' +
           p2(t.getHours()) + ':' + p2(t.getMinutes()) + '</time><span class="src">' + esc(x.s) + '</span>' +
           (st.cc ? '' : '<span class="cc">' + esc(m.name) + '</span>') + '</span><span class="ttl">' + esc(x.h) + '</span>' +
           (x.d ? '<span class="sum">' + esc(x.d) + '</span>' : '') + (okUrl(x.u) ? '</a>' : '</div>') +
           '<button class="sv" data-k="' + esc(x.k) + '" aria-pressed="' + (later[x.k] ? 'true' : 'false') +
           '" aria-label="あとで読む" title="あとで読む">&gt;&gt;</button></div>';
      n++;
    }
    if (!n && st.lat) h = '<p class="empty">あとで読む記事はまだありません。見出しの右の「>>」を押すと、ここに溜まります。</p>';
    else if (!n) h = '<p class="empty">' + (DATA.length ? 'この条件に合う記事はありません。国や「外/内」の絞り込み、検索語を変えてみてください。'
                                                   : 'まだ記事がありません。PC で news fetch か news serve を動かすと、ここに並びます。') + '</p>';
    h += '<footer>外 = 海外から見たその国 / 内 = その国の国内メディア。見出しを押すと記事を開きます。「>>」で あとで読む に入ります。<br>' + esc(META.note) + '</footer>';
    $('list').innerHTML = h;
    fresh = {};
    return n;
  }

  function status(text, bad){ var e = $('stat'); e.textContent = text; e.className = bad ? 'bad' : ''; }
  function clock(){ var t = new Date(); return p2(t.getHours()) + ':' + p2(t.getMinutes()); }

  $('ccs').addEventListener('click', function(ev){ var b = ev.target.closest('button'); if (!b) return; st.cc = b.dataset.c; save(); chips(); render(); window.scrollTo(0, 0); });
  $('lens').addEventListener('click', function(ev){ var b = ev.target.closest('button'); if (!b) return; st.lens = b.dataset.l; save(); chips(); render(); });
  $('sumbtn').addEventListener('click', function(){ st.sum = !st.sum; save(); chips(); });
  $('q').addEventListener('input', function(){ st.q = this.value; render(); });
  function byKey(k){ if (later[k]) return later[k]; for (var i = 0; i < DATA.length; i++) if (DATA[i].k === k) return DATA[i]; return null; }
  function setLater(list){ later = {}; list.forEach(function(x){ later[x.k] = x; }); }
  function toggle(k){
    var on = !later[k], x = byKey(k);
    if (!x) return;
    if (on) later[k] = x; else delete later[k];
    if (META.live) {
      fetch('api/later', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({k: k, on: on})})
        .then(function(r){ if (!r.ok) throw new Error(r.status); return r.json(); })
        .then(function(list){ setLater(list); chips(); if (st.lat) render(); })
        .catch(function(){ status('保存できませんでした (PC につながりません)', true); });
    } else {
      try { localStorage.setItem('news-later', JSON.stringify(laterList())); }
      catch (e) { status('この端末には保存できません', true); }
    }
    chips();
    if (st.lat) render();
    else Array.prototype.forEach.call(document.querySelectorAll('.sv'), function(b){ if (b.dataset.k === k) b.setAttribute('aria-pressed', String(on)); });
  }
  $('list').addEventListener('click', function(ev){ var b = ev.target.closest('.sv'); if (b) { ev.preventDefault(); toggle(b.dataset.k); } });
  $('lat').addEventListener('click', function(){ st.lat = !st.lat; chips(); render(); window.scrollTo(0, 0); });
  $('fresh').addEventListener('click', function(){ pending = 0; this.style.display = 'none'; window.scrollTo({top: 0, behavior: 'smooth'}); });
  window.addEventListener('scroll', function(){ if (window.scrollY < 80 && pending) { pending = 0; $('fresh').style.display = 'none'; } }, {passive: true});

  chips();
  var shown = render();
  if (!META.live) { status(DATA.length + '件 ・ ' + META.made + ' 作成'); return; }

  status(DATA.length + '件 ・ ' + clock() + ' 更新');
  function pull(){
    if (document.hidden) return;
    var after = DATA.length ? DATA[0].t - 600 : 0;
    fetch('api/items?after=' + after, {cache: 'no-store'}).then(function(r){ if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function(items){
        var add = items.filter(function(x){ return !seen[x.k]; });
        add.forEach(function(x){ seen[x.k] = 1; fresh[x.k] = 1; });
        if (add.length) {
          DATA = add.concat(DATA).sort(function(a, b){ return b.t - a.t; }).slice(0, META.limit);
          var y = window.scrollY; render();
          if (y > 200) { pending += add.length; var f = $('fresh'); f.textContent = '新着 ' + pending + ' 件を見る'; f.style.display = 'block'; }
        }
        status(DATA.length + '件 ・ ' + clock() + ' 更新');
        return fetch('api/later', {cache: 'no-store'}).then(function(r){ return r.ok ? r.json() : null; });
      })
      .then(function(list){   // PC の端末で >> した分もここに反映
        if (!list) return;
        var before = Object.keys(later).sort().join(','); setLater(list);
        if (Object.keys(later).sort().join(',') !== before) { chips(); render(); }
      })
      .catch(function(){ status('PC につながりません (' + clock() + ')', true); });
  }
  setInterval(pull, META.poll * 1000);
  document.addEventListener('visibilitychange', function(){ if (!document.hidden) pull(); });
})();
</script>
</body>
</html>
"""


def item_json(it):
    return {"t": int(it.time.timestamp()), "c": it.cc, "l": it.lens, "s": it.source, "h": it.title,
            "u": it.link, "d": it.summary, "k": it.key[:10]}


def _json_embed(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def page_html(cfg, items, title="news", live=False, poll_sec=30, limit=800):
    """スマホ用の1ファイル HTML を作る。items は新しい順。"""
    order = [c for c in cfg.cc_order()]
    for it in items:
        if it.cc not in order:
            order.append(it.cc)
    meta = {"countries": {c: {"name": cname(cfg, c), "color": xterm_rgb(cc_color(c))} for c in order},
            "order": order, "live": bool(live), "poll": poll_sec, "limit": limit,
            "made": datetime.now().astimezone().strftime("%m/%d %H:%M"),
            "later": [item_json(it) for it in later_load()] if live else [],
            "note": ("PC の news serve から配信中。%d秒ごとに新着を足します。" % poll_sec) if live
                    else "このページは保存した時点の内容です。新しくするには PC で news page を実行し直します。"
                         "「>>」はこのスマホの中だけに保存されます。"}
    return (PAGE_HTML.replace("__TITLE__", html.escape(title))
            .replace("__META__", _json_embed(meta))
            .replace("__DATA__", _json_embed([item_json(it) for it in items[:limit]])))


def recent_items(cfg, ccs=None, lens=None, feeds=None, since=None, limit=800):
    seen, out = set(), []
    for it in iter_archive(since=since, ccs=ccs, lenses={lens} if lens else None):
        if feeds and it.feed not in feeds:
            continue
        if it.key in seen:
            continue
        seen.add(it.key)
        out.append(it)
    out.sort(key=lambda x: x.time, reverse=True)
    return out[:limit] if limit else out


def cmd_page(a):
    """スマホで読める HTML を1枚書き出す (Samba の共有フォルダや同期フォルダに置く用)。"""
    cfg = load_config()
    if not (a.since or a.until or a.date):
        a.since = "24h"
    a.old_first = False
    lim = a.limit or 800
    a.limit = 0
    items = query(cfg, a, a.pattern)[:lim]
    text = page_html(cfg, items, a.title or "news", live=False, limit=lim)
    if a.output and a.output != "-":
        atomic_write(os.path.abspath(a.output), text)
        eprint("news: %d件 → %s" % (len(items), a.output))
    else:
        sys.stdout.write(text)
    return 0


def lan_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # 実際には何も送らない。経路から自分のアドレスを知るだけ
            return s.getsockname()[0]
    except OSError:
        return None


def cmd_serve(a):
    """PC で取得を続けながら、スマホのブラウザ向けにページを配る (家庭内 LAN 用)。
    書き込めるのは「あとで読む」の出し入れだけ。"""
    cfg = load_config()
    ccs = resolve_ccs(cfg, a.country)
    feeds = cfg.select(ccs or None, a.lens, set(split_list(a.feed)) or None)
    if not feeds:
        raise SystemExit("news: 対象のフィードがありません (news feeds で確認)")
    cc_set = set(f.cc for f in feeds)
    fid_set = set(f.id for f in feeds)
    title = a.title or "news"

    def items_since(after_epoch=None, limit=None):
        since = datetime.now().astimezone() - timedelta(hours=a.hours)
        if after_epoch:
            since = max(since, datetime.fromtimestamp(after_epoch, timezone.utc))
        return recent_items(cfg, cc_set, a.lens, fid_set, since, limit if limit is not None else a.limit)

    md_args = argparse.Namespace(title=title, old_first=False, no_links=False, summary=True)

    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "news/" + VERSION

        def log_message(self, fmt, *args):
            if a.verbose:
                eprint("news: %s %s" % (self.address_string(), fmt % args))

        def _send(self, code, body, ctype):
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            try:
                if u.path in ("/", "/index.html"):
                    self._send(200, page_html(cfg, items_since(), title, True, a.refresh, a.limit),
                               "text/html; charset=utf-8")
                elif u.path == "/api/items":
                    qs = urllib.parse.parse_qs(u.query)
                    try:
                        after = float(qs.get("after", ["0"])[0])
                    except ValueError:
                        after = 0
                    self._send(200, json.dumps([item_json(it) for it in items_since(after)], ensure_ascii=False),
                               "application/json; charset=utf-8")
                elif u.path == "/api/later":
                    self._send(200, json.dumps([item_json(it) for it in later_load()], ensure_ascii=False),
                               "application/json; charset=utf-8")
                elif u.path == "/later.md":
                    md = argparse.Namespace(title="あとで読む", old_first=False, no_links=False, summary=True)
                    self._send(200, fmt_md(cfg, later_load(), md), "text/markdown; charset=utf-8")
                elif u.path in ("/md", "/news.md"):
                    self._send(200, fmt_md(cfg, items_since(), md_args), "text/markdown; charset=utf-8")
                elif u.path in ("/txt", "/news.txt"):
                    self._send(200, fmt_text(cfg, items_since(), md_args), "text/plain; charset=utf-8")
                else:
                    self._send(404, "not found\n", "text/plain; charset=utf-8")
            except (BrokenPipeError, ConnectionResetError):
                pass

        do_HEAD = do_GET

        def do_POST(self):
            """あとで読むの出し入れ: {"k": 記事ID, "on": true|false}"""
            u = urllib.parse.urlparse(self.path)
            try:
                if u.path != "/api/later":
                    return self._send(404, "not found\n", "text/plain; charset=utf-8")
                # JSON 以外は受けない (他サイトのページから勝手に送られないように)
                if "application/json" not in (self.headers.get("Content-Type") or ""):
                    return self._send(415, "json only\n", "text/plain; charset=utf-8")
                n = int(self.headers.get("Content-Length") or 0)
                if not 0 < n <= 4096:
                    return self._send(400, "bad request\n", "text/plain; charset=utf-8")
                try:
                    req = json.loads(self.rfile.read(n).decode("utf-8"))
                    k, on = str(req["k"]).lower(), bool(req["on"])
                except (ValueError, KeyError, TypeError):
                    return self._send(400, "bad request\n", "text/plain; charset=utf-8")
                if not re.fullmatch(r"[0-9a-f]{6,16}", k):
                    return self._send(400, "bad id\n", "text/plain; charset=utf-8")
                if on:
                    hits = find_items(k, days=60)
                    if hits:
                        later_add(hits[:1])
                else:
                    later_remove([k])
                self._send(200, json.dumps([item_json(it) for it in later_load()], ensure_ascii=False),
                           "application/json; charset=utf-8")
            except (BrokenPipeError, ConnectionResetError, ValueError):
                pass

    def fetch_loop():
        last = {}
        while True:
            try:
                _, errors = poll(feeds, min_age=min(30, a.interval / 2))
                for fid, err in errors.items():
                    if last.get(fid) != err:
                        eprint("news: %s 取得失敗: %s" % (fid, err))
                last = errors
            except Exception as e:  # 配信は止めない
                eprint("news: 取得中のエラー: %s" % e)
            time.sleep(max(5.0, a.interval * random.uniform(0.9, 1.1)))

    host = a.bind or ("0.0.0.0" if a.lan else "127.0.0.1")
    try:
        httpd = http.server.ThreadingHTTPServer((host, a.port), Handler)
    except OSError as e:
        raise SystemExit("news: ポート %d を開けない: %s (--port で変える)" % (a.port, e))
    httpd.daemon_threads = True
    if not a.no_fetch:
        threading.Thread(target=fetch_loop, daemon=True).start()
    eprint("news: 配信中 (Ctrl-C で終了)")
    eprint("  この PC から   http://127.0.0.1:%d/" % a.port)
    if host != "127.0.0.1":
        ip = lan_ip()
        eprint("  スマホから     http://%s:%d/   (同じ Wi-Fi につないで開く)" % (ip or "<このPCのIPアドレス>", a.port))
        eprint("  Markdown       http://%s:%d/md" % (ip or "<このPCのIPアドレス>", a.port))
    else:
        eprint("  スマホから開くには --lan を付けて起動します")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        eprint("")
    finally:
        httpd.server_close()
    return 0


def cmd_later(a):
    """あとで読む: news later [list|add|open|done] [ID...]"""
    cfg = load_config()
    act, ids = a.action, list(a.ids)
    if act not in ("list", "add", "open", "done", "rm"):  # news later a3f9 → add
        ids, act = [act] + ids, "add"
    if act == "add":
        if not ids:
            raise SystemExit("news: 入れる記事の ID を指定してください (news later add a3f9)")
        for pid in ids:
            hits = find_items(pid, days=a.days)
            if len(hits) != 1:
                eprint("news: ID %s は%s" % (pid, "見つからない" if not hits else " %d 件あります: %s"
                                            % (len(hits), " ".join(h.key[:8] for h in hits[:5]))))
                continue
            later_add(hits)
            eprint(">> %s" % hits[0].title)
        return 0
    if act in ("done", "rm"):
        if not ids and not a.all:
            raise SystemExit("news: 外す記事の ID か --all を指定してください")
        gone = later_remove(None if a.all else ids)
        for it in gone:
            eprint("<< %s" % it.title)
        return 0 if gone else 1
    items = later_load()
    if ids:
        pre = [x.lower() for x in ids]
        items = [it for it in items if any(it.key.startswith(x) for x in pre)]
    if act == "open":
        if not items:
            raise SystemExit("news: あとで読むに該当する記事がありません")
        for it in items:
            err = open_url(it.link, a.browser)
            if err:
                raise SystemExit("news: " + err)
            eprint("news: 開きました: %s" % it.title)
        if a.done:
            later_remove([it.key for it in items])
        return 0
    if a.output or a.format:  # 書き出し (スマホへ持っていく用に md / html / page など)
        a.title = a.title or "あとで読む"
        a.old_first = a.no_links = a.bom = False
        a.summary = True
        a.pdf_engine = None
        if not a.format and a.output and a.output.lower().endswith((".html", ".htm")):
            a.format = "page"  # スマホで読む用の1枚ページ
        write_out(cfg, sorted(items, key=lambda x: x.time, reverse=True), a, pick_format(a, "md"))
        return 0
    style = Style(use_color(a.no_color))
    if not items:
        print("あとで読むは空です。流れている画面で `>> ID`、または `news later add ID` で入れます。")
        return 0
    link = style.on and osc8_ok()
    for it in items:
        tag = "[%s|%s]" % (it.cc.upper(), LENS.get(it.lens, (it.lens,))[0])
        print("%s %s %s %s  %s" % (style.c(221, short_id(it), True), style.dim(it.local().strftime("%m/%d %H:%M")),
                                   style.c(cc_color(it.cc), tag), style.c(245, it.source),
                                   osc8_wrap(it.link, it.title) if link else it.title))
        print("     " + style.c(242, it.link))
    print(style.dim("%d件  開く: news later open [ID]   外す: news later done ID   保存先: %s" % (len(items), later_path())))
    return 0


def cmd_path(a):
    what = a.what or "data"
    print({"data": data_home(), "archive": archive_root(), "config": config_path(),
           "state": os.path.join(data_home(), "state")}[what])
    return 0


def query(cfg, a, pattern=None):
    since = parse_when(a.since) if a.since else None
    until = parse_when(a.until, end=True) if a.until else None
    if a.date:
        since, until = parse_when(a.date), parse_when(a.date, end=True)
    ccs = set(resolve_ccs(cfg, a.country)) or None
    feeds = set(split_list(a.feed)) or None
    rx = None
    if pattern:
        flags = re.I if a.ignore_case else 0
        rx = re.compile(pattern if a.regex else re.escape(pattern), flags)
    a._rx = rx
    items = []
    for it in iter_archive(since, until, ccs, {a.lens} if a.lens else None):
        if feeds and it.feed not in feeds:
            continue
        if rx:
            hay = it.title if a.title_only else "%s\t%s\t%s" % (it.title, it.summary, it.source)
            if not rx.search(hay):
                continue
        items.append(it)
    seen, uniq = set(), []
    for it in items:  # 同じ記事が2回保存されていても1回に
        if it.key not in seen:
            seen.add(it.key)
            uniq.append(it)
    uniq.sort(key=lambda x: x.time, reverse=not a.old_first)
    if a.limit:
        uniq = uniq[: a.limit]
    return uniq


def pick_format(a, default):
    fmt = (a.format or "").lower()
    if not fmt and a.output:
        ext = os.path.splitext(a.output)[1].lstrip(".").lower()
        fmt = {"txt": "text", "htm": "html", "markdown": "md", "json": "jsonl"}.get(ext, ext)
    fmt = fmt or default
    if fmt not in FORMATS:
        raise SystemExit("news: 形式は %s のどれか" % ", ".join(sorted(set(FORMATS))))
    return fmt


def write_out(cfg, items, a, fmt, style=None):
    if fmt == "pdf":
        out = a.output or "news-%s.pdf" % datetime.now().strftime("%Y%m%d-%H%M")
        eng = html_to_pdf(fmt_html(cfg, items, a), out, a.pdf_engine)
        eprint("news: %d件 → %s (%s)" % (len(items), out, eng))
        return out
    if fmt == "page":
        text = page_html(cfg, sorted(items, key=lambda x: x.time, reverse=True), a.title or "news",
                         live=False, limit=max(len(items), 1))
    else:
        text = fmt_lines(cfg, items, a, style) if fmt == "lines" else FORMATS[fmt](cfg, items, a)
    if a.output and a.output != "-":
        atomic_write(os.path.abspath(a.output), text)
        eprint("news: %d件 → %s" % (len(items), a.output))
        return a.output
    sys.stdout.write(text)
    return None


def cmd_export(a):
    cfg = load_config()
    if not (a.since or a.until or a.date):
        a.since = "today"
    items = query(cfg, a, a.pattern)
    fmt = pick_format(a, "text")
    write_out(cfg, items, a, fmt)
    return 0 if items else 1


def cmd_grep(a):
    cfg = load_config()
    items = query(cfg, a, a.pattern)
    fmt = pick_format(a, "lines")
    style = Style(use_color(a.no_color)) if fmt == "lines" and not a.output else None
    write_out(cfg, items, a, fmt, style)
    if a.count:
        eprint("news: %d件" % len(items))
    return 0 if items else 1


def cmd_print(a):
    cfg = load_config()
    if not (a.since or a.until or a.date):
        a.since = "today"
    items = query(cfg, a, a.pattern)
    if not items:
        raise SystemExit("news: 印刷する記事がありません (期間や国を確認)")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    use_text = a.text or not any(shutil.which(n) for n, _ in pdf_engines())
    ext = "txt" if use_text else "pdf"
    out = a.output or os.path.join(state_dir("print"), "news-%s.%s" % (stamp, ext))
    a.output = out
    write_out(cfg, items, a, "text" if use_text else "pdf")
    lp = shutil.which("lp") or shutil.which("lpr")
    if not lp and not a.dry_run:
        eprint("news: lp / lpr が無いので印刷はせず、ファイルだけ作りました: %s" % out)
        eprint("      (CUPS を入れると印刷できます: pacman -S cups / apt install cups-client)")
        return 1
    lp = lp or "lp"
    cmd = [lp]
    if a.printer:
        cmd += ["-d", a.printer] if lp.endswith("lp") else ["-P", a.printer]
    if a.copies > 1:
        cmd += ["-n", str(a.copies)] if lp.endswith("lp") else ["-#", str(a.copies)]
    cmd.append(out)
    if a.dry_run:
        print(shlex.join(cmd))
        return 0
    return subprocess.call(cmd)


def cmd_wall(a):
    tmux = shutil.which("tmux")
    if not tmux and not a.dry_run:
        raise SystemExit("news: wall には tmux が要ります (pacman -S tmux / apt install tmux)")
    cfg = load_config()
    ccs = resolve_ccs(cfg, a.countries) or DEFAULT_WALL
    script = os.path.realpath(__file__)
    fwd = ["--pane", "--interval", "%g" % a.interval, "--backlog", str(a.backlog), "--max-new", str(a.max_new)]
    for p in a.hl or []:
        fwd += ["--hl", p]
    if a.lens:
        fwd += ["--lens", a.lens]
    if a.translate:
        fwd += ["--translate", a.translate]
    fwd += ["--soft", a.soft] if a.soft else ["--no-soft"]
    if a.keep_case:
        fwd.append("--keep-case")
    if a.browser:
        fwd += ["--browser", a.browser]
    for flag in ("links", "truncate", "bell", "no_osc8", "orig", "compact"):
        if getattr(a, flag):
            fwd.append("--" + flag.replace("_", "-"))
    cmds = [shlex.join([sys.executable, script, "stream", "-c", cc] + fwd) for cc in ccs]
    inside = bool(os.environ.get("TMUX"))
    sess = a.session
    run_log = []
    fake = [0]

    def tm(*args, capture=False, optional=False):
        cmd = ["tmux"] + list(args)
        run_log.append(cmd)
        if a.dry_run:
            fake[0] += 1
            return "@1 %%%d" % fake[0] if capture else ""
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0 and optional:
            return ""
        if r.returncode != 0:
            raise SystemExit("news: tmux %s: %s" % (args[0], r.stderr.strip()))
        return r.stdout.strip()

    if not inside and not a.dry_run and \
            subprocess.run([tmux, "has-session", "-t", "=" + sess], capture_output=True).returncode == 0:
        if not a.restart:
            os.execvp(tmux, [tmux, "-u", "attach", "-t", "=" + sess])
        subprocess.run([tmux, "kill-session", "-t", "=" + sess])
    size = shutil.get_terminal_size((200, 56))
    if inside:
        out = tm("new-window", "-P", "-F", "#{window_id} #{pane_id}", "-n", sess, cmds[0], capture=True)
    else:
        out = tm("new-session", "-d", "-P", "-F", "#{window_id} #{pane_id}", "-s", sess, "-n", sess,
                 "-x", str(size.columns), "-y", str(size.lines), cmds[0], capture=True)
    win, p0 = out.split()[:2]
    panes = [p0]
    for c in cmds[1:]:
        panes.append(tm("split-window", "-t", win, "-P", "-F", "#{pane_id}", c, capture=True).split()[-1])
        tm("select-layout", "-t", win, "tiled")
    tm("select-layout", "-t", win, a.layout)
    tm("set-option", "-w", "-t", win, "pane-border-status", "top")
    tm("set-option", "-w", "-t", win, "pane-border-format", " #{pane_title} ")
    tm("set-option", "-w", "-t", win, "remain-on-exit", "on")
    if not a.no_osc8:  # tmux 3.4+ でクリックできるリンクを外の端末へ通す (古い tmux では黙って飛ばす)
        tm("set-option", "-as", "terminal-features", ",*:hyperlinks", optional=True)
    for cc, p in zip(ccs, panes):
        tm("select-pane", "-t", p, "-T", "%s  外=海外から見た / 内=国内" % cname(cfg, cc))
    tm("select-pane", "-t", panes[0])
    if a.dry_run:
        for cmd in run_log:
            print(shlex.join(cmd))
        if not inside:
            print("tmux -u attach -t =" + sess)
        return 0
    if not inside:
        os.execvp(tmux, [tmux, "-u", "attach", "-t", "=" + sess])
    return 0


# ─────────────────────────────────────────────────────────────── 引数 ──


def add_select(p):
    p.add_argument("-c", "--country", action="append", metavar="CC", help="国コード (カンマ区切り/複数回可) 例: -c jp,ru")
    p.add_argument("-l", "--lens", choices=["in", "out"], help="視点を絞る: out=外(海外から見た) in=内(国内)")
    p.add_argument("--feed", action="append", metavar="ID", help="フィードIDで絞る")


def add_output(p, default_since):
    add_select(p)
    p.add_argument("--since", help="いつから (today, yesterday, 3h, 2d, 1w, 2026-09-29 ...)%s" % default_since)
    p.add_argument("--until", help="いつまで")
    p.add_argument("--date", help="その日だけ (2026-09-29 / today / yesterday)")
    p.add_argument("-f", "--format", help="lines text csv tsv md html pdf jsonl (既定は -o の拡張子から)")
    p.add_argument("-o", "--output", help="出力ファイル (省略時は標準出力。pdf は news-日時.pdf)")
    p.add_argument("-n", "--limit", type=int, default=0, help="件数の上限")
    p.add_argument("--old-first", action="store_true", help="古い順 (既定は新しい順)")
    p.add_argument("--summary", action="store_true", help="概要も出す")
    p.add_argument("--no-links", action="store_true", help="URL を出さない")
    p.add_argument("--bom", action="store_true", help="CSV に BOM を付ける (Excel 用)")
    p.add_argument("--title", help="見出し")
    p.add_argument("--pdf-engine", help="weasyprint / wkhtmltopdf / chromium など")
    p.add_argument("-i", "--ignore-case", action="store_true", help="大文字小文字を区別しない")
    p.add_argument("-E", "--regex", action="store_true", help="パターンを正規表現として扱う")
    p.add_argument("-t", "--title-only", action="store_true", help="タイトルだけを検索")
    p.add_argument("--no-color", action="store_true")


def add_stream_opts(p):
    p.add_argument("--interval", type=float, default=90, help="取得間隔(秒) 既定90")
    p.add_argument("-n", "--backlog", type=int, default=10, help="起動時に出す直近の件数 既定10")
    p.add_argument("-m", "--max-new", type=int, default=10, metavar="N",
                   help="1回の更新で流す新着の上限 既定10 (新しい方からN件。0 で無制限。残りは保存され、m で出せる)")
    p.add_argument("--hl", action="append", metavar="REGEX", help="強調する語 (チャットのメンションのように)")
    p.add_argument("--bell", action="store_true", help="--hl に当たったらベルを鳴らす")
    p.add_argument("--links", action="store_true", help="URL も表示")
    p.add_argument("--no-osc8", action="store_true", help="見出しをクリックできるリンクにしない (既定はリンクにする)")
    p.add_argument("--osc8", action="store_true", help=argparse.SUPPRESS)  # 以前の指定。今は既定で有効
    p.add_argument("--truncate", action="store_true", help="折り返さず1行に切る")
    p.add_argument("--compact", action="store_true",
                   help="2段表示 (1段目: 時刻・媒体・ID / 2段目: 見出し)。幅72桁未満の端末 (スマホ) では自動")
    p.add_argument("--translate", nargs="?", const=os.environ.get("NEWS_TRANSLATE") or "trans -b -no-autocorrect :ja",
                   default=None, metavar="CMD",
                   help="かな以外の見出しを翻訳 (既定: trans -b :ja / 環境変数 NEWS_TRANSLATE)")
    p.add_argument("--orig", action="store_true", help="翻訳・やわらか変換したとき原文も下に出す")
    p.add_argument("--soft", nargs="?", const="rule", default=os.environ.get("NEWS_SOFT") or "rule", metavar="MODE",
                   help="見出しをやわらかい口調にして流す (既定で有効)。MODE: rule (既定) / ollama[:モデル] / "
                        "claude[:モデル] / 任意のコマンド。環境変数 NEWS_SOFT でも指定可")
    p.add_argument("--no-soft", dest="soft", action="store_const", const=None, help="やわらか変換をしない (原文のまま)")
    p.add_argument("--keep-case", action="store_true", help="やわらか変換のとき大文字を残す (既定は全部小文字)")
    p.add_argument("--browser", metavar="CMD", help="記事を開くコマンド ({} に URL)。既定 $NEWS_BROWSER / $BROWSER / xdg-open")


def build_parser():
    ap = argparse.ArgumentParser(prog="news", formatter_class=argparse.RawDescriptionHelpFormatter,
                                 description=__doc__.split("\n\n")[0].strip(),
                                 epilog="サブコマンドごとの詳細: news <サブコマンド> -h")
    ap.add_argument("--version", action="version", version="news " + VERSION)
    sub = ap.add_subparsers(dest="cmd", metavar="サブコマンド")

    p = sub.add_parser("stream", help="タイムラインで流す (既定)")
    add_select(p)
    add_stream_opts(p)
    p.add_argument("--filter", metavar="REGEX", help="一致する記事だけ流す")
    p.add_argument("--days", type=int, default=2, help="起動時の直近表示で遡る日数")
    p.add_argument("--min-age", type=float, default=30, help=argparse.SUPPRESS)
    p.add_argument("--once", action="store_true", help="直近分を出して終了")
    p.add_argument("--pane", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--no-color", action="store_true")
    p.set_defaults(func=cmd_stream)

    p = sub.add_parser("wall", help="tmux で国ごとに分割表示 (既定: us cn jp ru の4分割)")
    p.add_argument("countries", nargs="*", metavar="CC", help="国コード (既定: us cn jp ru)")
    p.add_argument("-l", "--lens", choices=["in", "out"])
    add_stream_opts(p)
    p.add_argument("--layout", default="tiled", help="tmux のレイアウト (tiled, even-horizontal, main-vertical ...)")
    p.add_argument("--session", default="news", help="tmux セッション名")
    p.add_argument("--restart", action="store_true", help="既存のセッションを閉じて作り直す")
    p.add_argument("--dry-run", action="store_true", help="tmux のコマンドを表示するだけ")
    p.set_defaults(func=cmd_wall)

    p = sub.add_parser("grep", help="保存済みの記事を検索 (既定: 全期間)")
    p.add_argument("pattern", help="検索語 (-E で正規表現)")
    add_output(p, "")
    p.add_argument("--count", action="store_true", help="件数を標準エラーに出す")
    p.set_defaults(func=cmd_grep)

    p = sub.add_parser("export", help="書き出し text/csv/tsv/md/html/pdf/jsonl (既定: 今日の分)")
    p.add_argument("pattern", nargs="?", help="絞り込む語 (任意)")
    add_output(p, " 既定 today")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("print", help="印刷 (PDF を作って lp/lpr へ。既定: 今日の分)")
    p.add_argument("pattern", nargs="?", help="絞り込む語 (任意)")
    add_output(p, " 既定 today")
    p.add_argument("-P", "--printer", help="プリンタ名 (lpstat -p で一覧)")
    p.add_argument("--copies", type=int, default=1)
    p.add_argument("--text", action="store_true", help="PDF ではなくテキストで印刷")
    p.add_argument("--dry-run", action="store_true", help="印刷コマンドを表示するだけ")
    p.set_defaults(func=cmd_print)

    p = sub.add_parser("fetch", help="取得して保存するだけ (cron / systemd timer 用)")
    add_select(p)
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("--force", action="store_true", help="キャッシュを使わず取り直す")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("check", help="全フィードの疎通・鮮度を確認")
    add_select(p)
    p.add_argument("--stale", type=int, default=3, help="この日数より古ければ STALE (既定3)")
    p.add_argument("--no-color", action="store_true")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("feeds", help="フィード一覧 / 設定ファイル")
    p.add_argument("--path", action="store_true", help="設定ファイルの場所")
    p.add_argument("--edit", action="store_true", help="$EDITOR で開く")
    p.add_argument("--init", action="store_true", help="既定の設定ファイルを書く")
    p.add_argument("--force", action="store_true")
    p.add_argument("--default", action="store_true", help="組み込みの既定を表示")
    p.add_argument("--no-color", action="store_true")
    p.set_defaults(func=cmd_feeds)

    p = sub.add_parser("countries", help="国の一覧")
    p.set_defaults(func=cmd_countries)

    p = sub.add_parser("open", help="記事をブラウザで開く (news open a3f9 / ID 無しなら一覧から選ぶ)")
    p.add_argument("id", nargs="?", help="画面に出ている記事ID (先頭の数桁でよい)")
    p.add_argument("-c", "--country", action="append", metavar="CC", help="一覧を国で絞る")
    p.add_argument("-g", "--grep", dest="pattern", metavar="REGEX", help="一覧を語で絞る")
    p.add_argument("--days", type=int, default=14, help="探す日数 (既定14)")
    p.add_argument("-p", "--print-url", action="store_true", help="開かずに URL を出す")
    p.add_argument("-y", "--copy", action="store_true", help="開かずに URL をコピー")
    p.add_argument("--first", action="store_true", help="ID が複数当たったら一番新しいものを開く")
    p.add_argument("--browser", metavar="CMD", help="開くコマンド ({} に URL) 例: 'tmux new-window w3m {}'")
    p.set_defaults(func=cmd_open)

    p = sub.add_parser("later", help="あとで読む (流れている画面で >> ID と打つと入る)")
    p.add_argument("action", nargs="?", default="list", help="list (既定) / add / open / done")
    p.add_argument("ids", nargs="*", help="記事ID")
    p.add_argument("--all", action="store_true", help="done で全部外す")
    p.add_argument("--done", action="store_true", help="open したら外す")
    p.add_argument("--days", type=int, default=30, help="add で探す日数 (既定30)")
    p.add_argument("-f", "--format", help="書き出す形式: md html page text csv tsv jsonl pdf")
    p.add_argument("-o", "--output", help="書き出すファイル (例: later.md / later.html)")
    p.add_argument("--title", help="見出し")
    p.add_argument("--browser", metavar="CMD", help="開くコマンド ({} に URL)")
    p.add_argument("--no-color", action="store_true")
    p.set_defaults(func=cmd_later)

    p = sub.add_parser("page", help="スマホで読める HTML を1枚書き出す (既定: 直近24時間)")
    p.add_argument("pattern", nargs="?", help="絞り込む語 (任意)")
    add_output(p, " 既定 24h")
    p.set_defaults(func=cmd_page)

    p = sub.add_parser("serve", help="スマホのブラウザ向けに配信する (取得も続ける。--lan で家の中に公開)")
    add_select(p)
    p.add_argument("--lan", action="store_true", help="家庭内 LAN の他の機器 (スマホ) から開けるようにする")
    p.add_argument("--port", type=int, default=8765, help="ポート (既定 8765)")
    p.add_argument("--bind", metavar="ADDR", help="待ち受けるアドレスを直接指定")
    p.add_argument("--interval", type=float, default=90, help="フィードの取得間隔(秒) 既定90")
    p.add_argument("--refresh", type=int, default=30, help="ページが新着を見に行く間隔(秒) 既定30")
    p.add_argument("--hours", type=float, default=24, help="何時間前までを載せるか (既定24)")
    p.add_argument("-n", "--limit", type=int, default=800, help="載せる件数の上限 (既定800)")
    p.add_argument("--title", help="ページの見出し")
    p.add_argument("--no-fetch", action="store_true", help="取得はせず配信だけ (cron や wall が取得しているとき)")
    p.add_argument("-v", "--verbose", action="store_true", help="アクセスを表示")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("path", help="保存先を表示 (grep -r / find 用)")
    p.add_argument("what", nargs="?", choices=["data", "archive", "config", "state"])
    p.set_defaults(func=cmd_path)
    return ap


def main(argv=None):
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    for s in (sys.stdout, sys.stderr):
        try:
            if (s.encoding or "").lower().replace("-", "") != "utf8":
                s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or (argv[0].startswith("-") and argv[0] not in ("-h", "--help", "--version")):
        argv = ["stream"] + argv
    a = build_parser().parse_args(argv)
    if not getattr(a, "func", None):
        build_parser().print_help()
        return 2
    try:
        return a.func(a) or 0
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
