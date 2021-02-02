from gql import gql, Client
from gql.transport.requests import RequestsHTTPTransport

import sys
import json
import time
import uuid
import pytz
import lxml.etree as ET
import urllib.request
import unicodedata
import gzip

from dateutil import parser
from datetime import datetime, timedelta
from lxml.etree import CDATA, tostring

# Imports the Google Cloud client library
from google.cloud import storage

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

# To retrieve the latest 100 published posts
__qgl_post_template__ = '''
{
    allPosts(where: {source: "tv", state: published}, sortBy: publishTime_DESC, first: 75) {
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

__gql_query__ = gql(__qgl_post_template__)
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
    blob.upload_from_string(
        data=gzip.compress(data=data, compresslevel=9), content_type=content_type, client=storage_client)
    blob.content_language = 'zh'
    blob.cache_control = 'max-age=300,public'
    blob.patch()


if __name__ == '__main__':
    mainXML = {
        'UUID': str(uuid.uuid4()),
        'time': int(round(time.time() * 1000)),
        'article': []
    }

    articles = __result__['allPosts']

    news_available_days = 365
    base_url = 'https://dev.mnews.tw/story'
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
            'author': '鏡新聞'
        }
        if article['heroImage'] is not None:
            item['thumbnail'] = article['heroImage']['urlOriginal']

        mainXML['article'].append(item)

    root = ET.Element('articles')
    recparse(root, mainXML)

    data = '''<?xml version="1.0" encoding="UTF-8" ?>
    %s
    ''' % ET.tostring(root, encoding="unicode")

    # Instantiates a client
    storage_client = storage.Client()

    # The name for the new bucket
    bucket_name = 'static-mnews-tw-dev'

    # rss folder path
    rss_base = 'rss'

    upload_data(
        bucket_name=bucket_name,
        data=data.encode(),
        content_type='application/xml',
        destination_blob_name=rss_base +
        '/line_today.xml'
    )
