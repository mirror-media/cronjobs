from datetime import datetime, timedelta, timezone
from dateutil import parser, tz
# workaround as feegen raise error: AttributeError: module 'lxml' has no attribute 'etree'
from lxml import etree
from feedgen import util
from feedgen.feed import FeedGenerator
from google.cloud import storage
from gql import gql, Client
from gql.transport.requests import RequestsHTTPTransport
from json import JSONDecoder
import __main__
import argparse
import gzip
import hashlib
import yaml

print(f'[{__main__.__file__}] executing...')

CONFIG_KEY = 'config'
GRAPHQL_CMS_CONFIG_KEY = 'graphqlCMS'
NUMBER_KEY = 'number'

yaml_parser = argparse.ArgumentParser(
    description='Process configuration of generate_google_news_rss')
yaml_parser.add_argument('-c', '--config', dest=CONFIG_KEY,
                         help='config file for generate_google_news_rss', metavar='FILE', type=str)
yaml_parser.add_argument('-g', '--config-graphql', dest=GRAPHQL_CMS_CONFIG_KEY,
                         help='graphql config file for generate_google_news_rss', metavar='FILE', type=str, required=True)
yaml_parser.add_argument('-m', '--max-number', dest=NUMBER_KEY,
                         help='number of feed items', metavar='75', type=int, required=True)
args = yaml_parser.parse_args()

with open(getattr(args, CONFIG_KEY), 'r') as stream:
    config = yaml.safe_load(stream)
with open(getattr(args, GRAPHQL_CMS_CONFIG_KEY), 'r') as stream:
    config_graphql = yaml.safe_load(stream)
number = getattr(args, NUMBER_KEY)

__gql_transport__ = RequestsHTTPTransport(
    url=config_graphql['apiEndpoint'],
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
    allPosts(where: %s, sortBy: publishTime_DESC, first: %d) {
        name
        slug
        briefHtml
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

__gql_query__ = gql(__qgl_post_template__ %
                    (config['postWhereFilter'], number))
__result__ = __gql_client__.execute(__gql_query__)

__config_feed__ = config['feed']
# the timezone for rss
__timezone__ = tz.gettz(__config_feed__['timezone'])

fg = FeedGenerator()
fg.load_extension('media', atom=False, rss=True)
fg.load_extension('dc', atom=False, rss=True)
fg.title(__config_feed__['title'])
fg.description(__config_feed__['description'])
fg.id(__config_feed__['id'])
fg.pubDate(datetime.now(timezone.utc).astimezone(__timezone__))
fg.updated(datetime.now(timezone.utc).astimezone(__timezone__))
fg.image(url=__config_feed__['image']['url'],
         title=__config_feed__['image']['title'], link=__config_feed__['image']['link'])
fg.rights(rights=__config_feed__['copyright'])
fg.link(href=__config_feed__['link'], rel='alternate')
fg.ttl(__config_feed__['ttl'])  # 5 minutes
fg.language('zh-TW')

__base_url__ = config['baseURL']

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

    brief = item['briefHtml']
    if brief is not None:
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
        content += __config_feed__['item']['relatedPostPrependHtml']
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
    print(f'[{__main__.__file__}] uploadling data to gs://{bucket_name}{destination_blob_name}')
    blob.upload_from_string(
        data=gzip.compress(data=data, compresslevel=9), content_type=content_type, client=storage_client)
    blob.content_language = 'zh'
    blob.cache_control = 'max-age=300,public'
    blob.patch()

    print(
        f'[{__main__.__file__}] finished uploading gs://{bucket_name}{destination_blob_name}')


# Instantiates a client
__storage_client__ = storage.Client()

__file_config__ = config['file']
# The name for the new bucket
__bucket_name__ = __file_config__['gcsBucket']

# rss folder path
__rss_base__ = __file_config__['filePathBase']

print(f'[{__main__.__file__}] generated rss: {fg.rss_str(pretty=False, extensions=True,encoding="UTF-8", xml_declaration=True).decode("UTF-8")}')

upload_data(
    bucket_name=__bucket_name__,
    data=fg.rss_str(pretty=False, extensions=True,
                    encoding='UTF-8', xml_declaration=True),
    content_type='application/rss+xml; charset=utf-8',
    destination_blob_name=__rss_base__ +
    f'/{__file_config__["filenamePrefix"]}.{__file_config__["extension"]}'
)

print(f'[{__main__.__file__}] exiting... goodbye...')
