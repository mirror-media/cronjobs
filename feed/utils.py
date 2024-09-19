from google.cloud import storage
from datetime import datetime
from dateutil import parser
from lxml.etree import CDATA
import lxml.etree as ET
import gzip
import re
from gql import gql, Client
from gql.transport.aiohttp import AIOHTTPTransport
import logging
import pytz
import __main__


def create_authenticated_k5_client(config_graphql: dict) -> Client:
    logger = logging.getLogger(__main__.__file__)
    logger.setLevel('INFO')
    # Authenticate through GraphQL

    gql_endpoint = config_graphql['apiEndpoint']
    gql_transport = AIOHTTPTransport(url=gql_endpoint)
    gql_client = Client(transport=gql_transport,
                        fetch_schema_from_transport=False)
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


def stringWrapper(name, s):
    if name in ['title', 'content', 'author']:
        return CDATA(s)
    else:
        return s


def tsConverter(s):
    timeorigin = parser.parse(s)
    timediff = timeorigin - datetime(1970, 1, 1, tzinfo=pytz.utc)
    return round(timediff.total_seconds() * 1000)

def replace_alt_with_descrption(contentHtml, contentApiData, img_list):
    for img in img_list:
        if 'alt' in img:
            alt = re.findall('alt=\".*?\"', img)
            img_name = re.findall('\".*\"', alt[0])
            img_name = img_name[0].replace('"', '')
            for apidata_item in contentApiData:
                if apidata_item['type'] == 'image' and 'name' in apidata_item['content'][0] and apidata_item['content'][0]['name'] == img_name:
                    if 'title' in apidata_item['content'][0]:
                        new_description = apidata_item['content'][0]['title']
                    else:
                        new_description = ''
                    contentHtml = contentHtml.replace(
                        alt[0], f'alt="{new_description}"')
                    break
    return contentHtml


def sub(parentItem, tag, content=None):
    element = ET.SubElement(parentItem, tag)
    if content:
        element.text = stringWrapper(tag, content)
    return element

    
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
    elif t is str:
        parentItem.text = obj
    return


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
    blob.cache_control = 'max-age=300,public,must-revalidate'
    blob.patch()

    print(
        f'[{__main__.__file__}] finished uploading gs://{bucket_name}{destination_blob_name}')
