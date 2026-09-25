#!/usr/bin/env python3
"""Render a glyph test card in THIS terminal.

Point: the only authority on whether a glyph renders is the terminal that
draws it. Charset inspection and PIL mocks both lied earlier in this project
(PIL has no font fallback; PowerShell MeasureString reported false misses).
So: print the candidates, look at them.

Each row carries a control glyph known to exist in every Nerd Font, so a
blank/box in the candidate column means the candidate is missing -- not that
the whole line failed.

Usage: python3 glyphcheck.py
"""

RESET = "\033[0m"
DIM = "\033[2m"
BOLD = "\033[1m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"

# (glyph, codepoint label, nerd-font name, what we'd use it for)
BRAND = [
    ("\uec82", "U+EC82", "cod-claude", "Anthropic"),
    ("\uec81", "U+EC81", "cod-openai", "OpenAI"),
    ("\uec1e", "U+EC1E", "cod-copilot", "Copilot"),
    ("\uec20", "U+EC20", "cod-robot", "local/generic"),
    ("\U000f06a9", "U+F06A9", "md-robot", "local alt"),
    ("\U000f09d1", "U+F09D1", "md-brain", "local alt"),
]

GEOMETRIC = [
    ("\u25c6", "U+25C6", "black diamond", "Anthropic"),
    ("\u25c7", "U+25C7", "white diamond", "OpenAI"),
    ("\u25c9", "U+25C9", "fisheye", "alt"),
    ("\u2302", "U+2302", "house", "local"),
    ("\u2b22", "U+2B22", "black hexagon", "(known missing)"),
    ("\u2b21", "U+2B21", "white hexagon", "(known missing)"),
]

ASCII = [
    ("[A]", "ascii", "bracket A", "Anthropic"),
    ("[G]", "ascii", "bracket G", "OpenAI"),
    ("[L]", "ascii", "bracket L", "local"),
]

BARS = [
    ("\u2588\u2591", "U+2588/2591", "blocks", "default bar"),
    ("\u2593\u2591", "U+2593/2591", "shade", ""),
    ("\u25a0\u25a1", "U+25A0/25A1", "squares", ""),
    ("\u25cf\u25cb", "U+25CF/25CB", "dots", ""),
    ("\u25ae\u25af", "U+25AE/25AF", "half", ""),
    ("#-", "ascii", "ascii", ""),
]

STATUS = [
    ("\u26d4", "U+26D4", "no-entry", "blocked"),
    ("\u26d4\ufe0f", "U+26D4 FE0F", "no-entry + VS16", "blocked (emoji form)"),
    ("\u26a1", "U+26A1", "high voltage", "burn ETA"),
    ("\u21bb", "U+21BB", "clockwise arrow", "resets-in"),
    ("\u2605", "U+2605", "black star", "reset credits"),
    ("\u22ef", "U+22EF", "midline ellipsis", "no data"),
    ("\u007e", "U+007E", "tilde", "stale"),
]


def section(title, rows, control="\u2588"):
    print(f"\n{BOLD}{CYAN}{title}{RESET}")
    print(f"  {DIM}control  candidate  codepoint     name              use{RESET}")
    for glyph, code, name, use in rows:
        print(
            f"  {GREEN}{control}{RESET}        "
            f"{BOLD}{glyph}{RESET}{' ' * max(1, 10 - len(glyph))}"
            f"{DIM}{code:<13}{RESET} {name:<17} {DIM}{use}{RESET}"
        )


def main():
    print(f"{BOLD}Herdr-usage glyph check{RESET}")
    print(f"{DIM}Left column is a control glyph (block) that every Nerd Font has.{RESET}")
    print(f"{DIM}If the control shows but the candidate is a box/blank -> that glyph is missing.{RESET}")

    section("1. BRAND icons (Nerd Font private-use area)", BRAND)
    section("2. GEOMETRIC fallback", GEOMETRIC)
    section("3. ASCII fallback (always works)", ASCII)
    section("4. BAR sets", BARS)
    section("5. STATUS glyphs", STATUS)

    print(f"\n{BOLD}{CYAN}6. Sidebar preview, brand set{RESET}")
    for line in (
        "  \uec82 Opus",
        "  5h \u2588\u2588\u2588\u2588\u2588\u2588\u2591\u2591\u2591\u2591  57%",
        "  wk \u2588\u2588\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591  17%",
        "  \uec81 Terra",
        "  5h \u2588\u2588\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591  13%",
        "  \uec20 Qwen   (local \u00b7 no quota)",
    ):
        print(f"  {line}")

    print(f"\n{BOLD}{CYAN}7. Sidebar preview, geometric set{RESET}")
    for line in (
        "  \u25c6 Opus",
        "  5h \u2588\u2588\u2588\u2588\u2588\u2588\u2591\u2591\u2591\u2591  57%",
        "  \u25c7 Terra",
        "  5h \u2588\u2588\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591  13%",
        "  \u2302 Qwen   (local \u00b7 no quota)",
    ):
        print(f"  {line}")

    print(f"\n{BOLD}{CYAN}8. Sidebar preview, ascii set{RESET}")
    for line in (
        "  [A] Opus",
        "  5h ########--  57%",
        "  [G] Terra",
        "  5h ##--------  13%",
        "  [L] Qwen   (local - no quota)",
    ):
        print(f"  {line}")

    print(f"\n{YELLOW}Which sections rendered? Report by number.{RESET}")


if __name__ == "__main__":
    main()
