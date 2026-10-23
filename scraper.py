import re
import hashlib
from utils.response import Response
from urllib.parse import urlparse, urljoin, urldefrag, ParseResult, parse_qs
from dataclasses import dataclass, field
from bs4 import BeautifulSoup

import atexit
import json
import logging
import os
import traceback

########### CONSTANTS ###############

STOP_WORDS: set[str] = set({
    'i', 'me', 'my', 'myself', 'we', 'our', 'ours', 'ourselves', 'you', "you're", 
    "you've", "you'll", "you'd", 'your', 'yours', 'yourself', 'yourselves', 
    'he', 'him', 'his', 'himself', 'she', "she's", 'her', 'hers', 'herself', 
    'it', "it's", 'its', 'itself', 'they', 'them', 'their', 'theirs', 'themselves', 
    'what', 'which', 'who', 'whom', 'this', 'that', "that'll", 'these', 'those', 
    'am', 'is', 'are', 'was', 'were', 'be', 'been', 'being', 'have', 'has', 'had', 
    'having', 'do', 'does', 'did', 'doing', 'a', 'an', 'the', 'and', 'but', 'if', 
    'or', 'because', 'as', 'until', 'while', 'of', 'at', 'by', 'for', 'with', 
    'about', 'against', 'between', 'into', 'through', 'during', 'before', 'after', 
    'above', 'below', 'to', 'from', 'up', 'down', 'in', 'out', 'on', 'off', 'over', 
    'under', 'again', 'further', 'then', 'once', 'here', 'there', 'when', 'where', 
    'why', 'how', 'all', 'any', 'both', 'each', 'few', 'more', 'most', 'other', 
    'some', 'such', 'no', 'nor', 'not', 'only', 'own', 'same', 'so', 'than', 
    'too', 'very', 's', 't', 'can', 'will', 'just', 'don', "don't", 'should', 
    "should've", 'now', 'd', 'll', 'm', 'o', 're', 've', 'y', 'ain', 'aren', 
    "aren't", 'couldn', "couldn't", 'didn', 'doesn', "doesn't", 'hadn', "hadn't", 
    'hasn', "hasn't", 'haven', "haven't", 'isn', "isn't", 'ma', 'mightn', "mightn't", 
    'mustn', "mustn't", 'needn', "needn't", 'shan', "shan't", 'shouldn', "shouldn't", 
    'wasn', "wasn't", 'weren', "weren't", 'won', "won't", 'wouldn', "wouldn't"
})

STATS_FILE = "crawl_stats.json"
# a word: a whole token of 2 to 30 lowercase letters; (?<!\w)/(?!\w) means no digit,
# an accent letters, or an underscore (base64 blobs, 'x41', 'café' and giant tokens are skipped)
WORD_PATTERN        = r"(?<!\w)[a-z]{2,30}(?!\w)"
IGNORED_HTML_TAGS   = ["script", "style", "template", "noscript", "svg", "canvas", "meta", "link", "iframe"
                        "nav"]
IGNORED_CLASS_TAGS  = ["navbar", "site-navigation", "global-header", "menu-container", "masthead", "site-footer", "site-header",
                        "mikio-footer", "post-related"]
REDIRECT_CODES      = [300, 301, 302, 307]
SAVE_EVERY          = 25
SIMHASH_MASK        = 0xFFFF
LSB_MASK            = 0x01
SET_BIT             = LSB_MASK
HASHVAL_LEN_BITS    = 16
PG_SIMILARITY_THRSH = 0.875


################ DATACLASS TYPES #################
@dataclass
class CrawlStats:
    longest_page: tuple[str, int] = ("", 0) # URL,  cnt of words
    unique_pages: set[str] = field(default_factory=set)
    most_common: dict[str, int] = field(default_factory=dict)       # key: word, value: cnt
    subdomains_of_ics: dict[str, int] = field(default_factory=dict) # key: subdomains of ics.uci.edu, value: unique pages in each
    simhashes: set[int] = field(default_factory=set)
    page_crcs: set[int] = field(default_factory=set)

@dataclass
class LinkInfo:
    url: str = "" 
    words: dict[str, int] = field(default_factory=dict)  # hashmap of words its count
    word_hashes: dict[str, int] = field (default_factory=dict)
    word_cnt: int = 0
    simhash: int = 0
    page_chksum: int = 0

    is_subdomain_of_ics: bool = False
    subdomain_name: str = ""
    page_name: str = ""


pages_since_save = 0


############################## CRAWL STATISTICS APIs ###########################

def save_crawl_stats(cs: CrawlStats, path: str = STATS_FILE) -> None:
    data = {
        # json has no tuples
        "longest_page": list(cs.longest_page),
        "unique_pages": sorted(cs.unique_pages),
        "most_common": cs.most_common,
        "subdomains_of_ics": cs.subdomains_of_ics,
        "simhashes": list(cs.simhashes),
        "crcs": list(cs.page_crcs)
    }
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)

def load_crawl_stats(path: str = STATS_FILE) -> CrawlStats:
    if not os.path.exists(path):
        return CrawlStats()
    with open(path) as f:
        data = json.load(f)
    return CrawlStats(
        longest_page=(data["longest_page"][0], data["longest_page"][1]),
        unique_pages=set(data["unique_pages"]),
        most_common=data["most_common"],
        subdomains_of_ics=data["subdomains_of_ics"],
        simhashes=data["simhashes"],
        page_crcs=data["crcs"]
    )


crawl_stats: CrawlStats = load_crawl_stats()

def _save_at_exit() -> None:
    if pages_since_save > 0:         # only save if this process actually crawled something
        save_crawl_stats(crawl_stats)

# this is for Ctrl + C
atexit.register(_save_at_exit)

allowed_domains = ("ics.uci.edu", "cs.uci.edu", "informatics.uci.edu", "stat.uci.edu")

# big file limit
MAX_CONTENT_BYTES = 5 * 1024 * 1024
LOW_INFO_MIN_WORDS = 50 # TODO: TUNE via check skipped pages in the log

# low info tuner help
os.makedirs("Logs", exist_ok=True)
low_info_logger = logging.getLogger("LOWINFO")
low_info_logger.setLevel(logging.INFO)
_low_info_handler = logging.FileHandler("Logs/LOWINFO.log", delay=True)
_low_info_handler.setFormatter(logging.Formatter("%(asctime)s - %(message)s"))
low_info_logger.addHandler(_low_info_handler)
low_info_logger.propagate = False

crawler_logger = logging.getLogger("CRAWLER")
crawler_logger.setLevel(logging.INFO)
_crawler_handler = logging.FileHandler("Logs/CRAWLER.log", delay=True)
_crawler_handler.setFormatter(logging.Formatter("%(asctime)s - %(message)s"))
crawler_logger.addHandler(_crawler_handler)
crawler_logger.propagate = False

def print_report(cs: CrawlStats) -> None:
    print("1. Unique pages:", len(cs.unique_pages))
    url, count = cs.longest_page
    print("2. Longest page:", url, "(" + str(count) + " words)")
    print("3. Top 50 words:")
    # highest count first trick
    top = sorted(cs.most_common.items(), key=lambda item: (-item[1], item[0]))[:50]
    for word, cnt in top:
        print(f" {word}, {cnt}")
    print("4. ics.uci.edu subdomains:", len(cs.subdomains_of_ics))
    for host in sorted(cs.subdomains_of_ics):
        print(f"   http://{host}, {cs.subdomains_of_ics[host]}")


############################## SCRAPER APIs ###########################

def scraper(url, resp: Response):
    # safety net for a bad page shouldnt kill the worker thread and ruin the crawl
    try:
        return _scrape_page(url, resp)
    except Exception:
        traceback.print_exc() # still shows the error but skip page
        return []

def _scrape_page(url, resp: Response) -> list[str]:
    # global so that it persists between scraper calls to know when to save
    global pages_since_save

    redir = check_redirect(resp)
    if redir != "":
        crawler_logger.info(f"Redirecting to {redir} from {url}")
        return [redir]

    if not is_valid_response(resp):
        return list()

    ret, page_info = extract_information(url, resp)
    if not ret:
        return list()

    links = extract_next_links(url, resp)
    valid_links = [link for link in links if is_valid(link)]

    # a page with no words at all has nothing to record, but its links are still followed
    if page_info.word_cnt == 0:
        low_info_logger.info(f"0 words, {len(valid_links)} links followed: {url}")
        return valid_links
    # every downloaded html page counts as a unique page
    update_crawl_stats(page_info, crawl_stats)
    pages_since_save += 1
    if pages_since_save >= SAVE_EVERY:
        save_crawl_stats(crawl_stats)
        pages_since_save = 0
    # low-info pages are both logged and counted to tune the threshold
    if page_info.word_cnt < LOW_INFO_MIN_WORDS:
        low_info_logger.info(f"{page_info.word_cnt} words, {len(valid_links)} links followed: {url} | {list(page_info.words)[:12]}")
    else:
        unique_words = len(page_info.words)
        if page_info.word_cnt > 200 and unique_words / page_info.word_cnt < 0.08:
            low_info_logger.info(f"repetitive? {unique_words}/{page_info.word_cnt} unique: {url}")
    return valid_links

def check_redirect(resp: Response) -> str:
    redir_url = ""
    if (resp.status in REDIRECT_CODES):
        redir_url, _ = urldefrag(resp.url)
    return redir_url

def is_valid_response(resp: Response) -> bool:
    # invalid status check
    if resp.status != 200: return False
    # if there is no page object as all
    if resp.raw_response is None: return False
    # empty body = dead URL
    if not resp.raw_response.content: return False
    # very large file so skip it
    if len(resp.raw_response.content) > MAX_CONTENT_BYTES: return False
    # only parse html/text, not binary files (pptx, pdf, images) gives us garbage words
    content_type = str(getattr(resp.raw_response, "headers", {}).get("Content-Type", "")).lower()
    if content_type and "html" not in content_type:
        return False
    
    return True


def extract_next_links(url, resp: Response) -> list[str]:
    """
    url: the URL that was used to get the page
    resp.url: the actual url of the page
    resp.status: the status code returned by the server. 200 is OK, you got the page. Other numbers mean that there was some kind of problem.
    resp.error: when status is not 200, you can check the error here, if needed.
    resp.raw_response: this is where the page actually is. More specifically, the raw_response has two parts:
            resp.raw_response.url: the url, again
            resp.raw_response.content: the content of the page!
    # Return a tuple containing a boolean indicating whether page response is valid and a 
    # list with the hyperlinks (as strings) scrapped from resp.raw_response.content 
    """
    # builds searchable structr.
    soup: BeautifulSoup = BeautifulSoup(resp.raw_response.content, "lxml")
    links = []

    # find all links <a> tag, with an href, skips any without one.
    for tag in soup.find_all("a", href=True):
        # use tag["href"] here to read the attribute.
        # remove space and new lines via strip.
        href = tag["href"].strip()

        if not href:
            continue

        try:
            absolute = urljoin(resp.raw_response.url, href) # from relative to absolute
            absolute, _ = urldefrag(absolute) # removes the fragment #
        except ValueError:
            continue # fix http://[YOUR_IP]/ crash

        if not absolute.isascii():
            continue

        links.append(absolute)

    return links


def is_valid(url: str) -> str:
    # Decide whether to crawl this url or not. 
    # If you decide to crawl it, return True; otherwise return False.
    # There are already some conditions that return False.
    try:
        # TODO: TUNE this number 200
        if len(url) > 200:
            return False

        # plain ascii garbage detector
        if not url.isascii() or any(c <= " " or c in '<>"' for c in url):
            return False

        parsed = urlparse(url)
        if parsed.scheme not in set(["http", "https"]):
            return False

        # get hostname, or dont if invalid
        host = parsed.hostname if parsed.hostname else ""
        if not any(host == d or host.endswith("." + d) for d in allowed_domains):
            return False
        
        params = parse_qs(parsed.query, keep_blank_values=True)
        # from/precision/format key words sourced from Trac wiki at grape.ics...
        # it makes one URL per timestamp, and ?format=txt is copy of a page
        for name in ["do", "idx", "sectok", "rev", "skin", "action", "from", "precision", "format"]:
            if name in params:
                return False

        # trap searcher for self appending parameters
        for name in params:
            if len(params[name]) > 1:
                return False

        # faceted filter trap  seen in the a log at ics.uci.edu/happening/news/?filter[...]=N
        for name in params:
            if name.startswith("filter"):
                return False

        # trac wiki pages with no useful text from grape sub domain (timeline, login, prefs, attachments)
        path = parsed.path.lower()
        for part in ["/timeline", "/login", "/prefs", "/attachment/", "/raw-attachment/"]:
            if part in path:
                return False

        # helpdesk.ics.uci.edu/Ticket/Display.html?id=N patch out
        if "/ticket/" in path:
            return False

        # Apache directory listings out same pages, but with different sortings so we filter it
        if re.fullmatch(r"[CO]=[A-Za-z](?:[;&][CO]=[A-Za-z])?", parsed.query):
            return False

        # photo galleries: ics.uci.edu/~eppstein/pix was around 12,000 caption pages
        # about 25 words each and 44% of all requests in a full crawl
        if "/~eppstein/pix" in path:
            return False

        # PowerPoint to HTML slide exports in the form .../sld019.htm
        # ex. ~hziv/ooad, ~pazzani/Slides, ~peymano/dynamic-arch, ~irus/twist
        if re.search(r"/sld\d+\.html?$", path):
            return False

        # calendar traps: a path segment that is a date like 2024-05 or 2024-05-12, seen at
        # wics/isg/ics.uci.edu /events/month/2026-03/, /events/tag/talk/day/2022-10-28, /events/1980-01
        # (blog posts use slashes, /2026/03/04/slug, so they are not affected)
        for segment in parsed.path.split("/"):
            if re.match(r"^\d{4}-\d{2}(-\d{2})?$", segment):
                return False

        pieces = parsed.path.split("/")  # everything after e.g. www.ics.uci.edu/
        counts = {}                      # empty dictionary that is word : how many times seen
        for piece in pieces:
            if piece == "":              # skip empty
                continue
            if piece in counts:
                counts[piece] = counts[piece] + 1
            else:
                counts[piece] = 1

        # reject if any word shows up 3 or more times
        for piece in counts:
            if counts[piece] >= 3: # TODO: 3 TO BE TUNED 
                return False

        return not re.match(
            r".*\.(css|js|bmp|gif|jpe?g|ico"
            + r"|png|tiff?|mid|mp2|mp3|mp4"
            + r"|wav|avi|mov|mpeg|ram|m4v|mkv|ogg|ogv|pdf"
            + r"|ps|eps|tex|ppt|pptx|doc|docx|xls|xlsx|names"
            + r"|data|dat|exe|bz2|tar|msi|bin|7z|psd|dmg|iso"
            + r"|epub|dll|cnf|tgz|sha1"
            + r"|thmx|mso|arff|rtf|jar|csv"
            + r"|ppsx|pps|pptm|docm|xlsm|odt|ods|odp|war"
            + r"|txt|h|hpp|c|cc|cpp|cxx|java|py|pl|sh|bib|ff|sql|json|xml|db|scm|lif|m|dsw|mht"
            + r"|rm|smil|wmv|swf|wma|zip|rar|gz)$", parsed.path.lower())

    except TypeError:
        print ("TypeError for ", parsed)
        raise
    except ValueError:
        return False # un parsable url like: http://[YOUR_IP]/ raises this


def extract_information(url: str, resp: Response) -> tuple[bool, LinkInfo]:
    """
    This function takes in a page URL as a string and parses the page's 
    HTML contents for relevant content. This includes: total word count 
    (excluding stop words), hashmap of unique words + their ocurrences, 
    whether or not this page is in a subdomain of ics.uci.edu, etc.
    Refer to the LinkInfo dataclass for more information.

    resp: string representation of a valid page URL to be parsed
    returns: LinkInfo dataclass with relevant page information
    """
    page_info: LinkInfo = LinkInfo(url=url)
    # parse subdomain and page name
    parsed_url: ParseResult = urlparse(url)
    hostname: str = parsed_url.hostname
    domain_match: str = ""

    # gets the allowed domain suffix, might be redundant but keeping for now
    for domain in allowed_domains:
        # first if counts ics without the . as its own subdomain.
        if (hostname == domain or hostname.endswith(f'.{domain}')):
            domain_match = domain
            break
    # saves hostname if the suffix is ics.uci.edu
    if domain_match == allowed_domains[0]:
        page_info.is_subdomain_of_ics = True
        page_info.subdomain_name = hostname
        page_info.page_name = parsed_url.path # keep the entire path so we can check for uniqueness later

    html_content = getattr(resp.raw_response, "text", resp.raw_response.content)
    # parse the actual HTML for words
    soup: BeautifulSoup = BeautifulSoup(html_content, 'lxml')
    
    # remove all non-information, data-heavy tags like style, script, img, svg, etc.
    for hidden in soup(IGNORED_HTML_TAGS):
        hidden.decompose()
    
    for global_elem in soup(class_=IGNORED_CLASS_TAGS):
        global_elem.decompose()

    # add space between text in tags <p>a</p><a>b</a> -> "a b" instead of "ab"
    visible_text: str = soup.get_text(" ") # should be stripped of all tags, only text visible on page
    words: list[str] = re.findall(WORD_PATTERN, visible_text.lower())

    full_text: str = " ".join(words)
    page_info.page_chksum = hashlib.md5(full_text.encode('utf-8')).hexdigest()
    if (page_info.page_chksum in crawl_stats.page_crcs):
        low_info_logger.info(f"Page matches previously seen page exactly: {page_info.url}")
        return (False, page_info)

    for word in words:
        if word not in STOP_WORDS:
            if (word not in page_info.word_hashes.keys()):
                hash_16: int = fnv_hash_16(word)
                page_info.word_hashes[word] = hash_16

            page_info.words[word] = page_info.words.get(word, 0) + 1
            page_info.word_cnt += 1
    
    page_info.simhash: int = compute_simhash(page_info.word_hashes, page_info.words)
    if (is_similar_page(page_info.simhash)):
        low_info_logger.info(f"Page similarity exceeds {PG_SIMILARITY_THRSH * 100}% threshold: {page_info.url}")
        return (False, page_info)

    return (True, page_info)


def update_crawl_stats(link_stats: LinkInfo, cs: CrawlStats) -> None:
    """
    This function takes the link_stats gathered from the crawl
    and compiles the crawl stats that we are intereseted in. 
    E.g. number of unique pages, longest page, top 50 common
    words, subdomains of ics.uci.edu.

    link_stats: an array of LinkInfo dataclass types for each crawled
    URL
    cs: 
    returns: a CrawlStats dataclass type with all relevant information
    listed above
    """
    # after a crash or resume the page that was in progress is processed again
    if link_stats.url in cs.unique_pages:
        return
    # update words and frequencies across all pages
    for word, cnt in link_stats.words.items():
        cs.most_common[word] = cs.most_common.get(word, 0) + cnt
    # update longest page in terms of words
    if cs.longest_page[1] < link_stats.word_cnt:
        cs.longest_page = (link_stats.url, link_stats.word_cnt)
    # update set of unique pages
    cs.unique_pages.add(link_stats.url) # ignores duplicates (determined solely by URL minus fragment)
    # udpate ics.uci.edu subdomains + page cnt (currently assumes each page is unique based on is_valid function)
    if (link_stats.is_subdomain_of_ics):
        cs.subdomains_of_ics[link_stats.subdomain_name] = cs.subdomains_of_ics.get(link_stats.subdomain_name, 0) + 1
    cs.simhashes.add(link_stats.simhash)


############################## PAGE SIMILARITY APIs ###########################

def is_similar_page(page_hash: int) -> bool:
    for simhash in crawl_stats.simhashes:
        matching_bits: int = ~(page_hash ^ simhash) & 0xFFFF
        similarity: float = bin(matching_bits).count("1") / HASHVAL_LEN_BITS

        if similarity > PG_SIMILARITY_THRSH:
            return True
    return False

def compute_simhash(word_hashes: dict[str, int], word_weights: dict[str, int]) -> int:
    vec_sums = [0] * HASHVAL_LEN_BITS # this vector will hold the sum for each bit in the simhash
    for word, hash_val in word_hashes.items():
        for i in range(HASHVAL_LEN_BITS):
            bit = (hash_val >> i) & LSB_MASK
            if bit == 1:
                vec_sums[i] += word_weights[word]
            else:
                vec_sums[i] -= word_weights[word]
    simhash_val: int = 0x0
    for i in range(HASHVAL_LEN_BITS):
        if vec_sums[i] > 0:
            simhash_val |= (SET_BIT << i)
        else:
            simhash_val &= ~(SET_BIT << i)
    
    return simhash_val & 0xFFFF


def fnv_hash_16(data: str) -> int:
    """
    Computes a 32-bit FNV-1a hash and XOR-folds it into 16 bits.
    """
    fnv_prime = 16777619
    hash_value = 2166136261  # 32-bit offset basis

    for byte in data.encode():
        hash_value ^= byte
        hash_value = (hash_value * fnv_prime) & 0xFFFFFFFF

    # XOR-fold the 32-bit hash into 16 bits
    return (hash_value & 0xFFFF) ^ (hash_value >> 16)
