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
    from github import get_pr_files, get_readme, post_comment, check_github_config
    from prompt import build_file_prompt, build_summary_prompt
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

    async def process():
        try:
            log.info("Fetching files and README...")
            (files, skipped), readme = await asyncio.gather(
                get_pr_files(owner, repo, pr_number),
                get_readme(owner, repo),
            )
            log.info("Files: %d | Skipped: %d | README: %s", len(files), len(skipped), "yes" if readme else "no")

            extra = comment_body.replace("@review", "").strip()

            # Параллельное ревью файлов (макс. 3 одновременно)
            semaphore = asyncio.Semaphore(10)

            async def review_file(f: dict) -> dict:
                async with semaphore:
                    log.info("Reviewing file: %s (%d chars)", f["filename"], len(f["diff"]))
                    file_prompt = build_file_prompt(f["filename"], f["diff"], readme, extra)
                    file_review = await provider.complete(file_prompt, max_tokens=1024)
                    log.info("Done: %s", f["filename"])
                    return {"filename": f["filename"], "review": file_review}

            results = await asyncio.gather(*[review_file(f) for f in files], return_exceptions=True)
            file_reviews = []
            for r in results:
                if isinstance(r, Exception):
                    log.error("Ошибка при ревью файла: %s", r)
                elif r and r.get("review"):
                    file_reviews.append(r)

            # Финальная агрегация
            log.info("Building summary from %d file reviews...", len(file_reviews))
            summary_prompt = build_summary_prompt(file_reviews, readme, extra)
            review = await provider.complete(summary_prompt, max_tokens=4096)
            log.info("Got final review: %d chars", len(review))

            skipped_note = ""
            if skipped:
                skipped_note = f"\n\n> ⚠️ Пропущены файлы: {', '.join(skipped)}"

            log.info("Posting comment to PR...")
            await post_comment(owner, repo, pr_number, f"## 🤖 Code Review\n\n{review}{skipped_note}")
            log.info("Done")

        except Exception as e:
            log.exception("Error processing PR %s/%s#%s", owner, repo, pr_number)
            await post_comment(
                owner, repo, pr_number,
                f"## 🤖 Code Review\n\n❌ Произошла ошибка при обработке PR: `{e}`"
            )

    # Запускаем обработку в фоне — GitHub не ждёт
    asyncio.create_task(process())
    return {"status": "ok"}
