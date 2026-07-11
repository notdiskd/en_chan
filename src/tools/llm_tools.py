import asyncio
import httpx
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from tools.tool_schema import function_to_tool
from storage.diary import search_diary

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}


async def web_search(query: str, max_results: int = 5) -> str:
    """
    Search the Internet for a query. Returns a list of titles, links,
    and short descriptions. Use it to find up-to-date information,
    such as news, facts, and current events.

    Args:
        query: Search query in natural language
        max_results: How many results to return
    """
    url = "https://html.duckduckgo.com/html/"

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(url, data={"q": query}, headers=HEADERS)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            return f"Search error: {e}"

    soup = BeautifulSoup(resp.text, "lxml")
    results = []

    for result in soup.select(".result")[:max_results]:
        title_tag = result.select_one(".result__title a")
        snippet_tag = result.select_one(".result__snippet")

        if not title_tag:
            continue

        title = title_tag.get_text(strip=True)
        link = title_tag.get("href", "")
        snippet = snippet_tag.get_text(strip=True) if snippet_tag else ""

        results.append(f"[{title}]({link})\n{snippet}")

    if not results:
        return "Nothing was found for the query."

    return "\n\n".join(results)


async def web_fetch(url: str) -> str:
    """
    Open a specific page from a link and get its text content.
    Use it after web_search if you need details from a specific page,
    not just a snippet.

    Args:
        url: Full URL of page, starting from http:// or https://
    """
    max_chars = 3000

    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        try:
            resp = await client.get(url, headers=HEADERS)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            return f"Page loading error: {e}"

    content_type = resp.headers.get("content-type", "")
    if "text/html" not in content_type:
        return f"Page is not HTML (content-type: {content_type})"

    soup = BeautifulSoup(resp.text, "lxml")

    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
        tag.decompose()

    text = soup.get_text(separator="\n", strip=True)

    lines = [line for line in text.splitlines() if line.strip()]
    clean_text = "\n".join(lines)

    if len(clean_text) > max_chars:
        clean_text = clean_text[:max_chars] + "...\n[text is truncated]"

    return clean_text


async def get_current_time() -> str:
    """
    Get current full date and time in UTC timezone.
    """
    utc_now = datetime.now(timezone.utc)
    formatted_date = utc_now.strftime('%A, %d %B %Y, %H:%M')
    return formatted_date

async def wait(delay: float) -> str:
    """
    Wait for a specified amount of time.
    Use it when you need to pause the execution for a short period.

    Args:
        delay: The number of seconds to wait. Maximum 60 seconds.
    """
    if delay > 60:
        delay = 60
    await asyncio.sleep(delay)
    return f"Waited for {delay} seconds."

async def stay_silent() -> str:
    """
    Use it when you decide not to respond to a message — for example, you finished talking, the message is not addressed to you,
    the topic is not interesting, or you have already checked something through another tool and realized
    that you do not need to respond. No response will be sent after calling this function.
    """
    return "SILENCE_SIGNAL"

TOOL_FUNCS = {
    "web_search": web_search,
    "web_fetch": web_fetch,
    "get_current_time": get_current_time,
    "stay_silent": stay_silent,
    "wait": wait,
    "search_diary": search_diary,
}

TOOLS = [function_to_tool(fn) for fn in TOOL_FUNCS.values()]