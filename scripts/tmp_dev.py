import re
import requests

js = requests.get("https://app.invoapp.com/main.dart.js", timeout=120).content.decode("utf-8", errors="ignore")

for pat in [r'deviceId.{0,120}', r'deviceType.{0,120}']:
    ms = list(re.finditer(pat, js))
    print(pat, "->", len(ms))
    seen = set()
    for m in ms:
        seg = js[max(0, m.start()-160):m.start()+200].replace("\n", " ")
        key = seg[:120]
        if key in seen:
            continue
        seen.add(key)
        print("  ...", seg[:320])
        if len(seen) >= 4:
            break
