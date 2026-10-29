import requests
import cbor
import time

from utils.response import Response

def download(url, config, logger=None):
    host, port = config.cache_server
    # crawler was eating up all RAM because of a massive VM file download, now have to check content length before download
    head_resp = requests.head(f"http://{host}:{port}/", params=[("q", f"{url}"), ("u", f"{config.user_agent}")], timeout=5)
    content_length = int(head_resp.headers.get('Content-Length', 0))
    if content_length > 10_000_000: 
        logger.info(f"Skipping file with {content-length} bytes of data: {url}")
        return None
    resp = requests.get(
        f"http://{host}:{port}/",
        params=[("q", f"{url}"), ("u", f"{config.user_agent}")],
        timeout=60) # a stalled cache raises Timeout instead of hanging the crawler forever
    try:
        if resp and resp.content:
            return Response(cbor.loads(resp.content))
    except (EOFError, ValueError) as e:
        pass
    logger.error(f"Spacetime Response error {resp} with url {url}.")
    return Response({
        "error": f"Spacetime Response error {resp} with url {url}.",
        "status": resp.status_code,
        "url": url})
