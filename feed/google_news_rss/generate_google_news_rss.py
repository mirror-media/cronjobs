from datetime import datetime, timezone
# workaround as feegen raise error: AttributeError: module 'lxml' has no attribute 'etree'
from lxml import etree
from dateutil import parser, tz
from feedgen import util
from feedgen.feed import FeedGenerator
from google.cloud import storage
from gql import gql, Client
from gql.transport.aiohttp import AIOHTTPTransport
import __main__
import argparse
import gzip
import logging
import yaml
import re

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

print(f'[{__main__.__file__}] executing...')


def create_authenticated_k5_client(config_graphql: dict) -> Client:
    logger = logging.getLogger(__main__.__file__)
    logger.setLevel('INFO')
    # Authenticate through GraphQL

    gql_endpoint = config_graphql['apiEndpoint']
    gql_transport = AIOHTTPTransport(
        url=gql_endpoint,
    )
    gql_client = Client(
        transport=gql_transport,
        fetch_schema_from_transport=False,
    )
    qgl_mutation_authenticate_get_token = '''
    mutation {
        authenticate: authenticateUserWithPassword(email: "%s", password: "%s") {
            token
        }
    }
    '''
    mutation = qgl_mutation_authenticate_get_token % (
        config_graphql['username'], config_graphql['password'])

    token = gql_client.execute(gql(mutation))['authenticate']['token']

    gql_transport_with_token = AIOHTTPTransport(
        url=gql_endpoint,
        headers={
            'Authorization': f'Bearer {token}'
        },
        timeout=60
    )

    return Client(
        transport=gql_transport_with_token,
        execute_timeout=60,
        fetch_schema_from_transport=False,
    )


__gql_client__ = create_authenticated_k5_client(config_graphql)

# To retrieve the latest 25 published posts for the specified category
__qgl_post_template__ = '''
{
    allPosts(where: {%s, categories_some: {slug: "%s"}, state: published, isAdvertised_not:true}, sortBy: publishTime_DESC, first: %d) {
        name
        slug
        briefHtml
        contentHtml
        heroCaption
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


__base_url__ = config['baseURL']


def upload_data(bucket_name: str, data: bytes, content_type: str, destination_blob_name: str):
    '''Uploads a file to the bucket.'''
    # bucket_name = 'your-bucket-name'
    # data = 'storage-object-content'

    # Instantiates a client
    storage_client = storage.Client()

    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(destination_blob_name)
    blob.content_encoding = 'gzip'

    print(
        f'[{__main__.__file__}] uploadling data to gs://{bucket_name}{destination_blob_name}')

    blob.upload_from_string(
        data=gzip.compress(data=data, compresslevel=9), content_type=content_type, client=storage_client)
    blob.content_language = 'zh'
    blob.cache_control = 'max-age=300,public'
    blob.patch()

    print(
        f'[{__main__.__file__}] finished uploading gs://{bucket_name}{destination_blob_name}')


__categories__ = config['categories']

__file_config__ = config['file']
# The name for the new bucket
__bucket_name__ = __file_config__['gcsBucket']

# rss folder path
__rss_base__ = __file_config__['filePathBase']

__config_feed__ = config['feed']
# the timezone for rss
__timezone__ = tz.gettz(__config_feed__['timezone'])

for id, category in __categories__.items():
    print(f'[{__main__.__file__}] retrieving data for category({category["slug"]})')
    query = gql(__qgl_post_template__ %
                (config['postWhereSourceFilter'], category["slug"], number))
    result = __gql_client__.execute(query)

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

    print('total rows: ' + str(len(result['allPosts'])))
    for item in result['allPosts']:
        fe = fg.add_entry(order='append')
        fe.id(__base_url__+item['slug'])
        name = re.sub(
            u'[^\u0020-\uD7FF\u0009\u000A\u000D\uE000-\uFFFD\U00010000-\U0010FFFF]+', '', item['name'])
        fe.title(name)
        fe.link(href=__base_url__+item['slug'], rel='alternate')
        fe.guid(__base_url__ + item['slug'], isPermaLink="true")
        fe.pubDate(util.formatRFC2822(
            parser.isoparse(item['publishTime']).astimezone(__timezone__)))

        content = ''
        brief = item['briefHtml']
        if brief is not None:
            brief = re.sub(
                u'[^\u0020-\uD7FF\u0009\u000A\u000D\uE000-\uFFFD\U00010000-\U0010FFFF]+', '', brief)
            fe.description(description=brief, isSummary=True)
            content += brief
        if item['heroImage'] is not None:
            fe.media.content(
                content={'url': item['heroImage']['urlOriginal'], 'medium': 'image'}, group=None)
            if item['heroCaption'] is not None:
                content += '<img src="%s" alt="%s" />' % (
                    item['heroImage']['urlOriginal'], item['heroCaption'])
            else:
                content += '<img src="%s" />' % (
                    item['heroImage']['urlOriginal'])
        if item['contentHtml'] is not None:
            #content += re.sub(__config_feed__['ytb_iframe_regex'], '',item['contentHtml'])
            content += item['contentHtml']
        if len(item['relatedPosts']) > 0:
            #content += __config_feed__['item']['relatedPostPrependHtml']
            for related_post in item['relatedPosts'][:3]:
                content += '<br/><a href="%s">%s</a>' % (
                    __base_url__+related_post['slug']+__config_feed__['utmSource'], related_post['name'])
        content = re.sub(
            u'[^\u0020-\uD7FF\u0009\u000A\u000D\uE000-\uFFFD\U00010000-\U0010FFFF]+', '', content)
        fe.content(content=content, type='CDATA')
        fe.updated(util.formatRFC2822(
            parser.isoparse(item['updatedAt'])))
        if item['writers'] is not None:
            fe.dc.dc_creator(creator=list(
                map(lambda w: w['name'], item['writers'])))
        if item['heroImage'] is not None:
            fe.media.content(
                content={'url': item['heroImage']['urlOriginal'], 'medium': 'image'}, group=None)

    upload_data(
        bucket_name=__bucket_name__,
        data=fg.rss_str(pretty=True, extensions=True,
                        encoding='UTF-8', xml_declaration=True),
        content_type='application/xml; charset=utf-8',
        destination_blob_name=__rss_base__ +
        f'/{__file_config__["filenamePrefix"]}_{category["slug"]}.{__file_config__["extension"]}'
    )


print(f'[{__main__.__file__}] exiting... goodbye...')
