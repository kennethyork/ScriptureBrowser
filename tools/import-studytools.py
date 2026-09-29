#!/usr/bin/env python3
"""
Scripture Browser — import the start page's content from StudyTools.

The start page carries the verse for the day, the day's catechism question, a
challenge, and the church's year.  All of it already exists in
ChristInBackground's data.json, which is itself built from studytools.cc — so
this copies the parts the page needs and nothing else, rather than letting a
second, hand-typed copy of Scripture drift away from the first.

    python3 tools/import-studytools.py                     # find it next door
    python3 tools/import-studytools.py --from PATH         # or say where
    python3 tools/import-studytools.py --check             # would it change?

Writes src/home/data.json.  Run it again after the source is rebuilt.
"""

import argparse
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
OUT = os.path.join(PROJECT, "src", "home", "data.json")
DEFAULT_SOURCES = (
    os.path.join(os.path.dirname(PROJECT), "ChristInBackground", "src", "data.json"),
)


def find_source(explicit=None):
    if explicit:
        return explicit
    for candidate in DEFAULT_SOURCES:
        if os.path.isfile(candidate):
            return candidate
    return None


def as_qa(items):
    """Catechism entries, whatever shape they arrive in."""
    out = []
    for item in items:
        if isinstance(item, dict):
            question = item.get("q") or item.get("question") or item.get("Q")
            answer = item.get("a") or item.get("answer") or item.get("A")
            if question and answer:
                out.append({"q": str(question).strip(), "a": str(answer).strip()})
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            out.append({"q": str(item[0]).strip(), "a": str(item[1]).strip()})
    return out


def pick_list(mapping):
    """The list inside {title: …, <something>: [...]}, whatever it is called."""
    for key, value in mapping.items():
        if isinstance(value, list):
            return key, value
    return None, []


def main():
    parser = argparse.ArgumentParser(description="Import StudyTools content for the start page.")
    parser.add_argument("--from", dest="source", help="path to ChristInBackground's data.json")
    parser.add_argument("--check", action="store_true",
                        help="report whether the imported content would change, and exit")
    args = parser.parse_args()

    source = find_source(args.source)
    if not source or not os.path.isfile(source):
        print("no source found.  Looked for:", file=sys.stderr)
        for candidate in DEFAULT_SOURCES:
            print("   ", candidate, file=sys.stderr)
        print("Pass one with --from PATH (ChristInBackground/src/data.json).", file=sys.stderr)
        return 1

    with open(source, encoding="utf-8") as fh:
        raw = json.load(fh)

    verses = [{"ref": v["ref"], "text": v["text"], "chapter": v.get("chapter"),
               "testament": v.get("testament")}
              for v in raw.get("verses", []) if v.get("ref") and v.get("text")]

    catechism = {}
    for key, cat in (raw.get("catechism") or {}).items():
        if not isinstance(cat, dict):
            continue
        list_key, items = pick_list(cat)
        qa = as_qa(items)
        if qa:
            catechism[key] = {"title": cat.get("title") or key.title(), "items": qa}
            print("  %-12s %3d questions (from %r)" % (key, len(qa), list_key))

    challenges = []
    for item in raw.get("challenges", []):
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            challenges.append({"title": str(item[0]).strip(), "text": str(item[1]).strip()})

    feasts = raw.get("feasts") or {}
    if not verses or not catechism:
        print("the source has no verses or no catechism — is that the right file?", file=sys.stderr)
        return 1

    data = {
        "source": "studytools.cc, imported from ChristInBackground/src/data.json",
        "imported": datetime.date.today().isoformat(),
        "verses": verses,
        "catechism": catechism,
        "challenges": challenges,
        "feasts": {"fixed": feasts.get("fixed", {}), "movable": feasts.get("movable", [])},
    }
    text = json.dumps(data, ensure_ascii=False, indent=1, sort_keys=False) + "\n"

    before = None
    if os.path.isfile(OUT):
        with open(OUT, encoding="utf-8") as fh:
            before = fh.read()

    print("  source      %s" % source)
    print("  verses      %d" % len(verses))
    print("  challenges  %d" % len(challenges))
    print("  feasts      %d fixed, %d movable"
          % (len(data["feasts"]["fixed"]), len(data["feasts"]["movable"])))

    if args.check:
        print("  data.json   %s" % ("up to date" if before == text else "WOULD CHANGE"))
        return 0 if before == text else 1

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(text)
    print("  written     %s (%d KB)" % (OUT, len(text) // 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
