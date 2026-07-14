"""Curated list of NYC event source URLs, tagged by category.

Edit freely -- add/remove sources as you learn which ones extract cleanly
via Claude's WebFetch tool (JS-heavy sites often return nothing useful).
"""

SOURCES = [
    {
        "name": "Songkick NYC",
        "url": "https://www.songkick.com/metro-areas/7644-us-ny-new-york",
        "category": "concerts",
    },
    {
        "name": "The Skint",
        "url": "https://theskint.com/",
        "category": "free/community",
    },
    {
        "name": "NYC For Free",
        "url": "https://www.nycforfree.co/events",
        "category": "free/community",
    },
    {
        "name": "Meetup NYC Tech",
        "url": "https://www.meetup.com/find/?location=us--ny--new-york&categoryId=546",
        "category": "tech/professional",
    },
    {
        "name": "Time Out New York",
        "url": "https://www.timeout.com/newyork/things-to-do",
        "category": "arts/culture",
    },
    {
        "name": "Eventbrite NYC",
        "url": "https://www.eventbrite.com/d/ny--new-york/events/",
        "category": "general",
    },
]
