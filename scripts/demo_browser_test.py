"""Headless-browser test of the live demo site, plus the README screenshots.

    pip install playwright && playwright install chromium     # not in requirements-dev (heavy)
    python scripts/demo_browser_test.py --url https://<cloudfront-domain> [--out docs/img]

Opens the page, asks one answerable and one off-topic question through the chat widget's own UI
(typing into the box and clicking Send), then does the same on the company-assistant tab. It
asserts on what a user would see (answer, numbered clickable sources, latency badge, refusal
pill) and on browser errors (console errors, CSP violations, failed requests), and saves PNGs.
It makes real API calls (a handful of questions).
"""

import argparse
import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ANSWERABLE = "How do I make a POST endpoint answer with 201 instead of the usual 200?"
OFF_TOPIC = "What is the capital of France?"
BUSINESS = (
    "If I change my mind about something I bought, how long do I have to send it back for my money?"
)


def ask(page: Page, question: str) -> object:
    """Type a question into the widget and wait for the answer card; return that card."""
    cards_before = page.locator(".card").count()
    page.locator(".input").fill(question)
    page.locator(".send").click()
    # The typing indicator appears, then is replaced by an answer card (or an error bubble).
    page.locator(".card").nth(cards_before).wait_for(timeout=90_000)
    return page.locator(".card").nth(cards_before)


def show_start(card) -> None:
    """Scroll the chat so the top of this answer shows (long answers otherwise show the end)."""
    card.evaluate("el => { el.parentElement.scrollTop = el.offsetTop - 8; }")


def check(condition: bool, message: str, failures: list[str]) -> None:
    print(("  ok   " if condition else "  FAIL ") + message)
    if not condition:
        failures.append(message)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", required=True, help="CloudFront URL of the demo site")
    parser.add_argument("--out", type=Path, default=Path("docs/img"))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    failures: list[str] = []
    problems: list[str] = []  # console errors, page errors, failed requests

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.on(
            "console",
            lambda m: problems.append(f"console {m.type}: {m.text}") if m.type == "error" else None,
        )
        page.on("pageerror", lambda e: problems.append(f"page error: {e}"))
        page.on("requestfailed", lambda r: problems.append(f"request failed: {r.url} {r.failure}"))

        print("page")
        response = page.goto(args.url)
        check(response is not None and response.status == 200, "page loads with HTTP 200", failures)
        check(
            "Ask the docs" in page.title() or "RAG" in page.title(),
            f"title is set ({page.title()!r})",
            failures,
        )
        check(page.get_by_role("tab").count() == 2, "two knowledge-base tabs are shown", failures)
        check(
            page.locator(".bubble").count() == 1, "chat bubble is rendered by the widget", failures
        )

        print("developer docs tab")
        page.locator(".bubble").click()
        card = ask(page, ANSWERABLE)
        sources = card.locator(".sources li")
        check(sources.count() >= 1, f"answer shows numbered sources ({sources.count()})", failures)
        check(
            sources.first.locator(".n").inner_text().startswith("[1]"),
            "first source is numbered [1]",
            failures,
        )
        href = sources.first.locator("a").get_attribute("href") or ""
        check(
            href.startswith("https://fastapi.tiangolo.com/tutorial/"),
            f"source is a clickable link ({href})",
            failures,
        )
        raw_markdown = "```" in card.locator(".msg").inner_text()
        check(not raw_markdown, "code fences are rendered, not shown as raw backticks", failures)
        check(
            card.locator(".sources li.uncited").count() <= sources.count() - 1,
            "cited sources stay prominent",
            failures,
        )
        meta = card.locator(".meta").inner_text()
        check(
            "s" in meta and "tokens" in meta, f"latency/token badge is shown ({meta!r})", failures
        )
        show_start(card)
        page.screenshot(path=str(args.out / "demo-answer.png"))

        refusal = ask(page, OFF_TOPIC)
        check(
            refusal.locator(".pill").count() == 1,
            "off-topic question shows the 'I don't know' pill",
            failures,
        )
        check(
            "don't know" in refusal.locator(".msg").inner_text().lower(),
            "refusal text is shown",
            failures,
        )
        show_start(refusal)
        page.screenshot(path=str(args.out / "demo-refusal.png"))

        print("company support tab")
        page.get_by_role("tab", name="Company support assistant").click()
        check(
            page.locator(".card").count() == 0,
            "switching tabs starts a fresh conversation",
            failures,
        )
        check(page.locator("#panel-business").is_visible(), "company examples are shown", failures)
        card = ask(page, BUSINESS)
        keys = card.locator(".sources li").all_inner_texts()
        check(
            any("returns-and-warranty.pdf" in k for k in keys),
            f"answer cites the returns PDF ({keys[:2]})",
            failures,
        )
        link = card.locator(".sources li a").first.get_attribute("href") or ""
        check(
            link.startswith(
                "https://github.com/usmanibrahim17/rag-on-aws/blob/main/sample-docs/business/"
            ),
            f"business source links to GitHub ({link})",
            failures,
        )
        show_start(card)
        page.screenshot(path=str(args.out / "demo-business.png"))

        print("mobile layout")
        page.set_viewport_size({"width": 390, "height": 800})
        overflow = page.evaluate("document.documentElement.scrollWidth > window.innerWidth")
        check(not overflow, "no horizontal scrolling at 390px width", failures)
        browser.close()

    print("browser problems")
    check(not problems, "no console errors, CSP violations or failed requests", failures)
    for problem in problems:
        print("   ", problem)
    print(f"\n{'FAILED' if failures else 'PASSED'}: {len(failures)} failing check(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
