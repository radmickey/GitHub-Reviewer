import asyncio
import hmac
import hashlib
import logging
import os
import sys

try:
    from fastapi import FastAPI, Request, HTTPException
    from dotenv import load_dotenv
except ImportError as e:
    print(f"[ERROR] Missing dependency: {e}")
    print("Run: pip install -r requirements.txt")
    sys.exit(1)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

try:
    from github import get_pr_diff, get_readme, post_comment, check_github_config
    from prompt import build_prompt
    from providers import get_provider
except ImportError as e:
    log.error("Failed to import local module: %s", e)
    sys.exit(1)

app = FastAPI()


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Required environment variable {name!r} is not set")
    return value


def check_config():
    _require_env("GITHUB_WEBHOOK_SECRET")
    _require_env("ALLOWED_USERS")
    check_github_config()


check_config()

GITHUB_WEBHOOK_SECRET = os.environ["GITHUB_WEBHOOK_SECRET"]
ALLOWED_USERS = {u.strip() for u in os.environ["ALLOWED_USERS"].split(",") if u.strip()}

provider = get_provider()


def verify_signature(payload: bytes, signature: str) -> bool:
    expected = "sha256=" + hmac.new(
        GITHUB_WEBHOOK_SECRET.encode(), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


@app.post("/webhook")
async def webhook(request: Request):
    payload_bytes = await request.body()

    signature = request.headers.get("x-hub-signature-256", "")
    if not verify_signature(payload_bytes, signature):
        log.warning("Invalid webhook signature")
        raise HTTPException(status_code=401, detail="Invalid signature")

    payload = await request.json()
    event = request.headers.get("x-github-event")
    log.info("Event: %s | action: %s", event, payload.get("action"))

    if event not in ("issue_comment", "pull_request_review_comment"):
        log.info("Ignored: unsupported event")
        return {"status": "ignored"}

    if payload.get("action") != "created":
        log.info("Ignored: action is not 'created'")
        return {"status": "ignored"}

    comment = payload.get("comment", {})
    comment_body = comment.get("body", "")
    commenter = comment.get("user", {}).get("login", "")

    if commenter not in ALLOWED_USERS or "@review" not in comment_body:
        log.info("Ignored: commenter=%s trigger=%s", commenter, "@review" in comment_body)
        return {"status": "ignored"}

    repo_data = payload.get("repository", {})
    owner = repo_data.get("owner", {}).get("login")
    repo = repo_data.get("name")
    pr_number = (
        payload.get("issue", {}).get("number")
        or payload.get("pull_request", {}).get("number")
    )

    if not pr_number:
        log.warning("Could not determine PR number from payload")
        return {"status": "no pr found"}

    log.info("Processing PR %s/%s#%s by %s", owner, repo, pr_number, commenter)

    try:
        log.info("Fetching diff and README...")
        (diff, skipped), readme = await asyncio.gather(
            get_pr_diff(owner, repo, pr_number),
            get_readme(owner, repo),
        )
        log.info("Diff: %d chars | Skipped files: %d | README: %s", len(diff), len(skipped), "yes" if readme else "no")

        log.info("Calling LLM...")
        extra = comment_body.replace("@review", "").strip()
        prompt = build_prompt(diff, skipped, readme, extra)
        review = await provider.complete(prompt)
        log.info("Got review: %d chars", len(review))

        log.info("Posting comment to PR...")
        await post_comment(owner, repo, pr_number, f"## 🤖 Code Review\n\n{review}")
        log.info("Done")

    except Exception as e:
        log.exception("Error processing PR %s/%s#%s", owner, repo, pr_number)
        await post_comment(
            owner, repo, pr_number,
            f"## 🤖 Code Review\n\n❌ Произошла ошибка при обработке PR: `{e}`"
        )
        return {"status": "error"}

    return {"status": "ok"}
