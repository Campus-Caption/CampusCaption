"""Create the CampusCaption backlog on GitHub from backlog.json.

Run by the "Set up backlog" workflow (Actions tab -> Set up backlog -> Run workflow).
Safe to run more than once: labels, milestones and issues that already exist
(same name or title) are skipped, so nothing is created twice.
"""

import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

REPO = os.environ["GITHUB_REPOSITORY"]
TOKEN = os.environ["GITHUB_TOKEN"]
API = f"https://api.github.com/repos/{REPO}"
HERE = pathlib.Path(__file__).parent


def call(method, path, data=None):
    req = urllib.request.Request(
        API + path,
        method=method,
        data=json.dumps(data).encode() if data is not None else None,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            body = resp.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as err:
        print(f"{method} {path} failed: {err.code} {err.read().decode()}")
        sys.exit(1)


def get_all(path):
    items, page = [], 1
    while True:
        sep = "&" if "?" in path else "?"
        batch = call("GET", f"{path}{sep}per_page=100&page={page}")
        items += batch
        if len(batch) < 100:
            return items
        page += 1


backlog = json.loads((HERE / "backlog.json").read_text(encoding="utf-8"))

existing_labels = {label["name"] for label in get_all("/labels")}
for label in backlog["labels"]:
    if label["name"] not in existing_labels:
        call("POST", "/labels", label)
        print("label:", label["name"])

existing_milestones = {m["title"] for m in get_all("/milestones?state=all")}
for milestone in backlog["milestones"]:
    if milestone["title"] not in existing_milestones:
        call("POST", "/milestones", milestone)
        print("milestone:", milestone["title"])

existing_issues = {
    i["title"]: i for i in get_all("/issues?state=all") if "pull_request" not in i
}


def ensure_issue(spec):
    if spec["title"] in existing_issues:
        print("exists:", spec["title"])
        return existing_issues[spec["title"]], False
    issue = call("POST", "/issues", spec)
    existing_issues[spec["title"]] = issue
    print(f"#{issue['number']}:", spec["title"])
    return issue, True


for group in backlog["epics"]:
    epic, _ = ensure_issue(group["epic"])
    for spec in group["issues"]:
        child, created = ensure_issue(spec)
        if created:
            call("POST", f"/issues/{epic['number']}/sub_issues", {"sub_issue_id": child["id"]})

print("Backlog ready.")
