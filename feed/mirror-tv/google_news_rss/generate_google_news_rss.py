from gql import gql, Client
from gql.transport.requests import RequestsHTTPTransport

from feedgen.feed import FeedGenerator
from feedgen import util

from dateutil import parser, tz
from datetime import datetime, timezone

# Imports the Google Cloud client library
from google.cloud import storage

import gzip

__gql_transport__ = RequestsHTTPTransport(
    url='http://mirror-tv-graphql.default.svc.cluster.local/admin/api',
    use_json=True,
    headers={
        "Content-type": "application/json",
    },
    verify=True,
    retries=3,
)

__gql_client__ = Client(
    transport=__gql_transport__,
    fetch_schema_from_transport=True,
)

# To retrieve the latest 25 published posts for the specified category
__qgl_post_template__ = '''
{
    allPosts(where: {source: "tv", categories_some: {id: %d}, state: published}, sortBy: publishTime_DESC, first: 75) {
        name
        slug
        heroImage {
            urlOriginal
        }
        categories {
            name
            slug
        }
        publishTime
        updatedAt
    }
}

'''

__base_url__ = 'https://dev.mnews.tw/story/'

# Instantiates a client
__storage_client__ = storage.Client()


def upload_data(bucket_name: str, data: bytes, content_type: str, destination_blob_name: str):
    '''Uploads a file to the bucket.'''
    # bucket_name = 'your-bucket-name'
    # data = 'storage-object-content'

    bucket = __storage_client__.bucket(bucket_name)
    blob = bucket.blob(destination_blob_name)
    blob.content_encoding = 'gzip'
    blob.upload_from_string(
        data=gzip.compress(data=data, compresslevel=9), content_type=content_type, client=__storage_client__)
    blob.content_language = 'zh'
    blob.cache_control = 'max-age=300,public'
    blob.patch()


__categories__ = {
    1: {
        'slug': 'entertainment',
        'name': '娛樂',
    },
    2: {
        'slug': 'news',
        'name': '時事',
    },
    3: {
        'slug': 'life',
        'name': '生活',
    },
    4: {
        'slug': 'politics',
        'name': '政治',
    },
    5: {
        'slug': 'finance',
        'name': '財經',
    },
    6: {
        'slug': 'international',
        'name': '國際',
    },
    8: {
        'slug': 'person',
        'name': '人物',
    },
}

# The name for the new bucket
__bucket_name__ = "static-mnews-tw-dev"

# rss folder path
__rss_base__ = 'rss'

# the timezone for rss
__timezone__ = tz.gettz("Asia/Taipei")

for id, category in __categories__.items():
    query = gql(__qgl_post_template__ % id)
    result = __gql_client__.execute(query)

    fg = FeedGenerator()
    fg.load_extension('media', atom=False, rss=True)
    # TODO
    fg.title('鏡新聞 ' + category['name'] + ' Title')
    # TODO
    fg.description('鏡新聞 ' + category['name'] + ' Description')
    # TODO
    fg.id('https://dev.mnews.tw')
    # TODO
    fg.pubDate(datetime.now(timezone.utc).astimezone(__timezone__))
    # TODO
    fg.updated(datetime.now(timezone.utc).astimezone(__timezone__))
    # TODO
    fg.image(url='https://dev.mnews.tw/logo.png',
             title='鏡新聞 ' + category['name'] + ' Title', link='https://dev.mnews.tw')
    # TODO
    fg.rights(rights='Copyright 2019-2020')
    fg.link(href='https://dev.mnews.tw', rel='alternate')
    fg.ttl(300)  # 5 minutes
    fg.language('zh-TW')

    for item in result['allPosts']:
        fe = fg.add_entry(order='append')
        fe.id(__base_url__+item['slug'])
        fe.title(item['name'])
        fe.link(href=__base_url__+item['slug'], rel='alternate')
        fe.guid(__base_url__ + item['slug'])
        fe.pubDate(util.formatRFC2822(
            parser.isoparse(item['publishTime']).astimezone(__timezone__)))
        fe.updated(util.formatRFC2822(
            parser.isoparse(item['updatedAt'])))
        if item['heroImage'] is not None:
            fe.media.content(
                content={'url': item['heroImage']['urlOriginal'], 'medium': 'image'}, group=None)

    upload_data(
        bucket_name=__bucket_name__,
        data=fg.rss_str(pretty=False, extensions=True,
                        encoding='UTF-8', xml_declaration=True),
        content_type='application/rss+xml',
        destination_blob_name=__rss_base__ +
        '/google_news_' + category['slug'] + '.xml'
    )
