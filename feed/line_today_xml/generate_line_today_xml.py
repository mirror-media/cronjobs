from datetime import datetime, timedelta
from dateutil import parser
from google.cloud import storage
from gql import gql, Client
from gql.transport.requests import RequestsHTTPTransport
from lxml.etree import CDATA, tostring
import __main__
import argparse
import gzip
import json
import lxml.etree as ET
import pytz
import sys
import time
import unicodedata
import urllib.request
import uuid
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

# To retrieve the latest 100 published posts
__qgl_post_template__ = '''
{
    allPosts(where: %s, sortBy: publishTime_DESC, first: %d) {
        id
        name
        slug
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

# Can not accept structure contains 'array of array'


def recparse(parentItem, obj):
    t = type(obj)
    if t is dict:
        for name, value in obj.items():
            subt = type(value)
            # print(name, value)
            if subt is dict:
                thisItem = ET.SubElement(parentItem, name)
                recparse(thisItem, value)
            elif subt is list:
                for item in value:
                    thisItem = ET.SubElement(parentItem, name)
                    recparse(thisItem, item)
            elif subt is not str:
                thisItem = ET.SubElement(parentItem, name)
                thisItem.text = str(value)
            else:
                thisItem = ET.SubElement(parentItem, name)
                thisItem.text = stringWrapper(name, value)
    elif t is list:
        raise Exception('unsupported structure')
    return


def stringWrapper(name, s):
    if name in ['title', 'content', 'author']:
        return CDATA(s)
    else:
        return s


def tsConverter(s):
    timeorigin = parser.parse(s)
    timediff = timeorigin - datetime(1970, 1, 1, tzinfo=pytz.utc)
    return round(timediff.total_seconds() * 1000)


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


if __name__ == '__main__':
    mainXML = {
        'UUID': str(uuid.uuid4()),
        'time': int(round(time.time() * 1000)),
        'article': []
    }

    articles = __result__['allPosts']

    news_available_days = 365
    base_url = config['baseURL']
    for article in articles:
        availableDate = max(tsConverter(
            article['publishTime']), tsConverter(article['updatedAt']))

        item = {
            'ID': article['id'],
            'nativeCountry': 'TW',
            'language': 'zh',
            'startYmdtUnix': availableDate,
            'endYmdtUnix': tsConverter(article['publishTime']) + (round(timedelta(news_available_days, 0).total_seconds()) * 1000),
            'title': article['name'],
            'category': article['categories'][0]['name'] if len(article['categories']) > 0 else [],
            'publishTimeUnix': availableDate,
            'contentType': 0,
            'contents': {
                'text': {
                        'content': article['contentHtml']
                },
            },
            'recommendArticles': {
                'article': [{'title': x['name'], 'url': base_url + '/' + x['slug'] + '/'} for x in article['relatedPosts'][:6] if x]
            },
            'author': config['feed']['item']['author']
        }
        if article['heroImage'] is not None:
            item['thumbnail'] = article['heroImage']['urlOriginal']

        mainXML['article'].append(item)

    root = ET.Element('articles')
    recparse(root, mainXML)

    data = '''<?xml version="1.0" encoding="UTF-8" ?>
    %s
    ''' % ET.tostring(root, encoding="unicode")

    file_config = config['file']
    # The name for the new bucket
    bucket_name = file_config['gcsBucket']

    # rss folder path
    rss_base = file_config['filePathBase']

    print(f'[{__main__.__file__}] generated xml: {data}')

    upload_data(
        bucket_name=bucket_name,
        data=data.encode('utf-8'),
        content_type='application/xml; charset=utf-8',
        destination_blob_name=rss_base +
        f'/{file_config["filenamePrefix"]}.{file_config["extension"]}'
    )

print(f'[{__main__.__file__}] exiting... goodbye...')
