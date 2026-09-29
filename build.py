#!/usr/bin/env python3
"""
Scripture Browser — build.

One list in, every lock out.  This reads `allowlist.txt` and `config.json` and
writes a complete, ready-to-install tree in `build/`:

    build/tree/          the exact files, in their exact places
    build/MANIFEST       every file, its mode, its hash, and where it goes
    build/dns/           the optional DNS filter (stage three)

The tree is laid out as it will be on the machine, so install.sh copies it
straight across and the manifest needs no translation: what build.py writes is
what the guard restores.

    python3 build.py                  build it
    python3 build.py --check URL      is this URL on the list?
    python3 build.py --list           the list, as the two browsers see it
    python3 build.py --dns            also write the dnsmasq filter
    python3 build.py --home-url URL   serve the start page from somewhere else
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "src")
BUILD = os.path.join(HERE, "build")
TREE = os.path.join(BUILD, "tree")

# A hostname, and nothing else: at least two labels, no leading or trailing
# hyphen, 63 characters a label.
DOMAIN = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))+$")

# The port serve.py uses when the start page is served rather than read off the
# disk (`install.sh --home-server`).  The filter works by host, so it does not
# care about the port.
LOOPBACK_PORT = 8787

# Things this list is not for.  A nudge, not a rule: build.py says so, and
# `--add` refuses, but editing allowlist.txt by hand always works.  Matched
# against the domain and every parent of it, so "wikipedia.org" catches
# "en.wikipedia.org" — which is how the general web creeps back in.
OFF_LIMITS = {
    # general reference: a browser that reaches everything is centred on nothing
    "wikipedia.org", "wiktionary.org", "britannica.com", "merriam-webster.com",
    "dictionary.com", "google.com", "bing.com", "duckduckgo.com", "yahoo.com",
    # places that host everything: a site allowed whole allows everything on it
    "gutenberg.org", "standardebooks.org", "archive.org", "youtube.com",
    "vimeo.com", "dailymotion.com", "twitch.tv", "spotify.com", "netflix.com",
    "amazon.com", "ebay.com", "imgur.com", "reddit.com", "quora.com", "medium.com",
    # social
    "facebook.com", "instagram.com", "x.com", "twitter.com", "tiktok.com",
    "snapchat.com", "discord.com", "telegram.org", "whatsapp.com", "linkedin.com",
    "pinterest.com", "tumblr.com",
}


def off_limits(domain):
    """The off-limits entry covering this domain, if there is one."""
    parts = domain.split(".")
    for i in range(len(parts) - 1):
        candidate = ".".join(parts[i:])
        if candidate in OFF_LIMITS:
            return candidate
    return None

# Where the project sits on the machine being installed to.  Set from
# config.json's install_prefix in main(), and used as the root of every path
# inside build/tree, so that the tree and the running system agree.
TREE_ROOT = "opt/scripture-browser"


# --------------------------------------------------------------------------
# inputs
# --------------------------------------------------------------------------

def read_config(path=None):
    with open(path or os.path.join(HERE, "config.json"), encoding="utf-8") as fh:
        return json.load(fh)


def read_allowlist(path=None):
    """allowlist.txt -> ([{"section":…, "domain":…, "title":…}, …], [problems])"""
    path = path or os.path.join(HERE, "allowlist.txt")
    entries, seen, section, problems = [], set(), None, []

    with open(path, encoding="utf-8") as fh:
        for number, raw in enumerate(fh, 1):
            line = raw.strip()
            if not line:
                continue
            if line.startswith("##"):
                section = line.lstrip("#").strip()
                continue
            if line.startswith("#"):
                continue

            domain, _, title = line.partition("#")
            domain = domain.strip().lower()
            title = title.strip() or None

            # Be forgiving about what gets pasted in, strict about what is kept.
            domain = re.sub(r"^[a-z]+://", "", domain)
            domain = domain.split("/")[0].split("?")[0]
            domain = domain.split("@")[-1]          # user@host
            domain = domain.split(":")[0]           # host:port
            domain = domain.rstrip(".")

            if not DOMAIN.match(domain):
                problems.append("%s:%d: not a bare domain, ignored: %r"
                                % (os.path.basename(path), number, domain))
                continue
            if domain in seen:
                problems.append("%s:%d: already listed, ignored: %s"
                                % (os.path.basename(path), number, domain))
                continue

            seen.add(domain)
            entries.append({"section": section, "domain": domain, "title": title})

    return entries, problems


def hosts_of(domain):
    """The domain, and its www twin.  A subdomain listed by hand stays as it is."""
    if domain.startswith("www."):
        return [domain]
    return [domain, "www." + domain]


# --------------------------------------------------------------------------
# the two browsers' languages
# --------------------------------------------------------------------------

def allowed_hosts(entries, cfg, home_url):
    """Every hostname the filter will let through.

    The filter is an allow-list proxy, so the host is the unit of permission and
    the scheme is not: what is on this list is reachable, and what is not is not
    reachable at all.  That is the whole of the rule."""
    hosts = set()
    for entry in entries:
        hosts.update(hosts_of(entry["domain"]))
    if home_url.startswith("http"):
        hosts.add(re.sub(r"^https?://", "", home_url).split("/")[0].split(":")[0])
        hosts.add("127.0.0.1")
        hosts.add("localhost")
    return sorted(hosts)


# --------------------------------------------------------------------------
# firefox policies
# --------------------------------------------------------------------------

def firefox_policies(entries, cfg, home_url):
    policies = {
        "DisableTelemetry": True,
        "DisableFirefoxStudies": True,
        "DisablePocket": True,
        "DisableFeedbackCommands": True,
        "DisableFormHistory": True,
        "DisableProfileImport": True,
        "DisableProfileRefresh": True,
        "DisableSetDesktopBackground": True,
        "DisableMasterPasswordCreation": True,
        "DisableFirefoxAccounts": True,
        "DisableDefaultBrowserAgent": True,
        "DontCheckDefaultBrowser": True,
        "NoDefaultBookmarks": True,
        "AppAutoUpdate": False,        # a clone is re-vendored, never self-updated
        "BackgroundAppUpdate": False,

        # Doors that do not exist.
        "DisablePrivateBrowsing": True,
        "DisableDeveloperTools": True,
        "BlockAboutConfig": True,
        "BlockAboutAddons": True,
        "BlockAboutProfiles": True,
        "BlockAboutSupport": True,
        "OverrideFirstRunPage": "",
        "OverridePostUpdatePage": "",

        # The start page.
        "Homepage": {"URL": home_url, "StartPage": "homepage", "Locked": True},
        "Bookmarks": [{"Title": "%s — start page" % cfg["name"],
                       "URL": home_url, "Placement": "toolbar"}],
        "DisplayBookmarksToolbar": "always",

        # Asking for things.
        "Permissions": {
            "Camera": {"BlockNewRequests": True, "Locked": True},
            "Microphone": {"BlockNewRequests": True, "Locked": True},
            "Location": {"BlockNewRequests": True, "Locked": True},
            "Notifications": {"BlockNewRequests": True, "Locked": True},
            "Autoplay": {"Default": "block-audio-video", "Locked": True},
        },
        "OfferToSaveLogins": False,
        "PasswordManagerEnabled": False,
        "AutofillAddressEnabled": False,
        "AutofillCreditCardEnabled": False,
        "EnableTrackingProtection": {"Value": True, "Locked": True,
                                     "Cryptomining": True, "Fingerprinting": False},
        "Cookies": {"Behavior": "reject-foreign", "Locked": True},
        "EnableDoNotTrack": True,
        "DNSOverHTTPS": {"Enabled": False, "Locked": True},
        "CaptivePortal": False,
        "NetworkPrediction": False,

        "UserMessaging": {"SkipOnboarding": True, "ExtensionRecommendations": False,
                          "FeatureRecommendations": False, "UrlbarInterventions": False,
                          "MoreFromMozilla": False, "WhatsNew": False},
    }

    search_name = cfg["search"]["name"]
    policies["SearchEngines"] = {
        "Add": [{"Name": search_name,
                 "URLTemplate": cfg["search"]["url"].replace("{q}", "{searchTerms}"),
                 "Method": "GET",
                 "Alias": cfg["search"].get("alias", "")}],
        "Default": search_name,
        "PreventInstalls": True,
        "Remove": ["Bing", "Amazon.com", "eBay", "Twitter", "Google",
                   "DuckDuckGo", "Wikipedia (en)"],
    }

    if cfg["filter"].get("enabled", True):
        # The filter, as a policy *and* as a locked pref.  Both are generated from
        # config.json, so the two cannot disagree about the port.
        policies["Proxy"] = {
            "Mode": "manual",
            "HTTPProxy": "%s:%d" % (cfg["filter"]["bind"], cfg["filter"]["port"]),
            "UseHTTPProxyForAllProtocols": True,
            "Passthrough": "localhost, 127.0.0.1",
            "Locked": True,
        }

    return {"policies": policies}


# --------------------------------------------------------------------------
# the start page, and the firefox autoconfig
# --------------------------------------------------------------------------

PLUMBING_HEADING = "Browser plumbing"


def render_home(cfg, entries, content, built, table):
    with open(os.path.join(SRC, "home", "index.html"), encoding="utf-8") as fh:
        page = fh.read()

    sections, order = {}, []
    for entry in entries:
        # The plumbing hosts are not places to be offered, so they stay off the
        # page and out of the bookmark bar.  Which ones they are is decided by
        # the heading in allowlist.txt, not by a second copy of their names here.
        if (entry["section"] or "").startswith(PLUMBING_HEADING):
            continue
        section = entry["section"] or "Sites"
        if section not in sections:
            sections[section] = []
            order.append(section)
        sections[section].append({"domain": entry["domain"],
                                  "title": entry["title"] or entry["domain"]})

    blocks = []
    for section in order:
        links = "\n".join(
            '          <a class="site" href="https://{domain}/" '
            'title="https://{domain}/"><span class="site-name">{title}</span>'
            '<span class="site-host">{domain}</span></a>'.format(**link)
            for link in sections[section])
        blocks.append(
            '      <section class="group">\n'
            '        <h2>{heading}</h2>\n'
            '        <div class="sites">\n{links}\n        </div>\n'
            '      </section>'.format(heading=section, links=links))

    # The table carries everything else a generated file may want to know —
    # the prefix, the filter's port, where the clone lives — and the content
    # keys are added to it, so the page and the policies cannot disagree.
    swap = dict(table)
    swap.update({
        "__NAME__": cfg["name"],
        "__VERSION__": cfg["version"],
        "__BUILT__": built,
        "__LINKS__": "\n".join(blocks) if cfg["home"].get("links", True) else "",
        "__VERSES__": json.dumps(content.get("verses", []), ensure_ascii=False),
        "__CATECHISM__": json.dumps(content.get("catechism", {}), ensure_ascii=False),
        "__CHALLENGES__": json.dumps(content.get("challenges", []), ensure_ascii=False),
        "__FEASTS__": json.dumps(content.get("feasts", {}), ensure_ascii=False),
        "__CONTENT_SOURCE__": content.get("source", ""),
        "__SEARCH__": json.dumps(cfg["search"], ensure_ascii=False),
    })
    for key, value in swap.items():
        page = page.replace(key, value)
    return page


def swaps(cfg, home_url, built, firefox_dir):
    """Everything a generated file might want to know about this machine.

    One table for the autoconfig and for the unit files, so a value that appears
    in both — the filter's port, the install prefix, where Firefox lives — cannot
    drift between them."""
    return {
        "__NAME__": cfg["name"],
        "__VERSION__": cfg["version"],
        "__BUILT__": built,
        "__HOME__": home_url,
        "__PREFIX__": cfg["install_prefix"].rstrip("/"),
        "__KEYWORD__": "true" if cfg.get("address_bar_search") else "false",
        "__FILTER_HOST__": cfg["filter"]["bind"],
        "__FILTER_PORT__": str(cfg["filter"]["port"]),
        "__FIREFOX_DIR__": firefox_dir or "/usr/lib/firefox",
    }


def render(path, table):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    for key, value in table.items():
        text = text.replace(key, value)
    return text


# --------------------------------------------------------------------------
# putting files where they belong
# --------------------------------------------------------------------------

def place(rel, content, mode=0o644):
    dest = os.path.join(TREE, rel)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if isinstance(content, bytes):
        with open(dest, "wb") as fh:
            fh.write(content)
    else:
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(content)
    os.chmod(dest, mode)


def copy_into(rel, source, mode=0o644):
    with open(source, "rb") as fh:
        place(rel, fh.read(), mode)


def copy_tree(rel_dest, source_dir):
    for root, dirs, files in os.walk(source_dir):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for name in sorted(files):
            full = os.path.join(root, name)
            inner = os.path.relpath(full, source_dir)
            mode = 0o755 if os.access(full, os.X_OK) else 0o644
            copy_into(os.path.join(rel_dest, inner), full, mode)


def sealed_rules(rel):
    """(mode, freeze?) for a path inside the tree.

    Nothing under usr/lib/firefox is made immutable: that directory belongs to
    the Firefox package, and freezing files there would break the one thing
    that must never break — a security update."""
    if rel.startswith("usr/lib/firefox/"):
        # Nothing should ever land here again, but if it did: never freeze a file
        # in a directory the Firefox package owns.
        return 0o644, False
    if rel.startswith(TREE_ROOT + "/lib/firefox/"):
        # Ours, inside the clone.  Safe to freeze, and the point of freezing: these
        # are new files, not the ones vendor.sh hard-linked from the machine's copy.
        return 0o644, True
    if rel.startswith("usr/local/lib/scripture-browser-guard/"):
        return 0o700, True
    if rel.startswith("usr/local/bin/") or rel.startswith("usr/lib/systemd/system/"):
        return 0o755, True
    if rel.startswith(TREE_ROOT + "/build/"):
        return 0o644, False
    if rel.endswith(".sh") or rel == TREE_ROOT + "/build.py":
        return 0o755, True
    return 0o644, True


def write_inventory():
    """Every file in the tree: where it sits, what mode it wants, and its hash.

    install.sh reads this and writes the guard's manifest, which adds the two
    files that go into the Firefox directory (found at install time, not now)."""
    rows = []
    for root, dirs, files in os.walk(TREE):
        dirs.sort()
        for name in sorted(files):
            full = os.path.join(root, name)
            rel = os.path.relpath(full, TREE)
            mode, freeze = sealed_rules(rel)
            with open(full, "rb") as fh:
                digest = hashlib.sha256(fh.read()).hexdigest()
            rows.append("%s\t%o\t%d\t%s\t%s"
                        % (rel, mode, 1 if freeze else 0, digest, rel))

    header = (
        "# Scripture Browser — what the guard is responsible for.\n"
        "# destination<TAB>mode<TAB>freeze<TAB>sha256<TAB>path-in-sealed\n"
        "# Every destination is also its path in the sealed copy, because the tree\n"
        "# in build/ is laid out exactly as it will be on the machine.  freeze=0\n"
        "# means the file belongs to somebody else and must stay writable.\n"
    )
    with open(os.path.join(BUILD, "MANIFEST"), "w", encoding="utf-8") as fh:
        fh.write(header + "\n".join(rows) + "\n")
    return rows


def write_hosts(cfg, hosts):
    """The same list, flattened to hostnames, for the filter to enforce."""
    body = [
        "# Scripture Browser — the hostnames the filter will let through.",
        "# Generated from allowlist.txt by build.py.  The filter re-reads this file",
        "# when it changes, so adding a site does not need a restart.",
        "",
    ] + list(hosts)
    place(TREE_ROOT + "/filter/hosts.txt", "\n".join(body) + "\n")


def patched_application_ini(cfg, directory):
    """The clone's application.ini: the machine's own, with two lines changed.

    `--version` will still say Firefox — that string is compiled in — but the name
    in here is what the profile's compatibility record, the "Firefox is already
    running" dialogs and the window class the desktop groups by are made from."""
    path = os.path.join(directory, "application.ini")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    text = re.sub(r"(?m)^Name=.*$", "Name=%s" % cfg["name"], text, count=1)
    text = re.sub(r"(?m)^RemotingName=.*$", "RemotingName=scripture-browser",
                  text, count=1)
    return text


FIREFOX_DIRS = ("/usr/lib/firefox", "/usr/lib/firefox-esr", "/usr/lib64/firefox",
                "/opt/firefox", "/opt/firefox-esr", "/usr/local/lib/firefox")


def find_firefox_dir():
    """Where this machine's Firefox actually lives.

    A distribution package, a Mozilla tarball and a Snap all differ, so look for
    the directory that has both Firefox's application.ini and its pref directory."""
    for directory in FIREFOX_DIRS:
        if (os.path.isfile(os.path.join(directory, "application.ini"))
                and os.path.isdir(os.path.join(directory, "defaults"))):
            return directory
    binary = shutil.which("firefox")
    if binary:
        directory = os.path.dirname(os.path.realpath(binary))
        if os.path.isfile(os.path.join(directory, "application.ini")):
            return directory
    return None


# --------------------------------------------------------------------------
# the optional DNS filter (stage two — never installed by install.sh)
# --------------------------------------------------------------------------

def write_dns(cfg, entries):
    upstream = cfg["dns"].get("upstream", ["1.1.1.1", "9.9.9.9"])
    listen = cfg["dns"].get("listen", "127.0.0.1")
    lines = [
        "# Scripture Browser — the allowlist as a DNS filter.",
        "#",
        "# Generated from allowlist.txt.  Read README.md, section \"Stage two\",",
        "# before installing this: it makes the whole machine resolve only what is",
        "# on the list, which is a bigger claim than a browser policy makes.",
        "",
        "no-resolv",
        "no-hosts",
        "domain-needed",
        "bogus-priv",
        "cache-size=1000",
        "local-service",
        "listen-address=%s" % listen,
        "",
        "# Log every lookup, so a blocked site is visible rather than mysterious.",
        "log-queries",
        "log-facility=/var/log/scripture-browser-dns.log",
        "",
        "# --- everything on the list resolves normally -----------------------",
    ]
    for entry in entries:
        for host in hosts_of(entry["domain"]):
            lines.append("server=/%s/%s" % (host, upstream[0]))
    lines += [
        "",
        "# --- and everything else resolves to nothing ------------------------",
        "address=/#/0.0.0.0",
        "",
    ]
    dest = os.path.join(BUILD, "dns", "scripture-browser.conf")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return dest


# --------------------------------------------------------------------------
# asking questions about the list
# --------------------------------------------------------------------------

def check(url, cfg, entries, home_url):
    """Would the filter let this through?  The host is the unit of permission."""
    rest = re.sub(r"^[a-z]+://", "", url.strip())
    host = rest.split("/")[0].split("?")[0].split("@")[-1].lower()
    host = host.split(":")[0].strip("[]").rstrip(".")
    ok = host in allowed_hosts(entries, cfg, home_url)
    print("%s  %s  (%s)" % ("ALLOW" if ok else "BLOCK", url,
                            "on the list" if ok else "not on the list"))
    return 0 if ok else 1


def main():
    parser = argparse.ArgumentParser(description="Build the Scripture Browser tree.")
    parser.add_argument("--check", metavar="URL")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--dns", action="store_true")
    parser.add_argument("--home-url")
    parser.add_argument("--check-add", metavar="DOMAIN",
                        help="say whether a site may be added to the list")
    parser.add_argument("--print-firefox-dir", action="store_true",
                        help="print where this machine's Firefox lives, and exit")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    cfg = read_config()
    entries, problems = read_allowlist()

    global TREE_ROOT
    prefix = cfg["install_prefix"].rstrip("/")
    TREE_ROOT = prefix.strip("/")
    home_url = (args.home_url or cfg["home"].get("url")
                or "file://%s/home/index.html" % prefix)

    firefox_dir = find_firefox_dir()

    if args.check_add:
        domain = args.check_add.strip().lower().lstrip(".")
        hit = off_limits(domain)
        if hit:
            print("no: %s is on the off-limits list (%s)" % (domain, hit))
            print("    This browser is for Scripture and Christian study — not general")
            print("    reference, video, social or shopping.  If you mean it anyway,")
            print("    put the line in %s/allowlist.txt by hand." % cfg["install_prefix"])
            return 1
        print("ok: %s is not on the off-limits list" % domain)
        return 0

    if args.print_firefox_dir:
        print(firefox_dir or "")
        return 0

    if args.check:
        return check(args.check, cfg, entries, home_url)

    if args.list:
        for entry in entries:
            print("%-42s %s" % (entry["domain"], entry["title"] or ""))
        return 0

    for entry in entries:
        hit = off_limits(entry["domain"])
        if hit:
            problems.append("allowlist.txt: %s is on the off-limits list (%s) — this list "
                            "is for Scripture and Christian study, not the general web"
                            % (entry["domain"], hit))

    hosts = allowed_hosts(entries, cfg, home_url)

    # A search engine on a site that is not on the list is a bug worth shouting
    # about: the address bar would offer something the browser then refuses.
    search_host = re.sub(r"^https?://", "", cfg["search"]["url"]).split("/")[0]
    if search_host not in hosts:
        problems.append("config.json: the search engine lives on %s, which is not in "
                        "allowlist.txt — add it, or change the engine" % search_host)

    built = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    table = swaps(cfg, home_url, built, firefox_dir)

    # The start page's content: the verse, the catechism, the challenges and the
    # church's year, imported from StudyTools by tools/import-studytools.py.
    with open(os.path.join(SRC, "home", "data.json"), encoding="utf-8") as fh:
        content = json.load(fh)

    if os.path.isdir(TREE):
        shutil.rmtree(TREE)

    # --- the clone's own four files ---------------------------------------
    # These sit inside the clone, beside the files vendor.sh copied from the
    # machine's Firefox, and they are the reason a clone is worth having: this
    # browser's policies live in its own directory, and the machine's Firefox
    # never sees any of it.
    if cfg["firefox"].get("clone", True):
        place(TREE_ROOT + "/lib/firefox/scripture-browser.cfg",
              render(os.path.join(SRC, "firefox", "scripture-browser.cfg"), table))
        copy_into(TREE_ROOT + "/lib/firefox/defaults/pref/autoconfig.js",
                  os.path.join(SRC, "firefox", "autoconfig.js"))
        place(TREE_ROOT + "/lib/firefox/distribution/policies.json",
              json.dumps(firefox_policies(entries, cfg, home_url), indent=2) + "\n")
        if firefox_dir is None:
            problems.append("no Firefox on this machine to clone — install Firefox, "
                            "then run install.sh again")
        else:
            place(TREE_ROOT + "/lib/firefox/application.ini",
                  patched_application_ini(cfg, firefox_dir))

    # --- the filter --------------------------------------------------------
    if cfg["filter"].get("enabled", True):
        write_hosts(cfg, hosts)
        copy_into(TREE_ROOT + "/filter/filter.py",
                  os.path.join(SRC, "filter", "filter.py"), 0o755)

    # The project, so that the machine can rebuild itself without this checkout.
    copy_into(TREE_ROOT + "/build.py", os.path.join(HERE, "build.py"), 0o755)
    copy_into(TREE_ROOT + "/config.json", os.path.join(HERE, "config.json"))
    copy_into(TREE_ROOT + "/allowlist.txt", os.path.join(HERE, "allowlist.txt"))
    copy_into(TREE_ROOT + "/install.sh", os.path.join(HERE, "install.sh"), 0o755)
    copy_into(TREE_ROOT + "/vendor.sh", os.path.join(HERE, "vendor.sh"), 0o755)
    copy_into(TREE_ROOT + "/README.md", os.path.join(HERE, "README.md"))
    copy_tree(TREE_ROOT + "/src", SRC)
    copy_tree(TREE_ROOT + "/tools", os.path.join(HERE, "tools"))

    place(TREE_ROOT + "/home/index.html",
          render_home(cfg, entries, content, built, table))
    place(TREE_ROOT + "/VERSION",
          "%s %s\n%s\n" % (cfg["name"], cfg["version"], built))
    copy_into(TREE_ROOT + "/icon.svg",
              os.path.join(SRC, "desktop", "icon.svg"))

    copy_into("usr/local/bin/scripture-browser",
              os.path.join(SRC, "bin", "scripture-browser"), 0o755)
    place("usr/share/applications/scripture-browser.desktop",
          render(os.path.join(SRC, "desktop", "scripture-browser.desktop"), table))
    copy_into("usr/local/lib/scripture-browser-guard/guard.sh",
              os.path.join(SRC, "guard", "guard.sh"), 0o700)
    # serve.py lives in the prefix, not in the guard's directory: the guard keeps
    # things only root may read, and the home server runs as a sandboxed user.
    copy_into(TREE_ROOT + "/home/serve.py",
              os.path.join(SRC, "home", "serve.py"), 0o755)
    for unit in ("guard.service", "guard.path", "guard.timer",
                 "sync.service", "sync.path", "sync.timer",
                 "filter.service", "home.service"):
        name = "scripture-browser-%s" % unit
        for where in ("guard", "filter", "home"):
            source = os.path.join(SRC, where, name)
            if os.path.exists(source):
                # Units go through the same substitution as the autoconfig, so a
                # port or a prefix lives in config.json and nowhere else.
                place("usr/lib/systemd/system/%s" % name, render(source, table))
                break
        else:
            problems.append("no unit file for %s" % name)

    rows = write_inventory()

    if args.dns:
        write_dns(cfg, entries)

    if not args.quiet:
        print("%s %s — built %s" % (cfg["name"], cfg["version"], built))
        print("  %d sites on the list, %d hostnames allowed"
              % (len(entries), len(hosts)))
        print("  start page   %s" % home_url)
        print("  cloning      %s" % (firefox_dir or "— no Firefox found on this machine"))
        print("  filter       %s:%d, %d hostnames"
              % (cfg["filter"]["bind"], cfg["filter"]["port"], len(hosts)))
        print("  install tree %s  (%d files)" % (TREE, len(rows)))
        print("  manifest     %s" % os.path.join(BUILD, "MANIFEST"))
        if args.dns:
            print("  dns filter   %s"
                  % os.path.join(BUILD, "dns", "scripture-browser.conf"))
        for problem in problems:
            print("  ! %s" % problem, file=sys.stderr)
        print()
        print("  next:  sudo ./install.sh           (first install)")
        print("         sudo ./install.sh --update  (after editing allowlist.txt)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
