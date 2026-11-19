"""Shared visual tokens for public news, briefings and the private console."""

THEME = """
:root {
  color-scheme:dark;
  --bg:#08111d; --header:#0b1826; --surface:#101f30; --surface-raised:#172c40;
  --edge:#29475f; --edge-soft:#203a50; --ink:#eef4fc; --muted:#aabbd1;
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
.brand-mark {
  display:inline-block; align-self:center; width:.95rem; height:.95rem;
  border:1px solid var(--accent); border-radius:50%; position:relative;
  box-shadow:0 0 9px #70e4fa4d; margin-inline-end:.15rem;
}
.brand-mark::after { content:""; position:absolute; inset:32%; border-radius:50%; background:var(--accent); }
.brand small { font-weight:540; letter-spacing:0; color:var(--muted); font-size:.8rem; }
"""
