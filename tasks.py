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


@task(iterable=["service"])
def sul(ctx: Context, service: t.Optional[t.Collection[str]] = None) -> None:
    """
    Shortcut for `edwh setup up logs`
    """
    tasks.setup(ctx)
    tasks.up(ctx, service=service)
    # run as c.sudo to prevent elevate(), which would also run 'setup' and 'up' with sudo!
    cmd = f"{edwh} logs"
    if service:
        cmd += " -s " + " -s ".join(service)
    ctx.sudo(cmd, pty=True)
    # logs(ctx, service=service)


# @task()
# def migarte(c):
#    tasks.migrate(c)
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
    )

    table.add_column("repo", style="cyan", no_wrap=True)
    table.add_column("id", style="green", no_wrap=True)
    table.add_column("title", overflow="fold")
    table.add_column("author", style="magenta")
    table.add_column("assignees", style="blue")
    table.add_column("updated", justify="right")

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
            author,
            assignees,
            updated,
        )

    console.print(table)
