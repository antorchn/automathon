"""Embeds automathon_demarrage.ipynb into the guide's download button, so the HTML works as a single file.

Re-run after editing the notebook:  python embed_notebook.py
"""
import base64
import pathlib
import re

HERE = pathlib.Path(__file__).resolve().parent
GUIDE = HERE / "guide_participants_automathon.html"
NOTEBOOK = HERE / "automathon_demarrage.ipynb"

payload = base64.b64encode(NOTEBOOK.read_bytes()).decode("ascii")
html = GUIDE.read_text(encoding="utf-8")
html, count = re.subn(
    r'id="dl-notebook" href="[^"]*"',
    f'id="dl-notebook" href="data:application/x-ipynb+json;base64,{payload}"',
    html,
)
if count != 1:
    raise SystemExit(f"Expected one download button in {GUIDE.name}, found {count}.")
GUIDE.write_text(html, encoding="utf-8")
print(f"Embedded {NOTEBOOK.name} ({len(payload) // 1024} KB base64) into {GUIDE.name}")
