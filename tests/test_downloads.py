import httpx
import pytest

from indico_mcp.client import IndicoClient, IndicoError
from indico_mcp.config import InstanceConfig


async def make_client(handler, token="synthetic-test-token", base="https://indico.example"):
    client = IndicoClient(InstanceConfig("test", base, token))
    headers = client._http.headers
    await client._http.aclose()
    client._http = httpx.AsyncClient(headers=headers, transport=httpx.MockTransport(handler), follow_redirects=True)
    return client


@pytest.mark.asyncio
@pytest.mark.parametrize("object_path", ["", "contributions/20/", "contributions/20/subcontributions/30/", "sessions/40/"])
async def test_protected_attachment_uses_token_export_after_display_denial(object_path):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers["authorization"] == "Bearer synthetic-test-token"
        if len(requests) == 1:
            return httpx.Response(403)
        assert request.url.path == "/indico/export/event/1/material/2/3.bin"
        return httpx.Response(200, content=b"%PDF-synthetic", headers={"content-type": "application/pdf"})

    client = await make_client(handler, base="https://indico.example/indico")
    try:
        result = await client.download(f"https://indico.example/indico/event/1/{object_path}attachments/2/3/example.pdf")
        assert result.content == b"%PDF-synthetic"
        assert result.filename == "example.pdf"
        assert len(requests) == 2
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("token,path,status", [
    (None, "/event/1/attachments/2/3/example.pdf", 403),
    ("synthetic-test-token", "/other/example.pdf", 403),
    ("synthetic-test-token", "/event/1/attachments/2/3/example.pdf", 200),
])
async def test_download_does_not_retry_unrelated_or_public_requests(token, path, status):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, content=b"synthetic")

    client = await make_client(handler, token=token)
    try:
        if status == 403:
            with pytest.raises(IndicoError, match="Access denied"):
                await client.download("https://indico.example" + path)
        else:
            await client.download("https://indico.example" + path)
        assert len(requests) == 1
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_export_denial_is_preserved():
    client = await make_client(lambda request: httpx.Response(403))
    try:
        with pytest.raises(IndicoError, match="Access denied"):
            await client.download("https://indico.example/event/1/attachments/2/3/example.pdf")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_cross_host_redirect_does_not_trigger_export_retry():
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.host == "indico.example":
            return httpx.Response(302, headers={"location": "https://other.example/denied"})
        assert "authorization" not in request.headers
        return httpx.Response(403)

    client = await make_client(handler)
    try:
        with pytest.raises(IndicoError, match="unexpected host"):
            await client.download("https://indico.example/event/1/attachments/2/3/example.pdf")
        assert len(requests) == 2
    finally:
        await client.aclose()
