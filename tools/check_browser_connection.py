"""Read-only CDP diagnosis. Never navigates, reads cookies or changes readiness.

Run inside the app container: python -m tools.check_browser_connection
"""
import asyncio
import json

from playwright.async_api import async_playwright

from src.services.browser_session_service import BrowserConnectionError, connect_remote_browser


async def check():
    try:
        async with async_playwright() as playwright:
            browser, context = await connect_remote_browser(playwright)
            print(json.dumps({'ok': True, 'stage': 'connected', 'contexts': len(browser.contexts)}, ensure_ascii=False))
            # Disconnect the client by exiting Playwright. Do not close the browser.
    except BrowserConnectionError as exc:
        print(json.dumps({'ok': False, 'code': exc.code, 'message': str(exc)}, ensure_ascii=False))
        return 1
    except Exception as exc:
        print(json.dumps({'ok': False, 'code': 'cdp_driver_error', 'error_type': type(exc).__name__}))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(asyncio.run(check()))
