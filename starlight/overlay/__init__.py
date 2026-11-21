"""The overlay: an always-on-top, click-through layer that shows Starlight's orb and points
at things on the screen (``docs/plan-overlay.md``).

It runs as its own process (``python -m starlight.overlay``) and talks to the core in JSON
lines over stdin and stdout (``protocol``). ``geometry`` works out where things go on the
monitors. Only the drawing code in this package imports Qt; ``protocol`` and ``geometry``
are pure Python, so the core and its tests can use them without Qt installed.
"""
