import requests
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime, parsedate_to_datetime
from urllib.parse import urljoin
import hashlib
import re
import time


# --------------------------------------------------
# 設定
# --------------------------------------------------

URL = "https://sports-hosei.net/category/all/rugby/"
OUTPUT = Path(__file__).parent / "hosei_rugby.xml"

JST = timezone(timedelta(hours=9))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/154.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.8",
}


# --------------------------------------------------
# 既存RSSを読み込む
# --------------------------------------------------

old_items = {}

if OUTPUT.exists():
    try:
        old_tree = ET.parse(OUTPUT)

        for item in old_tree.getroot().findall("./channel/item"):
            guid = item.findtext("guid", "")

            if guid:
                old_items[guid] = {
                    "title": item.findtext("title", ""),
                    "link": item.findtext("link", ""),
                    "description": item.findtext("description", ""),
                    "pubDate": item.findtext("pubDate", ""),
                    "guid": guid,
                }

    except Exception:
        old_items = {}


# --------------------------------------------------
# 一覧ページ取得
# --------------------------------------------------

session = requests.Session()
session.headers.update(HEADERS)

response = session.get(
    URL,
    timeout=30
)

print("一覧ページ HTTP:", response.status_code)

response.raise_for_status()
response.encoding = response.apparent_encoding

soup = BeautifulSoup(
    response.text,
    "html.parser"
)


# --------------------------------------------------
# 一覧ページから記事URLを取得
# --------------------------------------------------

article_urls = []
seen_urls = set()

for a in soup.find_all("a", href=True):

    article_url = urljoin(
        URL,
        a.get("href", "").strip()
    )

    # 個別記事URL
    if not re.fullmatch(
        r"https://sports-hosei\.net/\d+/?",
        article_url
    ):
        continue

    # 一覧上でラグビー記事と確認できるものだけ
    text = " ".join(
        a.stripped_strings
    )

    if (
        "【ラグビー】" not in text
        and "〖ラグビー〗" not in text
    ):
        continue

    if article_url in seen_urls:
        continue

    seen_urls.add(article_url)
    article_urls.append(article_url)


print(
    "一覧から検出:",
    len(article_urls),
    "件"
)


# --------------------------------------------------
# 個別記事から正式タイトル・日付を取得
# --------------------------------------------------

current_items = []

date_patterns = [
    re.compile(r"20\d{2}\.\d{1,2}\.\d{1,2}"),
    re.compile(r"20\d{2}/\d{1,2}/\d{1,2}"),
    re.compile(r"20\d{2}年\d{1,2}月\d{1,2}日"),
]


for index, article_url in enumerate(
    article_urls,
    start=1
):

    try:
        article_response = session.get(
            article_url,
            timeout=30
        )

        print(
            f"[{index}/{len(article_urls)}]",
            article_response.status_code,
            article_url
        )

        article_response.raise_for_status()
        article_response.encoding = article_response.apparent_encoding

        article_soup = BeautifulSoup(
            article_response.text,
            "html.parser"
        )

        # ------------------------------------------
        # 正式タイトル
        # ------------------------------------------

        title = ""

        # og:titleを最優先
        og_title = article_soup.find(
            "meta",
            property="og:title"
        )

        if og_title and og_title.get("content"):
            title = og_title.get("content").strip()

        # og:titleがなければh1
        if not title:
            h1 = article_soup.find("h1")

            if h1:
                title = " ".join(
                    h1.stripped_strings
                ).strip()

        if not title:
            print(
                "  → タイトル取得失敗"
            )
            continue

        # サイト名が末尾に付いていた場合に除去
        title = re.sub(
            r"\s*[|｜]\s*スポーツ法政.*$",
            "",
            title
        ).strip()


        # ------------------------------------------
        # ラグビー記事か再確認
        # ------------------------------------------

        if (
            "【ラグビー】" not in title
            and "〖ラグビー〗" not in title
        ):
            print(
                "  → ラグビー記事ではないため除外:",
                title
            )
            continue


        # ------------------------------------------
        # 公開日
        # ------------------------------------------

        date_text = None

        # WordPress等の公開日時metaを優先
        time_tag = article_soup.find(
            "time",
            attrs={"datetime": True}
        )

        if time_tag:
            datetime_value = time_tag.get(
                "datetime",
                ""
            )

            match = re.search(
                r"(20\d{2})-(\d{2})-(\d{2})",
                datetime_value
            )

            if match:
                date_text = (
                    f"{match.group(1)}."
                    f"{match.group(2)}."
                    f"{match.group(3)}"
                )


        # meta article:published_time
        if not date_text:
            published_meta = article_soup.find(
                "meta",
                property="article:published_time"
            )

            if (
                published_meta
                and published_meta.get("content")
            ):
                match = re.search(
                    r"(20\d{2})-(\d{2})-(\d{2})",
                    published_meta.get("content")
                )

                if match:
                    date_text = (
                        f"{match.group(1)}."
                        f"{match.group(2)}."
                        f"{match.group(3)}"
                    )


        # 画面上の日付から探す
        if not date_text:

            page_text = " ".join(
                article_soup.stripped_strings
            )

            for pattern in date_patterns:

                match = pattern.search(
                    page_text
                )

                if match:
                    date_text = match.group(0)
                    break


        if not date_text:
            print(
                "  → 日付取得失敗:",
                title
            )
            continue


        # ------------------------------------------
        # 日付をdatetimeへ
        # ------------------------------------------

        normalized_date = (
            date_text
            .replace("年", ".")
            .replace("月", ".")
            .replace("日", "")
            .replace("/", ".")
        )

        parts = normalized_date.split(".")

        year = int(parts[0])
        month = int(parts[1])
        day = int(parts[2])

        dt = datetime(
            year,
            month,
            day,
            12,
            0,
            0,
            tzinfo=JST
        )

        pub_date = format_datetime(dt)


        # ------------------------------------------
        # GUID
        # ------------------------------------------

        guid = hashlib.sha256(
            article_url.encode("utf-8")
        ).hexdigest()


        # ------------------------------------------
        # RSS項目
        # ------------------------------------------

        current_items.append(
            {
                "title": title,
                "link": article_url,
                "description": (
                    "スポーツ法政 ラグビー"
                ),
                "pubDate": pub_date,
                "guid": guid,
            }
        )

        # サイトへ連続アクセスしすぎないよう少し待つ
        time.sleep(0.3)

    except Exception as e:

        print(
            "  → 記事取得失敗:",
            article_url,
            e
        )


# --------------------------------------------------
# 既存RSSと統合
# --------------------------------------------------

all_items = []
seen_guids = set()


for item in current_items:

    if item["guid"] not in seen_guids:
        all_items.append(item)
        seen_guids.add(item["guid"])


for guid, item in old_items.items():

    if guid not in seen_guids:
        all_items.append(item)
        seen_guids.add(guid)


# --------------------------------------------------
# 新しい順
# --------------------------------------------------

def get_date(item):

    try:
        return parsedate_to_datetime(
            item["pubDate"]
        )

    except Exception:
        return datetime.min.replace(
            tzinfo=timezone.utc
        )


all_items.sort(
    key=get_date,
    reverse=True
)

all_items = all_items[:300]


# --------------------------------------------------
# RSS作成
# --------------------------------------------------

rss = ET.Element(
    "rss",
    version="2.0"
)

channel = ET.SubElement(
    rss,
    "channel"
)

ET.SubElement(
    channel,
    "title"
).text = "スポーツ法政 ラグビー"

ET.SubElement(
    channel,
    "link"
).text = URL

ET.SubElement(
    channel,
    "description"
).text = (
    "スポーツ法政のラグビー新着記事"
)

ET.SubElement(
    channel,
    "language"
).text = "ja"


# --------------------------------------------------
# RSS記事
# --------------------------------------------------

for item in all_items:

    element = ET.SubElement(
        channel,
        "item"
    )

    ET.SubElement(
        element,
        "title"
    ).text = item["title"]

    ET.SubElement(
        element,
        "link"
    ).text = item["link"]

    ET.SubElement(
        element,
        "description"
    ).text = item["description"]

    ET.SubElement(
        element,
        "pubDate"
    ).text = item["pubDate"]

    guid_element = ET.SubElement(
        element,
        "guid"
    )

    guid_element.set(
        "isPermaLink",
        "false"
    )

    guid_element.text = item["guid"]


# --------------------------------------------------
# XML保存
# --------------------------------------------------

tree = ET.ElementTree(rss)

ET.indent(
    tree,
    space="  "
)

tree.write(
    OUTPUT,
    encoding="utf-8",
    xml_declaration=True
)


# --------------------------------------------------
# 結果表示
# --------------------------------------------------

print()
print("RSS作成成功")
print(
    "今回取得:",
    len(current_items),
    "件"
)
print(
    "RSS保存件数:",
    len(all_items),
    "件"
)
print(
    "保存先:",
    OUTPUT
)

print()
print("取得記事:")

for i, item in enumerate(
    current_items,
    start=1
):

    print()
    print(
        f"[{i}] {item['title']}"
    )
    print(
        "    ",
        item["pubDate"]
    )
    print(
        "    ",
        item["link"]
    )