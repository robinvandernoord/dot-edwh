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


CONTRIBUTION_QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      totalCommitContributions
    }
  }
}
"""


PR_COUNT_QUERY = """
query($search: String!) {
  search(query: $search, type: ISSUE, first: 1) {
    issueCount
  }
}
"""


LOC_QUERY = """
query($search: String!, $after: String) {
  search(query: $search, type: ISSUE, first: 100, after: $after) {
    pageInfo {
      hasNextPage
      endCursor
    }
    nodes {
      ... on PullRequest {
        additions
        deletions
      }
    }
  }
}
"""


class PRUser(t.TypedDict):
    login: str


class PRRepository(t.TypedDict):
    nameWithOwner: str


class PullRequest(t.TypedDict):
    number: int
    title: str
    url: str
    updatedAt: str
    isDraft: bool
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
        "number,title,repository,author,assignees,updatedAt,url,labels,isDraft",
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


def _github_graphql(query: str, **variables: str) -> dict[str, t.Any]:
    command = [
        "gh",
        "api",
        "graphql",
        "-f",
        f"query={query}",
    ]
    for key, value in variables.items():
        command.extend(("-f", f"{key}={value}"))
    result = subprocess.check_output(command, text=True)
    return json.loads(result)["data"]


def _periods(interval: str, count: int) -> list[tuple[dt.date, dt.date, str]]:
    today = dt.date.today()
    if interval == "month":
        start = dt.date(today.year, today.month, 1)

        def shift_months(offset: int) -> dt.date:
            month = start.year * 12 + start.month - 1 + offset
            return dt.date(month // 12, month % 12 + 1, 1)

        return [
            (
                shift_months(offset),
                today + dt.timedelta(days=1)
                if offset == 0
                else shift_months(offset + 1),
                shift_months(offset).strftime("%Y-%m"),
            )
            for offset in range(1 - count, 1)
        ]

    return [
        (
            dt.date(today.year + offset, 1, 1),
            today + dt.timedelta(days=1)
            if offset == 0
            else dt.date(today.year + offset + 1, 1, 1),
            str(today.year + offset),
        )
        for offset in range(1 - count, 1)
    ]


def _period_commits(login: str, start: dt.date, end: dt.date) -> int:
    collection = _github_graphql(
        CONTRIBUTION_QUERY,
        login=login,
        **{
            "from": f"{start.isoformat()}T00:00:00Z",
            "to": f"{end.isoformat()}T00:00:00Z",
        },
    )["user"]["contributionsCollection"]
    return collection["totalCommitContributions"]


def _period_prs(login: str, start: dt.date, end: dt.date) -> int:
    last_day = end - dt.timedelta(days=1)
    search = f"author:{login} is:pr created:{start.isoformat()}..{last_day.isoformat()}"
    return _github_graphql(PR_COUNT_QUERY, search=search)["search"]["issueCount"]


def _period_loc(login: str, start: dt.date, end: dt.date) -> int:
    last_day = end - dt.timedelta(days=1)
    search = f"author:{login} is:pr created:{start.isoformat()}..{last_day.isoformat()}"
    additions = deletions = 0
    after = ""

    while True:
        variables = {"search": search}
        if after:
            variables["after"] = after
        result = _github_graphql(LOC_QUERY, **variables)["search"]
        for pull_request in result["nodes"]:
            additions += pull_request["additions"]
            deletions += pull_request["deletions"]
        if not result["pageInfo"]["hasNextPage"]:
            return additions + deletions
        after = result["pageInfo"]["endCursor"]


@task
def contributions(
    _: Context,
    kind: str = "commits",
    interval: str = "month",
    periods: int = 12,
    user: str = "",
) -> None:
    """Plot commits, PRs, or LOC per calendar month or year for USER."""
    if kind not in {"commits", "prs", "loc"}:
        raise ValueError("kind must be one of: commits, prs, loc")
    if interval not in {"month", "year"}:
        raise ValueError("interval must be month or year")
    if periods < 1:
        raise ValueError("periods must be at least 1")

    try:
        import plotext as plt
    except ImportError as error:
        raise RuntimeError("Install plotext with: uvenv inject edwh plotext") from error

    if not user:
        user = subprocess.check_output(
            ["gh", "api", "user", "--jq", ".login"], text=True
        ).strip()

    periods_to_plot = _periods(interval, periods)
    labels = [label for _, _, label in periods_to_plot]
    if kind == "loc":
        values = [_period_loc(user, start, end) for start, end, _ in periods_to_plot]
        title = f"Lines changed in {user}'s PRs by {interval}"
        y_label = "lines changed"
    else:
        metric = _period_commits if kind == "commits" else _period_prs
        values = [metric(user, start, end) for start, end, _ in periods_to_plot]
        title = f"GitHub {kind} for {user} by {interval}"
        y_label = kind

    maximum = max(values, default=0)
    tick_step = max(1, (maximum + 4) // 5)
    y_ticks = list(range(0, maximum + tick_step + 1, tick_step))

    plt.clear_figure()
    plt.bar(labels, values)
    plt.title(title)
    plt.xlabel(interval)
    plt.ylabel(y_label)
    plt.yticks(y_ticks)
    plt.plotsize(None, 15)
    plt.show()


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


@task(iterable=("exclude",))
def prs(ctx: Context, exclude: list[str] = None) -> None:
    """
    List open PRs in the organisation, excluding archived and ignored repos.
    """
    # turn into dependabot AND dependabot[bot]
    exclude = set(item for user in (exclude or ()) for item in (user, f"{user}[bot]"))

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
        if pr["isDraft"]:
            continue

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

        if author in exclude:
            continue

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
