"""Publish the allowlisted site and remember only verified successful deployments."""
import argparse
import fcntl
import hashlib
import json
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from urllib.request import Request, urlopen
from zipfile import ZipFile

from . import ROOT
from .sources import atomic_json
from .stage_site import PUBLIC_FILES, stage_site

REPO = "watice555/flu_weekly"
SITE_URL = "https://watice555.github.io/flu_weekly/"
STATE = ROOT / "data" / "publish_state.json"


def gh(*args):
    result = subprocess.run(["gh", *args, "--repo", REPO], check=True,
                            capture_output=True, text=True, timeout=120)
    return result.stdout


def digest(data):
    return hashlib.sha256(data).hexdigest()


def fingerprint(directory):
    hashes = {}
    for name in PUBLIC_FILES:
        data = (directory / name).read_bytes()
        if name == "data/ili.json":
            payload = json.loads(data)
            payload.pop("generated_at", None)
            data = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        hashes[name] = digest(data)
    return digest(json.dumps(hashes, sort_keys=True).encode())


def find_run(tag):
    runs = json.loads(gh("run", "list", "--workflow", "deploy-pages.yml",
                         "--limit", "100", "--json", "databaseId,displayTitle,status,conclusion"))
    return next((run for run in runs if run["displayTitle"] == f"Deploy {tag}"), None)


def wait_run(tag, timeout=1200):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = find_run(tag)
        if run and run["status"] == "completed":
            return run
        time.sleep(10)
    raise RuntimeError(f"等待发布超时；下次将继续检查 {tag}，保留临时附件")


def verify_online(hashes, attempts=18):
    for attempt in range(attempts):
        try:
            for name, expected in hashes.items():
                request = Request(f"{SITE_URL}{name}?verify={uuid.uuid4().hex}",
                                  headers={"Cache-Control": "no-cache"})
                with urlopen(request, timeout=30) as response:
                    if digest(response.read()) != expected:
                        raise ValueError(f"线上文件尚未匹配：{name}")
            return
        except (OSError, ValueError) as error:
            if attempt == attempts - 1:
                raise RuntimeError("线上核验失败，下次重试") from error
            time.sleep(10)


def finish_pending(state):
    pending = state["pending"]
    # If dispatch was interrupted, discover the uniquely named run before retrying.
    if not find_run(pending["tag"]):
        gh("workflow", "run", "deploy-pages.yml", "--ref", "main",
           "-f", f"release_tag={pending['tag']}", "-f", f"sha256={pending['sha256']}")
    run = wait_run(pending["tag"])
    if run["conclusion"] == "success":
        if not pending.get("verified"):
            verify_online(pending["hashes"])
            pending["verified"] = True
        state["last_fingerprint"] = pending["fingerprint"]
        state["last_run_id"] = run["databaseId"]
        atomic_json(STATE, state)
    # Retain pending metadata until cleanup succeeds; retries also retry cleanup.
    releases = json.loads(gh("release", "list", "--limit", "100", "--json", "tagName"))
    if any(item["tagName"] == pending["tag"] for item in releases):
        gh("release", "delete", pending["tag"], "--yes", "--cleanup-tag")
    del state["pending"]
    atomic_json(STATE, state)
    if run["conclusion"] != "success":
        raise RuntimeError(f"发布失败：运行 {run['databaseId']}，{run['conclusion']}；下次重试")
    print(f"发布成功并已核对线上六个文件：{SITE_URL}（运行 {run['databaseId']}）", flush=True)


def publish(dry_run=False):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    # OS releases the lock on crashes, preventing overlapping uploads on this Mac.
    with (STATE.parent / "publish.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("已有发布任务运行中") from error
        state = json.loads(STATE.read_text()) if STATE.exists() else {}
        (ROOT / "tmp").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="publish-", dir=ROOT / "tmp") as temporary:
            directory = Path(temporary)
            staged = stage_site(directory / "public")
            current = fingerprint(staged)
            if dry_run:
                print(json.dumps({"files": list(PUBLIC_FILES), "fingerprint": current,
                                  "changed": current != state.get("last_fingerprint"),
                                  "pending": bool(state.get("pending"))}, ensure_ascii=False))
                return
            if state.get("uploading"):
                releases = json.loads(gh("release", "list", "--limit", "100", "--json", "tagName"))
                if any(item["tagName"] == state["uploading"] for item in releases):
                    gh("release", "delete", state["uploading"], "--yes", "--cleanup-tag")
                del state["uploading"]
                atomic_json(STATE, state)
            if state.get("pending"):
                finish_pending(state)
            if current == state.get("last_fingerprint"):
                print("公开内容没有变化，跳过发布。", flush=True)
                return
            archive = directory / "site.zip"
            with ZipFile(archive, "w") as bundle:
                for name in PUBLIC_FILES:
                    bundle.write(staged / name, name)
            tag = f"auto-pages-{uuid.uuid4().hex}"
            pending = {"tag": tag, "sha256": digest(archive.read_bytes()),
                       "fingerprint": current,
                       "hashes": {name: digest((staged / name).read_bytes()) for name in PUBLIC_FILES}}
            # Upload completes before dispatch; persist metadata first so upload failures are recoverable.
            state["uploading"] = tag
            atomic_json(STATE, state)
            gh("release", "create", tag, str(archive), "--prerelease", "--target", "main",
               "--title", tag, "--notes", "Temporary allowlisted GitHub Pages bundle; automatically removed after deployment.")
            state.pop("uploading")
            state["pending"] = pending
            atomic_json(STATE, state)
            print(f"已上传六文件发布包：{tag}", flush=True)
            finish_pending(state)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Only stage and compare; no network or deployment")
    args = parser.parse_args()
    publish(args.dry_run)
