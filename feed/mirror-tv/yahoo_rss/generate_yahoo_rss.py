# Imports the Google Cloud client library
from google.cloud import storage
from gql import gql, Client
from gql.transport.requests import RequestsHTTPTransport

from feedgen.feed import FeedGenerator
from feedgen import util

from dateutil import parser, tz
from datetime import datetime, timedelta, timezone

import hashlib
import gzip
from json import JSONDecoder

__gql_transport__ = RequestsHTTPTransport(
    url='http://mirror-tv-graphql.default.svc.cluster.local/admin/api',
    use_json=True,
    headers={
        'Content-type': 'application/json',
    },
    verify=True,
    retries=3,
)

__gql_client__ = Client(
    transport=__gql_transport__,
    fetch_schema_from_transport=True,
)

__seven_days_ago__ = datetime.now(timezone.utc) - timedelta(days=7)

# To retrieve the latest post published after a specified day
__qgl_post_template__ = '''
{
    allPosts(where: {source: "tv", state: published}, sortBy: publishTime_DESC, first: 75) {
        name
        slug
        brief
        contentHtml
        heroImage {
            urlOriginal
            name
        }
        categories {
            name
            slug
        }
        relatedPosts {
            name
            slug
        }
        writers {
            name
        }
        publishTime
        updatedAt
    }
}
'''

__gql_query__ = gql(__qgl_post_template__)
__result__ = __gql_client__.execute(__gql_query__)

# the timezone for rss
__timezone__ = tz.gettz("Asia/Taipei")

fg = FeedGenerator()
fg.load_extension('media', atom=False, rss=True)
fg.load_extension('dc', atom=False, rss=True)
# TODO
fg.title('鏡新聞 Yahoo Title')
# TODO
fg.description('鏡新聞 Yahoo Description')
# TODO
fg.id('https://dev.mnews.tw')
# TODO
fg.pubDate(datetime.now(timezone.utc).astimezone(__timezone__))
# TODO
fg.updated(datetime.now(timezone.utc).astimezone(__timezone__))
# TODO
fg.image(url='https://dev.mnews.tw/logo.png',
         title='鏡新聞 Yahoo Title', link='https://dev.mnews.tw')
# TODO
fg.rights(rights='Copyright 2019-2020')
fg.link(href='https://dev.mnews.tw', rel='alternate')
fg.ttl(300)  # 5 minutes
fg.language('zh-TW')

__base_url__ = 'https://dev.mnews.tw/story/'

__json_decoder__ = JSONDecoder()

for item in __result__['allPosts']:
    guid = hashlib.sha224((__base_url__+item['slug']).encode()).hexdigest()
    fe = fg.add_entry(order='append')
    fe.id(guid)
    fe.title(item['name'])
    fe.link(href=__base_url__+item['slug'], rel='alternate')
    fe.guid(guid)
    fe.pubDate(util.formatRFC2822(
        parser.isoparse(item['publishTime']).astimezone(__timezone__)))
    fe.updated(util.formatRFC2822(
        parser.isoparse(item['updatedAt']).astimezone(__timezone__)))
    content = ''

    if item['brief'] is not None:
        brief = __json_decoder__.decode(item['brief'])['html']
        fe.description(description=brief, isSummary=True)
        content += brief
    if item['heroImage'] is not None:
        fe.media.content(
            content={'url': item['heroImage']['urlOriginal'], 'medium': 'image'}, group=None)
        content += '<img src="%s" alt="%s" />' % (
            item['heroImage']['urlOriginal'], item['heroImage']['name'])
    if item['contentHtml'] is not None:
        content += item['contentHtml']
    if len(item['relatedPosts']) > 0:
        content += '<br/><p class="read-more-vendor"><span>更多鏡週刊報導</span>'
        for related_post in item['relatedPosts'][:3]:
            content += '<br/><a href="%s">%s</a>' % (
                __base_url__+related_post['slug'], related_post['name'])
    fe.content(content=content, type='CDATA')
    fe.category(
        list(map(lambda c: {'term': c['name'], 'label': c['name']}, item['categories'])))
    if item['writers'] is not None:
        fe.dc.dc_creator(creator=list(
            map(lambda w: w['name'], item['writers'])))


def upload_data(bucket_name: str, data: bytes, content_type: str, destination_blob_name: str):
    '''Uploads a file to the bucket.'''
    # bucket_name = 'your-bucket-name'
    # data = 'storage-object-content'

    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(destination_blob_name)
    blob.content_encoding = 'gzip'
    blob.upload_from_string(
        data=gzip.compress(data=data, compresslevel=9), content_type=content_type, client=storage_client)
    blob.content_language = 'zh'
    blob.cache_control = 'max-age=300,public'
    blob.patch()


# Instantiates a client
__storage_client__ = storage.Client()

# The name for the new bucket
__bucket_name__ = 'static-mnews-tw-dev'

# rss folder path
__rss_base__ = 'rss'

upload_data(
    bucket_name=__bucket_name__,
    data=fg.rss_str(pretty=False, extensions=True,
                    encoding='UTF-8', xml_declaration=True),
    content_type='application/rss+xml',
    destination_blob_name=__rss_base__ +
    '/yahoo.xml'
)
