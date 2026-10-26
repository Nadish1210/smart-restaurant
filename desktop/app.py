"""Smart Restaurant - Windows desktop wrapper (Developed by Nadish).

Opens the EXISTING Streamlit site in its own window. No data is stored here and
no keys are embedded: the site talks to Supabase exactly as it does in a browser.
"""
import os
import sys
import webbrowser
from urllib.parse import urlparse
from urllib.request import urlopen

import webview

APP_NAME = "Smart Restaurant"
SITE_URL = "https://smartrestaurant1210.streamlit.app/"
ALLOWED_HOSTS = ("streamlit.app", "supabase.co", "pages.dev")


def resource(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


OFFLINE_HTML = """<!doctype html><meta charset=utf-8><body style="font-family:Segoe UI,sans-serif;
text-align:center;padding:12vh 24px;color:#064e3b"><h2>Smart Restaurant</h2>
<p>No internet connection, or the server is waking up.</p>
<p><button onclick="location.href='%s'" style="padding:10px 22px;font-size:16px">Try again</button></p>
<p style="color:#666;font-size:13px">Developed by Nadish</p></body>""" % SITE_URL


def site_reachable():
    try:
        urlopen(SITE_URL, timeout=8)
        return True
    except Exception:
        return False


def on_loaded(window):
    # Keep external links (anything not our site) in the normal browser.
    window.evaluate_js("""
    document.addEventListener('click', function(e){
      var a = e.target.closest && e.target.closest('a[href]');
      if(!a) return;
      var h = a.getAttribute('href') || '';
      if(/^https?:/i.test(h) && !/(streamlit\\.app|supabase\\.co|pages\\.dev)/i.test(new URL(h).host)){
        e.preventDefault();
        if(window.pywebview) window.pywebview.api.open_external(h);
      }
    }, true);""")


class Api:
    def open_external(self, url):
        if urlparse(url).scheme in ("http", "https"):
            webbrowser.open(url)


def main():
    webview.settings["ALLOW_DOWNLOADS"] = True   # CSV / PDF / QR downloads
    api = Api()
    online = site_reachable()
    window = webview.create_window(
        f"{APP_NAME} - Developed by Nadish",
        SITE_URL if online else None,
        html=None if online else OFFLINE_HTML,
        width=1280, height=820, min_size=(900, 600),
        js_api=api,
    )
    window.events.loaded += lambda: on_loaded(window)
    storage = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "SmartRestaurant")
    os.makedirs(storage, exist_ok=True)
    # private_mode=False + storage_path keeps the login between launches
    webview.start(private_mode=False, storage_path=storage,
                  icon=resource(os.path.join("assets", "icon.ico")))


if __name__ == "__main__":
    main()
