#!/usr/bin/env python3
"""Sync jiaerdangjia's public GitHub Stars; Python 3.10+, standard library only."""
from __future__ import annotations

import argparse
import copy
import html
import json
import os
from pathlib import Path
import re
import sys
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

USERNAME = "jiaerdangjia"
ROOT = Path(__file__).resolve().parent
STATUSES = {"baseline", "pending", "done", "ignored"}
NAME_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def request_page(page: int, token: str) -> tuple[list, bool]:
    url = (f"https://api.github.com/users/{USERNAME}/starred"
           f"?sort=created&direction=desc&per_page=100&page={page}")
    headers = {"Accept": "application/vnd.github.star+json",
               "X-GitHub-Api-Version": "2026-03-10",
               "User-Agent": "jiaerdangjia-Awesome-star-sync"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers=headers), timeout=30) as response:
                data = json.load(response)
                if not isinstance(data, list):
                    raise ValueError("GitHub did not return a Star list; no files were changed.")
                return data, 'rel="next"' in response.headers.get("Link", "")
        except HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504} or attempt == 2:
                raise RuntimeError(f"GitHub HTTP {exc.code} on page {page}; preserving state.") from exc
        except (URLError, TimeoutError) as exc:
            if attempt == 2:
                raise RuntimeError(f"GitHub network failure on page {page}; preserving state.") from exc
        time.sleep(2 ** (attempt + 1))
    raise RuntimeError("Unreachable retry state")


def normalize(item: dict) -> dict | None:
    repo = item.get("repo")
    if not isinstance(repo, dict):
        raise ValueError("Missing repository metadata / starred_at response format.")
    # This repository is public. Never publish private or visibility-unknown repos.
    if repo.get("private") is not False:
        return None
    rid, name, starred_at = repo.get("id"), repo.get("full_name"), item.get("starred_at")
    if type(rid) is not int or rid <= 0 or not isinstance(name, str) or not NAME_PATTERN.fullmatch(name):
        raise ValueError("Invalid repository identity; preserving state.")
    if not isinstance(starred_at, str):
        raise ValueError("Missing starred_at; preserving state.")
    parsed = datetime.fromisoformat(starred_at.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("starred_at must include a timezone.")
    return {"repo_id": rid, "full_name": name, "url": f"https://github.com/{name}",
            "description": str(repo.get("description") or ""),
            "language": str(repo.get("language") or ""),
            "archived": bool(repo.get("archived", False)), "starred_at": starred_at}


def fetch_stars(token: str, get_page=None) -> list[dict]:
    get_page = get_page or request_page
    repos = {}
    # All pages must succeed before any persistent state is touched.
    for page in range(1, 501):
        items, has_next = get_page(page, token)
        if has_next and not items:
            raise ValueError("Empty page has a next link; refusing an incomplete snapshot.")
        for item in items:
            record = normalize(item)
            if record is not None:
                key = str(record["repo_id"])
                # Pagination can overlap while Stars are being changed.
                if key not in repos or record["starred_at"] > repos[key]["starred_at"]:
                    repos[key] = record
        if not has_next:
            if not repos:
                raise ValueError("No public Stars returned. Check profile visibility/access; state preserved.")
            return list(repos.values())
    raise ValueError("Pagination safety limit reached; state preserved, not a partial import.")


def load_state(path: Path) -> dict | None:
    if not path.exists():
        return None
    state = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(state, dict) or state.get("schema_version") != 1
            or state.get("username") != USERNAME
            or not state.get("initialized_at") or not state.get("last_changed_at")
            or not isinstance(state.get("repositories"), dict)):
        raise ValueError("Invalid state schema/account; do not reset or overwrite existing data.")
    for key, record in state["repositories"].items():
        if (not isinstance(record, dict) or str(record.get("repo_id")) != key
                or record.get("analysis_status") not in STATUSES
                or not isinstance(record.get("full_name"), str)
                or not NAME_PATTERN.fullmatch(record["full_name"])
                or not isinstance(record.get("starred_at"), str)):
            raise ValueError("Invalid state record; preserving the original file.")
    return state


def merge_stars(previous: dict | None, stars: list[dict], now: str) -> dict:
    initial = previous is None
    state = copy.deepcopy(previous) if previous is not None else {
        "schema_version": 1, "username": USERNAME, "initialized_at": now,
        "last_changed_at": now, "repositories": {}}
    for star in stars:
        key = str(star["repo_id"])
        if key in state["repositories"]:
            # Keep all human-written status/notes. Rename/transfer/restar keeps the same ID.
            state["repositories"][key].update(star)
        else:
            state["repositories"][key] = {
                **star, "first_seen_at": now,
                "analysis_status": "baseline" if initial else "pending", "notes": ""}
    # Append-only history: an Unstar, deleted repo or permission change never deletes work.
    if state != previous:
        state["last_changed_at"] = now
    return state


def set_status(state: dict, repository: str, status: str, now: str) -> None:
    if status not in {"pending", "done", "ignored"}:
        raise ValueError("Status must be pending, done or ignored.")
    matches = [r for r in state["repositories"].values()
               if r["full_name"].casefold() == repository.casefold()]
    if len(matches) != 1:
        raise ValueError("Repository is unknown/ambiguous; use its current owner/name after sync.")
    if matches[0]["analysis_status"] != status:
        matches[0]["analysis_status"] = status
        state["last_changed_at"] = now


def pending_records(state: dict) -> list[dict]:
    return sorted((r for r in state["repositories"].values() if r["analysis_status"] == "pending"),
                  key=lambda r: (r["starred_at"], r["repo_id"]), reverse=True)


def md_text(value: str) -> str:
    text = html.escape(" ".join(str(value).split()), quote=False).replace("|", "&#124;")
    return re.sub(r"([\\`*\[\]_])", r"\\\1", text)


def render_pending(state: dict) -> str:
    pending = pending_records(state)
    counts = {status: sum(r["analysis_status"] == status for r in state["repositories"].values())
              for status in sorted(STATUSES)}
    lines = ["# GitHub Star 待分析清单", "", f"账号：`{USERNAME}`。本文件自动生成，请勿手工编辑。", "",
             f"首次基线：{state['initialized_at']}；最近内容变化：{state['last_changed_at']}（UTC）。",
             "最近成功检查时间以 Actions 运行记录为准；无变化时不制造提交。", "",
             f"累计登记 {len(state['repositories'])} 个仓库；历史基线 {counts['baseline']}；"
             f"待分析 {counts['pending']}；已分析 {counts['done']}；忽略 {counts['ignored']}。", "",
             "历史基线不等于已分析。取消收藏不删除已有记录，也不会自动取消待分析任务。", ""]
    if not pending:
        lines.append("当前没有新增待分析项目。首次同步只建立历史基线，不批量派发旧收藏。")
    else:
        lines += ["| 仓库 | 收藏时间（UTC） | 语言 | 归档 | 简介 |",
                  "| --- | --- | --- | --- | --- |"]
        for r in pending:
            lines.append(f"| [{md_text(r['full_name'])}]({r['url']}) | {md_text(r['starred_at'])} | "
                         f"{md_text(r['language'])} | {'是' if r['archived'] else '否'} | "
                         f"{md_text(r['description'][:200])} |")
    lines += ["", "[使用说明](README.md) · [机器可读待分析清单](data/pending.json) · "
              "[完整历史与分析状态](data/stars.json)", ""]
    return "\n".join(lines)


def save_outputs(root: Path, state: dict) -> None:
    def dump(value):
        return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    outputs = {root / "data/stars.json": dump(state),
               root / "data/pending.json": dump({"schema_version": 1, "username": USERNAME,
                    "last_changed_at": state["last_changed_at"], "items": pending_records(state)}),
               root / "PENDING.md": render_pending(state)}
    # Build and validate every output before writing. Git commits all three together.
    for path, content in outputs.items():
        if path.exists() and path.read_text(encoding="utf-8") == content:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT)
    parser.add_argument("--set-status", nargs=2, metavar=("OWNER/REPO", "STATUS"))
    parser.add_argument("--dry-run", action="store_true", help="Read and validate only; no file writes")
    args = parser.parse_args()
    try:
        previous = load_state(args.output_dir / "data/stars.json")
        now = utc_now()
        stars = fetch_stars(os.environ.get("GITHUB_TOKEN", ""))
        state = merge_stars(previous, stars, now)
        if args.set_status:
            set_status(state, *args.set_status, now)
        if not args.dry_run:
            save_outputs(args.output_dir, state)
        print(json.dumps({"username": USERNAME, "initial_baseline": previous is None,
                          "public_stars_fetched": len(stars),
                          "registered_total": len(state["repositories"]),
                          "pending": len(pending_records(state)),
                          "changed": state != previous, "dry_run": args.dry_run}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError, TypeError, KeyError) as exc:
        print(f"Star sync failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
