import re
from urllib.parse import urlparse, urljoin, urldefrag
from dataclasses import dataclass
from bs4 import BeautifulSoup

@dataclass
class CrawlStats:
    longest_page: str
    unique_pages: int
    most_common: dict[str, int] # key: word, value: cnt
    subdomains_of_ics: dict[str, int] # key: subdomains of ics.uci.edu, value: unique pages in each


@dataclass
class LinkInfo:
    url: str                    # page URL
    words: dict[str, int]       # hashmap of words and count of each word
    word_cnt: int

    is_subdomain_of_ics: bool   # is this URL a subdomain of ics.uci.edu ?
    subdomain_name: str         # name of the subdomain of ics.uci.edu (only valid if is_subdomain_of_ics)
    page_name: str              # name of page in subdomain of ics.uci.edu (only valid if is_subdomain_of_ics)


def scraper(url, resp):

    links = extract_next_links(url, resp)
    valid_links = [link for link in links if is_valid(link)]

    link_stats: list[LinkInfo] = []
    for link in valid_links:
        link_info: LinkInfo = extract_information(link)
        link_stats.append(link_info)
    crawl_stats: CrawlStats = get_crawl_stats(link_stats)

    return valid_links

def extract_next_links(url, resp):
    """
    url: the URL that was used to get the page
    resp.url: the actual url of the page
    resp.status: the status code returned by the server. 200 is OK, you got the page. Other numbers mean that there was some kind of problem.
    resp.error: when status is not 200, you can check the error here, if needed.
    resp.raw_response: this is where the page actually is. More specifically, the raw_response has two parts:
            resp.raw_response.url: the url, again
            resp.raw_response.content: the content of the page!
    # Return a list with the hyperlinks (as strings) scrapped from resp.raw_response.content
    """
    # invalid status check
    if resp.status != 200: return list()
    # if there is no page object as all
    if resp.raw_response is None: return list()
    # empty body = dead URL
    if not resp.raw_response.content: return list()

    # builds searchable structr.
    soup = BeautifulSoup(resp.raw_response.content, "lxml")
    links = []

    # find all links <a> tag, with an href, skips any without one.
    for tag in soup.find_all("a", href=True):
        # use tag["href"] here to read the attribute.
        # remove space and new lines via strip.
        href = tag["href"].strip()

        if not href:
            continue

        absolute = urljoin(resp.raw_response.url, href) # from relative to absolute
        absolute, _ = urldefrag(absolute) # removes the fragment #

        if not absolute.isascii():
            continue

        links.append(absolute)

    return links


def is_valid(url):
    # Decide whether to crawl this url or not. 
    # If you decide to crawl it, return True; otherwise return False.
    # There are already some conditions that return False.
    try:
        # TODO: TUNE this number 200
        if len(url) > 200:
            return False

        parsed = urlparse(url)
        if parsed.scheme not in set(["http", "https"]):
            return False

        # get hostname, or dont if invalid
        host = parsed.hostname if parsed.hostname else ""
        allowed = ("ics.uci.edu", "cs.uci.edu", "informatics.uci.edu", "stat.uci.edu")
        if not any(host == d or host.endswith("." + d) for d in allowed):
            return False
        
        pieces = parsed.path.split("/") # everything after e.g. www.ics.uci.edu/ 
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

        lowered = url.lower()

        # general calendar keywords, not valid if calendar.
        for word in ["calendar", "ical=", "tribe-bar-date", "eventdisplay"]:
            if word in lowered:
                return False

        return not re.match(
            r".*\.(css|js|bmp|gif|jpe?g|ico"
            + r"|png|tiff?|mid|mp2|mp3|mp4"
            + r"|wav|avi|mov|mpeg|ram|m4v|mkv|ogg|ogv|pdf"
            + r"|ps|eps|tex|ppt|pptx|doc|docx|xls|xlsx|names"
            + r"|data|dat|exe|bz2|tar|msi|bin|7z|psd|dmg|iso"
            + r"|epub|dll|cnf|tgz|sha1"
            + r"|thmx|mso|arff|rtf|jar|csv"
            + r"|rm|smil|wmv|swf|wma|zip|rar|gz)$", parsed.path.lower())

    except TypeError:
        print ("TypeError for ", parsed)
        raise



def extract_information(link: str) -> LinkInfo:
    """
    This function takes in a page URL as a string and parses the page's 
    HTML contents for relevant content. This includes: total word count 
    (excluding stop words), hashmap of unique words + their ocurrences, 
    whether or not this page is in a subdomain of ics.uci.edu, etc.
    Refer to the LinkInfo dataclass for more information.

    link: string representation of a valid page URL to be parsed
    returns: LinkInfo dataclass with relevant page information
    """


def get_crawl_stats(link_stats: list[LinkInfo]) -> CrawlStats:
    """
    This function takes the link_stats gathered from the crawl
    and compiles the crawl stats that we are intereseted in. 
    E.g. number of unique pages, longest page, top 50 common
    words, subdomains of ics.uci.edu.

    link_stats: an array of LinkInfo dataclass types for each crawled
    URL
    returns: a CrawStats dataclass type with all relevant information
    listed above
    """