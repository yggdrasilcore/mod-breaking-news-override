# Content constraints

The body file configured by `BreakingNews.HtmlPath` is **not** arbitrary web
HTML. After being read it is wrapped in the module's Lua payload and handed to
the client's `ServerAlertText` frame, which only renders a small subset.

## Allowed

- A minimal document shell: `<html>`, `<body>`.
- Block tags: `<br/>`, `<h3>`, `<p>`.
- Plain links: `<a href="https://…">text</a>`.
- Inline color codes: `|cffRRGGBB … |r` (reset). Yggdrasil's accent is
  `|cffD4AF37`.

## Not allowed

- CSS (`style="…"`, `<style>`), `<div>`, `<span>`, `<img>`, `<script>`, JS
  handlers such as `onclick` — anything beyond the subset above is ignored or
  mangled by the client frame.
- Internal WoW hyperlinks, item/spell links and texture paths.

## Formatting conventions

- Separate blocks with `<br/>` on their own lines.
- Lead each line with a colored label, e.g. `|cffD4AF37Title:|r body text`.
- Keep it short; the payload is chunked (128 bytes) and shown on the character
  screen.

See `docs/samples/breakingnews.html` for a real, working file.
