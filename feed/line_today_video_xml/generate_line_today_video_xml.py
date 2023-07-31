from datetime import  timedelta
from gql import gql
import __main__
import argparse
import lxml.etree as ET
import time
import uuid
import yaml
import re
import sys
sys.path.append('/cronjobs')
from feed.utils import create_authenticated_k5_client, upload_data, tsConverter, recparse

print(f'[{__main__.__file__}] executing...')

CONFIG_KEY = 'config'
GRAPHQL_CMS_CONFIG_KEY = 'graphqlCMS'
NUMBER_KEY = 'number'

yaml_parser = argparse.ArgumentParser(
    description='Process configuration of generate_line_today_video_xml')
yaml_parser.add_argument('-c', '--config', dest=CONFIG_KEY,
                         help='config file for generate_line_today_video_xml', metavar='FILE', type=str)
yaml_parser.add_argument('-g', '--config-graphql', dest=GRAPHQL_CMS_CONFIG_KEY,
                         help='graphql config file for generate_line_today_video_xml', metavar='FILE', type=str, required=True)
yaml_parser.add_argument('-m', '--max-number', dest=NUMBER_KEY,
                         help='number of feed items', metavar='25', type=int, required=True)
args = yaml_parser.parse_args()

with open(getattr(args, CONFIG_KEY), 'r') as stream:
    config = yaml.safe_load(stream)
with open(getattr(args, GRAPHQL_CMS_CONFIG_KEY), 'r') as stream:
    config_graphql = yaml.safe_load(stream)
number = getattr(args, NUMBER_KEY)


__gql_client__ = create_authenticated_k5_client(config_graphql)

__gql_post_template__ = '''
query{
  allVideos(where:%s, first:%s, sortBy:createdAt_DESC){
    id
    name
    url
    description
    categories{
      name
    }
    createdAt
    updatedAt
    relatedPosts(first:2, sortBy:publishTime_DESC, where:{state:published}){
        name
        slug
        heroImage {
        	urlOriginal
        }
    }
  }
}'''

__gql_query__ = gql(__gql_post_template__ %
                    (config['postWhereFilter'], number))
__result__ = __gql_client__.execute(__gql_query__)


if __name__ == '__main__':
    mainXML = {
        'UUID': str(uuid.uuid4()),
        'time': int(round(time.time() * 1000)),
        'article': []
    }
    articles = __result__['allVideos']
    news_available_days = 365
    base_url = config['baseURL']
    for article in articles:
        availableDate = max(tsConverter(
            article['createdAt']), tsConverter(article['updatedAt']))
        title = re.sub(
            u'[^\u0020-\uD7FF\u0009\u000A\u000D\uE000-\uFFFD\U00010000-\U0010FFFF]+', '', article['name'])
        item = {
            'ID': article['id'],
            'nativeCountry': 'TW',
            'language': 'zh',
            'startYmdtUnix': availableDate,
            'endYmdtUnix': tsConverter(article['createdAt']) + (round(timedelta(news_available_days, 0).total_seconds()) * 1000),
            'title': title,
        }
        if article['categories']:
            item['category'] = article['categories'][0]['name']
        item['publishTimeUnix'] = availableDate
        if article['updatedAt'] is not None:
            updateTimeUnix = tsConverter(article['updatedAt'])
            item['updateTimeUnix'] = updateTimeUnix
        item['contentType'] = 5
        if article['description'] is not None:
            content = re.sub(
                u'[^\u0020-\uD7FF\u0009\u000A\u000D\uE000-\uFFFD\U00010000-\U0010FFFF]+', '', article['description']+ config['feed']['item']['officialLine'])
        else:
            content = config['feed']['item']['officialLine']
        if article['relatedPosts']:
            content += config['feed']['item']['relatedPostPrependHtml']
            for relatedPost in article['relatedPosts']:
                if relatedPost:
                    relatedPostTitle = relatedPost['name']
                    relatedPostUrl = base_url + relatedPost['slug'] + config['feed']['item']['utmSource'] + '_' + article['name']
                    content += '<li><a href="%s">%s</li>' % (relatedPostUrl, relatedPostTitle)
                    recommendArticle = {'title': relatedPostTitle, 'url': relatedPostUrl}
                    if relatedPost['heroImage'] is not None:
                        recommendArticle['thumbnail'] = relatedPost['heroImage']['urlOriginal']
            content += "</ul>"
        item['contents'] = {'video': {'url': article['url']}, 'text': {'content': content}}
        item['author'] = config['feed']['item']['author']
        mainXML['article'].append(item)

    root = ET.Element('articles')
    recparse(root, mainXML)

    data = '''<?xml version="1.0" encoding="UTF-8" ?>
    %s
    ''' % ET.tostring(root, encoding="unicode")

    file_config = config['file']
    bucket_name = file_config['gcsBucket']
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
