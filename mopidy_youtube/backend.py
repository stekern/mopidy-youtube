# -*- coding: utf-8 -*-

from __future__ import unicode_literals

from itertools import chain
from multiprocessing.pool import ThreadPool

from mopidy import backend
from mopidy.models import Album, SearchResult, Track

import pykka
import requests
import re
import youtube_dl

from mopidy_youtube import logger

video_uri_prefix = 'youtube:video'
search_uri = 'youtube:search'

def list_unique(seq, idfun=None): 
   # https://www.peterbe.com/plog/uniqifiers-benchmark
   if idfun is None:
       def idfun(x): return x
   seen = {}
   result = []
   for item in seq:
       marker = idfun(item)
       if marker in seen: continue
       seen[marker] = 1
       result.append(item)
   return result

class YouTubeBackend(pykka.ThreadingActor, backend.Backend):
    def __init__(self, config, audio):
        super(YouTubeBackend, self).__init__()
        self.config = config
        self.library = YouTubeLibraryProvider(backend=self)
        self.playback = YouTubePlaybackProvider(audio=audio, backend=self)

        self.uri_schemes = ['youtube', 'yt']


class YouTubeLibraryProvider(backend.LibraryProvider):
    def lookup(self, uri=None):
        if uri.startswith('yt:'):
            uri = uri[len('yt:'):]
        elif uri.startswith('youtube:'):
            uri = uri[len('youtube:'):]

        ytOpts = {
                  'format': 'bestaudio/best'
        }
        with youtube_dl.YoutubeDL(ytOpts) as ydl:
            ytUri = ydl.extract_info(
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
                album=Album(
                    name='YouTube',
                    images=[img['url'] for img in video['thumbnails']]
                ),
                uri="yt:" + video['webpage_url']
            )

            result.append(track)
            logger.debug("Found video '%s'", track.uri)

        return result

    def search(self, query=None, uris=None, exact=False):
        # TODO Support exact search

        if not query:
            return None

        if 'uri' in query:
            search_query = ''.join(query['uri'])
            trackList = self.lookup(search_query)

        else:
            search_query = ' '.join(query.values()[0])
            logger.debug("Searching YouTube for query '%s'", search_query)

            try:
                r = requests.get("https://www.youtube.com/results", params={"search_query": search_query})
                videoIds = re.findall(r'href=\"\/watch\?v=(.{11})', r.text)
                videoIds = list_unique(videoIds)
                logger.debug("Found the following IDs '%s'", videoIds)
            except Exception as e:
                logger.error("Error when searching in youtube: %s", repr(e))
                return None

            if len(videoIds) > 0:
                resolve_pool = ThreadPool(processes=min(16, len(videoIds)))
                trackList = resolve_pool.map(self.lookup, videoIds)
                resolve_pool.close()

                trackList = list(chain.from_iterable(trackList))
            else:
                logger.info("Searching YouTube for query '%s', nothing found",
                            search_query)
                return None

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

        ytOpts = {
            'format': 'bestaudio/best'
        }
        with youtube_dl.YoutubeDL(ytOpts) as ydl:
            ytInfo = ydl.extract_info(
                url=uri,
                download=False
            )

            if 'url' in ytInfo:
                logger.debug("URL '%s'", ytInfo['url'])
                return ytInfo['url']
            else:
                logger.debug("URL: None")
                return None
