import __main__
import argparse
import yaml
import logging
import datetime
from gql import gql, Client
from gql.transport.aiohttp import AIOHTTPTransport
from google.cloud import storage
import json
import sys
sys.path.append('/cronjobs')
from feed.utils import create_authenticated_k5_client

CONFIG_KEY = 'config'
GRAPHQL_CMS_CONFIG_KEY = 'graphqlCMS'
NUMBER_KEY = 'number'

yaml_parser = argparse.ArgumentParser(
    description='Process configuration of mnews_sitemap_xml')
yaml_parser.add_argument('-c', '--config', dest = CONFIG_KEY,
                         help = 'config file for mnews_sitemap_xml', metavar = 'FILE', type=str)
yaml_parser.add_argument('-g', '--config-graphql', dest = GRAPHQL_CMS_CONFIG_KEY,
                         help = 'graphql config file for mnews_sitemap_xml', metavar = 'FILE', type = str, required = True)
yaml_parser.add_argument('-m', '--max-number', dest = NUMBER_KEY,
                         help = 'number of feed items', metavar = '75', type = int, required = True)
args = yaml_parser.parse_args()

with open(getattr(args, CONFIG_KEY), 'r') as stream:
    config = yaml.safe_load(stream)
with open(getattr(args, GRAPHQL_CMS_CONFIG_KEY), 'r') as stream:
    config_graphql = yaml.safe_load(stream)
number = getattr(args, NUMBER_KEY)

print(f'[{__main__.__file__}] executing...')

__base_url__ = config['base_url']
__file_config__ = config['file']
__bucket_name__ = __file_config__['bucket_name']
__destination_prefix__ = __file_config__['destination_prefix']
__template__ = config['template']
__src_file_name__ = __file_config__['src_file_name']

video_data = {}


def string_wrapper(s):
    return f'<![CDATA[{s}]]>'


def query_show_slug(endpoints, gql_client):
    query_show = '''
    query{
	allShows(sortBy:sortOrder_ASC){
    slug
  }
}
    '''
    query = gql(query_show)
    allShows = gql_client.execute(query)
    if isinstance(allShows, dict) and 'allShows' in allShows:
        allShows = allShows['allShows']
        if isinstance(allShows, list) and allShows:
            for item in allShows:
                slug = item['slug']
                show = '/show/' + slug
                endpoints.append(show)
    else:
        print("no show")


def query_cate_slug(endpoints, gql_client):
    categories = []
    query_cate = '''
    query{
	allCategories(sortBy:sortOrder_ASC){
    slug
  }
}
'''
    query = gql(query_cate)
    allCategories = gql_client.execute(query)
    if isinstance(allCategories, dict) and 'allCategories' in allCategories:
        allCategories = allCategories['allCategories']
        if isinstance(allCategories, list) and allCategories:
            for item in allCategories:
                slug = item['slug']
                categories.append(slug)
                if slug == 'ombuds':
                    endpoints.append('/ombuds')
                    continue
                if slug == 'stream':
                    slug = 'video'
                cate = '/category/' + slug
                endpoints.append(cate)
        else:
            print("no cate")
    return categories


def query_latest_slug(endpoints, gql_client):

    query_latest = '''
    query{
    allPosts(where:{
      state:published}, sortBy:publishTime_DESC, first:120){
        slug

    }
  }'''
    query = gql(query_latest)
    allPosts = gql_client.execute(query)
    if isinstance(allPosts, dict) and 'allPosts' in allPosts:
        allPosts = allPosts['allPosts']
        if isinstance(allPosts, list) and allPosts:
            for item in allPosts:
                slug = item['slug']
                post = '/story/' + slug
                endpoints.append(post)
        else:
            print("no latest post")


def concate_video(item):
    video = ''
    if item['name'] and item['content'] and item['heroImage'] and item['briefHtml']:
        title = item['name']
        draft_content = item['content']
        draft_content = json.loads(draft_content)
        thumbnail_loc = item['heroImage']['urlOriginal']
        description = string_wrapper(item['briefHtml'])

        if draft_content['entityMap']:
            entityMap = draft_content['entityMap']
            for v in entityMap.values():
                if v['type'] == 'YOUTUBE':
                    yt_id = v['data']['id'] if 'id' in v['data'] else v['data']['youtubeId']
                    content_loc = f"<video:player_loc>https://www.youtube.com/watch?v={yt_id}</video:player_loc>"
                    video += __template__['video'].format(
                        thumbnail_loc, title, description, content_loc)
    return video if video else False


def query_post_slug(cate, gql_client):
    post_endpoint = []
    query_post = '''query{
    allPosts(where:{state:published, categories_some:{slug:"%s"}
    }, sortBy:publishTime_DESC, first:200){
    slug
    style
    name
    content
    briefHtml
    heroImage{
        urlOriginal
        }
    }
}''' % cate
    query = gql(query_post)
    allPosts = gql_client.execute(query)
    if isinstance(allPosts, dict) and 'allPosts' in allPosts:
        allPosts = allPosts['allPosts']
        if isinstance(allPosts, list) and allPosts:
            for item in allPosts:
                slug = item['slug']
                post = '/story/' + slug
                post_endpoint.append(post)
                if item['style'] == 'videoNews':
                    video_tag_content = concate_video(item)
                    if video_tag_content:
                        video_data[post] = video_tag_content
    return post_endpoint


def generate_sitemap_xml(endpoint_slug):
    sitemap_template = __template__['sitmap']
    sitemap = sitemap_template['header']
    lastmod = datetime.datetime.now().strftime('%Y-%m-%d')
    for slug in endpoint_slug:
        loc = __base_url__ + slug
        if slug == '/':
            priority = 1.0
        else:
            priority = 0.5
        video_tag = ''
        if slug in video_data:
            video_tag = video_data[slug]
        url_tag = sitemap_template['urltag'].format(loc, lastmod, priority, video_tag)
        sitemap += url_tag
    sitemap += sitemap_template['endtag']
    return sitemap


def upload_blob(bucket_name, source_file_name, destination_blob_name):
    """Uploads a file to the bucket."""
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(destination_blob_name)
    blob.upload_from_filename(source_file_name)
    blob.content_language = 'zh'
    blob.cache_control = 'max-age=300,public'
    blob.content_type = 'application/xml; charset=utf-8'
    blob.patch()

    print(
        "File {} uploaded to {}.".format(
            source_file_name, destination_blob_name
        )
    )


def sitemap():
    sitemap_index_url = []
    gql_client = create_authenticated_k5_client(config_graphql)

    # made homepage sitemap
    hp_endpoint_slug = config['configs_endpoint']
    categories = query_cate_slug(hp_endpoint_slug, gql_client)
    query_show_slug(hp_endpoint_slug, gql_client)
    query_latest_slug(hp_endpoint_slug, gql_client)
    homepage_sitemap = generate_sitemap_xml(hp_endpoint_slug)
    # store and upload homepage sitemap
    source_file_name = __src_file_name__['homepage']
    destination_blob_name = __destination_prefix__ + source_file_name
    with open(source_file_name, 'w', encoding='utf8') as f:
        f.write(homepage_sitemap)
    upload_blob(__bucket_name__, source_file_name, destination_blob_name)
    # store_sitemap_index url
    sitemap_index_url.append('/' + destination_blob_name)

    # made category sitemap
    if categories:
        for cate in categories:
            if cate == 'stream':
                continue
            post_endpoint_slug = query_post_slug(cate, gql_client)
            if post_endpoint_slug:
                post_sitemap = generate_sitemap_xml(post_endpoint_slug)
            else:
                print("no post")
                continue
            source_file_name = __src_file_name__['cate_post'].format(cate)
            destination_blob_name = __destination_prefix__ + source_file_name
            with open(source_file_name, 'w', encoding='utf8') as f:
                f.write(post_sitemap)
            upload_blob(__bucket_name__, source_file_name,
                        destination_blob_name)
            sitemap_index_url.append('/' + destination_blob_name)
    else:
        print('no categories')
    return sitemap_index_url


def generate_sitemap_index_xml(sitemap_index_url):
    sitemap_index_template = __template__['sitemap_index']
    index_sitemap = sitemap_index_template['header']
    lastmod = datetime.datetime.now().strftime('%Y-%m-%d')
    for slug in sitemap_index_url:
        loc = __base_url__ + slug
        sitemap_tag = sitemap_index_template['urltag'].format(loc, lastmod)

        index_sitemap += sitemap_tag

    index_sitemap += sitemap_index_template['endtag']

    return index_sitemap


sitemap_index_url = sitemap()
index_sitemap = generate_sitemap_index_xml(sitemap_index_url)
source_file_name = __src_file_name__['sitemap_index']
destination_blob_name = __destination_prefix__ + source_file_name
with open(source_file_name, 'w', encoding='utf8') as f:
    f.write(index_sitemap)
upload_blob(__bucket_name__, source_file_name, destination_blob_name)
