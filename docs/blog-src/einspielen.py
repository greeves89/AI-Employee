"""Beiträge und Bilder über den MCP-Dienst des Blogs einspielen.

  export BLOG_MCP_URL=https://example.com/api/v1/mcp/blog
  export BLOG_MCP_TOKEN=...            # aus der .env der Anlage, nie ins Repo
  python3 einspielen.py bilder         # bilder/out/*.png hochladen (vorher: node bilder/render.mjs)
  python3 einspielen.py entwurf        # alle Beiträge als Entwurf speichern, Prüfung zeigen
  python3 einspielen.py verweise       # wo wird der Hauptbegriff schon genannt?
  python3 einspielen.py online [slug]  # veröffentlichen (alle oder einen)
  python3 einspielen.py liste
"""
import base64
import glob
import json
import os
import re
import sys
import urllib.request

import lesen

URL = os.environ["BLOG_MCP_URL"]
TOKEN = os.environ["BLOG_MCP_TOKEN"]
_id = 0


def rpc(method, params=None):
    global _id
    _id += 1
    req = urllib.request.Request(
        URL,
        data=json.dumps({"jsonrpc": "2.0", "id": _id, "method": method, "params": params or {}}).encode(),
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json",
                 "Accept": "application/json", "User-Agent": "blog-einspielen/1.0"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def werkzeug(name, **args):
    antwort = rpc("tools/call", {"name": name, "arguments": args})["result"]
    return antwort["content"][0]["text"], antwort["isError"]


def alt_texte():
    """Beschreibung je Bild aus den Beiträgen: ![Beschreibung](/blog/media/name.png)"""
    texte = {}
    for a in lesen.alle():
        for alt, name in re.findall(r"!\[([^\]]*)\]\(/blog/media/([^)]+)\)", a["body_markdown"]):
            texte[name] = alt
        if a.get("cover"):
            texte.setdefault(a["cover"], f"Titelbild: {a['title']}")
    return texte


def main():
    modus = sys.argv[1]
    nur = sys.argv[2] if len(sys.argv) > 2 else None
    init = rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                              "clientInfo": {"name": "blog-einspielen", "version": "1.0"}})
    print("Dienst:", init["result"]["serverInfo"]["name"], "| Werkzeuge:", len(rpc("tools/list")["result"]["tools"]))

    if modus == "liste":
        print(werkzeug("blog_list_posts")[0])
        print(werkzeug("blog_list_images")[0])
        return

    if modus == "bilder":
        texte = alt_texte()
        for pfad in sorted(glob.glob(os.path.join(lesen.HIER, "bilder", "out", "*.png"))):
            name = os.path.basename(pfad)
            daten = base64.b64encode(open(pfad, "rb").read()).decode()
            text, fehler = werkzeug("blog_upload_image", name=name, data_base64=daten, alt=texte.get(name, ""))
            print(("FEHLER " + text) if fehler else f'{name:40} {json.loads(text)["ergebnis"]:12} {json.loads(text)["kb"]} kB')
        return

    for a in lesen.alle():
        if nur and a["slug"] != nur:
            continue
        if modus == "entwurf":
            text, fehler = werkzeug("blog_save_post", **a)
            if fehler:
                print("FEHLER", a["slug"], text)
                continue
            d = json.loads(text)
            p = d["pruefung"]
            print(f'{d["slug"]:30} {d["ergebnis"]:22} W={p["worte"]} Bilder={p["bilder"]} fehler={p["fehler"]} hinweise={p["hinweise"]}')
            print("   ", d["vorschau"])
        elif modus == "online":
            text, fehler = werkzeug("blog_publish", slug=a["slug"])
            print(("FEHLER " if fehler else "online ") + a["slug"], text if fehler else "")
        elif modus == "verweise":
            text, _ = werkzeug("blog_find_mentions", keyword=a["keyword"], target_slug=a["slug"])
            d = json.loads(text)
            print(f'{a["keyword"]:28} genannt in {d["anzahl"]}: ' + ", ".join(
                t["slug"] + ("" if t["verlinkt_schon"] else " (ohne Verweis)") for t in d["treffer"]))


if __name__ == "__main__":
    main()
