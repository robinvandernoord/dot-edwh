import datetime as dt
import json
import subprocess
import sys
import typing as t

from invoke import Context
from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

from edwh import task, tasks

edwh = sys.argv[0]


@task(iterable=["service"], pre=[tasks.require_sudo])
def smul(ctx: Context, service: t.Optional[t.Collection[str]] = None) -> None:
    """
    Shortcut for `edwh setup migrate up logs`
    """
    tasks.setup(ctx)
    tasks.build(ctx)  # includes 'pull'
    tasks.migrate(ctx)
    tasks.up(ctx, service=service)
    tasks.logs(ctx, service=service)


# setup alias:
_ = task(aliases=("migarte",))(tasks.migrate)


# ---------------------------------------------------------------------------
# PR listing task
# ---------------------------------------------------------------------------

PR_ORG = "educationwarehouse"
PR_LIMIT = 1000

PR_EXCLUDED_REPOS = {
    "educationwarehouse/stagiairedocumentatie",
}


class PRUser(t.TypedDict):
    login: str


class PRRepository(t.TypedDict):
    nameWithOwner: str


class PullRequest(t.TypedDict):
    number: int
    title: str
    url: str
    updatedAt: str
    author: PRUser
    assignees: list[PRUser]
    repository: PRRepository


def _pr_run_gh() -> list[PullRequest]:
    cmd = [
        "gh",
        "search",
        "prs",
        "--state",
        "open",
        "--owner",
        PR_ORG,
        "--archived=false",
        "--limit",
        str(PR_LIMIT),
        "--json",
        "number,title,repository,author,assignees,updatedAt,url,labels",
    ]
    result = subprocess.check_output(cmd, text=True)
    return json.loads(result)


def _pr_branches(repo: str, number: int) -> tuple[str, str]:
    cmd = [
        "gh",
        "pr",
        "view",
        str(number),
        "--repo",
        repo,
        "--json",
        "baseRefName,headRefName",
    ]
    result = subprocess.check_output(cmd, text=True)
    data = json.loads(result)
    return data.get("baseRefName", "-"), data.get("headRefName", "-")


def _pr_age_color(updated_at: str) -> str:
    updated = dt.datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    days = (dt.datetime.now(dt.UTC) - updated).days

    if days < 2:
        return "green"
    if days < 7:
        return "yellow"
    return "red"


def _pr_age_style(updated_at: str) -> str:
    updated = dt.datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    days = (dt.datetime.now(dt.UTC) - updated).days

    if days < 2:
        return "green"
    if days < 7:
        return "yellow"
    if days < 30:
        return "orange1"
    return "red"


@task
def prs(ctx: Context) -> None:
    """
    List open PRs in the organisation, excluding archived and ignored repos.
    """
    console = Console()

    table = Table(
        box=box.SIMPLE,
        header_style="bold",
        show_lines=False,
        expand=True,
    )

    table.add_column(
        "repo", style="cyan", no_wrap=True, max_width=36, overflow="ellipsis"
    )
    table.add_column("id", style="green", no_wrap=True)
    table.add_column("title", overflow="fold", ratio=2, min_width=24)
    table.add_column("to", style="white", overflow="ellipsis", ratio=1, min_width=12)
    table.add_column("from", style="white", overflow="ellipsis", ratio=2, min_width=20)
    table.add_column(
        "author", style="magenta", no_wrap=True, overflow="ellipsis", max_width=12
    )
    table.add_column(
        "assignees", style="blue", no_wrap=True, overflow="ellipsis", max_width=12
    )
    table.add_column("updated", justify="right", no_wrap=True)

    prs = sorted(
        _pr_run_gh(),
        key=lambda pr: pr["updatedAt"],
        reverse=True,
    )

    for pr in prs:
        repo = pr["repository"]["nameWithOwner"]
        if repo in PR_EXCLUDED_REPOS:
            continue

        pr_id = Text(
            f"#{pr['number']}",
            style=f"green link {pr['url']}",
        )

        title = Text.from_markup(pr["title"], emoji=True)
        try:
            base_ref, head_ref = _pr_branches(repo, pr["number"])
        except subprocess.CalledProcessError:
            base_ref, head_ref = "-", "-"

        author = pr["author"]["login"]

        assignees = (
            ", ".join(a["login"] for a in pr["assignees"]) if pr["assignees"] else "-"
        )

        updated = Text(
            pr["updatedAt"].replace("T", " ").replace("Z", ""),
            style=_pr_age_style(pr["updatedAt"]),
        )

        table.add_row(
            repo,
            pr_id,
            title,
            base_ref,
            head_ref,
            author,
            assignees,
            updated,
        )

    console.print(table)
