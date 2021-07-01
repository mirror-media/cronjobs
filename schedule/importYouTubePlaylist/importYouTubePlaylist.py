from gql import gql, Client
from gql.transport.requests import RequestsHTTPTransport
from mergedeep import merge, Strategy
from urllib.parse import urlparse
import argparse
import json
import os
import requests
import sys
import urllib.request
import yaml

CONFIG_KEY = 'config'
GRAPHQL_CMS_CONFIG_KEY = 'graphqlCMS'
PLAYLIST_IDS_KEY = 'playlistIds'
YOUTUBE_CREDENTIAL_KEY = 'youtubeCredential'
MAX_NUMBER_KEY = 'maxNumber'

__defaultConfig = {
    'ytrelayEndpoints': {
        'playlistItems': 'http://yt-relay.default.svc.cluster.local/youtube/v3/playlistItems',
    },
    'converTextToDraftApiEndpoint': 'https://api.mirrormedia.mg/converttext',
}

__defaultgraphqlCmsConfig = {
    'username': '',
    'password': '',
    'apiEndpoint': '',
}

__queryExistingVideosTemplate = '''
query {
  allVideos(where: {AND: [{OR: [%s]}, {OR: [{url_contains_i: "youtube"}, {url_contains_i: "youtu.be"}]}]}) {
    url
  }
}
'''


def convertTextToDraft(config: dict, s: str) -> tuple:
    '''convertTextToDraft converts a string to a tuple of strings (draft, html, apiData)
    '''
    data = s.encode('utf-8')
    req = urllib.request.Request(config['converTextToDraftApiEndpoint'])
    req.add_header('Content-Type', 'text/plain')
    req.add_header('Accept', 'Application/json')
    with urllib.request.urlopen(req, data) as f:
        j = json.loads(f.read().decode('utf-8'))

    return (j['draft'], j['html'], j['apiData'])


def main(config: dict = None, configGraphQL: dict = None, playlistIds: list = None, maxNumber: int = 3):
    ''' Import YouTube Channel program starts here '''
    print(f'{__file__} is executing...')

    # merge option to the default configs
    config = merge({}, __defaultConfig, config,
                   strategy=Strategy.TYPESAFE_REPLACE)
    config_graphql = merge({}, __defaultgraphqlCmsConfig, configGraphQL,
                           strategy=Strategy.TYPESAFE_REPLACE)
    ytrelayPlaylistItemsAPI = config['ytrelayEndpoints']['playlistItems']

    # CMS get authentication token
    print(f'attempting to log in as CMS user:{configGraphQL["username"]}')

    # Authenticate through GraphQL

    gqlEndpoint = configGraphQL['apiEndpoint']
    gqlTransport = RequestsHTTPTransport(
        url=gqlEndpoint,
        use_json=True,
        headers={
            "Content-type": "application/json",
        },
        verify=True,
        retries=3,
    )
    gqlClient = Client(
        transport=gqlTransport,
        fetch_schema_from_transport=False,
    )
    qglMutationAuthenticateGetToken = '''
    mutation {
        authenticate: authenticateUserWithPassword(email: "%s", password: "%s") {
            token
        }
    }
    '''
    mutation = gql(qglMutationAuthenticateGetToken %
                   (configGraphQL['username'], configGraphQL['password']))

    token = gqlClient.execute(mutation)['authenticate']['token']
    print(
        f'{os.path.basename(__file__)} has authenticated as {configGraphQL["username"]}')

    gqlTransportWithToken = RequestsHTTPTransport(
        url=gqlEndpoint,
        use_json=True,
        headers={
            "Content-type": "application/json",
            'Authorization': f'Bearer {token}'
        },
        verify=True,
        retries=3,
    )

    gqlAuthenticatedClient = Client(
        transport=gqlTransportWithToken,
        fetch_schema_from_transport=False,
    )

    for playlistId in playlistIds:
        params = dict(
            part='snippet',
            maxResults=maxNumber,
            playlistId=playlistId,
            fields='items(snippet/title,snippet/description,snippet/resourceId/videoId)'
        )
        headers = {'Cache-Set-TTL': 600}
        resp = requests.get(url=ytrelayPlaylistItemsAPI,
                            params=params, headers=headers)

        if resp.status_code < 200 or resp.status_code >= 300:
            print(
                f'ytrelayPlaylistItemsAPI has error({resp.status_code}):' + resp.text)
            sys.exit(1)

        data = resp.json()

        # check videos' existence in CMS
        items = [{'id': item['snippet']['resourceId']['videoId'], 'item': item}
                 for item in data['items']]

        # format query array
        queryConditions = ','.join(
            ['{url_ends_with: ' + '"' + item['id'] + '"}' for item in items])
        query = gql(__queryExistingVideosTemplate % queryConditions)

        existingVideos = [urllib.parse.parse_qs(urlparse(video['url'])[4])['v'][0] for video in gqlAuthenticatedClient.execute(query)[
            'allVideos']]

        for item in items:
            if item['id'] in existingVideos:
                print(f'Video({item["id"]}) is in CMS. Skip it.')
                continue
            # print(item)
            # save new video to CMS
            snippet = item['item']['snippet']
            brief = convertTextToDraft(config, snippet['description'])
            print(f'convert [{snippet["title"]}] brief to:\n{brief}')
            insertMutationStr = f'''
mutation {{
    createPost(data: {{
        slug: {json.dumps(snippet['resourceId']['videoId'], ensure_ascii=False)},
        state: draft,
        name: {json.dumps(snippet['title'], ensure_ascii=False)},
        style: videoNews,
        brief: {json.dumps(brief[0], ensure_ascii=False)},
        briefHtml: {json.dumps(brief[1], ensure_ascii=False)},
        briefApiData: {json.dumps(brief[2], ensure_ascii=False)},
        source: "yt",
        heroVideo: {{
            create: {{
                state: draft,
                youtubeUrl: {json.dumps('https://www.youtube.com/watch?v=' + snippet['resourceId']['videoId'], ensure_ascii=False)},
                name: {json.dumps(snippet['title'], ensure_ascii=False)}
            }}
        }}
    }}){{
        id
        slug
    }}
}}
'''
            print(
                f'insert mutation for post[{snippet["title"]}]:\n{insertMutationStr}')
            insertMutation = gql(insertMutationStr)
            result = gqlAuthenticatedClient.execute(insertMutation)
            if 'errors' not in result:
                print(
                    f'post(id:{result["createPost"]["id"]}) is created for {snippet["title"]}')
            else:
                print(f'[Error] {result["errors"]}')


if __name__ == '__main__':

    parser = argparse.ArgumentParser(
        description='Process configuration of importYouTubeChannel')
    parser.add_argument('-c', '--config', dest=CONFIG_KEY,
                        help='config file for importYouTubeChannel', metavar='FILE', type=str)
    parser.add_argument('-g', '--config-graphql', dest=GRAPHQL_CMS_CONFIG_KEY,
                        help='graphql config file for importYouTubeChannel', metavar='FILE', type=str, required=True)
    parser.add_argument('-p', '--playlist-ids', dest=PLAYLIST_IDS_KEY,
                        help='playlist ids, seperated by comma', metavar='PLIufxCyJpxOx4fCTTNcC7XCVZgY8MYQT5,PL1jBQxu5EklfCQ6rC-UzScji5zvNOBElG', type=str, required=True)
    parser.add_argument('-m', '--max-number', dest=MAX_NUMBER_KEY,
                        help='max number of videos per playlist', metavar='8', type=int, required=True)

    args = parser.parse_args()

    with open(getattr(args, CONFIG_KEY), 'r') as stream:
        config = yaml.safe_load(stream)
    with open(getattr(args, GRAPHQL_CMS_CONFIG_KEY), 'r') as stream:
        configGraphQL = yaml.safe_load(stream)
    playlistIds = getattr(args, PLAYLIST_IDS_KEY).split(',')
    maxNumber = getattr(args, MAX_NUMBER_KEY)

    main(config, configGraphQL, playlistIds, maxNumber)
