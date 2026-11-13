"""Reading pages through the owner's own signed-in browser profile, on the owner's own machine."""
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from rainbo_scrape.policy import ScrapeError
from rainbo_scrape.settings import Settings, brave_path
from rainbo_scrape.signed_in import profile_dir, read_signed_in, sign_in

needs_brave = pytest.mark.skipif(not brave_path(), reason="installed Brave is required")


@pytest.fixture
async def members_site():
    async def login(request):
        response = web.Response(text="<html><body><h1>Signed in</h1></body></html>", content_type="text/html")
        response.set_cookie("member", "yes", max_age=3600)
        return response

    async def reviews(request):
        if request.cookies.get("member") != "yes":
            return web.Response(status=403, text="<html><body><h1>Sign in to continue</h1></body></html>", content_type="text/html")
        return web.Response(text="<html><body><main><h1>Company reviews</h1><p>Members-only review text.</p>"
                                 "<a href='/reviews/2'>Next page</a></main></body></html>", content_type="text/html")
    app = web.Application()
    app.router.add_get("/login", login)
    app.router.add_get("/reviews", reviews)
    server = TestServer(app)
    await server.start_server()
    yield f"http://127.0.0.1:{server.port}"
    await server.close()


def test_profile_names_stay_inside_the_data_folder(tmp_path):
    settings = Settings(data_dir=tmp_path)
    assert profile_dir(settings, "work") == tmp_path / "profiles" / "work"
    for bad in ("../outside", "a/b", "", "x" * 60, "con:"):
        with pytest.raises(ValueError):
            profile_dir(settings, bad)


@needs_brave
async def test_signed_in_profile_reads_what_a_fresh_browser_is_refused(members_site, tmp_path):
    settings = Settings(data_dir=tmp_path, host_delay=0, allow_test_loopback=True)
    # A profile that has never signed in is refused, and says so plainly.
    with pytest.raises(ScrapeError) as refused:
        await read_signed_in(members_site + "/reviews", profile="fresh", settings=settings)
    assert refused.value.code == "sign_in_required"
    # The owner signs in once in that profile (here the page sets the cookie itself; a person would type their login).
    await sign_in(members_site + "/login", profile="mine", settings=settings, headless=True, wait_for_close=False)
    page = await read_signed_in(members_site + "/reviews", profile="mine", settings=settings)
    assert "Members-only review text." in page["markdown"] and page["status"] == 200
    assert page["receipt"]["signed_in_profile"] == "mine" and page["receipt"]["read_on"] == "this machine"
    assert any(str(link.get("url") if isinstance(link, dict) else link).endswith("/reviews/2") for link in page["links"])
    # The other profile is still signed out: profiles do not share sessions.
    with pytest.raises(ScrapeError):
        await read_signed_in(members_site + "/reviews", profile="fresh", settings=settings)
