# Scripture Browser

A browser that opens the sites on its list, and nothing else — and it is a
**clone** of Firefox rather than a modification of it.

The clone is one copy of Firefox in its own directory, with its own policies, its
own locked preferences, its own profile and its own name. The machine's Firefox
is not touched: not its directory, not `/etc/firefox`, not its profile, not its
bookmarks. You keep a normal browser; this is a separate application with a lock
on it, and it is the locked one that goes on the child's account, the study
laptop, or your own.

---

## Why a clone

Because the two things you want are in tension. You want the browser locked, and
you want to keep using Firefox. A policy in `/etc/firefox/policies/` cannot tell
those apart — it applies to every Firefox on the machine, including the one you
are working in.

An application can carry its own policy in its own directory. That is what this
does, and it is what makes the lock both strict and *local*:

* the clone's policies live in `lib/firefox/distribution/policies.json`
* its locked preferences live in `lib/firefox/defaults/pref/autoconfig.js` and
  `lib/firefox/scripture-browser.cfg`
* its name and window class come from its own `application.ini`

Verified rather than assumed: `about:policies` inside the clone lists the active
policies, and on the machine's own Firefox — same command, same minute — the
answer is *"The Enterprise Policies service is inactive."*

**The one thing a clone cannot do is update itself.** That is deliberate: the
updater would write into files the clone shares with the machine's Firefox, and
the machine's Firefox is the one thing that must keep working. So updates arrive
the other way round — the package manager patches Firefox as usual, and
`scripture-browser-sync` notices and re-vendors the clone from the new copy,
immediately when the package changes and at least every half hour. The copy is
made with hard links (`cp -al`) where the filesystem allows, so it costs no disk
and no time: the clone has its own directory tree and shares the bytes of
`libxul.so` with the copy apt keeps.

---

## How the lock works

The allow list is enforced by a **filter** — a small allow-list proxy on
loopback that the browser is locked to. Every page it opens is asked about there
first, and a host that is not on the list never gets a connection. It fails
closed: if the filter is not running, the browser reaches nothing at all.

| Layer | What it does | How it was checked |
|---|---|---|
| `lib/firefox/scripture-browser.cfg` | 103 locked preferences, including `network.proxy.*` and `failover_direct=false`. A `user.js` in the profile cannot override a locked pref. | A profile carrying `user_pref("network.proxy.type", 0)` still went through the filter. |
| `lib/firefox/distribution/policies.json` | 40 policies: private browsing, devtools, `about:config`, `about:profiles`, autofill, telemetry, the proxy. | `about:policies` in the clone lists them; the machine's Firefox reports none. |
| `filter/filter.py` | The allow list itself. CONNECT tunnels for https are refused by *hostname* — nothing is decrypted, no certificate is impersonated — and plain HTTP requests are forwarded or answered with a page that explains. | On the list loaded in 1s; off the list refused. See below for the log. |
| `chattr +i`, on our files only | Even root must undo the flag before editing or deleting. The guard refuses to freeze any file with more than one hard link, because those are shared with the machine's Firefox and freezing one would break the next security update. | Both behaviours exercised. |
| The guard | A timer every two minutes, and a path unit that fires the moment a file is touched. Anything missing or altered is put back. | 46 files deleted or edited, then restored byte for byte — including the clone's own `application.ini`. |

**And the honest limit: on this machine you have `sudo`.** Anything root can do,
root can undo — `chattr -i`, `apt remove firefox`, a live USB, a new disk. No
program can prevent that, and one that claims to is lying. What makes this *not
easily* undone is layers, and the escape hatch below is deliberate: a lock you
cannot open is a lock that eventually costs you a weekend.

**The real lock is an account without `sudo`.** If this is for a child, install
it from your account and give them theirs with no admin rights. Then "cannot
easily be uninstalled" stops being a claim about cleverness and becomes a fact
about permissions — and *this* design is the right one for that case, because
their locked browser and your normal browser are different applications.

---

## What the testing found, and why it matters

Three findings changed the design, and one of them would have shipped a browser
that could not open a single page.

**`WebsiteFilter` cannot express an allow list.** The documented-looking idiom
`"Block": ["<all_urls>"], "Allow": ["https://example.com/"]` blocks
*everything*: the block list wins whenever it matches, and `<all_urls>` always
matches. It blocked every allow entry I tried — including `Allow: ["<all_urls>"]`
— and it blocked the browser's own `file://` start page. The way I found it: a
navigation that should have been allowed never reached the local server, so the
request had been refused inside the browser before any network call was made.
v1 of this project was built on that idiom. It was replaced with the filter.

**Firefox's remote-settings host is `.com`, not `.org`.** The filter's log showed
`DENY CONNECT firefox.settings.services.mozilla.com` — the host Firefox actually
uses for the blocklists behind Safe Browsing. The list had `.org`, so Firefox's
own protection data was being refused. That is now `.com`, and the log is how you
find the next one: if something the browser needs is missing, the filter names it.

**A clone shares inodes with the machine's Firefox.** Under hard links, a
`chmod` or a `chattr` on a file in the clone is a change to the system's copy:
freezing one would make the next `apt upgrade firefox` fail to unpack. The guard
therefore refuses to freeze anything with a link count above one, and vendor.sh
never edits a file it hard-linked — `application.ini` is generated fresh by
build.py from the machine's own, and *replaced* rather than written through.

---

## Install

```bash
cd ScriptureBrowser
sudo ./install.sh
scripture-browser --install-user     # make it this account's browser
```

Requires Firefox and Python 3. Nothing is fetched: no packages, no extensions,
no fonts, no accounts.

## Day to day

```bash
scripture-browser --list                 # what is on the list
scripture-browser --check example.com    # would the filter let this through?
scripture-browser --add hymns.example.com "The Hymnal"
scripture-browser --remove example.com
scripture-browser --status               # guard, filter, clone, sync
scripture-browser --update               # after editing allowlist.txt by hand
scripture-browser --sync                 # re-vendor now, after a Firefox update
scripture-browser --kiosk                # full screen, no window furniture
```

`--add` rebuilds the list, re-seals it, and the filter picks the new file up on
its own — no restart, and it applies to the next page load.

## When a site is refused

Off the list, the browser shows Firefox's own connection error — the filter
refuses the connection rather than answering it, and does not pretend to be the
site. Over plain `http://` it can answer, so it does, with a short page naming
the host and the command to add it. Every refusal is logged once a minute per
host, so `journalctl -u scripture-browser-filter` is a record of what was asked
for and refused.

## The way out

```bash
sudo scripture-browser --release
```

That unfreezes everything, stops the units, and removes every file the installer
placed — the list of what to remove is the manifest the guard sealed, not a
second list that can drift from it. The machine's own Firefox was never touched,
so it simply carries on. Two things are deliberately left behind: the browser's
own profile in `~/.local/share/scripture-browser/`, and whichever default-browser
setting `--install-user` changed (put it back with
`xdg-settings set default-web-browser firefox.desktop`).

If you ever lock yourself out — worth reading twice — boot a live USB, mount the
root filesystem, and
`chattr -R -i /mnt/usr/local/lib/scripture-browser-guard /mnt/opt/scripture-browser`
before removing anything. Keep your root password, and keep this repository
somewhere other than the machine.

---

## The start page

`opt/scripture-browser/home/index.html`, opened from local disk. It makes no
network requests at all: no fonts, no scripts, no analytics, no verse lookup. It
is a page about God first and a page of links second.

- **The verse for today** — 120 of them, chosen by the date, not by an algorithm,
  with the words blanking out for memorising (`m`) and a count of what you have
  learnt, kept on this machine.
- **The question for today** — the Westminster Shorter and the Heidelberg
  catechisms, 235 questions between them, one a day, with the answer behind a
  button (`a`). Which catechism, and which question, is the date's business.
- **Asked of you today** — one of 35 challenges, on the same day-arithmetic, and
  the day's links: readings, morning prayer, the Daily Office, the Hours.
- **The day in the church's year** — up in the corner beside the clock, from the
  fixed feasts and the movable ones measured from Easter.
- `n` another verse, `m` memorise, `q` another question, `a` the answer,
  `/` search, and `I have it ✓` marks a verse learnt.
- The search box goes to whichever engine `config.json` names. By default that is
  BibleGateway's quick search in the KJV, so the search box cannot leave the list.
- `allowlist.txt` headings become the groups on the page, and the text after `#`
  becomes each site's name. Lines under `## Browser plumbing` are left off the
  page: they are not places to visit.

The verse, the catechism, the challenges and the feasts are **imported from
StudyTools**, which is where they were written and where they are kept:

```bash
python3 tools/import-studytools.py          # from ChristInBackground/src/data.json
python3 tools/import-studytools.py --check  # has anything changed?
```

Nothing is retyped, so the two projects cannot drift apart. No translation is
claimed over the verse text on the page — it is StudyTools' wording, and the
footer credits it.

The page is read straight off the disk, which is how it works on this machine.  If
a Firefox build ever refuses to open a local file, the same page can be served on
loopback instead:

```bash
sudo scripture-browser --serve on      # 127.0.0.1:8787, systemd-sandboxed
```

`--serve off` puts it back.

---

## What is on the list, and what is not

The list is the whole of the judgement, so it is worth saying what standard it
applies. A site belongs if it serves the reading of Scripture, the study of it,
the worship of God, or the ordinary Christian life — and it is left off for one
of two reasons:

* **It is general-purpose.** Encyclopaedias, dictionaries, news, search engines.
  Useful, and the opposite of a room built for one thing. A browser that can
  reach everything is not centred on anything.
* **It hosts everything.** Free libraries, video sites, social media, shops.
  A site is allowed *whole* here — every page of it — so a site that hosts
  everything is a site that allows everything. That is why Project Gutenberg and
  the Internet Archive are not on it, and why the six devotional libraries that
  replaced them are: CCEL, Chapel Library, Monergism, Grace Gems, Hymnary,
  Hymnal.net.

`build.py` holds that as an off-limits list, matched against a domain and its
parents, so listing `wikipedia.org` catches `en.wikipedia.org`. It warns when an
entry looks like the general web, and `scripture-browser --add` refuses such a
host outright:

```console
$ scripture-browser --check example.com
ok: example.com is not on the off-limits list
$ scripture-browser --check youtube.com
no: youtube.com is on the off-limits list (youtube.com)
    This browser is for Scripture and Christian study — not general
    reference, video, social or shopping.  If you mean it anyway,
    put the line in /opt/scripture-browser/allowlist.txt by hand.
```

It is a nudge, not a rule. The list is yours, editing it by hand always works,
and the build only warns.

Everything lives in `config.json`, and the generated files follow it, so nothing
can disagree with anything else:

| Key | What it decides |
|---|---|
| `filter.port`, `filter.bind` | where the filter listens — the locked prefs and the policy are generated from it |
| `filter.enabled` | false removes the proxy entirely, which is only sensible on a machine you are not trying to lock |
| `firefox.clone`, `firefox.autoconfig`, `firefox.sync` | vendor a clone at all; lock the preferences; re-vendor after a Firefox update |
| `guard.enabled`, `guard.immutable`, `guard.interval_minutes` | the guardian, and whether it uses `chattr +i` |
| `search.url`, `search.name` | the one search engine the browser will use |
| `address_bar_search` | false means a phrase typed in the address bar is never turned into a web search |
| `home.*`, `install_prefix` | the start page and where it all lives |
| `dns.*` | the optional filter for the whole machine — stage three |

## Files

| Path | What it is |
|---|---|
| `allowlist.txt` | the list — the only file you normally edit |
| `config.json` | the settings above |
| `src/home/data.json` | the verse, catechism, challenges and church's year the start page carries |
| `tools/import-studytools.py` | refreshes that from ChristInBackground's `data.json` |
| `build/tree/` | the built files, in their final layout |
| `build/MANIFEST` | every file, its mode, its hash, and where it goes |
| `<prefix>/lib/firefox/` | **the clone** — Firefox's own files, plus four of ours |
| `<prefix>/filter/filter.py`, `filter/hosts.txt` | the filter, and the list flattened for it |
| `<prefix>/home/index.html` | the start page |
| `/usr/local/bin/scripture-browser` | the launcher |
| `/usr/local/lib/scripture-browser-guard/` | the guard, its manifest, and the sealed copy |
| `/usr/lib/systemd/system/scripture-browser-*` | filter, guard, sync, home server |

## Three things that will surprise you

**A site can be allowed and still look broken.** The filter allows a *host*, not
a page, so a site that pulls its fonts or its scripts from somewhere else will
come up plain. That is the filter working. Add the host (open the page's source
and look for `src=` or `href="https://…"`), or leave it plain.

**Subdomains have to be listed.** A bare `example.com` allows `example.com` and
`www.example.com`. It does not allow `my.example.com` — write that line as well.
The filter log will tell you the exact hostname it refused, which is the fastest
way to find out what a site actually needs.

**A second autoconfig could outrank ours, and the guard will say so.** Firefox
reads every `*.js` in its own `defaults/pref` directory in alphabetical order,
and the last to set `general.config.filename` wins. A file dropped there as root
could point Firefox at an autoconfig with no locks. The guard will not delete it —
that directory belongs to Firefox, and pruning it would break an update — but it
names the file, every two minutes, until it is looked at. An account without
`sudo` cannot write there in the first place.

## Stage three: the same list as a DNS filter

A browser policy stops browsers; the filter stops this browser. A DNS filter
stops everything else on the machine — the game with a chat window in it, the
mail client, `curl`. It is not installed by `install.sh`, on purpose: it changes
how the whole machine resolves, which is a much bigger claim than a browser lock,
and a mistake there is felt everywhere.

```bash
python3 build.py --dns
sudo install -m 644 build/dns/scripture-browser.conf /etc/dnsmasq.d/scripture-browser.conf
```

Then point the machine's resolver at that dnsmasq and make DNS-over-HTTPS
impossible to fall back on: `DNSStubListener=no` in `/etc/systemd/resolved.conf`,
dnsmasq on `127.0.0.1:53`, `nameserver 127.0.0.1` in `/etc/resolv.conf` with
`chattr +i`, and an `nftables` rule redirecting all outgoing port 53 to loopback.
Both browsers already refuse DoH here (`network.trr.mode = 5`, `DnsOverHttpsMode:
off`), which is what stops the easy way round it. Read the generated file before
installing it: it allows the list and answers `0.0.0.0` to everything else.

## Licence

The program is MIT — see `LICENSE`, which is the same licence as ChristInBackground
and Quiesce.

The content the start page carries is a different matter, and is split out
honestly in `DATA-LICENSE`:

* the Scripture text is the **World English Bible, Updated** (eBible.org), which
  is public domain;
* the two catechisms are public domain;
* the **verse selection, the challenges and the calendar of feasts come from
  studytools.cc**, and that part is carried under **CC BY-SA 4.0** — attribution
  and share-alike, which is why the licence travels with the file rather than
  being mentioned in passing.

That is why `src/home/data.json` is a build input rather than something typed
into the page, and why `tools/import-studytools.py` names its source in the file
it writes. If you fork this and rebuild the data from your own StudyTools copy,
the share-alike term is on that file and not on the program.

## What this is not

Not a content filter. It does not read pages, score them, or decide what is
wholesome: the list is the whole of the judgement, and it is yours. A site that is
on the list is on it entirely, every page of it. Read the list before you trust
it, and keep it short.

**And it is not a blanket over the machine.** Everything here is inside this one
browser: the clone, its policies, its profile, its filter. No other browser is
touched, configured or restricted — there is no Chrome in this project and no
Chrome policy, deliberately, because the point was a Firefox-based browser and
not a modified machine. The consequence is worth saying plainly: any other
browser on the same account is an ordinary browser, and can reach anything. If
the account this is for has a second browser, the lock is only as good as the
account — so keep it off that account, or give that account no `sudo` and no
second browser. The filter is the one thing that *could* be made machine-wide
(stage three, below), and it is not, by default, for the same reason: it would
reach beyond this browser, and this browser is what you asked for.
