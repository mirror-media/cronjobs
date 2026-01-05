from datetime import datetime
from gql import gql
from lxml.etree import CDATA, tostring, SubElement, Element
import __main__
import argparse
import yaml
import hashlib
import re
import sys
sys.path.append('/cronjobs')
from feed.utils import create_authenticated_k5_client, upload_data, sub

CONFIG_KEY = 'config'
GRAPHQL_CMS_CONFIG_KEY = 'graphqlCMS'
NUMBER_KEY = 'number'

yaml_parser = argparse.ArgumentParser(
    description='Process configuration of generate_yahoo_video_rss')
yaml_parser.add_argument('-c', '--config', dest=CONFIG_KEY,
                         help='config file for generate_yahoo_video_rss', metavar='FILE', type=str)
yaml_parser.add_argument('-g', '--config-graphql', dest=GRAPHQL_CMS_CONFIG_KEY,
                         help='graphql config file for generate_yahoo_video_rss', metavar='FILE', type=str, required=True)
yaml_parser.add_argument('-m', '--max-number', dest=NUMBER_KEY,
                         help='number of feed items', metavar='25', type=int, required=True)
args = yaml_parser.parse_args()

with open(getattr(args, CONFIG_KEY), 'r') as stream:
    config = yaml.safe_load(stream)
with open(getattr(args, GRAPHQL_CMS_CONFIG_KEY), 'r') as stream:
    config_graphql = yaml.safe_load(stream)
number = getattr(args, NUMBER_KEY)


__gql_client__ = create_authenticated_k5_client(config_graphql)

# To retrieve the latest 100 published posts
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
    relatedPosts {
        name
        slug
        heroImage {
        	urlOriginal
        }
    }
  }
}'''

__gql_query__ = gql(__gql_post_template__ %(config['postWhereFilter'], number))
__result__ = __gql_client__.execute(__gql_query__)
print(__result__)





if __name__ == '__main__':
    media = config['media']
    dcterms = config['dcterms']
    feed = config['feed']
    root = Element('rss', nsmap={'media':media, 'dcterms':dcterms}, version='2.0')
    channel = sub(root, 'channel')
    sub(channel, 'title', feed['title'])
    sub(channel, 'link', feed['link'])
    sub(channel, 'description', feed['description'])
    sub(channel, 'language', feed['language'])
    sub(channel, 'copyright', feed['copyright'])
    image = sub(channel, 'image')
    sub(image, 'title', feed['image']['title'])
    sub(image, 'url', feed['image']['url'])
    sub(image, 'link', feed['image']['link'])
    articles = __result__['allVideos']
    for article in articles:
        item = SubElement(channel, 'item')
        sub(item, 'title', article['name'])
        sub(item, 'link', article['url'])
        if article['description']:
            content = article['description']
        else: 
            content = ""
            
        if len(article['relatedPosts']) > 0:
            content += feed['item']['relatedPostPrependHtml']
            for related_post in article['relatedPosts'][:3]:
                content += '<br/><a href="%s">%s</a>' % (feed['link']+related_post['slug'] + config['feed']['item']['utmSource'], related_post['name'])
        content = re.sub(u'[^\u0020-\uD7FF\u0009\u000A\u000D\uE000-\uFFFD\U00010000-\U0010FFFF]+', '', content)
        sub(item, 'description', content)
        if article['categories']:
            sub(item, 'category', article['categories'][0]['name'])
        if re.search(config['baseURL'], article['url']):
            SubElement(_tag='{%s}content' % media,_parent=item, nsmap={'media':media}, type="video/mp4", medium="video", url=article['url'], isDefault="true")
        media_credit = SubElement(_tag='{%s}credit' % media,_parent=item, nsmap={'media':media}, role="author")
        media_credit.text = CDATA(feed['title'])
        media_credit = SubElement(_tag='{%s}keywords' % media,_parent=item, nsmap={'media':media})
        guid = sub(item, 'guid', hashlib.sha224((article['url']).encode()).hexdigest())
        guid.set('isPermaLink','false')
        sub(item, 'pubDate', datetime.strptime(article['createdAt'], '%Y-%m-%dT%H:%M:%S.%fZ').strftime('%A, %d %B %Y %H:%M:%S +0800'))

    data = tostring(root, encoding="unicode")
    file_config = config['file']
    bucket_name = file_config['gcsBucket']
    rss_base = file_config['filePathBase']

    upload_data(
        bucket_name=bucket_name,
        data=data.encode('utf-8'),
        content_type='application/xml; charset=utf-8',
        destination_blob_name=rss_base +
        f'/{file_config["filenamePrefix"]}.{file_config["extension"]}'
    )
   
print(f'[{__main__.__file__}] exiting... goodbye...')
