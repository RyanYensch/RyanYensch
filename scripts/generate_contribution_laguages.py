#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

API_ROOT = "https://api.github.com"
API_VERSION = "2026-03-10"
TOP_LANGUAGE_COUNT = 5

# Language -> GitHub-style colour.
LANGUAGES: dict[str, tuple[str, str]] = {
    ".ts": ("TypeScript", "#3178c6"),
    ".tsx": ("TypeScript", "#3178c6"),
    ".js": ("JavaScript", "#f1e05a"),
    ".jsx": ("JavaScript", "#f1e05a"),
    ".mjs": ("JavaScript", "#f1e05a"),
    ".cjs": ("JavaScript", "#f1e05a"),

    ".py": ("Python", "#3572A5"),
    ".pyi": ("Python", "#3572A5"),

    ".c": ("C", "#555555"),
    ".h": ("C", "#555555"),

    ".cpp": ("C++", "#f34b7d"),
    ".cc": ("C++", "#f34b7d"),
    ".cxx": ("C++", "#f34b7d"),
    ".hpp": ("C++", "#f34b7d"),
    ".hh": ("C++", "#f34b7d"),
    ".hxx": ("C++", "#f34b7d"),
    ".ino": ("C++", "#f34b7d"),

    ".java": ("Java", "#b07219"),

    ".go": ("Go", "#00ADD8"),
    ".rs": ("Rust", "#dea584"),
    ".rb": ("Ruby", "#701516"),
    ".php": ("PHP", "#4F5D95"),
    ".cs": ("C#", "#178600"),

    ".swift": ("Swift", "#F05138"),
    ".kt": ("Kotlin", "#A97BFF"),
    ".kts": ("Kotlin", "#A97BFF"),

    ".sh": ("Shell", "#89e051"),
    ".bash": ("Shell", "#89e051"),
    ".zsh": ("Shell", "#89e051"),
    ".fish": ("Shell", "#89e051"),

    ".html": ("HTML", "#e34c26"),
    ".htm": ("HTML", "#e34c26"),
    ".css": ("CSS", "#563d7c"),
    ".scss": ("SCSS", "#c6538c"),
    ".sass": ("Sass", "#a53b70"),
    ".less": ("Less", "#1d365d"),

    ".vue": ("Vue", "#41b883"),
    ".svelte": ("Svelte", "#ff3e00"),

    ".sql": ("SQL", "#e38c00"),
    ".lua": ("Lua", "#000080"),
    ".r": ("R", "#198CE7"),
    ".dart": ("Dart", "#00B4AB"),
    ".scala": ("Scala", "#c22d40"),

    ".ex": ("Elixir", "#6e4a7e"),
    ".exs": ("Elixir", "#6e4a7e"),
    ".hs": ("Haskell", "#5e5086"),
    ".jl": ("Julia", "#a270ba"),

    ".v": ("Verilog", "#b2b7f8"),
    ".sv": ("SystemVerilog", "#DAE1C2"),
    ".svh": ("SystemVerilog", "#DAE1C2"),
    ".vhd": ("VHDL", "#adb2cb"),
    ".vhdl": ("VHDL", "#adb2cb"),

    ".tex": ("TeX", "#3D6117"),

    ".graphql": ("GraphQL", "#e10098"),
    ".gql": ("GraphQL", "#e10098"),

    ".proto": ("Protocol Buffer", "#6b8e23"),
    ".cmake": ("CMake", "#DA3434"),
}

SPECIAL_FILES: dict[str, tuple[str, str]] = {
    "Dockerfile": ("Dockerfile", "#384d54"),
    "Makefile": ("Makefile", "#427819"),
    "CMakeLists.txt": ("CMake", "#DA3434"),
}

# These are deliberately excluded because they can massively distort a
# line-addition based language statistic.
SKIP_FILENAMES = {
    "package-lock.json",
    "npm-shrinkwrap.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "bun.lock",
    "bun.lockb",
    "poetry.lock",
    "Pipfile.lock",
    "Cargo.lock",
    "composer.lock",
    "Gemfile.lock",
}

SKIP_SUFFIXES = {
    ".min.js",
    ".min.css",
    ".map",
    ".ipynb",
}

TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
USERNAME = os.environ.get("GITHUB_USERNAME", "").strip()

if not TOKEN:
    raise SystemExit("GITHUB_TOKEN is not set.")

if not USERNAME:
    raise SystemExit("GITHUB_USERNAME is not set.")


def api_get(
    path: str,
    params: dict[str, object] | None = None,
) -> object:
    url = f"{API_ROOT}{path}"

    if params:
        url = f"{url}?{urlencode(params)}"

    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {TOKEN}",
            "User-Agent": "contribution-language-card",
            "X-GitHub-Api-Version": API_VERSION,
        },
    )

    for attempt in range(3):
        try:
            with urlopen(request, timeout=30) as response:
                return json.load(response)

        except HTTPError as error:
            body = error.read().decode(
                "utf-8",
                errors="replace",
            )

            if error.code >= 500 and attempt < 2:
                time.sleep(2**attempt)
                continue

            raise RuntimeError(
                f"GitHub API request failed "
                f"({error.code}) for {url}: {body}"
            ) from error

        except URLError as error:
            if attempt < 2:
                time.sleep(2**attempt)
                continue

            raise RuntimeError(
                f"GitHub API request failed for {url}: {error}"
            ) from error

    raise RuntimeError(
        f"GitHub API request failed for {url}"
    )


def detect_language(
    filename: str,
) -> tuple[str, str] | None:
    basename = Path(filename).name

    if basename in SKIP_FILENAMES:
        return None

    lower_name = filename.lower()

    if any(
        lower_name.endswith(suffix)
        for suffix in SKIP_SUFFIXES
    ):
        return None

    if basename in SPECIAL_FILES:
        return SPECIAL_FILES[basename]

    suffix = Path(filename).suffix.lower()

    return LANGUAGES.get(suffix)


def search_merged_pull_requests() -> list[tuple[str, int]]:
    user = api_get(
        f"/users/{quote(USERNAME)}"
    )

    if (
        not isinstance(user, dict)
        or "created_at" not in user
    ):
        raise RuntimeError(
            "Could not determine GitHub account creation date."
        )

    created_year = int(
        str(user["created_at"])[:4]
    )

    current_year = datetime.now(
        timezone.utc
    ).year

    pull_requests: set[tuple[str, int]] = set()

    # Search year-by-year so this continues to work even if the
    # account eventually has a very large number of PRs.
    for year in range(
        created_year,
        current_year + 1,
    ):
        start = f"{year}-01-01"
        end = f"{year}-12-31"

        query = (
            f"is:pr "
            f"is:merged "
            f"author:{USERNAME} "
            f"created:{start}..{end}"
        )

        page = 1

        while True:
            result = api_get(
                "/search/issues",
                {
                    "q": query,
                    "per_page": 100,
                    "page": page,
                },
            )

            if not isinstance(result, dict):
                raise RuntimeError(
                    "Unexpected response from GitHub search API."
                )

            items = result.get(
                "items",
                [],
            )

            if not isinstance(items, list):
                raise RuntimeError(
                    "Unexpected items response "
                    "from GitHub search API."
                )

            for item in items:
                if not isinstance(item, dict):
                    continue

                repository_url = str(
                    item.get(
                        "repository_url",
                        "",
                    )
                )

                number = item.get("number")

                marker = "/repos/"

                if (
                    marker not in repository_url
                    or not isinstance(number, int)
                ):
                    continue

                repo = repository_url.split(
                    marker,
                    1,
                )[1]

                pull_requests.add(
                    (repo, number)
                )

            if len(items) < 100:
                break

            page += 1

    return sorted(pull_requests)


def get_pull_request_files(
    repo: str,
    number: int,
) -> list[dict[str, object]]:
    files: list[dict[str, object]] = []

    page = 1

    while True:
        result = api_get(
            f"/repos/{repo}/pulls/{number}/files",
            {
                "per_page": 100,
                "page": page,
            },
        )

        if not isinstance(result, list):
            raise RuntimeError(
                f"Unexpected files response "
                f"for {repo}#{number}."
            )

        files.extend(
            item
            for item in result
            if isinstance(item, dict)
        )

        if len(result) < 100:
            break

        page += 1

    return files


def collect_language_stats(
    pull_requests: list[tuple[str, int]],
) -> tuple[
    dict[str, int],
    dict[str, str],
    int,
]:
    additions_by_language: dict[str, int] = defaultdict(int)
    colours: dict[str, str] = {}

    for index, (
        repo,
        number,
    ) in enumerate(
        pull_requests,
        start=1,
    ):
        print(
            f"[{index}/{len(pull_requests)}] "
            f"Reading {repo}#{number}"
        )

        files = get_pull_request_files(
            repo,
            number,
        )

        for file in files:
            filename = file.get("filename")
            additions = file.get(
                "additions",
                0,
            )

            if (
                not isinstance(filename, str)
                or not isinstance(additions, int)
            ):
                continue

            if additions <= 0:
                continue

            language = detect_language(
                filename
            )

            if language is None:
                continue

            language_name, colour = language

            additions_by_language[
                language_name
            ] += additions

            colours[
                language_name
            ] = colour

    total_additions = sum(
        additions_by_language.values()
    )

    return (
        dict(additions_by_language),
        colours,
        total_additions,
    )


def render_svg(
    additions: dict[str, int],
    colours: dict[str, str],
    total_additions: int,
    pull_request_count: int,
) -> str:
    if total_additions <= 0:
        raise RuntimeError(
            "No recognised code additions were found."
        )

    sorted_languages = sorted(
        additions.items(),
        key=lambda item: item[1],
        reverse=True,
    )

    top_languages = sorted_languages[
        :TOP_LANGUAGE_COUNT
    ]

    other_lines = sum(
        lines
        for _, lines
        in sorted_languages[
            TOP_LANGUAGE_COUNT:
        ]
    )

    progress_items = list(
        top_languages
    )

    if other_lines:
        progress_items.append(
            ("Other", other_lines)
        )

        colours["Other"] = "#6e7681"

    width = 320
    height = 175

    bar_x = 25
    bar_y = 61
    bar_width = 270
    bar_height = 8

    bar_rects: list[str] = []

    current_x = float(bar_x)

    for language, lines in progress_items:
        segment_width = (
            bar_width
            * lines
            / total_additions
        )

        bar_rects.append(
            f'<rect '
            f'x="{current_x:.2f}" '
            f'y="{bar_y}" '
            f'width="{segment_width:.2f}" '
            f'height="{bar_height}" '
            f'fill="{colours[language]}" '
            f'/>'
        )

        current_x += segment_width

    item_groups: list[str] = []

    for index, (
        language,
        lines,
    ) in enumerate(top_languages):
        if index < 3:
            x = 25
            y = 94 + index * 24

        else:
            x = 175
            y = 94 + (index - 3) * 24

        percentage = (
            100
            * lines
            / total_additions
        )

        label = (
            f"{escape(language)} "
            f"{percentage:.2f}%"
        )

        item_groups.append(
            f"""
            <g transform="translate({x}, {y})">
              <circle
                cx="5"
                cy="5"
                r="4"
                fill="{colours[language]}"
              />
              <text
                x="15"
                y="9"
                class="lang"
              >
                {label}
              </text>
            </g>
            """
        )

    description = escape(
        f"Based on "
        f"{pull_request_count} merged pull requests "
        f"and {total_additions:,} "
        f"recognised code lines added."
    )

    return f"""<svg
  width="{width}"
  height="{height}"
  viewBox="0 0 {width} {height}"
  fill="none"
  xmlns="http://www.w3.org/2000/svg"
  role="img"
  aria-labelledby="titleId descId"
>
  <title id="titleId">
    Contribution Languages
  </title>

  <desc id="descId">
    {description}
  </desc>

  <style>
    .title {{
      font: 600 18px 'Segoe UI', Ubuntu, Sans-Serif;
      fill: #70a5fd;
    }}

    .subtitle {{
      font: 400 10px 'Segoe UI', Ubuntu, Sans-Serif;
      fill: #a9b1d6;
    }}

    .lang {{
      font: 400 10px 'Segoe UI', Ubuntu, Sans-Serif;
      fill: #38bdae;
    }}
  </style>

  <rect
    x="0.5"
    y="0.5"
    width="{width - 1}"
    height="{height - 1}"
    rx="4.5"
    fill="#1a1b27"
    stroke="#e4e2e2"
  />

  <text
    x="25"
    y="31"
    class="title"
  >
    Contribution Languages
  </text>

  <text
    x="25"
    y="47"
    class="subtitle"
  >
    Merged PR additions • public + accessible private repos
  </text>

  <clipPath id="barClip">
    <rect
      x="{bar_x}"
      y="{bar_y}"
      width="{bar_width}"
      height="{bar_height}"
      rx="4"
    />
  </clipPath>

  <g clip-path="url(#barClip)">
    {''.join(bar_rects)}
  </g>

  {''.join(item_groups)}
</svg>
"""


def main() -> None:
    output_path = Path(
        sys.argv[1]
        if len(sys.argv) > 1
        else "dist/languages.svg"
    )

    pull_requests = (
        search_merged_pull_requests()
    )

    if not pull_requests:
        raise RuntimeError(
            f"No merged pull requests "
            f"found for {USERNAME}."
        )

    print(
        f"Found "
        f"{len(pull_requests)} "
        f"merged pull requests."
    )

    (
        additions,
        colours,
        total_additions,
    ) = collect_language_stats(
        pull_requests
    )

    print(
        f"Recognised code additions: "
        f"{total_additions:,}"
    )

    for language, lines in sorted(
        additions.items(),
        key=lambda item: item[1],
        reverse=True,
    ):
        print(
            f"  {language}: {lines:,}"
        )

    svg = render_svg(
        additions,
        colours,
        total_additions,
        len(pull_requests),
    )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_path.write_text(
        svg,
        encoding="utf-8",
    )

    print(
        f"Wrote {output_path}"
    )


if __name__ == "__main__":
    main()