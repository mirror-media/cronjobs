from bson import json_util
from elasticsearch import Elasticsearch, NotFoundError
from gql import gql, Client
from gql.transport.aiohttp import AIOHTTPTransport
from mergedeep import merge, Strategy
from util import auth
import argparse
import datetime
import dateutil.parser
import json
import math
import re
import sys
import time
import yaml

# prepare instances
__es__ = None


def getAuthenticatedClient(gqlEndpoint: str, username: str, secret: str) -> Client:
    """set authentication cookie"""
    transport = AIOHTTPTransport(
        url=gqlEndpoint,
        headers={
            "Cookie": auth.getAuthenticationCookie(gqlEndpoint, username, secret)
        }
    )
    return Client(transport=transport, fetch_schema_from_transport=True)


__default_config__ = {
    "ELASTICSEARCH": {
        "ENDPOINT": "",
    },
    "GRAPHQL": {
        "ENDPOINT": "",
    },
    "SEARCHFEED": {
        "POSTS_INDEX": "",
        "META_INDEX": "",
        "UNIT_DAYS": 100,
        "SAVED_FIELDS": [],
    },
}


def main(option: dict = None, beforeDays: int = None):
    ''' Search-feed program starts here '''

    # merge option to the default options
    option = merge({}, __default_config__, option,
                   strategy=Strategy.TYPESAFE_REPLACE)

    # Crete es instance
    global __es__
    __es__ = Elasticsearch(option["ELASTICSEARCH"]["ENDPOINT"])

    # create search-feed indices if not exist

    createSearchFeedIndices(option)

    initDt = getLastUpdateDatetime(option, beforeDays)
    print("[SearchFeed] starts to update docs modified after `{dt}` to es at {current}:\n".format(
        dt=initDt, current=datetime.datetime.now()))

    client = getAuthenticatedClient(
        option["GRAPHQL"]["ENDPOINT"], option["GRAPHQL"]["USER"], option["GRAPHQL"]["SECRET"])

    if beforeDays is not None:
        total = 0
        for i in range(int(math.ceil(beforeDays/option["SEARCHFEED"]["UNIT_DAYS"]))):
            remainingDays = ((beforeDays - i * option["SEARCHFEED"]["UNIT_DAYS"]) % option["SEARCHFEED"]["UNIT_DAYS"],
                             option["SEARCHFEED"]["UNIT_DAYS"])[(beforeDays - i * option["SEARCHFEED"]["UNIT_DAYS"]) / option["SEARCHFEED"]["UNIT_DAYS"] >= 1]
            startDt = initDt + \
                datetime.timedelta(
                    days=(i * option["SEARCHFEED"]["UNIT_DAYS"]))
            endDt = startDt + datetime.timedelta(days=remainingDays)

            fetchedPosts = getPostsUpdatedBetween(client, startDt, endDt)
            processSearchFeed(fetchedPosts, option)
            total += len(fetchedPosts)
        printFinMessages(total)
    else:
        fetchedPosts = getPostsUpdatedBetween(client, initDt)
        processSearchFeed(fetchedPosts, option)
        printFinMessages(len(fetchedPosts))


def printFinMessages(fetchedPostsCount):
    print(
        "[SearchFeed] Search-feed done at {current}!\n".format(current=datetime.datetime.now()))
    print("[SearchFeed] {count} docs handled.\n".format(
        count=fetchedPostsCount))


def processSearchFeed(fetchedPosts, option):
    for post in fetchedPosts:
        cleanedPost = clean(post, option)
        updateElasticsearch(cleanedPost, option)
    if len(fetchedPosts) > 0:
        saveLastUpdateDatetime(dateutil.parser.isoparse(
            fetchedPosts[-1]["updatedAt"]), option)


def getPostsUpdatedBetween(client: Client, startDt, endDt=None):
    timeRange = "{{updatedAt_gt: \"{}\"}}".format(startDt.isoformat())
    if endDt:
        timeRange = timeRange + \
            ", {{updatedAt_lte: \"{}\"}}".format(endDt.isoformat())

    getScheduledItemsQuery = gql(
        """
        query {
            allPosts(where: %s ) {
                id
                slug
                name
                subtitle
                state
                publishTime
                categories {
                    name
                    ogTitle
                    ogDescription
                }
                writers {
                    name
                }
                photographers {
                    name
                }
                cameraOperators {
                    name
                }
                designers {
                    name
                }
                engineers {
                    name
                }
                vocals {
                    name
                }
                otherbyline
                heroVideo {
                    name
                    description
                }
                heroImage {
                    name
                    keywords
                    urlMobileSized
                }
                heroCaption
                style
                briefHtml
                contentHtml
                tags {
                    name
                    ogTitle
                    ogDescription
                }
                audio {
                    name
                }
                ogTitle
                ogDescription
                ogImage {
                    name
                    keywords
                }
                updatedAt
            }
        }
        """ % (timeRange)
    )
    return client.execute(getScheduledItemsQuery)["allPosts"]


def clean(post, option: dict = None):
    cleanedPost = {}
    _id = post["id"]
    state = post["state"]
    for field in option["SEARCHFEED"]["SAVED_FIELDS"]:
        try:
            cleanedPost[field] = post[field]
        except KeyError:
            if field == "topics":
                cleanedPost[field] = []
                continue
            print(
                f"[SearchFeed] id({_id}) post doesn't have field: {field}\n")

    return {"_id": _id, "state": state, "doc": cleanedPost}


def updateElasticsearch(cleanedPost, option: dict = None):
    _id = cleanedPost["_id"]
    state = cleanedPost["state"]
    doc = cleanedPost["doc"]
    name = doc["name"]

    if state == "published":
        __es__.update(index=option["SEARCHFEED"]["POSTS_INDEX"], doc_type="_doc", id=_id,
                      body={"doc": doc, "doc_as_upsert": True})
        print(
            "[SearchFeed] insert/update {id}: {name}\n".format(id=str(_id), name=name))
    else:
        __es__.delete(index=option["SEARCHFEED"]["POSTS_INDEX"],
                      doc_type="_doc", id=_id, ignore=[400, 404])
        print("[SearchFeed] delete {id}: {name}\n".format(
            id=str(_id), name=name))


def getLastUpdateDatetime(option: dict = None, beforeDays: int = None):
    try:
        if beforeDays is not None:
            print("[SearchFeed] recieved a time param. Will fetch posts started from `{beforeDays}` days ago!\n".format(
                beforeDays=beforeDays))
            return datetime.datetime.now() - datetime.timedelta(days=beforeDays)

        meta = __es__.get(index=option["SEARCHFEED"]
                          ["META_INDEX"], doc_type="_doc", id="meta")
        ts = int(meta['_source']['ts'])
        return datetime.datetime.fromtimestamp(ts / 1000) + datetime.timedelta(milliseconds=ts % 1000)
    except NotFoundError:
        return datetime.datetime.now() - datetime.timedelta(minutes=5)


def saveLastUpdateDatetime(dt, option: dict = None):
    milliseconds = int(time.mktime(dt.utctimetuple())
                       * 1000 + dt.microsecond / 1000.0)
    __es__.index(index=option["SEARCHFEED"]["META_INDEX"], doc_type="_doc",
                 id="meta", body={"ts": str(milliseconds)})


def createSearchFeedIndices(option: dict = None):
    __es__.indices.create(index=option["SEARCHFEED"]["POSTS_INDEX"], ignore=400, body={
        "mappings": {
            "_doc": {
                "properties": {
                    "publishTime": {
                        "type": "date",
                        "format": "yyyy-MM-dd HH:mm:ss||yyyy-MM-dd HH:mm:ss.SSSSSS",
                    }
                }
            }
        }
    })
    __es__.indices.create(index=option["SEARCHFEED"]["META_INDEX"], ignore=400)


# define some helpers for debug use
def pp(obj):
    print(json_util.dumps(obj, indent=2) + "\n")


if __name__ == '__main__':

    parser = argparse.ArgumentParser(
        description='Process configuration of esFeed')
    parser.add_argument("-c", "--config", dest="config",
                        help="config file for searchFeed", metavar="FILE")
    parser.add_argument("-b", "--before-days", dest="beforeDays",
                        help="start search from specific days before", metavar="N", type=int)

    args = parser.parse_args()

    with open(args.config, 'r') as stream:
        option = yaml.safe_load(stream)

    main(option, args.beforeDays)
