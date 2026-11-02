"""Delete branches whose work is already preserved in a closed pull request.

Dry run by default. A branch is deleted only when ALL of these hold:

* it is not the default branch, not ``main`` and not reported ``protected``;
* it has no open pull request;
* some closed pull request (merged, or closed unmerged such as a disposable
  acceptance PR) has exactly the branch's current head commit as its head.

GitHub keeps every pull request's commits under ``refs/pull/<n>/head``, so a
deleted branch can be restored from its pull request page ("Restore branch").
A branch whose head moved after its pull request closed, or that never had a
pull request, is always kept. Standard library only.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.github.com"


class GitHub:
    def __init__(self, repository, token, api=API):
        self.repository, self.token, self.api = repository, token, api

    def request(self, method, path, query=None):
        url = f"{self.api}/repos/{self.repository}/{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        request = urllib.request.Request(url, method=method, headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
        return json.loads(body) if body else None

    def repository_info(self):
        url = f"{self.api}/repos/{self.repository}"
        request = urllib.request.Request(url, headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
        })
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)

    def paginate(self, path, query):
        page, items = 1, []
        while True:
            batch = self.request("GET", path, {**query, "per_page": 100, "page": page})
            items.extend(batch)
            if len(batch) < 100:
                return items
            page += 1

    def branches(self):
        return self.paginate("branches", {})

    def pulls_for(self, branch):
        owner = self.repository.split("/", 1)[0]
        return self.paginate("pulls", {"state": "all", "head": f"{owner}:{branch}"})

    def delete_branch(self, branch):
        self.request("DELETE", "git/refs/heads/" + urllib.parse.quote(branch, safe="/"))


def classify(branch, pulls, keep):
    """Return (delete?, reason) for one branch and its pull requests."""
    name, sha = branch["name"], branch["commit"]["sha"]
    if name in keep:
        return False, "DEFAULT_BRANCH"
    if branch.get("protected"):
        return False, "PROTECTED"
    if any(pr.get("state") == "open" for pr in pulls):
        return False, "OPEN_PULL_REQUEST"
    for pr in sorted(pulls, key=lambda p: p.get("number", 0), reverse=True):
        if pr.get("head", {}).get("sha") == sha and pr.get("state") == "closed":
            kind = "MERGED" if pr.get("merged_at") else "CLOSED_UNMERGED"
            return True, f"{kind}_PR_{pr['number']}"
    if pulls:
        return False, "HEAD_MOVED_AFTER_PULL_REQUEST"
    return False, "NO_PULL_REQUEST"


def plan(github):
    keep = {"main", github.repository_info().get("default_branch", "main")}
    rows = []
    for branch in sorted(github.branches(), key=lambda b: b["name"]):
        pulls = [] if branch["name"] in keep else github.pulls_for(branch["name"])
        delete, reason = classify(branch, pulls, keep)
        rows.append({"branch": branch["name"], "sha": branch["commit"]["sha"],
                     "delete": delete, "reason": reason})
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--apply", action="store_true",
                        default=os.environ.get("CLEANUP_APPLY", "").lower() == "true")
    args = parser.parse_args(argv)
    token = os.environ.get("GITHUB_TOKEN")
    if not (args.repository and token):
        print("FAIL: GITHUB_REPOSITORY and GITHUB_TOKEN are required", file=sys.stderr)
        return 1
    github = GitHub(args.repository, token)
    rows = plan(github)
    doomed = [row for row in rows if row["delete"]]
    for row in rows:
        mark = "DELETE" if row["delete"] else "keep  "
        print(f"{mark} {row['branch']} {row['sha'][:7]} {row['reason']}")
    print(f"{len(doomed)} of {len(rows)} branches eligible; "
          f"mode={'APPLY' if args.apply else 'DRY RUN'}")
    if not args.apply:
        return 0
    failures = 0
    for row in doomed:
        try:
            github.delete_branch(row["branch"])
            print(f"deleted {row['branch']}")
        except urllib.error.URLError as error:
            failures += 1
            print(f"FAILED {row['branch']}: {error}", file=sys.stderr)
    print(f"deleted {len(doomed) - failures}, failed {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
