import requests
from threading import Thread

from inspect import getsource
from utils.download import download
from utils import get_logger
import scraper
import time


class Worker(Thread):
    def __init__(self, worker_id, config, frontier):
        self.logger = get_logger(f"Worker-{worker_id}", "Worker")
        self.config = config
        self.frontier = frontier
        # basic check for requests in scraper
        assert {getsource(scraper).find(req) for req in {"from requests import", "import requests"}} == {-1}, "Do not use requests in scraper.py"
        assert {getsource(scraper).find(req) for req in {"from urllib.request import", "import urllib.request"}} == {-1}, "Do not use urllib.request in scraper.py"
        super().__init__(daemon=True)
        
    def download_with_retry(self, url):
        # crawler resumes by itself when the cache comes back
        delay = 30
        slow_tries = 0
        while True:
            try:
                return download(url, self.config, self.logger)
            except requests.exceptions.ConnectionError as e:
                # cache unreachable so keep waiting
                self.logger.error(f"Cache unreachable for {url} ({type(e).__name__}); retrying in {delay}s")
                time.sleep(delay)
                delay = min(delay * 2, 600)
            except requests.exceptions.Timeout:
                # cache is up but this one url never answers so try few times, then skip
                slow_tries += 1
                self.logger.error(f"Timeout for {url} (try {slow_tries}/3)")
                if slow_tries >= 3:
                    return None
            except Exception as e:
                # anything else is a proble so skip it and keep crawling
                self.logger.error(f"Giving up on {url}: {type(e).__name__}: {e}")
                return None

    def run(self):
        # breakpoint()
        while True:
            tbd_url = self.frontier.get_tbd_url()
            if not tbd_url:
                self.logger.info("Frontier is empty. Stopping Crawler.")
                break
            resp = self.download_with_retry(tbd_url)
            if resp is None:
                self.frontier.mark_url_complete(tbd_url)
                continue
            self.logger.info(
                f"Downloaded {tbd_url}, status <{resp.status}>, "
                f"using cache {self.config.cache_server}.")
            scraped_urls = scraper.scraper(tbd_url, resp)
            for scraped_url in scraped_urls:
                self.frontier.add_url(scraped_url)
            self.frontier.mark_url_complete(tbd_url)
            time.sleep(self.config.time_delay)
