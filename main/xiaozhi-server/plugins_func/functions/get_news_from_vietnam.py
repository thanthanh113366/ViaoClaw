import random
import requests
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
from config.logger import setup_logging
from plugins_func.register import register_function, ToolType, ActionResponse, Action
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()

# RSS feeds by source and category
RSS_SOURCES = {
    "vnexpress": {
        "name": "VnExpress",
        "categories": {
            "mới nhất":   "https://vnexpress.net/rss/tin-moi-nhat.rss",
            "thời sự":    "https://vnexpress.net/rss/thoi-su.rss",
            "thế giới":   "https://vnexpress.net/rss/the-gioi.rss",
            "kinh doanh": "https://vnexpress.net/rss/kinh-doanh.rss",
            "công nghệ":  "https://vnexpress.net/rss/khoa-hoc.rss",
            "thể thao":   "https://vnexpress.net/rss/the-thao.rss",
            "giải trí":   "https://vnexpress.net/rss/giai-tri.rss",
        },
        "default": "mới nhất",
    },
    "tuoitre": {
        "name": "Tuổi Trẻ",
        "categories": {
            "mới nhất":   "https://tuoitre.vn/rss/tin-moi-nhat.rss",
            "thời sự":    "https://tuoitre.vn/rss/thoi-su.rss",
            "thế giới":   "https://tuoitre.vn/rss/the-gioi.rss",
            "kinh tế":    "https://tuoitre.vn/rss/kinh-te.rss",
            "công nghệ":  "https://tuoitre.vn/rss/nhip-song-so.rss",
            "thể thao":   "https://tuoitre.vn/rss/the-thao.rss",
            "giải trí":   "https://tuoitre.vn/rss/giai-tri.rss",
        },
        "default": "mới nhất",
    },
    "dantri": {
        "name": "Dân Trí",
        "categories": {
            "mới nhất":   "https://dantri.com.vn/rss/home.rss",
            "xã hội":     "https://dantri.com.vn/rss/xa-hoi.rss",
            "thế giới":   "https://dantri.com.vn/rss/the-gioi.rss",
            "kinh doanh": "https://dantri.com.vn/rss/kinh-doanh.rss",
            "công nghệ":  "https://dantri.com.vn/rss/khoa-hoc-cong-nghe.rss",
            "thể thao":   "https://dantri.com.vn/rss/the-thao.rss",
            "giải trí":   "https://dantri.com.vn/rss/giai-tri.rss",
        },
        "default": "mới nhất",
    },
    "thanhnien": {
        "name": "Thanh Niên",
        "categories": {
            "mới nhất":   "https://thanhnien.vn/rss/home.rss",
            "thời sự":    "https://thanhnien.vn/rss/thoi-su.rss",
            "thế giới":   "https://thanhnien.vn/rss/the-gioi.rss",
            "kinh tế":    "https://thanhnien.vn/rss/kinh-te.rss",
            "công nghệ":  "https://thanhnien.vn/rss/cong-nghe.rss",
            "thể thao":   "https://thanhnien.vn/rss/the-thao.rss",
            "giải trí":   "https://thanhnien.vn/rss/giai-tri.rss",
        },
        "default": "mới nhất",
    },
}

# Keyword → (source_key, category_key)
KEYWORD_MAP = {
    "vnexpress": ("vnexpress", None),
    "vn express": ("vnexpress", None),
    "tuổi trẻ": ("tuoitre", None),
    "tuoi tre": ("tuoitre", None),
    "dân trí": ("dantri", None),
    "dan tri": ("dantri", None),
    "thanh niên": ("thanhnien", None),
    "thanh nien": ("thanhnien", None),
    # categories (search all sources → use default source vnexpress)
    "thời sự": (None, "thời sự"),
    "thoi su": (None, "thời sự"),
    "thế giới": (None, "thế giới"),
    "the gioi": (None, "thế giới"),
    "kinh doanh": (None, "kinh doanh"),
    "kinh tế": (None, "kinh tế"),
    "công nghệ": (None, "công nghệ"),
    "cong nghe": (None, "công nghệ"),
    "thể thao": (None, "thể thao"),
    "the thao": (None, "thể thao"),
    "giải trí": (None, "giải trí"),
    "giai tri": (None, "giải trí"),
    "xã hội": (None, "xã hội"),
    "xa hoi": (None, "xã hội"),
}

DEFAULT_SOURCE = "vnexpress"

GET_NEWS_FROM_VIETNAM_FUNCTION_DESC = {
    "type": "function",
    "function": {
        "name": "get_news_from_vietnam",
        "description": (
            "Gọi khi người dùng muốn nghe tin tức Việt Nam "
            "(ví dụ: 'đọc tin tức', 'có tin gì mới không', 'tin thể thao hôm nay'). "
            "Hỗ trợ các nguồn: VnExpress, Tuổi Trẻ, Dân Trí, Thanh Niên. "
            "Hỗ trợ các chuyên mục: thời sự, thế giới, kinh doanh, công nghệ, thể thao, giải trí."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Nguồn hoặc chuyên mục tin tức. Ví dụ: 'vnexpress', 'tuổi trẻ', "
                        "'thể thao', 'công nghệ', 'thế giới'. Nếu không cung cấp thì lấy tin mới nhất."
                    ),
                },
                "detail": {
                    "type": "boolean",
                    "description": "true nếu người dùng muốn đọc chi tiết bài báo vừa đọc tiêu đề.",
                },
            },
            "required": [],
        },
    },
}


def fetch_news_from_rss(rss_url):
    """Fetch and parse RSS feed, return list of news items."""
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(rss_url, headers=headers, timeout=10)
        response.raise_for_status()

        root = ET.fromstring(response.content)
        items = []
        for item in root.findall(".//item"):
            title = item.find("title")
            link = item.find("link")
            description = item.find("description")
            pub_date = item.find("pubDate")

            title_text = title.text.strip() if title is not None and title.text else "Không có tiêu đề"
            link_text = link.text.strip() if link is not None and link.text else "#"
            desc_text = ""
            if description is not None and description.text:
                soup = BeautifulSoup(description.text, "html.parser")
                desc_text = soup.get_text().strip()
            pub_text = pub_date.text.strip() if pub_date is not None and pub_date.text else ""

            items.append({
                "title": title_text,
                "link": link_text,
                "description": desc_text,
                "pubDate": pub_text,
            })

        return items
    except Exception as e:
        logger.bind(tag=TAG).error(f"Lỗi lấy RSS {rss_url}: {e}")
        return []


def fetch_news_detail(url):
    """Fetch full article content from URL."""
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, "html.parser")

        # Try common Vietnamese news article content selectors
        content_div = soup.select_one(
            "article, .fck_detail, .content-detail, .article-body, "
            ".singular-content, .content_detail, [class*='article-content']"
        )
        if content_div:
            paragraphs = content_div.find_all("p")
        else:
            paragraphs = soup.find_all("p")

        content = "\n".join(
            p.get_text().strip() for p in paragraphs if p.get_text().strip()
        )
        return content[:3000] if content else "Không thể lấy nội dung."
    except Exception as e:
        logger.bind(tag=TAG).error(f"Lỗi lấy chi tiết bài báo {url}: {e}")
        return "Không thể lấy nội dung."


def resolve_query(query: str):
    """Return (source_key, category_key, rss_url, source_name)."""
    if not query:
        src = DEFAULT_SOURCE
        cat = RSS_SOURCES[src]["default"]
        return src, cat, RSS_SOURCES[src]["categories"][cat], RSS_SOURCES[src]["name"]

    q = query.lower().strip()

    matched_source, matched_cat = KEYWORD_MAP.get(q, (None, None))

    # If only category matched, use default source
    if matched_cat and not matched_source:
        matched_source = DEFAULT_SOURCE

    src = matched_source or DEFAULT_SOURCE
    src_info = RSS_SOURCES[src]

    # Find category URL
    if matched_cat and matched_cat in src_info["categories"]:
        cat = matched_cat
    else:
        # Try to find category by partial match
        cat = None
        for key in src_info["categories"]:
            if key in q or q in key:
                cat = key
                break
        if not cat:
            cat = src_info["default"]

    rss_url = src_info["categories"][cat]
    return src, cat, rss_url, src_info["name"]


@register_function(
    "get_news_from_vietnam",
    GET_NEWS_FROM_VIETNAM_FUNCTION_DESC,
    ToolType.SYSTEM_CTL,
)
def get_news_from_vietnam(
    conn: "ConnectionHandler",
    query: str = None,
    detail: bool = False,
):
    """Fetch Vietnamese news from RSS and read a random article."""
    try:
        # Detail mode: read full content of last article
        if detail:
            last = getattr(conn, "last_vn_news", None)
            if not last or not last.get("link") or last.get("link") == "#":
                return ActionResponse(
                    Action.REQLLM,
                    "Chưa có bài báo nào được đọc. Hãy yêu cầu tin tức trước.",
                    None,
                )
            content = fetch_news_detail(last["link"])
            if not content or content == "Không thể lấy nội dung.":
                return ActionResponse(
                    Action.REQLLM,
                    f"Không thể lấy nội dung chi tiết bài '{last['title']}'.",
                    None,
                )
            report = (
                f"Dựa vào nội dung sau, hãy tóm tắt và đọc bài báo bằng tiếng Việt tự nhiên:\n\n"
                f"Tiêu đề: {last['title']}\n"
                f"Nội dung: {content}\n\n"
                f"(Tóm tắt ngắn gọn, súc tích, đọc như đang kể chuyện, không cần nói 'tóm tắt'.)"
            )
            return ActionResponse(Action.REQLLM, report, None)

        # Fetch news list
        src_key, cat_key, rss_url, src_name = resolve_query(query)
        logger.bind(tag=TAG).info(f"Lấy tin từ {src_name} / {cat_key}: {rss_url}")

        items = fetch_news_from_rss(rss_url)
        if not items:
            return ActionResponse(
                Action.REQLLM,
                f"Không lấy được tin từ {src_name}. Vui lòng thử lại sau.",
                None,
            )

        news = random.choice(items[:20])  # pick from top 20

        conn.last_vn_news = {
            "link": news["link"],
            "title": news["title"],
            "source": src_name,
        }

        report = (
            f"Hãy đọc tin tức sau bằng tiếng Việt tự nhiên:\n\n"
            f"Nguồn: {src_name} / {cat_key}\n"
            f"Tiêu đề: {news['title']}\n"
            f"Tóm tắt: {news['description']}\n"
            f"Thời gian: {news['pubDate']}\n\n"
            f"(Đọc ngắn gọn, tự nhiên. Nếu người dùng muốn biết thêm, bảo họ nói 'đọc chi tiết'.)"
        )
        return ActionResponse(Action.REQLLM, report, None)

    except Exception as e:
        logger.bind(tag=TAG).error(f"Lỗi get_news_from_vietnam: {e}")
        return ActionResponse(
            Action.REQLLM,
            "Xin lỗi, đã có lỗi khi lấy tin tức. Vui lòng thử lại.",
            None,
        )
