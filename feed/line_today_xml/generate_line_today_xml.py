from datetime import timedelta
from gql import gql
import __main__
import argparse
import lxml.etree as ET
import time
import uuid
import yaml
import re
import json
import sys
sys.path.append('/cronjobs')
from feed.utils import create_authenticated_k5_client, upload_data, tsConverter, recparse, replace_alt_with_descrption



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


__gql_client__ = create_authenticated_k5_client(config_graphql)

# To retrieve the latest 100 published posts
__qgl_post_template__ = '''
{
    allPosts(where: %s, sortBy: publishTime_DESC, first: %d) {
        id
        name
        slug
        briefHtml
        contentHtml
        contentApiData
        heroCaption
        heroImage {
            urlOriginal
            name
        }
        categories {
            name
            slug
        }
        relatedPosts(first:2) {
            name
            slug
            heroImage {
            urlOriginal
            
            }
        }
        writers {
            name
        }
        tags{
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

def generate_heroImge_tag(article):
    if article['heroImage'] is None:
        return f"<figure class=\"image\"><img alt=\"logo\" src=\"{config['feed']['item']['logo_url']}\"><figcaption>logo" + config['feed']['item']['figuretag']
    if article['heroCaption'] :
        return f"<figure class=\"image\"><img alt=\"{article['heroCaption']}\" src=\"{article['heroImage']['urlOriginal']}\"><figcaption>{article['heroCaption']}" + config['feed']['item']['figuretag']
    return f"<img src=\"{article['heroImage']['urlOriginal']}\">"

def replace_alt_with_descrption(contentHtml, contentApiData, img_list):
    for img in img_list:
        if 'alt' in img:
            # new_img_tag = f'<figure class="image">{img}<figcaption><\/figcaption><\/figure>'
            # contentHtml = contentHtml.replace(img, new_img_tag)
            alt = re.findall('alt=\".*?\"', img)
            img_name = re.findall('\".*\"', alt[0])
            img_name = img_name[0].replace('"', '')
            for apidata_item in contentApiData:
                if apidata_item['type'] == 'image' and 'name' in apidata_item['content'][0] and apidata_item['content'][0]['name'] == img_name:
                    if 'title' in apidata_item['content'][0]:
                        new_description = apidata_item['content'][0]['title']
                    else:
                        new_description = ''
                    new_img = f'<figure class="image">{img}<figcaption>{new_description}' + config['feed']['item']['figuretag'] 
                    contentHtml = contentHtml.replace(img, new_img)
                    contentHtml = contentHtml.replace(alt[0], f'alt="{new_description}"')
                    break
    return contentHtml

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
        content =  generate_heroImge_tag(article)# add hero img in beginning of content
        if article['briefHtml'] is not None:
            brief = article['briefHtml']
            content += brief 
        if article['contentHtml'] is not None:
            ytb_iframe = re.search(config['feed']['item']['ytb_iframe_regex'], article['contentHtml'])
            contentHtml = re.sub(config['feed']['item']['ytb_iframe_regex'], '', article['contentHtml'])
            contentHtml = re.sub(config['feed']['item']['redundantContent'], '', contentHtml)
            img_list = re.findall('<img.*?>', contentHtml)
            if img_list:
                contentHtml = replace_alt_with_descrption(contentHtml, json.loads(article['contentApiData']), img_list)
            content += contentHtml + config['feed']['item']['officialLine']
            content = re.sub(u'[^\u0020-\uD7FF\u0009\u000A\u000D\uE000-\uFFFD\U00010000-\U0010FFFF]+', '', content)
        title = re.sub(u'[^\u0020-\uD7FF\u0009\u000A\u000D\uE000-\uFFFD\U00010000-\U0010FFFF]+', '', article['name'])
        item = {
            'ID': article['id'],
            'nativeCountry': 'TW',
            'language': 'zh',
            'startYmdtUnix': availableDate,
            'endYmdtUnix': tsConverter(article['publishTime']) + (round(timedelta(news_available_days, 0).total_seconds()) * 1000),
            'title': title,
            'category': article['categories'][0]['name'] if len(article['categories']) > 0 else [],
            'publishTimeUnix': tsConverter(article['publishTime']),
        }
        if article['updatedAt'] is not None:
            updateTimeUnix = tsConverter(article['updatedAt'])
            item['updateTimeUnix'] = updateTimeUnix
        item['contentType'] = 0
        if article['heroImage'] is not None:
            item['thumbnail'] = article['heroImage']['urlOriginal']
        if article['relatedPosts']:
            content += config['feed']['item']['relatedPostPrependHtml']
            for relatedPost in article['relatedPosts']:
                if relatedPost:
                    relatedPostTitle = relatedPost['name']
                    relatedPostUrl = base_url + relatedPost['slug'] + config['feed']['item']['utmSource'] + '_' + article['slug'] + '_' + title
                    content += '<li><a href="%s">%s</li>' % (relatedPostUrl, relatedPostTitle)
                    recommendArticle = {'title': relatedPostTitle, 'url': relatedPostUrl }
                    if relatedPost['heroImage'] is not None:
                        recommendArticle['thumbnail'] = relatedPost['heroImage']['urlOriginal']
            content += "</ul>"
        item['contents'] = {'text':{'content': content}}
        item['author'] = config['feed']['item']['author']
        item['sourceUrl'] = base_url + article['slug'] + config['feed']['item']['utmSource']      
        if article['tags']:
            tags = []
            for tag in article['tags']:
                if tag:
                    tags.append(tag['name'])
            item['tags'] = {'tag':tags}
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
