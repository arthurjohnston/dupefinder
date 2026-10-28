"""Open one .eml draft as a Thunderbird compose window from a link: the handler for gi-draft: URLs, which
_institution_emails.html (gi_backfill_current.py --review) uses, so a click opens the draft with its To,
subject, body and attachments filled in (gi_report_case.open_drafts()). A browser can't start Thunderbird
itself; a mailto: link can't carry the attachments, and Thunderbird opens a .eml file as a received message.

Only a .eml under data/dupe_publisher/ is opened, so a gi-draft: link on any other page can't run anything else.

Usage: gi_open_draft.py --install        register the gi-draft: handler for this user (xdg, ~/.local/share/applications)
       gi_open_draft.py gi-draft:PATH    what the browser runs on a click
"""
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

import gi_case_findings

SCHEME = "gi-draft"
DESKTOP = Path.home() / ".local/share/applications/gi-draft.desktop"


def link(eml):
    """The gi-draft: URL for a draft."""
    return f"{SCHEME}://" + Path(eml).resolve().as_uri()[len("file://"):]


def draft_path(url):
    """The .eml a gi-draft: URL names, or ValueError when it isn't a draft under REPORT_DIR."""
    u = urlparse(url)
    if u.scheme != SCHEME:
        raise ValueError(f"not a {SCHEME}: link: {url}")
    path = Path(unquote(u.path)).resolve()
    root = gi_case_findings.REPORT_DIR.resolve()
    if path.suffix != ".eml" or root not in path.parents or not path.is_file():
        raise ValueError(f"not a draft under {root}: {path}")
    return path


def install():
    python = Path(__file__).resolve().parent.parent / ".venv/bin/python3"
    DESKTOP.parent.mkdir(parents=True, exist_ok=True)
    DESKTOP.write_text(f"""[Desktop Entry]
Type=Application
Name=Open GI email draft in Thunderbird
Exec={python} {Path(__file__).resolve()} %u
Path={Path(__file__).resolve().parent}
MimeType=x-scheme-handler/{SCHEME};
NoDisplay=true
Terminal=false
""")
    subprocess.run(["xdg-mime", "default", DESKTOP.name, f"x-scheme-handler/{SCHEME}"], check=True)
    subprocess.run(["update-desktop-database", str(DESKTOP.parent)], check=False)
    print(f"registered {SCHEME}: -> {DESKTOP}")


def main():
    if sys.argv[1:] == ["--install"]:
        return install()
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    import gi_report_case
    try:
        path = draft_path(sys.argv[1])
    except ValueError as e:
        subprocess.run(["notify-send", "gi-draft", str(e)], check=False)
        sys.exit(str(e))
    gi_report_case.open_drafts([path])


if __name__ == "__main__":
    main()
