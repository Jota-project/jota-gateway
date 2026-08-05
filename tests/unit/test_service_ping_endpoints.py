"""TranscriberClient.ping()/TTSClient.ping() must check /ready, not /health.

/health is pure liveness (always 200 if the process is up) on both
jota-transcriber and jota-speaker. /ready reflects real capacity/readiness
(503 when GPU-saturated / engine not loaded) — see issue #101.
"""

import httpx
import respx

from src.services.transcriber_client import TranscriberClient
from src.services.tts_client import TTSClient


async def test_transcriber_ping_hits_ready_endpoint():
    with respx.mock(assert_all_mocked=True) as router:
        ready_route = router.get("http://localhost:9000/ready").mock(
            return_value=httpx.Response(200)
        )
        result = await TranscriberClient.ping("localhost:9000")
    assert ready_route.called
    assert result is True


async def test_transcriber_ping_false_when_ready_reports_busy():
    with respx.mock(assert_all_mocked=True) as router:
        router.get("http://localhost:9000/ready").mock(
            return_value=httpx.Response(503, json={"status": "busy"})
        )
        result = await TranscriberClient.ping("localhost:9000")
    assert result is False


async def test_tts_ping_hits_ready_endpoint():
    with respx.mock(assert_all_mocked=True) as router:
        ready_route = router.get("http://localhost:8005/ready").mock(
            return_value=httpx.Response(200)
        )
        result = await TTSClient.ping("localhost:8005")
    assert ready_route.called
    assert result is True


async def test_tts_ping_false_when_ready_reports_not_ready():
    with respx.mock(assert_all_mocked=True) as router:
        router.get("http://localhost:8005/ready").mock(
            return_value=httpx.Response(503, json={"status": "not_ready"})
        )
        result = await TTSClient.ping("localhost:8005")
    assert result is False
