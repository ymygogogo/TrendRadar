"""Shared visual tokens for public news, briefings and the private console."""

THEME = """
:root {
  color-scheme:dark;
  --bg:#0c1320; --header:#0f1928; --surface:#131f30; --surface-raised:#1a293c;
  --edge:#2b4059; --edge-soft:#23344a; --ink:#eef4fc; --muted:#aabbd1;
  --accent:#79def0; --accent-quiet:#183848; --accent-ink:#0b2530;
  --good:#90e6b7; --warn:#ffd08d;
  --font:system-ui,-apple-system,"Noto Sans SC","Microsoft YaHei",sans-serif;
  --radius:.7rem;
}
* { box-sizing:border-box; }
:focus-visible { outline:2px solid var(--accent); outline-offset:3px; }
.brand {
  display:inline-flex; align-items:baseline; gap:.45rem; color:var(--ink); text-decoration:none;
  white-space:nowrap; font-weight:780; letter-spacing:-.02em;
}
.brand small { font-weight:540; letter-spacing:0; color:var(--muted); font-size:.8rem; }
"""
