#!/usr/bin/env python3
"""Use the existing branch-based Pages publisher, including retry after a failed build."""
import json
import os
import subprocess
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class BuildError(Exception):
    pass


def request_build(repository, token, head, default_branch, opener=urlopen):
    def call(path, method="GET"):
        req = Request("https://api.github.com/repos/" + repository + path, method=method,
                      data=b"{}" if method == "POST" else None,
                      headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
        try:
            with opener(req, timeout=30) as response:
                return json.load(response)
        except (HTTPError, URLError, ValueError, OSError):
            raise BuildError("Could not confirm/request the existing GitHub Pages build. Check Pages write permission and the Actions log; next sync retries.") from None
    settings = call("/pages")
    if settings.get("build_type") != "legacy" or settings.get("source") != {"branch": default_branch, "path": "/"}:
        raise BuildError("Pages publishing settings changed. Review deployment integration; no competing deployment was started.")
    latest = call("/pages/builds/latest")
    if latest.get("status") in ("building", "queued"):
        return "Existing Pages build is already running; next daily check verifies publication."
    if latest.get("status") == "built" and latest.get("commit") == head:
        return "Pages already publishes the current commit; no build needed."
    call("/pages/builds", "POST")
    return "Requested a build through the existing GitHub Pages publisher."


if __name__ == "__main__":
    try:
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        print(request_build(os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_TOKEN"], head, os.environ["DEFAULT_BRANCH"]))
    except (BuildError, KeyError) as e:
        print(str(e) if isinstance(e, BuildError) else "Pages build configuration missing", file=sys.stderr)
        sys.exit(1)
