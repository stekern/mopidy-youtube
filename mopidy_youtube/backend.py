# -*- coding: utf-8 -*-

from __future__ import unicode_literals

from itertools import chain
from multiprocessing.pool import ThreadPool
from collections import OrderedDict

from mopidy import backend
from mopidy.models import Album, SearchResult, Track, Artist

import pykka
import requests
import re
import youtube_dl

import HTMLParser

from mopidy_youtube import logger

video_uri_prefix = 'youtube:video'
search_uri = 'youtube:search'

# https://stackoverflow.com/a/2437645
class LimitedSizeDict(OrderedDict):
  def __init__(self, *args, **kwds):
    self.size_limit = kwds.pop("size_limit", None)
    OrderedDict.__init__(self, *args, **kwds)
    self._check_size_limit()

  def __setitem__(self, key, value):
    OrderedDict.__setitem__(self, key, value)
    self._check_size_limit()

  def _check_size_limit(self):
    if self.size_limit is not None:
      while len(self) > self.size_limit:
        self.popitem(last=False)

# https://stackoverflow.com/a/6798042
class Singleton(type):
    _instances = {}
    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super(Singleton, cls).__call__(*args, **kwargs)
        return cls._instances[cls]

class YDLCache(object):
    __metaclass__ = Singleton

    def __init__(self):
        self._cache = LimitedSizeDict(size_limit=1000)

    def extract_info(self, url, download=True, ie_key=None, extra_info={}, process=True, force_generic_extractor=False):
        if url in self._cache:
            logger.debug("Found in cache: '%s'", url)
            return self._cache[url]
        else:
            ytOpts = {
                      'format': 'bestaudio/best'
            }
            with youtube_dl.YoutubeDL(ytOpts) as ydl:
                logger.debug("Not found in cache, calling extract_info(): '%s'", url)
                self._cache[url] = ydl.extract_info(url, download=download, ie_key=ie_key, extra_info=extra_info, process=process, force_generic_extractor=force_generic_extractor)
                return self._cache[url]

class YouTubeBackend(pykka.ThreadingActor, backend.Backend):
    def __init__(self, config, audio):
        super(YouTubeBackend, self).__init__()
        self.config = config
        self.library = YouTubeLibraryProvider(backend=self)
        self.playback = YouTubePlaybackProvider(audio=audio, backend=self)

        self.uri_schemes = ['youtube', 'yt']


class YouTubeLibraryProvider(backend.LibraryProvider):
    PARSER = HTMLParser.HTMLParser()

    def _unescape(self, text):
        return self.PARSER.unescape(text)

    def lookup(self, uri=None):
        logger.debug("Performing lookup for '%s'", uri)
        if uri.startswith('yt:'):
            uri = uri[len('yt:'):]
        elif uri.startswith('youtube:'):
            uri = uri[len('youtube:'):]

        ytUri = YDLCache().extract_info(
            url=uri,
            download=False
        )

        if 'entries' in ytUri:  # if playlist
            videoList = ytUri['entries']
        else:
            videoList = [ytUri]

        result = []
        for video in videoList:
            track = Track(
                name=video['title'],
                comment=video['description'],
                length=video['duration'] * 1000,
                bitrate=video['abr'],
                artists=[Artist(
                    name=video['uploader'], 
#                   uri='https://www.youtube.com/channel/' + video['uploader_id'],
                )],
                album=Album(
                    name='YouTube',
                    images=[img['url'] for img in video['thumbnails']]
                ),
                uri="yt:" + video['webpage_url']
            )
            result.append(track)

        return result

    def _fetch_results(self, obj):
        r = requests.get(obj["url"], params=obj['params'], headers=obj["headers"])
        regex = r'<a href="/watch\?v=(?P<id>.{11})" class=".*?" data-sessionlink=".*?"  title="(?P<title>.+?)" .+?Duration: (?:(?P<durationHours>[0-9]+):)?(?P<durationMinutes>[0-9]+):(?P<durationSeconds>[0-9]{2}).?</span>.*?<a href="(?P<uploaderUrl>/(?:user|channel)/[^"]+)"[^>]+>(?P<uploader>.*?)</a>.*?<div class="yt-lockup-description[^>]*>(?P<description>.*?)</div>'
        trackList = []
        for match in re.finditer(regex, self._unescape(r.text)):
            length = int(match.group('durationSeconds')) * 1000
            length += int(match.group('durationMinutes')) * 60 * 1000
            if match.group('durationHours') != None:
                length += (int(match.group('durationHours'))) * 60 * 60 * 1000
            track = Track(
                name=match.group('title'),
                comment=match.group('description'),
                length=length,
                artists=[Artist(
                    name=match.group("uploader"),
    #                            uri='https://www.youtube.com/channel/' + match.group('uploaderUrl')
                    )],
                    album=Album(
                        name='YouTube'
                    ),
                uri="yt:https://www.youtube.com/watch?v=%s" % match.group('id')
            )
            trackList.append(track)
            logger.debug("Found '%s'", track.name)
        return trackList


    def search(self, query=None, uris=None, exact=False, pages=3):
        if not query:
            return None

        if 'uri' in query:
            search_query = ''.join(query['uri'])
            trackList = self.lookup(search_query)

        else:
            search_query = ' '.join(query.values()[0])
            if search_query.startswith("https://www.youtube.com/watch?v=") or search_query.startswith("https://youtu.be/"):
                trackList = self.lookup(search_query)
            else:
                logger.info("Searching YouTube for query '%s' and fetching %d pages of results", search_query, pages)

                try:
                    headers = {"Accept-Language": "en-US,en;q=0.5"}
                    rs = [{ "url": "https://www.youtube.com/results", "params": { "search_query": search_query, "page": page + 1}, "headers": headers } for page in range(pages)]
                    results = ThreadPool(pages).imap(self._fetch_results, rs)
                    trackList = []
                    for result in results:
                        trackList.extend(result)

                except Exception as e:
                    logger.error("Error when searching in youtube: %s", repr(e))
                    return None

                if len(trackList) == 0:
                    logger.info("Searching YouTube for query '%s', nothing found", search_query)

        return SearchResult(
            uri=search_uri,
            tracks=trackList
        )


class YouTubePlaybackProvider(backend.PlaybackProvider):
    def translate_uri(self, uri):
        logger.debug("Translating uri '%s'", uri)
        if uri.startswith('yt:'):
            uri = uri[len('yt:'):]
        elif uri.startswith('youtube:'):
            uri = uri[len('youtube:'):]

        ytInfo = YDLCache().extract_info(
            url=uri,
            download=False
        )

        if 'url' in ytInfo:
            logger.debug("URL '%s'", ytInfo['url'])
            return ytInfo['url']
        else:
            logger.debug("URL: None")
            return None
