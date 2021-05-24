from datetime import datetime
from gql import gql, Client
from gql.transport.aiohttp import AIOHTTPTransport
from mergedeep import merge, Strategy
import __main__
import aiohttp
import argparse
import io
import json
import logging
import sys
import urllib
import yaml

CONFIG_KEY = 'config'
GRAPHQL_CMS_CONFIG_KEY = 'graphqlCMS'
MAX_NUMBER_KEY = 'maxNumber'

__default_config = {
    "sourceK3Endpoints":
    {
        "list": "https://api.mirrormedia.mg/getlist",
        "posts": "https://api.mirrormedia.mg/drafts",
    },
    "author": "鏡週刊",
    "blacklist": {"sectionNames": []},
    "writerID": 201,
    "source": "mm",
    "destSlugPrefix": "mm-",
    "fileHostDomainRule":
    {
        "https://storage.googleapis.com/mirrormedia-files": "https://www.mirrormedia.mg",
        "https://storage.googleapis.com/static-mnews-tw-prod": "https://statics.mnews.tw",
        "https://storage.googleapis.com/static-mnews-tw-dev": "https://dev.mnews.tw",
        "https://storage.googleapis.com/static-mnews-tw-stag": "https://www-stag.mnews.tw",
    },
}

__default_graphql_cms_config = {
    'username': '',
    'password': '',
    'apiEndpoint': '',
}

__query_existing_posts_template = '''query {
  allPosts(where: {AND: [{OR: [%s]}]}) {
    slug
  }
}
'''


def get_k3_posts(k3_endpoint: str, max_results: int = 20, sort: str = '-publishedDate', populate: str = 'categories,heroImage') -> dict:
    '''getK3Posts get posts from k3'''
    logger = logging.getLogger(__main__.__file__)
    logger.setLevel('INFO')
    url = f'{k3_endpoint}?where={{"state":"published"}}&max_results={max_results}&sort={sort}&populate={populate}'
    logger.info(f'sending request:{url}')
    print
    req = urllib.request.Request(
        url=url, headers={'Accept': 'application/json;charset=utf-8'})
    resp = urllib.request.urlopen(req)
    if resp.status < 200 and resp.status >= 300:
        with resp as f:
            raise f'[{__file__}] response from {req.get_full_url()} has status({resp.status}), resp body:{f.read().decode("utf-8")}'

    with resp as f:
        return json.loads(f.read().decode('utf-8'))['_items']


def find_existing_slugs_set(config_graphql: dict = None, slugs: list = []) -> set:

    gql_endpoint = config_graphql['apiEndpoint']
    gql_transport = AIOHTTPTransport(
        url=gql_endpoint,
    )
    gql_client = Client(
        transport=gql_transport,
        fetch_schema_from_transport=False,
    )

    # format query array
    query_conditions = ','.join(
        ['{slug: "%s"}' % slug for slug in slugs])
    query = __query_existing_posts_template % query_conditions
    logger.info(f'query slugs for existence...')
    # extract slugs
    existing_slugs = [post['slug'] for post in gql_client.execute(gql(query))[
        'allPosts']]
    logger.info(f'existing_slugs:{existing_slugs}')
    return set(existing_slugs)


def convert_hero_image(image_src: dict, post_dest: dict):
    if image_src != None:
        post_dest['heroImage'] = {
            'name': image_src['description'],
            'filename': image_src['image']['filename'],
            'file': json.dumps(image_src['image'], ensure_ascii=False),
            'meta': image_src['image']['filetype'],
            'urlOriginal': image_src['image']['url'],
            'urlDesktopSized': image_src['image']['resizedTargets']['desktop']['url'],
            'urlMobileSized': image_src['image']['resizedTargets']['mobile']['url'],
            'urlTabletSized': image_src['image']['resizedTargets']['tablet']['url'],
            'urlTinySized': image_src['image']['resizedTargets']['tiny']['url'],
        }


def convert_and_clean_post_for_k5(posts: list, delegated_writer: int) -> list:
    new_posts = []

    for post in posts:
        new_post = {}
        convert_hero_image(post.get('heroImage', None), new_post)
        new_post['briefJsonStr'] = json.dumps(json.dumps(
            post['brief']['draft'], ensure_ascii=False), ensure_ascii=False)
        new_post['briefApiDataJsonStr'] = json.dumps(json.dumps(
            post['brief']['apiData'], ensure_ascii=False), ensure_ascii=False)
        new_post['briefHtmlJsonStr'] = json.dumps(
            post['brief']['html'], ensure_ascii=False)
        new_post['contentJsonStr'] = json.dumps(json.dumps(
            post['content']['draft'], ensure_ascii=False), ensure_ascii=False)
        new_post['contentApiDataJsonStr'] = json.dumps(json.dumps(
            post['content']['apiData'], ensure_ascii=False), ensure_ascii=False)
        new_post['contentHtmlJsonStr'] = json.dumps(
            post['content']['html'], ensure_ascii=False)
        new_post['heroCaptionJsonStr'] = json.dumps(
            post.get('heroCaption', None), ensure_ascii=False)
        new_post['nameJsonStr'] = json.dumps(post['title'], ensure_ascii=False)
        new_post['slugJsonStr'] = json.dumps(post['slug'], ensure_ascii=False)
        new_post['writer'] = delegated_writer
        new_posts.append(new_post)

    return new_posts


def is_category_not_member_only(category: dict) -> bool:
    # is_member_only mey not be presented. In such case, we treat it as False.
    return category.get('is_member_only') in (None, False)


def is_section_allowed(section_name_blacklist: list, section: dict) -> bool:
    return section.get('name', None) not in set(section_name_blacklist)


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


def convert_file_url_base(file_host_domain_rule: dict, url: str) -> str:
    for key in file_host_domain_rule.keys():
        url = url.replace(key, file_host_domain_rule[key], 1)
    return url


__query_images_by_name_template = '''query {
  allImages(where: {name: "%s"}) {
    id
  }
}
'''

__mutation_create_image_for_id_template = '''mutation {
    allImages(where: {name: "%s"}) {
        id
    }
}
'''


def create_and_get_image_id(client: Client, image: dict, file_host_domain_rule: dict) -> id:
    logger = logging.getLogger(__main__.__file__)
    logger.setLevel('INFO')
    query_image_ids_by_name = __query_images_by_name_template % image['name']
    images = client.execute(gql(query_image_ids_by_name))['allImages']

    if len(images) == 0:
        # create images
        # FIXME it's a workaround to raise error on server side so that the image data won't be overwritten by the server
        params = {"file": io.StringIO('')}
        create_image_mutation = f'''
        mutation($file: Upload!) {{
            createImage (data:{{
                name: {json.dumps(image['name'], ensure_ascii=False)},
                file: $file,
                meta: {json.dumps(image['meta'], ensure_ascii=False)},
                urlOriginal: {json.dumps(image['urlOriginal'], ensure_ascii=False)},
                urlDesktopSized: {json.dumps(convert_file_url_base(file_host_domain_rule, image['urlDesktopSized']), ensure_ascii=False)},
                urlMobileSized: {json.dumps(convert_file_url_base(file_host_domain_rule, image['urlMobileSized']), ensure_ascii=False)},
                urlTabletSized: {json.dumps(convert_file_url_base(file_host_domain_rule, image['urlTabletSized']), ensure_ascii=False)},
                urlTinySized: {json.dumps(convert_file_url_base(file_host_domain_rule, image['urlTinySized']), ensure_ascii=False)},
            }}) {{
                name
                id
            }}
        }}'''
        created_image = client.execute(
            gql(create_image_mutation), variable_values=params, upload_files=True)['createImage']
        id = created_image['id']
        logger.info(
            f'created image(id:{created_image["id"]}, name:{created_image["name"]})')
    else:
        id = images[0]['id']

    return id


def insert_post_to_k5(client: Client, post: dict, file_host_domain_rule: dict):
    logger = logging.getLogger(__main__.__file__)
    logger.setLevel('INFO')
    # check image existent
    if post.get('heroImage') != None:
        hero_image_id = create_and_get_image_id(client, post.get(
            'heroImage'), file_host_domain_rule)
        create_post_mutation = f'''
        mutation {{
            createPost(data: {{
                slug: {post["slugJsonStr"]},
                state: draft,
                name: {post["nameJsonStr"]},
                writers: {{
                    connect:{{
                        id: {post['writer']}
                    }}
                }},
                heroImage: {{
                    connect: {{
                        id: {hero_image_id}
                        }}
                    }},
                heroCaption: {post["heroCaptionJsonStr"]},
                brief: {post["briefJsonStr"]},
                briefHtml: {post["briefHtmlJsonStr"]},
                briefApiData: {post["briefApiDataJsonStr"]},
                content: {post["contentJsonStr"]},
                contentHtml: {post["contentHtmlJsonStr"]},
                contentApiData: {post["contentApiDataJsonStr"]},
                source: "{config['source']}"
            }}) {{
                id
                slug
                name
            }}
        }}'''

        created_post = client.execute(gql(create_post_mutation))['createPost']
        logger.info(f'post created: {created_post}')


def k5_signout(client: Client):
    logger = logging.getLogger(__main__.__file__)
    logger.setLevel('INFO')
    unauthenticate_mutation = '''
    mutation {
        unauthenticate: unauthenticateUser {
            success
        }
    }'''
    result = client.execute(gql(unauthenticate_mutation))
    logger.info(result)


def insert_posts_to_k5(config_graphql: dict, file_host_domain_rule: dict, posts: list):
    logger = logging.getLogger(__main__.__file__)
    logger.setLevel('INFO')
    # Authenticate through GraphQL
    authenticated_graphql_client = create_authenticated_k5_client(
        config_graphql)
    logger.info(f'login as {config_graphql["username"]}')
    for post in posts:
        insert_post_to_k5(authenticated_graphql_client,
                          post, file_host_domain_rule)

    k5_signout(authenticated_graphql_client)


def main(config: dict = None, config_graphql: dict = None, playlist_ids: list = None, max_number: int = 3):
    ''' Import YouTube Channel program starts here '''
    logger = logging.getLogger(__main__.__file__)
    logger.setLevel('INFO')

    # merge option to the default configs
    config = merge({}, __default_config, config,
                   strategy=Strategy.TYPESAFE_REPLACE)
    config_graphql = merge({}, __default_graphql_cms_config, config_graphql,
                           strategy=Strategy.TYPESAFE_REPLACE)

    # 1. request https://api.mirrormedia.mg/getposts?where={"state": "published"}&max_results=100&sort=-publishedDate&populate=categories,heroImage
    posts = get_k3_posts(
        k3_endpoint=config['sourceK3Endpoints']['posts'], max_results=max_number)
    # 2. Check post existence
    posts_with_new_slug = posts
    for i in range(len(posts_with_new_slug)):
        posts_with_new_slug[i]['slug'] = f'{config["destSlugPrefix"]}{posts_with_new_slug[i]["slug"]}'
        try:
            posts_with_new_slug[i]['heroImage'][
                'description'] = f'{config["destSlugPrefix"]}{posts_with_new_slug[i]["heroImage"]["description"]}'
        except:
            # in case of posts without heroimage
            pass

    slugs = [post['slug'] for post in posts_with_new_slug]
    existing_slugs_set = find_existing_slugs_set(
        config_graphql=config_graphql, slugs=slugs)

    new_posts = [
        post
        for post in posts_with_new_slug
        if f'{post["slug"]}' not in existing_slugs_set
        and all([is_category_not_member_only(c) for c in post.get('categories', [])])
        and all([is_section_allowed(config['blacklist']['sectionNames'], section) for section in post.get('sections', [])])
    ]

    logger.info(f'news post slugs:{[post["slug"] for post in new_posts]}')
    # 3. Generate and clean up Posts for k5
    k5_posts = convert_and_clean_post_for_k5(new_posts, config['writerID'])
    logger.info(f'posts generated for k5:{k5_posts}')
    # 4. Insert post only or insert post and image together
    insert_posts_to_k5(config_graphql, config['fileHostDomainRule'], k5_posts)


logging.basicConfig()

if __name__ == '__main__':
    logger = logging.getLogger(__main__.__file__)
    logger.setLevel('INFO')
    logger.info(f'{__file__} is executing...')
    parser = argparse.ArgumentParser(
        description='Process configuration of importPosts')
    parser.add_argument('-c', '--config', dest=CONFIG_KEY,
                        help='config file for importPosts', metavar='FILE', type=str)
    parser.add_argument('-g', '--config-graphql', dest=GRAPHQL_CMS_CONFIG_KEY,
                        help='graphql config file for importPosts', metavar='FILE', type=str, required=True)
    parser.add_argument('-m', '--max-number', dest=MAX_NUMBER_KEY,
                        help='max number of posts', metavar='10', type=int, required=True)

    args = parser.parse_args()

    with open(getattr(args, CONFIG_KEY), 'r') as stream:
        config = yaml.safe_load(stream)
    with open(getattr(args, GRAPHQL_CMS_CONFIG_KEY), 'r') as stream:
        config_graphql = yaml.safe_load(stream)
    max_number = getattr(args, MAX_NUMBER_KEY)

    main(config=config, config_graphql=config_graphql, max_number=max_number)

    logger.info('exiting...good bye...')
