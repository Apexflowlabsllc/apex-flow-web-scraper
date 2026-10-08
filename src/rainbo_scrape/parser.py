"""Killable parsing of untrusted documents, with Linux memory/CPU limits."""
import asyncio
import base64
import json
import os
import sys
from .policy import ScrapeError


async def parse(body, url, content_type, recipe=None):
    flags = {"creationflags": 0x08000000} if os.name == "nt" else {}
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "rainbo_scrape.parser", stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, **flags)
    payload = json.dumps({"body": base64.b64encode(body).decode(), "url": url,
                          "content_type": content_type, "recipe": recipe}).encode()
    try:
        async with asyncio.timeout(15):
            output, _ = await process.communicate(payload)
        if process.returncode or len(output) > 12*1024*1024:
            raise ScrapeError("parser_limit", "Document parser exceeded its resource limit")
        result = json.loads(output)
        if "parse_error" in result:
            raise ScrapeError(result["code"], result["parse_error"])
        return result
    except asyncio.TimeoutError as exc:
        raise ScrapeError("parser_timeout", "Document parsing exceeded 15 seconds") from exc
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


def main():
    if sys.platform == "linux":
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (512*1024*1024, 512*1024*1024))
        resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    from .extract import extract
    try:
        data = json.loads(sys.stdin.buffer.read(8*1024*1024))
        result = extract(base64.b64decode(data["body"]), data["url"], data["content_type"], data["recipe"])
        encoded = json.dumps(result, ensure_ascii=False)
        if len(encoded.encode()) > 12*1024*1024:
            raise ScrapeError("parser_output_limit", "Extracted document exceeds the result size limit")
    except Exception as exc:
        encoded = json.dumps({"parse_error": str(exc)[:500], "code": getattr(exc, "code", "parse_failed")})
    sys.stdout.buffer.write(encoded.encode())


if __name__ == "__main__":
    main()
