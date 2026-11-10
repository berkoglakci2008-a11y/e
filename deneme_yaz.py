"""Ollama yazarını videosuz dene: python deneme_yaz.py ["altyazı cümlesi" ...]
Modelin ham çıktısını ve filtrenin neden kabul/ret ettiğini gösterir (model/prompt ayarı için)."""
import json, sys
from yca.llm import OllamaWriter, KEYS, _J, _stems, check, BOUNDS

cfg = json.load(open("config.json", encoding="utf-8")).get("llm", {})
w = OllamaWriter(cfg.get("url", "http://127.0.0.1:11434"), cfg.get("model", "qwen2.5:7b"), cfg.get("timeout", 90))
ok, model, why = w.status(force=True)
print("Ollama:", "hazır, model = " + model if ok else "HAZIR DEĞİL: " + why)
if not ok: sys.exit(1)
subs = sys.argv[1:] or ["adamlara poseidon bıçak awp ne varsa geldi", "battle zaten dokuz ona kasa kala gitmişti bile", "bu ara drop bu kasa jokerle denenir bence"]
for s in subs:
    m = {"category": "Genel", "subtitle": s, "context": s, "t": 0}
    body = {"model": model, "stream": False, "format": __import__("yca.llm", fromlist=["SCHEMA"]).SCHEMA,
            "messages": [{"role": "system", "content": __import__("yca.llm", fromlist=["SYSTEM"]).SYSTEM}, {"role": "user", "content": w.prompt(m, "", None, [])}],
            "options": {"temperature": 0.9, "top_p": 0.92, "repeat_penalty": 1.15, "num_ctx": 4096}}
    raw = json.loads(w._http("/api/chat", body)["message"]["content"])
    print("\nALTYAZI:", s)
    for k in KEYS:
        t = raw.get(_J[k]); r = check(t, k, _stems(s))
        print(f"  {k:5} {'KABUL' if r else 'RET  '} | {t!r}" + (f"  ->  {r}" if r else ""))
