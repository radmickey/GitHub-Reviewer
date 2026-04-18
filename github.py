import httpx
import os
import subprocess

IGNORE_PATTERNS = [
    # Бинарники и скомпилированное
    ".o", ".a", ".so", ".dylib", ".dll", ".exe", ".out",
    # Папки сборки
    "/build/", "/cmake-build-", "/_build/", "/out/", "/obj/",
    # CMake / ninja артефакты
    "/_deps/", "/Testing/", ".ninja",
    # IDE и системное
    ".idea/", ".vscode/", ".vs/", ".DS_Store", "Thumbs.db",
    # Зависимости
    "/node_modules/", "/vendor/", "/.conan/", "/vcpkg_installed/",
    # Артефакты
    ".log", ".cache", ".tmp",
    # Большие data-файлы
    ".csv", ".json", ".xml", ".bin", ".dat",
    # Конфиги и служебные файлы — не несут логики
    ".gitignore", ".gitattributes", ".clang-format", ".clang-tidy",
    "CMakeLists.txt", ".cmake", "Makefile", "makefile",
    "LICENSE", "LICENSE.md", ".editorconfig",
]


def _get_github_token() -> str:
    result = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True)
    return result.stdout.strip()


GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN") or _get_github_token()


def check_github_config():
    if not GITHUB_TOKEN:
        raise RuntimeError(
            "GitHub token not found. Either set GITHUB_TOKEN in .env "
            "or run: gh auth login"
        )


def _filter_files(files: list[dict]) -> tuple[list[dict], list[str]]:
    kept, skipped = [], []
    for f in files:
        filename = f["filename"]
        if any(pat.lower() in ("/" + filename).lower() for pat in IGNORE_PATTERNS):
            skipped.append(filename)
        else:
            kept.append(f)
    return kept, skipped


def _build_file_diff(f: dict) -> str:
    filename = f["filename"]
    patch = f.get("patch")
    if not patch:
        status = f.get("status", "modified")
        return f"diff --git a/{filename} b/{filename}\n# {status}, no patch (binary or too large)\n"
    return (
        f"diff --git a/{filename} b/{filename}\n"
        f"--- a/{filename}\n"
        f"+++ b/{filename}\n"
        f"{patch}\n"
    )


async def get_pr_files(owner: str, repo: str, pr_number: int) -> tuple[list[dict], list[str]]:
    """Возвращает ([{"filename": str, "diff": str}], skipped_list)"""
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/files",
            headers={
                "Authorization": f"Bearer {GITHUB_TOKEN}",
                "Accept": "application/vnd.github.v3+json",
            },
            params={"per_page": 100},
        )
        r.raise_for_status()
        kept, skipped = _filter_files(r.json())
        files = [{"filename": f["filename"], "diff": _build_file_diff(f)} for f in kept]
        return files, skipped


async def get_readme(owner: str, repo: str) -> str:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"https://api.github.com/repos/{owner}/{repo}/readme",
            headers={
                "Authorization": f"Bearer {GITHUB_TOKEN}",
                "Accept": "application/vnd.github.v3.raw",
            },
        )
        if r.status_code == 200:
            return r.text
        return ""


async def post_comment(owner: str, repo: str, pr_number: int, body: str):
    async with httpx.AsyncClient() as client:
        await client.post(
            f"https://api.github.com/repos/{owner}/{repo}/issues/{pr_number}/comments",
            headers={
                "Authorization": f"Bearer {GITHUB_TOKEN}",
                "Accept": "application/vnd.github.v3+json",
            },
            json={"body": body},
        )
