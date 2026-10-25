# news — 「外から見たその国」と「その国の中」を並べて流す端末ニュース

国ごとにニュースを2つの視点で集めます。

| 視点 | 表示 | 中身 | 例 (日本) |
|---|---|---|---|
| `out` | **外** | 海外から見たその国(外国メディア、外国人向けの英字紙) | Japan Today, The Japan Times |
| `in`  | **内** | その国の多くの人が見る国内メディア(経済紙を含む) | NHK, Yahoo!ニュース, 日経, 東洋経済 |

最初から入っているのは 日本・アメリカ・中国・ロシア の4か国(23フィード)。国は設定ファイルに1行ずつ足すだけで増やせます。

```
━━ ロシア 外 The Moscow Times, Meduza(EN) │ 内 РИА Новости, ТАСС, РБК, Lenta.ru
──────────────── 2026-09-29 (火) ────────────────
22:24 [内] РИА Новости  Суд вынес приговор жительнице Алтая, укравшей 96 роз
22:31 [外] Meduza(EN)   Putin claims Ukraine has no antidote to jet drones
22:46 [内] РИА Новости  ВС России поразили в Одесской области цех производства дронов
```

## v2.4 で変わったこと

- 英語の見出しも、やさしい単語のくだけた英語に言い換えるようにしました。
- 見出しの大文字をなくしました(英語・ロシア語、日本語の中の英字も)。`--keep-case` で残せます。

## v2.3 で変わったこと

- 日本語の見出しを、流す前に毎回やわらかい口調に変えるようにしました(下の「やわらかい口調で流す」)。保存する記事は原文のままです。

## v2.2 で変わったこと

- 3件の縛りを10件に緩めました: 起動時は直近10件、更新1回につき新着は10件まで(`-n` / `-m` で変更)。

## v2.1 で変わったこと

- Android (Termux) でそのまま流せるようにしました。幅72桁未満の画面では自動で2段表示(1段目: 時刻・国・媒体・ID / 2段目: 見出し)になります。PC でも `--compact` で同じ表示にできます。
- Termux では記事を `termux-open-url` で Android のブラウザに開きます。
- 折り返しで「、」や「）」が行頭に来ないようにしました。

## v2.0 で変わったこと

- 端末の表示を絞りました: 起動時は直近3件、更新1回につき新着は3件まで(`-n` / `-m` で変更、`m` で残りを出す)。
- 保存・検索・書き出し・スマホ向けページは今までどおり全件を扱います。

## 入れ方

Python 3.8 以上があれば、追加のライブラリは要りません(標準ライブラリだけ)。

```sh
install -Dm755 news ~/.local/bin/news
news check          # まず全フィードが取れるか確認
news                # 全部の国を1本のタイムラインで流す
```

初回起動時に `~/.config/news/feeds.tsv` が作られます。

あると使える機能:

| 用途 | Arch | Mint / Ubuntu |
|---|---|---|
| 4分割 (`wall`) | `pacman -S tmux` | `apt install tmux` |
| 印刷 (`print`) | `pacman -S cups` | `apt install cups-client` |
| PDF | `pacman -S python-weasyprint` か chromium | `apt install weasyprint` か chromium |
| 翻訳 (`--translate`) | `pacman -S translate-shell` | `apt install translate-shell` |
| AI でやわらか変換 (`--soft ollama`) | `pacman -S ollama` | ollama.com のインストーラ |

## 流す (タイムライン)

```sh
news                          # 全部
news -c jp,ru                 # 国を絞る
news -c cn -l in              # 中国の国内メディアだけ
news --hl 'NVIDIA|原油' --bell # チャットのメンションのように強調して鳴らす
news --filter 'Iran|イラン'    # 一致するものだけ流す
news --links                  # 見出しの下に URL も出す (下の「気になる記事を開く」)
news --translate              # かなを含まない見出しを日本語に (translate-shell)
news --interval 60            # 取得間隔 (秒, 既定 90)
```

起動するとまず直近 **10件** を出し、あとは更新のたびに新着を **最大10件**(新しい方から)流します。
10件を超えた分は画面に出さないだけで、保存はされています(`news grep` で引けます)。流れている画面で `m` と打つと、出さなかった分を新しい方から10件ずつ出せます(`m 20` で20件)。端末のタイトルに `(+5: m)` のように残りの件数が出ます。

```sh
news -n 3 -m 3      # v2.0 の3件縛り
news -n 20          # 起動時に出す件数 (既定 10)
news -m 20          # 更新1回で流す新着の上限 (既定 10)
news -n 30 -m 0     # v1 と同じ動き (起動時30件、新着は無制限)
news --truncate     # 長い見出しを折り返さず、1記事を必ず1行にする
```

`news wall` のペインも同じ既定(各ペイン 10件 / 10件)で、`-n` `-m` はそのまま渡せます。
RSS はプッシュではないので「即」は取得間隔しだいです。相手に優しいように ETag / If-Modified-Since の条件付き取得をしていて、変化がなければ本文は落としません。

## やわらかい口調で流す

日本語と英語の見出しは、画面に出す直前に話し言葉に変わり、大文字もなくなります(既定で有効)。

```
日経速報  NYダウは反落、347ドル安　ボーイングやセールスフォース株が下落
       → アメリカの株価は下がった、347ドル安、ボーイングやセールスフォース株が下がったんだって！
NHK      熊本地震「水と氷と弁当」を　ガス管180キロ復旧へ
       → 熊本地震「水と氷と弁当」を、ガス管180キロ復旧するみたい！
         【速報】首相が辞任を表明
       → 速報！総理が辞めると発表らしい！
Japan Today  China's Wang tells ex-Japan foreign minister bilateral ties need to be normalized
       → china's wang tells ex-japan foreign affairs boss relations gotta go back to normal, apparently
Meduza   Russian pizza chain Dodo Pizza says hackers breached its systems
       → russian pizza chain dodo pizza says hackers broke into its systems, they say
```

- 変わるのは画面の表示だけです。保存する記事・`news grep`・書き出し・スマホのページは原文のままです。
- 言い換えるのは日本語と英語の見出しです。中国語・ロシア語は言い換えません(ロシア語は小文字になるだけ)。`--translate` で訳したものは、訳をやわらかくします。
- 大文字は全部なくなります。残したいときは `--keep-case`。
- `news --orig` で原文も下に並べて出します。`news --no-soft` か `NEWS_SOFT=off` で原文のまま流れます。

**変え方は4つ**(`--soft 方式` か、環境変数 `NEWS_SOFT=方式` で常にその方式に)

| 方式 | 中身 | 要るもの |
|---|---|---|
| `rule` (既定) | 辞書で言い換え(内閣総理大臣 → 国のいちばん偉い人、反落 → 下がった、bilateral ties → relations、plunged → dropped hard など)+ 語尾を「〜らしい！」「〜だって！」「〜みたい！」/「, apparently」「, they say」に | なし。オフラインで一瞬 |
| `ollama` / `ollama:モデル名` | 手元の AI で自然な文に言い換える | Ollama(既定のモデルは `qwen2.5:7b`) |
| `claude` / `claude:モデル名` | Claude API で言い換える | `ANTHROPIC_API_KEY`(既定のモデルは `claude-haiku-4-5`。見出しが Anthropic に送られ、API 料金がかかります) |
| 任意のコマンド | 標準入力に「番号 TAB 見出し」の行が来るので、同じ形で返すもの | そのコマンド |

```sh
news --soft ollama                         # Ollama で
OLLAMA_HOST=192.168.1.20:11434 news --soft ollama:gemma3   # 家の別の PC の Ollama を使う
export NEWS_SOFT=ollama                    # .bashrc に書けば毎回 Ollama で
```

`rule` は辞書と語尾の決まりで変えるだけなので、文が少しぎこちないことがあります。自然な文にしたいときは `ollama` か `claude` を使ってください。
AI の結果は `~/.local/share/news/state/soft.tsv` に「方式 TAB 原文 TAB 言い換え」で溜まるので、同じ見出しを二度問い合わせません(これも grep できます)。
AI につながらないときは画面に一度だけ知らせて、5分間は `rule` で代わりに変えます。

**辞書を足す** — `~/.config/news/soft.tsv` に「硬い語 TAB やわらかい語」を1行ずつ書きます。同じ語なら既定の言い換えを上書きし、右側を空にするとその語は変えません。左が英数字だけの行は英語の辞書に入ります(小文字で比べます)。

```
# ~/.config/news/soft.tsv
日銀	日本の銀行の親分
首相	総理大臣
原油	石油
government	the people in charge
```

## 気になる記事を開く

どの記事にも4桁の ID が付いています(`22:46 [内] 日経速報  d35a サウジ西部で…` の `d35a`)。

**流れている画面でそのまま打つ** — ペインに ID を打って Enter するとブラウザで開きます。

| 入力 | 動作 |
|---|---|
| `d35a` | 開く |
| `p d35a` | URL を表示 |
| `y d35a` | URL をクリップボードへ (wl-copy / xclip / xsel / tmux) |
| `s d35a` | 概要を表示 |
| `>> d35a` | **あとで読む** に入れる (`>> d35a 9bda` と並べてもよい。`>>d35a` や `+ d35a` でも同じ) |
| `>>` | あとで読む の一覧 |
| `<< d35a` | あとで読む から外す (`- d35a` でも同じ) |
| `m` / `m 20` | 件数の上限で出さなかった記事を、新しい方から 10件 / 20件 出す |
| `l` | 直近10件の URL 一覧 |
| `?` / `q` | 操作一覧 / 終了 |

**クリックで開く** — 見出しそのものがリンク(OSC 8)になっているので、対応した端末ならそのまま踏めます(foot / kitty / wezterm / alacritty / GNOME 端末など。端末によっては Ctrl+クリック)。
Linux コンソール(`TERM=linux`)やパイプへの出力では自動で付きません。表示が崩れる端末では `--no-osc8` で切れます。
`news wall` は tmux 3.4 以上のとき、リンクを外の端末へ通す設定も入れます(それより古い tmux ではクリックできないので ID で開いてください)。

**別の端末から** — 保存済みの記事なら、いつのものでも開けます。

```sh
news open d35a            # 開く
news open -p d35a         # URL を出すだけ (w3m "$(news open -p d35a)" など)
news open -y d35a         # コピー
news open                 # 一覧から選ぶ (fzf があれば fzf で、Tab で複数選択)
news open -c ru -g drone  # 一覧を国と語で絞る
news grep 原油            # grep の結果にも ID が出る
```

開くコマンドは `--browser` → `$NEWS_BROWSER` → `$BROWSER` → `xdg-open` の順で決まります。`{}` に URL が入ります。

```sh
export NEWS_BROWSER='firefox --new-tab'
export NEWS_BROWSER='tmux new-window w3m {}'   # 端末内で読む (tmux の別ウィンドウ)
```

## あとで読む (`>>`)

流れている画面で `>> ID` と打った記事は `~/.local/share/news/later.tsv` に溜まり、以後その記事の ID の横に黄色い `>>` が付きます。
これもただの TSV(記事の8列 + 入れた時刻)なので grep できます。

```sh
news later                 # 一覧
news later add d35a 9bda   # 別の端末から入れる (news later d35a でも同じ)
news later open            # 全部ブラウザで開く
news later open d35a --done   # 開いて、一覧から外す
news later done d35a       # 読んだので外す (rm でも同じ)
news later done --all      # 全部外す
news later -o later.md     # Markdown に書き出す (スマホのメモアプリなどへ)
news later -o later.html   # スマホ向けの1枚ページに書き出す
```

外した記事は `later.done.tsv` に移るだけで、消えません。

## スマホで読む (HTML / Markdown)

やり方は2つあります。どちらも見出しを押すと記事が開き、右端の `>>` を押すと あとで読む に入ります。上の `>> 3` を押すと あとで読む だけの表示になります。

**1. PC から配信する (おすすめ。新着が自動で足される)**

```sh
news serve --lan           # → http://192.168.x.x:8765/ と表示される
```

同じ Wi-Fi のスマホのブラウザで、表示された `http://PCのIP:8765/` を開きます。ホーム画面に追加しておくとアプリのように使えます。

- PC 側が取得も兼ねます(既定 90秒ごと)。`news wall` や cron がすでに取得しているなら `--no-fetch` で配信だけに。
- スマホで押した `>>` は PC の `later.tsv` に入るので、端末の `news later` と**同じ一覧**になります(端末で `>> ID` したものもスマホに出ます)。
- Markdown / テキストで欲しいとき: `http://PCのIP:8765/md`、`/txt`、あとで読む は `/later.md`。
- `-c jp,ru` で国を絞る、`--port` でポート変更、`--hours 48` で載せる範囲を変更。
- `--lan` を付けないと PC 自身(127.0.0.1)からしか開けません。`--lan` を付けると、家の LAN につながっている機器なら誰でもページを見られて あとで読む を付け外しできます(パスワードはありません)。ルーターでこのポートを外へ開けないでください。
- つながらないときは PC のファイアウォールを確認: `sudo ufw allow from 192.168.0.0/16 to any port 8765 proto tcp`

**2. HTML を1枚書き出して、Samba の共有フォルダなどに置く**

```sh
news page -o /srv/samba/share/news.html             # 直近24時間
news page -c jp,us --since 3d -o ~/Sync/news.html   # 国と期間を絞る
# 取得はしないので、定期的に更新するなら:  news fetch -q && news page -o …
```

ファイル1枚で完結していて(外部の読み込みなし)、スマホのファイルアプリやブラウザでそのまま開けます。国の切り替え・外/内・検索・概要の表示は同じです。
こちらの `>>` はそのスマホのブラウザの中だけに保存され、PC の `later.tsv` とは共有されません(共有したいなら 1. の `serve`)。ファイルアプリ内の簡易ビューアだと保存されないことがあるので、そのときは「ブラウザで開く」を使ってください。

**Markdown で持ち出す**

```sh
news export --since 24h -o news.md     # 日付 → 国 → 外 | 内 でまとめた Markdown (リンク付き)
news later -o later.md                 # あとで読む だけ
```

## Android で流す (Termux)

PC なしで、スマホだけで動きます。Termux は Android 用の端末アプリです(F-Droid 版か GitHub 版を入れてください)。

```sh
# Termux の中で
pkg install python                 # 必須はこれだけ
termux-setup-storage               # スマホの「ダウンロード」を ~/storage/downloads で見えるようにする
cp ~/storage/downloads/news $PREFIX/bin/news && chmod +x $PREFIX/bin/news
news                               # 流れる
```

`news` のファイルは、PC の Samba 共有などからスマホの「ダウンロード」に置いてください。`news: not found` や `bad interpreter` と出たら `python $PREFIX/bin/news` で動きます。

```
10:56 [JP|内] 日経速報 d35a
  サウジ西部で原油の輸出再開 東西パイプライ
  ン、能力の半分まで回復
10:56 [RU|外] Meduza(EN) 9bda
  Russian pizza chain Dodo Pizza says hackers
  breached its systems
```

- **開く**: ID を打って Enter(例 `d35a`)。Android のブラウザで開きます。
- **あとで読む**: `>> d35a`。スマホのキーボードで `>` が打ちにくければ `+ d35a` で同じです。外すのは `- d35a`。
- **国を絞る**: `news -c jp,us`。縦持ちの画面は狭いので、国を絞ると読みやすくなります。
- **1記事を短く**: `news --truncate` で見出しを1行に切ります(1記事 = 2行)。
- **画面を消しても動かす**: 先に `termux-wake-lock` を実行しておきます(Android が Termux を止めるのを防ぐ。電池は減ります)。
- **URL をコピー** (`y d35a`) するには `pkg install termux-api` と Termux:API アプリが要ります。
- **ブラウザで見たいとき**: Termux で `news serve` を動かしたまま、同じスマホの Chrome で `http://127.0.0.1:8765/` を開きます。
- 横持ちにして幅が72桁以上になると、PC と同じ1行表示に戻ります。`pkg install tmux` すれば `news wall us jp` も使えます。

保存先は Termux の中の `~/.local/share/news/` で、PC とは別です。`grep -r 原油 "$(news path)"` などは Termux の中でそのまま使えます。

## 4分割 (1920×1080 に)

```sh
news wall                     # アメリカ 中国 / 日本 ロシア の 2×2
news wall jp kr cn           # 国を選んで3分割 (feeds に足した国なら何か国でも)
news wall --hl 'NVIDIA|原油'   # stream のオプションはそのまま渡せる
news wall --restart           # 作り直す
```

tmux のセッション `news` を作って国ごとのペインで流します。tmux の中で実行すると新しいウィンドウに作ります。
`wall-preview.jpg` がテストデータで 240×60 桁(1920×1080 相当)に出したときの見た目です。

タイトルが `____` になるときはロケールが UTF-8 になっていません(`LANG=ja_JP.UTF-8` など)。

sway で tmux を使わずにやるなら、端末を4つ開けば同じことができます:

```sh
for c in us cn jp ru; do foot -T "news-$c" news -c "$c" & done
```

## 保存と検索 (grep / find)

取った記事はすべて、ただの TSV で溜まります。1記事1行、1行だけで完結しているので grep がそのまま効きます。

```
~/.local/share/news/archive/2026/09/29/ru.in.tsv
                            年   月 日  国.視点
```

列: `時刻  国  視点  フィードID  媒体名  タイトル  URL  概要`

```sh
grep -r 'NVIDIA' "$(news path)"                      # 全期間
rg -i 'iran' "$(news path archive)/2026/09"          # 9月だけ
find "$(news path archive)" -name 'ru.*' -newermt '2026-09-28'  # ロシアの2日分のファイル
cat "$(news path archive)"/2026/09/29/*.tsv | cut -f2,3 | sort | uniq -c   # 国×視点の件数
awk -F'\t' '$2=="cn" && $3=="out" {print $6}' "$(news path archive)"/2026/09/*/cn.out.tsv
```

同じことを `news grep` でも(一致部分に色、国・視点・期間で絞り込み):

```sh
news grep イラン
news grep -iE 'iran|hormuz' -c us,cn --since 3d
news grep 原油 -l in --since today -o oil.csv
```

## 書き出し・印刷

形式は `text` `csv` `tsv` `md` `html` `pdf` `jsonl`(と、スマホ向けの `page`)。`-o` の拡張子から自動で決まります。
text / md / html / pdf は「日付 → 国 → 外 | 内」でまとめ、PDF と HTML では外と内を左右2段で並べます。

```sh
news export                                  # 今日の分をテキストで標準出力
news export -c jp,ru -o today.pdf --summary  # 外と内の比較表 PDF
news export --since 1w -o week.csv --bom     # Excel で開くなら --bom
news export --date yesterday -o y.html
news export 'Iran|イラン' -E --since 3d -o iran.md

news print                                   # 今日の分を PDF にして lp へ
news print -c jp --printer Brother_HL --copies 2
news print --text                            # PDF ではなくテキストで
news print --dry-run                         # 印刷コマンドを見るだけ
```

`--since` / `--until` には `today` `yesterday` `3h` `2d` `1w` `2026-09-29` `0929` `'2026-09-29 18:00'` が使えます。

## フィードを足す (全世界へ)

```sh
news feeds          # 一覧
news feeds --edit   # $EDITOR で開く
news check          # 取れるか、古くないかを確認 (OK / STALE / EMPTY / FAIL)
```

書式は1行1フィード、空白区切り:

```
# 国  視点  ID          URL                                    [オプション]  表示名
kr    in    yonhap      https://www.yna.co.kr/rss/news.xml                    연합뉴스
gb    out   aljazeera-gb https://www.aljazeera.com/xml/rss/all.xml  match=(?<![\w.])(UK|Britain|British|London)(?!\w)  Al Jazeera
country np ネパール Nepal
```

| オプション | 意味 |
|---|---|
| `match=正規表現` | タイトル+概要+URL が一致する記事だけ採る。世界ニュースのフィード1本を国ごとに振り分けられるので、「外」の視点はこれでどの国にも作れます |
| `skip=正規表現` | 一致した記事を捨てる (例: Japan Today の `/category/world/`) |
| `strip=正規表現` | タイトルから消す (末尾の「\| 東洋経済オンライン」など) |
| `tz=+08:00` | 日付にタイムゾーンが無いフィード用 |

値に空白は使えないので正規表現の中の空白は `\s` で書きます。同じ URL を複数の行で使っても取得は1回です。
設定ファイルの末尾に韓国・イギリス・ドイツ・ウクライナの例がコメントで入っています。

## 既定のフィードについて

2026-09-29 に取得を確認したもの: Japan Today / 日経速報 / 東洋経済 / Al Jazeera / NPR / Fox News / WSJ Markets / SCMP / 中新网 / 36氪 / The Moscow Times / Meduza / РИА Новости / ТАСС / РБК / Lenta.ru。

確認できなかったもの(有名な URL ですが、こちらからは相手側に拒否されて届かなかった): The Japan Times / NHK / Yahoo!ニュース / BBC / NYTimes。
最初に `news check` を実行して、`FAIL` や `STALE` の行は直すか `#` で外してください。
日経は公式の RSS が無いため、非公式ミラー (assets.wor.jp) を使っています。

## 定期取得 (cron / systemd)

`news` を流していないあいだも溜めておきたいとき:

```sh
# crontab -e
*/10 * * * * $HOME/.local/bin/news fetch -q
```

`wall` や `stream` と同時に動かしても、ロックをかけて保存するので記事が重複したり欠けたりはしません。

## 翻訳

`--translate` は、かなを含まない見出し(英・中・露など)をコマンドに通して訳します。既定は `trans -b -no-autocorrect :ja`(translate-shell)。
好きなコマンドに差し替えられます。原文は標準入力で渡され、`{}` と書けば引数で渡されます。

```sh
news --translate 'trans -b -e bing :ja'
export NEWS_TRANSLATE='my-translator --to ja'   # --translate だけでこれを使う
news --translate --orig                         # 原文も下に出す
```

訳は `~/.local/share/news/state/translate.tsv` に「原文 TAB 訳」で溜まるので、これも grep できます。保存する記事そのものは原文のままです。

## 場所

| | 既定 | 変える環境変数 |
|---|---|---|
| 設定 | `~/.config/news/feeds.tsv` | `NEWS_FEEDS` |
| 記事 | `~/.local/share/news/archive/` | `NEWS_HOME` (これの下に archive と state) |
| あとで読む | `~/.local/share/news/later.tsv` (外したものは `later.done.tsv`) | 同上 |
| HTTP キャッシュ・既読・印刷用の一時ファイル | `~/.local/share/news/state/` | 同上 |

## なぜ Python か

最初は `news.sh` も考えましたが、RSS / RDF / Atom の3形式、Shift_JIS・GBK・windows-1251 といった文字コード、`&nbsp;` などが混じって XML として壊れたフィードを sh + curl + xmllint で扱うと壊れやすいので、依存ゼロで済む Python 1ファイルにしました(壊れたフィードは、直して読む → それでも駄目なら正規表現で拾う、の順で読みます)。
中身は外部コマンドとよく組み合わさるように作ってあります(TSV で保存、cron で取得、lp で印刷、tmux で分割、trans で翻訳)。
