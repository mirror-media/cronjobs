from apiclient import discovery
from datetime import timedelta, date, datetime
from google.cloud import storage
from gql import gql, Client
from gql.transport.aiohttp import AIOHTTPTransport
from mergedeep import merge, Strategy
import argparse
import gzip
import json
import logging
import yaml


def CDN(file_host_domain_rule: dict, url: str):
    for key in file_host_domain_rule.keys():
        url = url.replace(key, file_host_domain_rule[key], 1)
    return url


def create_authenticated_k5_client(config_graphql: dict) -> Client:
    logger = logging.getLogger(__file__)
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


def gql_query_from_slugs(config_graphql: dict, file_host_domain_rule: dict, slugs: list) -> list:
    slug_cond = ','.join([f'{{slug: "{slug}" }}' for slug in slugs])

    gql_query = f"""
    query{{ 
        allPosts(where: {{OR:[ {slug_cond} ] }}){{
            id
            heroImage {{urlMobileSized, urlTinySized}}
            name
            publishTime
            slug
            source
        }}
    }}
    """

    gql_client = create_authenticated_k5_client(config_graphql)
    r = gql_client.execute(gql(gql_query))

    data = r['allPosts']
    for item in data:
        if item['heroImage']:
            item['heroImage']['urlMobileSized'] = CDN(file_host_domain_rule,
                                                      item['heroImage']['urlMobileSized'])
            item['heroImage']['urlTinySized'] = CDN(file_host_domain_rule,
                                                    item['heroImage']['urlTinySized'])

    return data


def initialize_analyticsreporting() -> discovery.Resource:
    '''Initializes an analyticsreporting service object.

    Returns:
        analytics an authorized analyticsreporting service object.
    '''

    # Build the service object.
    analytics = discovery.build('analyticsreporting', 'v4')

    return analytics


def get_report(analytics: discovery.Resource, analytics_id: str, page_path_level1_regex_filter: list, additional_dimension_filters: list, page_size: int, date_range: tuple) -> dict:
    # Use the Analytics Service Object to query the Analytics Reporting API V4.
    dimensions = [{'name': 'ga:pagePathLevel2'}]
    dimension_filters = [
        {
            'dimensionName': 'ga:pagePathLevel1',
            'operator': 'REGEXP',
            'expressions': page_path_level1_regex_filter,
        }
    ]
    if additional_dimension_filters is not None:
        for additional_dimension_filter in additional_dimension_filters:
            dimensions.append(
                {'name': additional_dimension_filter['dimensionName']})
            dimension_filters.append({
                'dimensionName': additional_dimension_filter['dimensionName'],
                'not': additional_dimension_filter['not'] if dict.get(additional_dimension_filter,
                                                                      'not') != None else False,
                'operator': additional_dimension_filter['operator'] if dict.get(additional_dimension_filter,
                                                                                'operator') != None else 'REGEXP',
                'expressions': additional_dimension_filter['expressions'],
            })
    print(f'requesting report in {date_range}')
    return analytics.reports().batchGet(
        body={
            'reportRequests': [
                {
                    'viewId': analytics_id,
                    'dateRanges': [{'startDate': date_range[0], 'endDate': date_range[-1]}],
                    'metrics': [
                        {'expression': 'ga:pageviews'}
                    ],
                    'orderBys': [{'fieldName': 'ga:pageviews', 'sortOrder': 'DESCENDING'}],
                    'dimensions': dimensions,
                    'dimensionFilterClauses': [
                        {
                            'operator': 'AND',
                            'filters': dimension_filters
                        }
                    ],
                    'pageSize': page_size,
                }]
        }
    ).execute()


def upload_blob(bucket_name: str, destination_blob_name: str, report: bytes):
    '''Uploads a string to the bucket.'''
    # destination_blob_name = 'storage-object-name'

    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(f'json/{destination_blob_name}')

    blob.content_encoding = 'gzip'
    blob.upload_from_string(
        data=gzip.compress(data=report, compresslevel=9), content_type='application/json; charset=utf-8', client=storage_client)
    blob.content_language = 'zh'
    blob.cache_control = 'max-age=300,public'
    blob.patch()

    print(
        f'Report is uploaded to bucket://{bucket_name}/json/{destination_blob_name}')


def convert_response_to_report(config_graphql: dict, slugBlacklist: list, date_range: tuple, response: dict) -> str:
    '''Parse the response and generate the json format file for it'''
    result = {}
    if 'rows' in response['reports'][0]['data']:
        data = response['reports'][0]['data']['rows']
        data = sorted(
            data, key=lambda x: int(x['metrics'][0]['values'][0]), reverse=True)
    else:
        data = []
    slugs = [item['dimensions'][0].replace(
        '/', '') for item in data if item['dimensions'][0].replace('/', '') not in slugBlacklist]

    result['report'] = gql_query_from_slugs(
        config_graphql, config['report']['fileHostDomainRule'], slugs)
    result['start_date'] = str(date_range[0])
    result['end_date'] = str(date_range[-1])
    result['generate_time'] = str(datetime.now())

    return json.dumps(result, ensure_ascii=False)


__default_config = {
    'analyticsID': '',
    'report': {
        'bucketName': '',
        'fileName': 'popularlist.json',
        'filter': ['^\/story\/|^\/projects\/'],
        'pageSize': 20,
    },
    'fileHostDomainRule': {
        'https://storage.googleapis.com/mirrormedia-files': 'https://www.mirrormedia.mg',
        'https://storage.googleapis.com/static-mnews-tw-dev': 'https://dev.mnews.tw',

        'https://storage.googleapis.com/mirror-tv-file': 'https://dev.mnews.tw',
        'slugBlacklist':
        [
        ],
    }
}

__default_graphql_cms_config = {
    'username': '',
    'password': '',
    'apiEndpoint': '',
}


def main(config: dict, config_graphql: dict, days: int):
    print(f'{__file__} is executing...')

    # merge option to the default configs
    config = merge({}, __default_config, config,
                   strategy=Strategy.TYPESAFE_REPLACE)
    config_graphql = merge({}, __default_graphql_cms_config, config_graphql,
                           strategy=Strategy.TYPESAFE_REPLACE)
    if days < 0:
        days = 1

    today = date.today()
    date_range = (str(today - timedelta(days=days)), str(today))

    analytics = initialize_analyticsreporting()
    response = get_report(
        analytics, config['analyticsID'], config['report']['pagePathLevel1RegexFilter'], config['report']['additionalDimensionFilters'], config['report']['pageSize'], date_range)
    print(f'ga response:{response}')
    report = convert_response_to_report(
        config_graphql, config['slugBlacklist'], date_range, response)
    print(f'report generated: {report}')
    upload_blob(
        bucket_name=config['report']['bucketName'], destination_blob_name=config['report']['fileName'], report=report.encode('utf-8'))


CONFIG_KEY = 'config'
GRAPHQL_CMS_CONFIG_KEY = 'graphqlCMS'
DAYS_KEY = 'days'

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Process configuration of generatePopularArticles')
    parser.add_argument('-c', '--config', dest=CONFIG_KEY,
                        help='config file for generatePopularArticles', metavar='FILE', type=str)
    parser.add_argument('-g', '--config-graphql', dest=GRAPHQL_CMS_CONFIG_KEY,
                        help='graphql config file for generatePopularArticles', metavar='FILE', type=str, required=True)
    parser.add_argument('-d', '--days', dest=DAYS_KEY,
                        help='the number of days for the report before now', metavar='2', type=int, required=True)

    args = parser.parse_args()

    with open(getattr(args, CONFIG_KEY), 'r') as stream:
        config = yaml.safe_load(stream)
    with open(getattr(args, GRAPHQL_CMS_CONFIG_KEY), 'r') as stream:
        config_graphql = yaml.safe_load(stream)
    days = getattr(args, DAYS_KEY)

    main(config, config_graphql, days)
