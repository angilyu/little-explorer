"""Making new picture books in the cloud (owner-only for now; a parent page comes later).

- :mod:`.config`: settings from the environment (OpenAI key, R2 bucket, limits).
- :mod:`.store`: jobs, books and their R2 files in the database, with leases.
- :mod:`.runner`: the one in-process worker (text → photos → narration → publish).
- :mod:`.writer`, :mod:`.photos`, :mod:`.narration`, :mod:`.moderation`: the steps.
- :mod:`.manifest`: made books added to the content manifest.
- :mod:`.service`: what the web service wires together.
"""
