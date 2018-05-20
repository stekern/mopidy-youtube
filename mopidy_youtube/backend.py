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

class YouTubeBackend(pykka.ThreadingActor, backend.Backend):
    def __init__(self, config, audio):
        super(YouTubeBackend, self).__init__()
        self.config = config
        self.library = YouTubeLibraryProvider(backend=self)
        self.playback = YouTubePlaybackProvider(audio=audio, backend=self)

        self.uri_schemes = ['youtube', 'yt']


class YouTubeLibraryProvider(backend.LibraryProvider):
    def lookup(self, uri=None):
        logger.debug("Performing lookup for '%s'", uri)
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
                regex = r'<a href="/watch\?v=(?P<id>.{11})" class=".*?" data-sessionlink=".*?"  title="(?P<title>.+?)" .+?Duration: (?P<duration>[0-9]+:[0-9]{2}).</span>.*?<div class="yt-lockup-description[^>]*>(?P<description>.*?)</div>'
                trackList = []
                for match in re.finditer(regex, r.text):
                    track = Track(
                        name=match.group('title'),
                        comment=match.group('description'),
                        length=1000,
                        bitrate=1, #fake bitrate
                        album=Album(
                            name='YouTube',
                            images=[]#no images
                        ),
                        uri="yt:https://www.youtube.com/watch?v=%s" % match.group('id')
                    )
                    trackList.append(track)
                    logger.debug("Found '%s'", track.uri)

            except Exception as e:
                logger.error("Error when searching in youtube: %s", repr(e))
                return None

            if len(trackList) == 0:
                logger.info("Searching YouTube for query '%s', nothing found", search_query)
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
